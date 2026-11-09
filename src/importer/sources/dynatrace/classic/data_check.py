"""Check, read-only, that the New Relic signal behind each converted Dynatrace condition has data.

Dynatrace metrics do not exist in New Relic unless an agent, integration or exporter sends
equivalent data, and a condition on a signal that never arrives looks healthy and never fires.
For every converted metric condition this counts the points of each term's signal over the last
hours. A condition is blind when any term has none. Only count queries built from names the
converter produced (map-checked identifiers) are sent.
"""

from importer.core.apply import NewRelicClient
from importer.core.data_check import Check, Status, first_number, has_blind_conditions, rows
from importer.core.data_check import render as render_checks
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.emit.nrql import identifier
from importer.core.ir import Confidence, IRMonitor, MetricTerm, SignalKind
from importer.core.text import printable
from importer.sources.dynatrace.classic.metric_map import lookup

INTRO = (
    "Points seen for the New Relic signal behind each converted Dynatrace condition. A threshold "
    "condition on a signal with no data cannot fire; the data must be sent first (see the data "
    "prerequisite in the conversion report)."
)
NO_DATA = (
    "the signal has not arrived, so this condition would never fire; install or enable what the "
    "data prerequisite names"
)


def _count_query(term: MetricTerm, hours: int) -> str:
    """A count of the term's signal; a fixed expression is counted as rows of its event."""
    target = term.nr_target
    if target is None:
        raise CannotEmit("a term has no New Relic target")
    attribute = target.attribute
    if attribute is None:  # a select was built from the map; the map still knows the attribute
        row = lookup(term.metric)
        attribute = row.attribute if row else None
    what = f"count({identifier(attribute)})" if attribute else "count(*)"
    return f"SELECT {what} FROM {identifier(target.event)} SINCE {int(hours)} hours ago"  # noqa: S608


def check_monitor(client: NewRelicClient, account_id: int, monitor: IRMonitor, hours: int) -> Check:
    name = monitor.name
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return Check(name, Status.NOT_CHECKED, 0, None, "manual conversion: no query was made")
    metric = monitor.signal.metric
    if monitor.signal.kind is not SignalKind.METRIC or metric is None:
        return Check(name, Status.NOT_CHECKED, 0, None, "not a metric condition: no query was made")
    try:
        nrql = nrql_for(monitor)
        queries = {label: _count_query(term, hours) for label, term in metric.terms.items()}
    except (CannotEmit, ValueError) as exc:
        return Check(name, Status.NOT_CHECKED, 0, None, printable(str(exc), 120))
    counts = {
        label: int(first_number(rows(client, account_id, q)) or 0) for label, q in queries.items()
    }
    empty = sorted(label for label, n in counts.items() if not n)
    if empty:
        which = f" (term {', '.join(empty)} of {len(counts)})" if len(counts) > 1 else ""
        return Check(name, Status.NO_DATA, 0, None, NO_DATA + which)
    value = first_number(rows(client, account_id, f"{nrql} SINCE {int(hours)} hours ago"))
    return Check(name, Status.DATA, min(counts.values()), value, "")


def check_all(
    client: NewRelicClient, account_id: int, monitors: list[IRMonitor], hours: int = 24
) -> list[Check]:
    return [check_monitor(client, account_id, m, hours) for m in monitors]


def render_report(checks: list[Check], hours: int) -> str:
    return render_checks(checks, hours, INTRO)


__all__ = ["check_all", "check_monitor", "has_blind_conditions", "render_report"]
