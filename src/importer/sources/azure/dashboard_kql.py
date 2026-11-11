"""The one place where dashboards ask for a KQL query to be turned into NRQL.

A workbook or dashboard query is not an alert rule: nothing compares its result with a threshold.
It is read with the same KQL subset parser, table classifier and NRQL writers as the scheduled
query rules (`kql_parse.py`, `kql_tables.py`, `kql_nrql.py`), and becomes one SELECT over `Log`:
the aggregates, a WHERE for the rows, a FACET for the `by` columns and TIMESERIES for a
`bin(TimeGenerated, x)`. A query without an aggregate (raw rows), a `where` on the result and every
construct outside the subset stay placeholders with the reason. Callers reach this as
`dashboard_kql.translate_kql(...)` (never `from ... import`), so a test can replace it.
"""

import re
from collections.abc import Collection
from dataclasses import dataclass

from importer.core.emit.nrql import quote_string
from importer.sources.azure import dashboard_reasons as DR
from importer.sources.azure import reasons as R
from importer.sources.azure.kql_lex import KqlSyntax, KqlUnsupported
from importer.sources.azure.kql_nrql import aggregate_sql, predicate_sql, ref
from importer.sources.azure.kql_parse import Agg, Cmp, Query, Regex, is_negated, parse, walk
from importer.sources.azure.kql_tables import AGENT, classify
from importer.sources.azure.models import NameMap
from importer.sources.azure.names import log_attribute

LOG_FORWARDING = "needs-log-forwarding"
FORWARDED = ("plugin.type", "azure")  # the attribute New Relic's forwarder documents
UNITS = ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second"))
MAX_AGGREGATES = 6
PARAMETER = re.compile(r"\{[A-Za-z_][^{}]{0,80}\}")  # a workbook parameter: {Name}, {Name:label}
COUNT_LIKE = ("count", "countif")
AUTO = "AUTO"  # the TIMESERIES size New Relic picks from the time picker (the workbook's grain)


@dataclass(frozen=True)
class KqlNrql:
    """A KQL query as NRQL: one SELECT over a table the logs arrive in."""

    nrql: str
    timeseries: bool = False  # the query returns one value per time bucket
    notes: tuple[str, ...] = ()
    variables: tuple[str, ...] = ()  # New Relic dashboard variables the NRQL uses as {{name}}
    data_path: str = LOG_FORWARDING  # what must be in place for the NRQL to return data


def _span(seconds: int) -> str:
    for size, unit in UNITS:
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return f"{seconds} seconds"


class _Names:
    """Column to attribute, and which names are guesses rather than entries of the name map."""

    def __init__(self, table: str) -> None:
        self.table, self.map = table, NameMap()
        self.found: dict[str, tuple[str, bool]] = {}

    def __call__(self, column: str) -> str:
        if column not in self.found:
            mapped = log_attribute(self.map, self.table, column)
            if mapped is None:
                raise ValueError(f"column {R.shown(column, 40)} is not a plain name")
            self.found[column] = mapped
        return self.found[column][0]


def _alias(agg: Agg) -> str:
    return agg.alias or agg.fn


def _notes(query: Query, names: _Names, table_path: str, azure_filter: bool) -> list[str]:
    notes: list[str] = []
    if azure_filter:
        notes += [R.N_LOG_FORWARD, R.N_LOG_TABLE.format(table=R.shown(query.table, 40))]
    elif table_path == AGENT:
        notes.append(R.N_LOG_AGENT.format(table=R.shown(query.table, 40)))
    else:
        notes.append(R.N_LOG_WORKSPACE.format(table=R.shown(query.table, 40)))
    if any(not guessed for _, guessed in names.found.values()):
        attrs = sorted({a for a, guessed in names.found.values() if not guessed})
        notes.append(R.N_LOG_ATTRIBUTES.format(names=", ".join(attrs)))
    if query.ignored:
        notes.append(R.N_KQL_IGNORED.format(names=", ".join(dict.fromkeys(query.ignored))))
    leaves = [
        leaf for p in [*query.rows, *(a.cond for a in query.aggs if a.cond)] for leaf in walk(p)
    ]
    if any(is_negated(leaf) for leaf in leaves):
        notes.append(R.N_KQL_NEGATED)
    if any(isinstance(p, Cmp) and p.op.lstrip("!") == "has" for p in leaves):
        notes.append(R.N_KQL_HAS)
    if any(isinstance(p, Cmp) and p.op.lstrip("!") in ("contains", "has") for p in leaves):
        notes.append(R.N_FILTER_CASE)
    if any(a.fn in ("dcount", "dcountif", "percentile") for a in query.aggs):
        notes.append(R.N_KQL_ESTIMATE)
    if any(a.fn == "any" for a in query.aggs):
        notes.append(R.N_KQL_ANY)
    if any(isinstance(p, Regex) for p in leaves):
        notes.append(R.N_KQL_REGEX)
    return notes


