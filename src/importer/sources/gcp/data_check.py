"""Check, read-only, that the data behind each converted Google Cloud condition reaches New Relic.

A condition on a metric that never arrives looks healthy and never fires: `sum()` of nothing is
0 and `average()` is null, neither is an error. This asks New Relic how many data points each
metric (or, for log conditions, each log event) has had recently, and says which conditions would
be blind. It runs only count queries built from names the converter produced.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from importer.core.apply import NewRelicClient
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.emit.nrql import identifier
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.core.text import printable

NRQL_QUERY = (
    "query($a: Int!, $n: Nrql!) { actor { account(id: $a) { nrql(query: $n) { results } } } }"
)


class Status(StrEnum):
    DATA = "data arrives"
    NO_DATA = "no data"
    NOT_CHECKED = "not checked"


@dataclass
class Check:
    name: str
    status: Status
    points: int
    value: float | None
    note: str


def _rows(client: NewRelicClient, account_id: int, query: str) -> list[dict[str, Any]]:
    data = client.run(NRQL_QUERY, {"a": account_id, "n": query}, is_write=False)
    rows: list[dict[str, Any]] = data["actor"]["account"]["nrql"]["results"]
    return rows


def _number(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    return float(value) if isinstance(value, int | float) else None


def check_monitor(client: NewRelicClient, account_id: int, monitor: IRMonitor, hours: int) -> Check:
    name = monitor.name
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return Check(name, Status.NOT_CHECKED, 0, None, "manual conversion: no query was made")
    try:
        nrql = nrql_for(monitor)
    except CannotEmit as exc:
        return Check(name, Status.NOT_CHECKED, 0, None, printable(str(exc), 120))
    since = f"SINCE {int(hours)} hours ago"
    if monitor.signal.kind is SignalKind.LOG:
        points = int(_number(_rows(client, account_id, f"{nrql} {since}")) or 0)
        note = "" if points else "no matching log events; are the project's logs forwarded?"
        return Check(name, Status.DATA if points else Status.NO_DATA, points, float(points), note)
    metric = monitor.signal.metric
    targets = (
        [t.nr_target for t in metric.terms.values() if t.nr_target and t.nr_target.attribute]
        if metric
        else []
    )
    if not targets:
        return Check(name, Status.NOT_CHECKED, 0, None, "the query has no metric name to check")
    points = 0
    for target in targets:
        attr, event = identifier(target.attribute or ""), identifier(target.event)
        count_query = f"SELECT count({attr}) FROM {event} {since}"  # noqa: S608
        points += int(_number(_rows(client, account_id, count_query)) or 0)
    value = _number(_rows(client, account_id, f"{nrql} {since}")) if points else None
    note = "" if points else "the metric has not arrived, so this condition would never fire"
    return Check(name, Status.DATA if points else Status.NO_DATA, points, value, note)


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [check_monitor(client, account_id, m, hours) for m in monitors]


def render(checks: list[Check], hours: int) -> str:
    from importer.core.report import md_cell

    lines = [
        "# Data check",
        "",
        f"Data points seen in the last {hours} hours for the metric behind each converted "
        "condition. A condition with no data cannot fire.",
        "",
        "| Condition | Status | Points | Latest query value | Note |",
        "|---|---|---|---|---|",
    ]
    for c in checks:
        value = "" if c.value is None else f"{c.value:g}"
        lines.append(
            f"| {md_cell(c.name)} | {c.status.value} | {c.points} | {value} | {md_cell(c.note)} |"
        )
    return "\n".join(lines) + "\n"


def has_blind_conditions(checks: list[Check]) -> bool:
    return any(c.status is Status.NO_DATA for c in checks)
