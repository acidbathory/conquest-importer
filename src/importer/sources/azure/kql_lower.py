"""From a parsed KQL query and the rule's condition to what the NRQL needs.

The rule decides what is compared with the threshold (`timeAggregation`, `metricMeasureColumn`,
`dimensions`, `failingPeriods`); the query decides the rows and the aggregate. This module picks
the aggregate the rule measures, the facets, a threshold that came from a `where` on the result
column, and writes the NRQL pieces. `logs.py` builds the IR from the result.

Two forms leave here. A plain form (only `==`, `!=`, text matches and `in` on text, and `count()`
or one aggregate of a plain column) keeps the filters as IR filters. Anything else is written as
an NRQL aggregate in `select`, with the row conditions inside filter(..., WHERE ...).
"""

from dataclasses import dataclass, field
from typing import Any

from importer.core.ir import Aggregation, Operator
from importer.sources.azure import reasons as R
from importer.sources.azure.common import Fail
from importer.sources.azure.kql_nrql import LOOSE_NAME, aggregate_sql
from importer.sources.azure.kql_parse import (
    Agg,
    And,
    Cmp,
    InList,
    Pred,
    Query,
    Regex,
    is_negated,
    walk,
)
from importer.sources.azure.kql_tables import AGENT, TableInfo, classify
from importer.sources.azure.models import AzureAlert
from importer.sources.azure.names import log_attribute

AGGREGATIONS = {
    "average": Aggregation.AVG,
    "minimum": Aggregation.MIN,
    "maximum": Aggregation.MAX,
    "total": Aggregation.SUM,
}
TIME_TO_FUNCTION = {"average": "avg", "minimum": "min", "maximum": "max", "total": "sum"}
SIMPLE_AGGREGATES = {"avg": Aggregation.AVG, "sum": Aggregation.SUM}
SIMPLE_AGGREGATES |= {"min": Aggregation.MIN, "max": Aggregation.MAX}
NR_AGGREGATION = {
    "count": Aggregation.COUNT,
    "countif": Aggregation.COUNT,
    "dcount": Aggregation.COUNT,
    "dcountif": Aggregation.COUNT,
    "stdev": Aggregation.STDDEV,
    "any": Aggregation.MAX,
    "percentile": Aggregation.AVG,
    **SIMPLE_AGGREGATES,
    "avgif": Aggregation.AVG,
    "sumif": Aggregation.SUM,
    "minif": Aggregation.MIN,
    "maxif": Aggregation.MAX,
}
# a finer bin summed by timeAggregation gives the same number as one bin over the window
COMPOSES = {
    ("count", "total"),
    ("countif", "total"),
    ("sum", "total"),
    ("sumif", "total"),
    ("min", "minimum"),
    ("minif", "minimum"),
    ("max", "maximum"),
    ("maxif", "maximum"),
}
MEASURE_NAMES = {"", "count_", "aggregatedvalue"}
COUNT_LIKE = {"count", "countif"}
RESULT_OPERATORS = {
    ">": Operator.ABOVE,
    ">=": Operator.ABOVE_OR_EQUALS,
    "<": Operator.BELOW,
    "<=": Operator.BELOW_OR_EQUALS,
    "==": Operator.EQUALS,
    "!=": Operator.NOT_EQUALS,
}
TEXT_FORMS = {"==", "!=", "contains", "!contains", "has", "!has", "startswith", "!startswith"}
TEXT_FORMS |= {"endswith", "!endswith"}


def is_empty_sensitive(operator: Operator, value: float) -> bool:
    """A comparison that is true for a count of 0 (so an empty window must read as 0)."""
    return operator in (Operator.BELOW, Operator.BELOW_OR_EQUALS, Operator.EQUALS) or (
        operator is Operator.ABOVE_OR_EQUALS and value <= 0
    )


@dataclass
class Lowered:
    info: TableInfo
    filters: list[dict[str, Any]] = field(default_factory=list)  # IR filter arguments
    select: str | None = None  # an NRQL aggregate, when the plain form is not enough
    measured: str | None = None  # the attribute of a plain aggregate
    aggregation: Aggregation = Aggregation.COUNT
    count: bool = False  # a plain count(*) over the filters
    facets: list[str] = field(default_factory=list)
    threshold: tuple[Operator, float] | None = None  # from a where on the result column
    attributes: list[str] = field(default_factory=list)
    derived: bool = False  # some attribute name was not in the name map
    notes: list[str] = field(default_factory=list)
    needs_null_safe: bool = False  # the filters must stay inside filter() for an empty window


class _Names:
    def __init__(self, alert: AzureAlert, table: str, loose: set[str]) -> None:
        self.alert, self.table, self.loose = alert, table, loose
        self.found: dict[str, tuple[str, bool]] = {}

    def __call__(self, column: str) -> str:
        if column not in self.found:
            mapped = log_attribute(self.alert.names, self.table, column)
            if mapped is None and column in self.loose and LOOSE_NAME.fullmatch(column):
                mapped = (column, False)
            if mapped is None:
                head = R.M_LOG_MEASURE if column in self.loose else R.M_KQL
                raise Fail(f"{head}: column {R.shown(column, 40)} is not a plain name")
            self.found[column] = mapped
        return self.found[column][0]


