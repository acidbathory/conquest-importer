"""Check, read-only, that log events matching each converted Dynatrace condition reach New Relic.

Only log-count conditions convert from Dynatrace, so the check is a count of matching `Log`
events. When there are none it asks a second question, whether any log events arrive at all, so
the note can say whether logs are not forwarded or the attribute names do not match.
"""

from importer.core.apply import NewRelicClient
from importer.core.data_check import Check, Status, first_number, has_blind_conditions, rows
from importer.core.data_check import render as render_checks
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.core.text import printable

NOT_FORWARDED = "no log events at all in {hours} hours; are the logs forwarded to New Relic?"
NO_MATCH = (
    "no matching events in {hours} hours: the filters may not match the attribute names in "
    "New Relic, or the event did not occur"
)
INTRO = (
    "Log events seen for each converted log-count condition. No matching events does not "
    "prove the condition is broken: the event may simply not have occurred in the window."
)


def check_monitor(client: NewRelicClient, account_id: int, monitor: IRMonitor, hours: int) -> Check:
    name = monitor.name
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return Check(name, Status.NOT_CHECKED, 0, None, "manual conversion: no query was made")
    if monitor.signal.kind is not SignalKind.LOG:
        return Check(name, Status.NOT_CHECKED, 0, None, "not a log condition: no query was made")
    try:
        nrql = nrql_for(monitor)
    except CannotEmit as exc:
        return Check(name, Status.NOT_CHECKED, 0, None, printable(str(exc), 120))
    since = f"SINCE {int(hours)} hours ago"
    points = int(first_number(rows(client, account_id, f"{nrql} {since}")) or 0)
    if points:
        return Check(name, Status.DATA, points, float(points), "")
    anything = first_number(rows(client, account_id, f"SELECT count(*) FROM Log {since}"))  # noqa: S608 - fixed text, int hours
    return Check(
        name,
        Status.NO_DATA,
        0,
        None,
        (NO_MATCH if anything else NOT_FORWARDED).format(hours=int(hours)),
    )


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [check_monitor(client, account_id, m, hours) for m in monitors]


def render_report(checks: list[Check], hours: int) -> str:
    return render_checks(checks, hours, INTRO)


__all__ = ["check_all", "check_monitor", "has_blind_conditions", "render_report"]
