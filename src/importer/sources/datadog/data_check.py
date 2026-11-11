"""Check, read-only, that the data behind each converted Datadog condition reaches New Relic.

Metric and tag names are assumed to be the Datadog ones (UNVERIFIED, docs/research/
datadog-verified.md section 13), so this check is how they become verified. For each converted
condition it counts the data points of each metric (`WHERE metricName = ...`) or the logs, and
checks that the attributes the condition filters or facets on exist. When a metric is missing it
lists metric names that did arrive with the same first two name segments, as candidates for
`name-map.json`. Only SELECT queries built from validated names are sent.
"""

from importer.core.apply import NewRelicClient
from importer.core.data_check import Check, Status, first_number, rows
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.emit.nrql import identifier, quote_string
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.core.report import md_cell
from importer.core.report_brand import header_lines

CANDIDATES = 15
NOTE_LIMIT = 300
N_MANUAL = "manual conversion: no query was made"
N_NO_NAME = "the query has no metric name to check"
N_NO_LOGS = "no logs arrive at all: is the log forwarder sending Datadog-style logs?"


def _metrics(monitor: IRMonitor) -> list[tuple[str, str]]:
    sig = monitor.signal.metric
    if sig is None:
        return []
    found = [(t.nr_target.event, t.nr_target.attribute) for t in sig.terms.values() if t.nr_target]
    return [(e, a) for e, a in found if a]


def _attributes(monitor: IRMonitor) -> list[str]:
    sig = monitor.signal.metric
    if sig is not None:
        keys = [f.key for f in sig.scope] + sig.group_by
        keys += [f.key for t in sig.terms.values() for f in t.scope]
    elif monitor.signal.count is not None:
        keys = [f.key for f in monitor.signal.count.filters] + monitor.signal.count.group_by
    else:
        keys = []
    return list(dict.fromkeys(k for k in keys if k != "message"))


def _count(client: NewRelicClient, account: int, query: str) -> int:
    return int(first_number(rows(client, account, query)) or 0)


def _missing(
    client: NewRelicClient, account: int, event: str, where: str, attrs: list[str], since: str
) -> list[str]:
    gone: list[str] = []
    clause = f"WHERE {where} " if where else ""
    for attr in attrs:
        query = (
            f"SELECT filter(count(*), WHERE {identifier(attr)} IS NOT NULL) "  # noqa: S608
            f"FROM {identifier(event)} {clause}{since}"
        )
        if not _count(client, account, query):
            gone.append(attr)
    return gone


def _candidates(client: NewRelicClient, account: int, name: str, since: str) -> list[str]:
    prefix = ".".join(name.split(".")[:2]) + "%"
    query = (
        f"SELECT uniques(metricName, {CANDIDATES}) FROM Metric "  # noqa: S608
        f"WHERE metricName LIKE {quote_string(prefix)} {since}"
    )
    found: list[str] = []
    for row in rows(client, account, query):
        for value in row.values():
            found += [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
    return found[:CANDIDATES]


def _check_metric(client: NewRelicClient, account: int, monitor: IRMonitor, since: str) -> Check:
    targets = _metrics(monitor)
    if not targets:
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, N_NO_NAME)
    points = 0
    for event, name in targets:
        where = f"metricName = {quote_string(name)}"
        count = _count(
            client,
            account,
            f"SELECT count(*) FROM {identifier(event)} WHERE {where} {since}",  # noqa: S608
        )
        if not count:
            note = f"the metric {name} has not arrived, so this condition would never fire"
            near = _candidates(client, account, name, since) if event == "Metric" else []
            if near:
                note += (
                    "; metric names that did arrive with the same start: "
                    + ", ".join(near)
                    + ". If one is the same metric, put it in name-map.json and convert again"
                )
            return Check(monitor.name, Status.NO_DATA, 0, None, note)
        points += count
        gone = _missing(client, account, event, where, _attributes(monitor), since)
        if gone:
            note = (
                "the metric arrives but these attributes are missing, so the filter or facet "
                "matches nothing: " + ", ".join(gone) + "; the tag may arrive under another "
                "name (put it in name-map.json under attributes)"
            )
            return Check(monitor.name, Status.NO_DATA, count, None, note)
    value = first_number(rows(client, account, f"{nrql_for(monitor)} {since}"))
    return Check(monitor.name, Status.DATA, points, value, "metrics and attributes found")


def _check_log(client: NewRelicClient, account: int, monitor: IRMonitor, since: str) -> Check:
    count = _count(client, account, f"SELECT count(*) FROM Log {since}")  # noqa: S608
    if not count:
        return Check(monitor.name, Status.NO_DATA, 0, None, N_NO_LOGS)
    gone = _missing(client, account, "Log", "", _attributes(monitor), since)
    if gone:
        note = (
            "logs arrive but these attributes are missing: "
            + ", ".join(gone)
            + "; inspect one record and use name-map.json"
        )
        return Check(monitor.name, Status.NO_DATA, count, None, note)
    value = first_number(rows(client, account, f"{nrql_for(monitor)} {since}"))
    return Check(monitor.name, Status.DATA, count, value, "logs and attributes found")


def check_monitor(client: NewRelicClient, account: int, monitor: IRMonitor, hours: int) -> Check:
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, N_MANUAL)
    since = f"SINCE {int(hours)} hours ago"
    try:
        nrql_for(monitor)
        if monitor.signal.kind is SignalKind.LOG:
            return _check_log(client, account, monitor, since)
        return _check_metric(client, account, monitor, since)
    except (CannotEmit, ValueError) as exc:
        return Check(monitor.name, Status.NOT_CHECKED, 0, None, str(exc)[:120])


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [
        check_monitor(client, account_id, m, hours)
        for m in monitors
        if m.source_system == "datadog"
    ]


def render_report(checks: list[Check], hours: int) -> str:
    lines = [
        *header_lines("Data check", "datadog"),
        "",
        f"Data points seen in the last {hours} hours for the metric or logs behind each "
        "converted condition. A condition with no data cannot fire. Names are assumed to be the "
        "Datadog ones: a row that says data arrives is the first confirmation of that name.",
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