def _simple_filter(pred: Pred, name_of: _Names) -> dict[str, Any] | None:
    """IR filter arguments for `==`, `!=`, a text match or `in` on plain text, else None."""
    if isinstance(pred, InList):
        texts = [v for v in pred.values if isinstance(v, str) and not isinstance(v, bool)]
        if len(texts) != len(pred.values) or any(("*" in t or "%" in t) for t in texts):
            return None
        return {"key": name_of(pred.column), "value": "", "values": texts, "negate": pred.negate}
    if not isinstance(pred, Cmp) or pred.op not in TEXT_FORMS or not isinstance(pred.value, str):
        return None
    text = pred.value
    if "*" in text or "%" in text:
        return None
    key, negate = name_of(pred.column), pred.op.startswith("!")
    if pred.op in ("==", "!="):
        return {"key": key, "value": text, "negate": negate}
    shape = {"contains": "*{}*", "has": "*{}*", "startswith": "{}*", "endswith": "*{}"}
    pattern = shape[pred.op.lstrip("!")].format(text)
    return {"key": key, "value": pattern, "wildcard": True, "negate": negate}


def _result_alias(agg: Agg, column: str) -> bool:
    return agg.alias.lower() == column.lower()


def _choose(query: Query, cond: dict[str, Any]) -> tuple[Agg, tuple[Operator, float] | None]:
    """The aggregate the rule measures, and a threshold taken from a where on the result."""
    time_agg = str(cond.get("timeAggregation", "")).lower()
    raw = cond.get("metricMeasureColumn")
    column = raw if isinstance(raw, str) else ""
    if not query.summarized:
        if not column and time_agg in ("count", ""):
            return Agg("count_", "count"), None
        if column and time_agg in TIME_TO_FUNCTION:
            return Agg(column, TIME_TO_FUNCTION[time_agg], column), None
        raise Fail(f"{R.M_LOG_MEASURE}: no column or time aggregation New Relic can take")
    if query.result_filter:
        return _folded(query, cond, time_agg, column)
    matches = [a for a in query.aggs if column and _result_alias(a, column)]
    if len(matches) != 1 and column.lower() in MEASURE_NAMES and len(query.aggs) == 1:
        matches = list(query.aggs)
    if len(matches) != 1:
        raise Fail(f"{R.M_LOG_MEASURE}: {R.shown(column or 'no column', 40)} is not one result")
    agg = matches[0]
    if time_agg not in TIME_TO_FUNCTION:
        raise Fail(f"{R.M_LOG_MEASURE}: {agg.fn}() with time aggregation {time_agg or 'none'}")
    return agg, None


def _folded(
    query: Query, cond: dict[str, Any], time_agg: str, column: str
) -> tuple[Agg, tuple[Operator, float] | None]:
    operator = str(cond.get("operator", "")).lower()
    threshold = cond.get("threshold")
    any_row = (operator, threshold) in (("greaterthan", 0), ("greaterthanorequal", 1))
    if column or time_agg not in ("count", "") or not any_row or len(query.result_filter) != 1:
        raise Fail(f"{R.M_KQL}: {R.M_KQL_RESULT}")
    pred = query.result_filter[0]
    if isinstance(pred, And) or not isinstance(pred, Cmp) or pred.op not in RESULT_OPERATORS:
        raise Fail(f"{R.M_KQL}: {R.M_KQL_RESULT}")
    if not isinstance(pred.value, float) or isinstance(pred.value, bool):
        raise Fail(f"{R.M_KQL}: the where on the result column compares with a number only")
    matches = [a for a in query.aggs if _result_alias(a, pred.column)]
    if len(matches) != 1:
        raise Fail(f"{R.M_KQL}: the where compares {R.shown(pred.column, 40)}, not a result")
    return matches[0], (RESULT_OPERATORS[pred.op], pred.value)


def _bin_note(query: Query, agg: Agg, window: int, cond: dict[str, Any]) -> list[str]:
    seconds = {g.bin_seconds for g in query.groups if g.bin_seconds is not None}
    if not seconds:
        return []
    (size,) = seconds if len(seconds) == 1 else (0,)
    if size <= 0 or size > window or window % size:
        raise Fail(f"{R.M_KQL}: the summarize bin size differs from the window")
    time_agg = str(cond.get("timeAggregation", "")).lower()
    if size < window and (agg.fn, time_agg) not in COMPOSES:
        raise Fail(
            f"{R.M_KQL}: the bin of {agg.fn}() cannot be combined over the window with "
            f"time aggregation {time_agg or 'none'}"
        )
    return [R.N_KQL_BIN.format(bin=size)]


