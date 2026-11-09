"""Check, read-only, that the data behind each converted Grafana condition reaches New Relic.

A condition on data that never arrives looks healthy and never fires. For each converted
Prometheus condition this asks whether the metric arrives, which attribute names it carries
(the labels the NRQL filters or facets on must be among them) and how many Prometheus servers
write it; for each converted Loki condition, whether matching log events arrive and which
attributes they carry. Queries are built only from validated names (`datapath/plan.py`) or from
the converter's own NRQL; only SELECT queries are sent.

Uses the shared pieces in `core/data_check.py`. Evidence levels are in the research note
(section 6.8): the label-to-attribute naming and the `prometheus_server` attribute are from
documentation, none of it was seen on a live account.
"""

from typing import Any

from importer.core.apply import NewRelicClient
from importer.core.data_check import (
    Check,
    Status,
    first_number,
    has_blind_conditions,
    render,
    rows,
)
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.core.text import printable
from importer.sources.grafana.datapath.plan import (
    ATTRIBUTE_NAME,
    METRIC_NAME,
    log_queries,
    metric_queries,
)

__all__ = [
    "check_all",
    "check_monitor",
    "has_blind_conditions",
    "render_report",
    "targets_from_monitors",
]

NAME_LIMIT = 120
INTRO = (
    "Data points seen for the metric or log events behind each converted Grafana condition. A "
    "threshold condition with no data cannot fire. A missing filter attribute means the "
    "condition's WHERE clause matches nothing."
)
N_NO_METRIC = (
    "the metric has not arrived, so this condition would never fire; is it sent by remote "
    "write or OTLP?"
)
N_NO_LOGS = "no matching log events; are the logs forwarded, and do they carry these attributes?"
N_MANUAL = "manual conversion: no query was made"
N_NO_NAME = "the query has no metric name that can be checked safely"


def _grafana(monitor: IRMonitor) -> bool:
    return monitor.source_system == "grafana"


def _metric_names(monitor: IRMonitor) -> list[str]:
    metric = monitor.signal.metric
    return [t.metric for t in metric.terms.values()] if metric else []


def _required_labels(monitor: IRMonitor) -> tuple[list[str], list[str]]:
    """(labels the WHERE clause needs, labels the FACET uses)."""
    metric = monitor.signal.metric
    if metric is None:
        return [], []
    where = [f.key for f in metric.scope if not f.negate and not f.absent]
    return list(dict.fromkeys(where)), list(dict.fromkeys(metric.group_by))


def targets_from_monitors(monitors: list[IRMonitor]) -> tuple[list[str], list[str]]:
    """(metric names, log attribute names) of the usable converted Grafana conditions."""
    metrics: set[str] = set()
    attributes: set[str] = set()
    for monitor in monitors:
        if not _grafana(monitor) or monitor.confidence is Confidence.MANUAL:
            continue
        metrics.update(n for n in _metric_names(monitor) if METRIC_NAME.fullmatch(n))
        count = monitor.signal.count
        if count is not None:
            attributes.update(f.key for f in count.filters if ATTRIBUTE_NAME.fullmatch(f.key))
    return sorted(metrics), sorted(attributes)


def _missing(wanted: list[str], keys: set[str]) -> list[str]:
    return [w for w in wanted if w not in keys]


def _servers(result: list[dict[str, Any]]) -> int:
    for value in result[0].values() if result else []:
        if isinstance(value, list):
            return len(value)
    return 0


def _check_metric(client: NewRelicClient, account: int, monitor: IRMonitor, hours: int) -> Check:
    name = printable(monitor.name, NAME_LIMIT)
    metrics = _metric_names(monitor)
    if len(metrics) != 1 or not METRIC_NAME.fullmatch(metrics[0]):
        return Check(name, Status.NOT_CHECKED, 0, None, N_NO_NAME)
    count_q, keyset_q, servers_q = metric_queries(metrics[0], hours)
    points = int(first_number(rows(client, account, count_q)) or 0)
    if not points:
        return Check(name, Status.NO_DATA, 0, None, N_NO_METRIC)
    keys = {str(r["key"]) for r in rows(client, account, keyset_q) if "key" in r}
    where, facet = _required_labels(monitor)
    blind = _missing(where, keys)
    notes = []
    if blind:
        notes.append("the filter attribute(s) " + ", ".join(blind) + " are not on the metric")
    if missing := _missing(facet, keys):
        notes.append("the group-by attribute(s) " + ", ".join(missing) + " are not on the metric")
    if (servers := _servers(rows(client, account, servers_q))) > 1:
        notes.append(f"{servers} Prometheus servers write this metric; sum() counts it twice")
    status = Status.NO_DATA if blind else Status.DATA
    return Check(name, status, points, float(points), "; ".join(notes))


def _check_logs(client: NewRelicClient, account: int, monitor: IRMonitor, hours: int) -> Check:
    name = printable(monitor.name, NAME_LIMIT)
    try:
        nrql = nrql_for(monitor)
    except CannotEmit as exc:
        return Check(name, Status.NOT_CHECKED, 0, None, printable(str(exc), NAME_LIMIT))
    count = monitor.signal.count
    wanted = [f.key for f in count.filters if ATTRIBUTE_NAME.fullmatch(f.key)] if count else []
    points = int(first_number(rows(client, account, f"{nrql} SINCE {int(hours)} hours ago")) or 0)
    if points:
        return Check(name, Status.DATA, points, float(points), "")
    keyset = log_queries([], hours)[0]
    keys = {str(r["key"]) for r in rows(client, account, keyset) if "key" in r}
    missing = _missing(list(dict.fromkeys(wanted)), keys)
    note = (
        N_NO_LOGS
        if not missing
        else "log attribute(s) " + ", ".join(missing) + " are not on any log event"
    )
    return Check(name, Status.NO_DATA, 0, None, note)


def check_monitor(client: NewRelicClient, account_id: int, monitor: IRMonitor, hours: int) -> Check:
    name = printable(monitor.name, NAME_LIMIT)
    if not _grafana(monitor) or monitor.confidence not in (
        Confidence.EXACT,
        Confidence.APPROXIMATE,
    ):
        return Check(name, Status.NOT_CHECKED, 0, None, N_MANUAL)
    if monitor.signal.kind is SignalKind.LOG:
        return _check_logs(client, account_id, monitor, hours)
    if monitor.signal.kind is SignalKind.METRIC:
        return _check_metric(client, account_id, monitor, hours)
    return Check(name, Status.NOT_CHECKED, 0, None, N_NO_NAME)


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [check_monitor(client, account_id, m, hours) for m in monitors]


def render_report(checks: list[Check], hours: int) -> str:
    return render(checks, hours, INTRO)
