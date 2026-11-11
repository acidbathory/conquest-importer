"""Check, read-only, that the data behind each converted Azure condition reaches New Relic.

A condition on a metric that never arrives looks healthy and never fires. Metric names here are
derived from the documented naming rule and are UNVERIFIED until seen arriving, so this check is how
they become verified. For each converted condition it counts the data points of the metric (or the
forwarded Azure logs) and checks that the attributes the condition filters or facets on exist. When
a metric is missing it lists the metric names the Azure Monitor integration did send for the same
resource type, as candidates for `name-map.json`. Only SELECT queries built from validated names
are sent.
"""

from importer.core.apply import NewRelicClient
from importer.core.data_check import Check, Status, first_number, rows
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.emit.nrql import identifier, quote_string
from importer.core.ir import Confidence, IRMonitor, NrTarget, SignalKind
from importer.core.report import md_cell
from importer.core.report_brand import header_lines

COLLECTOR = "collector.name = 'azure-monitor'"
FORWARDED = "plugin.type = 'azure'"
CANDIDATES = 15
NOTE_LIMIT = 300
N_NO_LOGS = (
    "no forwarded Azure logs: is the Event Hub forwarder deployed with a diagnostic setting on "
    "the resource?"
)
N_MANUAL = "manual conversion: no query was made"
N_NO_NAME = "the query has no metric name to check"


def _metric_target(monitor: IRMonitor) -> NrTarget | None:
    metric = monitor.signal.metric
    if metric is None:
        return None
    return next((t.nr_target for t in metric.terms.values() if t.nr_target), None)


def _attributes(monitor: IRMonitor) -> list[str]:
    """Attributes beyond the metric itself that the condition filters or facets on."""
    metric = monitor.signal.metric
    if metric is None:
        return []
    keys = [f.key for f in metric.scope if f.key not in ("collector.name", "plugin.type")]
    return list(dict.fromkeys([*keys, *metric.group_by]))


def _log_attributes(monitor: IRMonitor) -> list[str]:
    count = monitor.signal.count
    keys = [f.key for f in count.filters if f.key != "plugin.type"] if count else []
    return list(dict.fromkeys([*keys, *(count.group_by if count else [])]))


def _missing(
    client: NewRelicClient, account: int, event: str, where: str, attrs: list[str], since: str
) -> list[str]:
    missing: list[str] = []
    for attr in attrs:
        name = identifier(attr)
        query = (
            f"SELECT filter(count(*), WHERE {name} IS NOT NULL) FROM {identifier(event)} "  # noqa: S608
            f"WHERE {where} {since}"
        )
        if not (first_number(rows(client, account, query)) or 0):
            missing.append(attr)
    return missing


def _candidates(client: NewRelicClient, account: int, attribute: str, since: str) -> list[str]:
    prefix = attribute.rsplit(".", 1)[0] + ".%"
    query = (
        f"SELECT uniques(metricName, {CANDIDATES}) FROM Metric WHERE {COLLECTOR} "  # noqa: S608
        f"AND metricName LIKE {quote_string(prefix)} {since}"
    )
    found: list[str] = []
    for row in rows(client, account, query):
        for value in row.values():
            found += [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
    return found[:CANDIDATES]


def _check_metric(client: NewRelicClient, account: int, monitor: IRMonitor, since: str) -> Check:
    target = _metric_target(monitor)
    if target is None or not target.attribute:
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, N_NO_NAME)
    attr = identifier(target.attribute)
    count_query = f"SELECT count({attr}) FROM {identifier(target.event)} WHERE {COLLECTOR} {since}"  # noqa: S608
    points = int(first_number(rows(client, account, count_query)) or 0)
    if not points:
        near = _candidates(client, account, target.attribute, since)
        note = f"the metric {target.attribute} has not arrived, so this condition would never fire"
        if near:
            note += (
                "; metric names that did arrive for this resource type: "
                + ", ".join(near)
                + ". If one is the same metric, put it in name-map.json and convert again"
            )
        return Check(monitor.name, Status.NO_DATA, 0, None, note)
    where = f"{COLLECTOR} AND {attr} IS NOT NULL"
    gone = _missing(client, account, target.event, where, _attributes(monitor), since)
    if gone:
        note = (
            "the metric arrives but these attributes are missing, so the filter or facet "
            "matches nothing: " + ", ".join(gone)
        )
        return Check(monitor.name, Status.NO_DATA, points, None, note)
    value = first_number(rows(client, account, f"{nrql_for(monitor)} {since}"))
    return Check(monitor.name, Status.DATA, points, value, "metric and attributes found")


def _check_log(client: NewRelicClient, account: int, monitor: IRMonitor, since: str) -> Check:
    count = int(
        first_number(
            rows(client, account, f"SELECT count(*) FROM Log WHERE {FORWARDED} {since}")  # noqa: S608
        )
        or 0
    )
    if not count:
        return Check(monitor.name, Status.NO_DATA, 0, None, N_NO_LOGS)
    attrs = (
        _log_attributes(monitor) if monitor.signal.kind is SignalKind.LOG else _attributes(monitor)
    )
    gone = _missing(client, account, "Log", FORWARDED, attrs, since)
    if gone:
        note = (
            "forwarded logs arrive but these attributes are missing: "
            + ", ".join(gone)
            + "; inspect one record and use name-map.json"
        )
        return Check(monitor.name, Status.NO_DATA, count, None, note)
    value = first_number(rows(client, account, f"{nrql_for(monitor)} {since}"))
    return Check(monitor.name, Status.DATA, count, value, "forwarded logs and attributes found")


def check_monitor(client: NewRelicClient, account: int, monitor: IRMonitor, hours: int) -> Check:
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, N_MANUAL)
    since = f"SINCE {int(hours)} hours ago"
    try:
        nrql_for(monitor)
        target = _metric_target(monitor)
        on_log = monitor.signal.kind is SignalKind.LOG or (
            target is not None and target.event == "Log"
        )
        if on_log:
            return _check_log(client, account, monitor, since)
        return _check_metric(client, account, monitor, since)
    except (CannotEmit, ValueError) as exc:
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, str(exc)[:120])


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [
        check_monitor(client, account_id, m, hours) for m in monitors if m.source_system == "azure"
    ]


def render_report(checks: list[Check], hours: int) -> str:
    lines = [
        *header_lines("Data check", "azure"),
        "",
        f"Data points seen in the last {hours} hours for the metric or forwarded logs behind each "
        "converted condition. A condition with no data cannot fire. Metric names are derived "
        "from the documented naming rule: a row that says data arrives is the first confirmation "
        "of that name.",
        "",
        "| Condition | Status | Points | Latest query value | Note |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        value = "" if c.value is None else f"{c.value:g}"
        note = md_cell(c.note, NOTE_LIMIT)
        lines.append(f"| {md_cell(c.name)} | {c.status.value} | {c.points} | {value} | {note} |")
    return "\n".join(lines) + "\n"


def has_blind_conditions(checks: list[Check]) -> bool:
    return any(c.status is Status.NO_DATA for c in checks)