def lower(
    query: Query, cond: dict[str, Any], alert: AzureAlert, rule_facets: list[str], window: int
) -> Lowered:
    info = classify(query.table)
    agg, folded = _choose(query, cond)
    raw = cond.get("metricMeasureColumn")
    loose = {raw} if isinstance(raw, str) and not query.summarized else set()
    names = _Names(alert, query.table, loose)
    out = Lowered(info, threshold=folded)
    out.notes += _bin_note(query, agg, window, cond)
    by = [g.column for g in query.groups if g.bin_seconds is None]
    mapped_by = [names(c) for c in by]
    if folded is None:
        for column, attr in zip(by, mapped_by, strict=True):
            if attr not in rule_facets:
                raise Fail(
                    f"{R.M_KQL}: the summarize groups by {R.shown(column, 40)}, "
                    "which is not a rule dimension"
                )
        out.facets = list(rule_facets)
    else:
        out.facets = list(dict.fromkeys([*rule_facets, *mapped_by]))
        out.notes.append(R.N_KQL_RESULT)
    simple_filters = [_simple_filter(p, names) for p in query.rows]
    plain_rows = all(f is not None for f in simple_filters)
    function = agg.fn
    out.aggregation = NR_AGGREGATION[function]
    if function == "count" and agg.cond is None and plain_rows:
        out.count = True
    elif (
        function in SIMPLE_AGGREGATES
        and plain_rows
        and agg.cond is None
        and log_attribute(alert.names, query.table, agg.column) is not None
    ):
        out.measured = names(agg.column)
    if out.count or out.measured:
        out.filters = [f for f in simple_filters if f is not None]
    else:
        _select(out, query, agg, names, plain_rows, simple_filters, folded, cond)
    _notes(out, query, window)
    counts_everything = agg.fn in COUNT_LIKE and agg.cond is None and not out.facets
    if not info.azure_filter and not query.rows and counts_everything:
        raise Fail(
            f"{R.M_KQL_TABLE}: {R.shown(query.table, 40)} has no filter that tells its "
            "rows apart from other logs, so the count would include every log line"
        )
    out.attributes = sorted({v[0] for v in names.found.values()} | set(out.facets))
    out.derived = any(not v[1] for v in names.found.values())
    return out


def _select(
    out: Lowered,
    query: Query,
    agg: Agg,
    names: _Names,
    plain_rows: bool,
    simple_filters: list[dict[str, Any] | None],
    folded: tuple[Operator, float] | None,
    cond: dict[str, Any],
) -> None:
    operator = folded[0] if folded else _rule_operator(cond)
    value = folded[1] if folded else float(cond.get("threshold") or 0)
    sensitive = agg.fn in COUNT_LIKE and is_empty_sensitive(operator, value)
    try:
        inside = list(query.rows) if (not plain_rows or sensitive) else []
        out.filters = [] if inside else [f for f in simple_filters if f is not None]
        out.select = aggregate_sql(agg, names, inside)
        out.needs_null_safe = sensitive
    except ValueError as exc:
        raise Fail(f"{R.M_KQL}: {R.shown(exc, 120)}") from None


def _rule_operator(cond: dict[str, Any]) -> Operator:
    mapping = {
        "greaterthan": Operator.ABOVE,
        "greaterthanorequal": Operator.ABOVE_OR_EQUALS,
        "lessthan": Operator.BELOW,
        "lessthanorequal": Operator.BELOW_OR_EQUALS,
        "equals": Operator.EQUALS,
    }
    return mapping.get(str(cond.get("operator", "")).lower(), Operator.ABOVE)


def _notes(out: Lowered, query: Query, window: int) -> None:
    info = out.info
    if info.path == AGENT:
        out.notes.append(R.N_LOG_AGENT.format(table=R.shown(info.table, 40)))
    elif not info.azure_filter:
        out.notes.append(R.N_LOG_WORKSPACE.format(table=R.shown(info.table, 40)))
    if query.time_filter is not None and query.time_filter != window:
        out.notes.append(R.N_KQL_RANGE.format(span=query.time_filter, window=window))
    if query.ignored:
        out.notes.append(R.N_KQL_IGNORED.format(names=", ".join(dict.fromkeys(query.ignored))))
    leaves = [
        leaf for p in [*query.rows, *(a.cond for a in query.aggs if a.cond)] for leaf in walk(p)
    ]
    if any(is_negated(leaf) for leaf in leaves):
        out.notes.append(R.N_KQL_NEGATED)
    if any(isinstance(p, Cmp) and p.op.lstrip("!") == "has" for p in leaves):
        out.notes.append(R.N_KQL_HAS)
    if any(a.fn in ("dcount", "dcountif", "percentile") for a in query.aggs):
        out.notes.append(R.N_KQL_ESTIMATE)
    if any(a.fn == "any" for a in query.aggs):
        out.notes.append(R.N_KQL_ANY)
    if any(isinstance(p, Regex) for p in leaves):
        out.notes.append(R.N_KQL_REGEX)