def _time_filter(names: Collection[str]) -> re.Pattern[str]:
    """`| where TimeGenerated {Name}` (or `{Name:value}`) as a whole stage."""
    alt = "|".join(re.escape(n) for n in sorted(names))
    return re.compile(
        rf"\|\s*where\s+(?:TimeGenerated|timestamp)\s*\{{(?:{alt})(?::value)?\}}\s*(?=[|;]|$)",
        re.IGNORECASE,
    )


def _time_grain(names: Collection[str]) -> re.Pattern[str]:
    alt = "|".join(re.escape(n) for n in sorted(names))
    return re.compile(rf"\bbin\(\s*TimeGenerated\s*,\s*\{{(?:{alt}):grain\}}\s*\)")


def resolve_time(query: str, names: Collection[str]) -> tuple[str, bool]:
    """The query without the time-range filter, and whether its bin is the workbook's grain.

    A workbook time-range parameter fills `where TimeGenerated {Name}` with the range the reader
    picked; a New Relic widget takes its range from the dashboard time picker, so that stage is
    dropped. `bin(TimeGenerated, {Name:grain})` is the size the workbook derives from the range;
    the bin is kept at a placeholder size that `_nrql` replaces with TIMESERIES AUTO. Any other
    use of the parameter (`:start`, `:end`, in a `let`) is left in place and refused later.
    """
    if not names:
        return query, False
    query = _time_filter(names).sub(" ", query)
    query, grains = _time_grain(names).subn("bin(TimeGenerated, 1m)", query)
    return query, bool(grains)


def _nrql(
    query: Query, names: _Names, azure_filter: bool, auto_grain: bool = False
) -> tuple[str, bool]:
    selects = [f"{aggregate_sql(a, names, [])} AS {quote_string(_alias(a))}" for a in query.aggs]
    where = [f"{ref(FORWARDED[0])} = {quote_string(FORWARDED[1])}"] if azure_filter else []
    if query.rows:
        # each term on its own, so an OR is bracketed next to the plugin.type term
        where += [predicate_sql(p, names, "AND") for p in query.rows]
    text = f"SELECT {', '.join(selects)} FROM Log"  # noqa: S608 (every part is escaped)
    if where:
        text += f" WHERE {' AND '.join(where)}"
    facets = [ref(names(g.column)) for g in query.groups if g.bin_seconds is None]
    if facets:
        text += f" FACET {', '.join(facets)}"
    bins = {g.bin_seconds for g in query.groups if g.bin_seconds is not None}
    if len(bins) > 1:
        raise ValueError("the summarize has more than one bin size")
    if bins:
        (size,) = bins
        text += f" TIMESERIES {AUTO if auto_grain else _span(int(size or 60))}"
    return text, bool(bins)


def translate_kql(
    query: str,
    variables: Collection[str],  # noqa: ARG001
    time_params: Collection[str] = (),
) -> KqlNrql | str:
    """NRQL for a workbook or dashboard KQL query, or the reason it cannot be converted.

    `variables` are the names of New Relic dashboard variables this dashboard defines. The
    subset has no way to read a workbook parameter written in braces inside a query, except the
    time-range parameters in `time_params` (see `resolve_time`); any other use stays a placeholder
    that names the parameters, and no variable is used.
    """
    query, auto_grain = resolve_time(query, time_params)
    left = list(dict.fromkeys(PARAMETER.findall(query)))
    if left:
        shown = ", ".join(R.shown(p, 40) for p in left[:3])
        return (
            f"{DR.QUERY_KQL}: the query uses workbook parameter {shown} in its text; "
            "only a time-range filter and a time-range grain are converted"
        )
    try:
        parsed = parse(query)
    except (KqlSyntax, KqlUnsupported) as exc:
        return f"{DR.QUERY_KQL}: {R.shown(exc, 150)}"
    if not parsed.summarized or not parsed.aggs:
        return f"{DR.QUERY_KQL}: the query returns rows and no aggregate, which has no NRQL form"
    if parsed.result_filter:
        return f"{DR.QUERY_KQL}: a where on the result of the summarize is not converted"
    if len(parsed.aggs) > MAX_AGGREGATES:
        return f"{DR.QUERY_KQL}: more than {MAX_AGGREGATES} aggregates"
    info = classify(parsed.table)
    if not info.azure_filter and not parsed.rows and all(a.fn in COUNT_LIKE for a in parsed.aggs):
        return (
            f"{DR.QUERY_KQL}: {R.shown(parsed.table, 40)} has no filter that tells its rows apart "
            "from other logs, so the count would include every log line"
        )
    names = _Names(parsed.table)
    try:
        nrql, timeseries = _nrql(parsed, names, info.azure_filter, auto_grain)
    except ValueError as exc:
        return f"{DR.QUERY_KQL}: {R.shown(exc, 150)}"
    return KqlNrql(
        nrql=nrql,
        timeseries=timeseries,
        notes=tuple(_notes(parsed, names, info.path, info.azure_filter)),
        data_path=info.path,
    )
