"""From interpreted series to an IR metric signal, through the metric map.

Every Dynatrace metric key and dimension key is looked up in the map; one that is missing makes
the condition manual with what must be installed or sent to New Relic. Names that reach NRQL come
only from the map file (checked as identifiers when it loads) and from `identifier`/`quote_string`
at emit time; values from the export are checked here for characters NRQL cannot carry.
"""

import re
from dataclasses import dataclass
from typing import Literal

from importer.core.emit.nrql import identifier, quote_string
from importer.core.ir import (
    Aggregation,
    BinOp,
    Expr,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Num,
    Ref,
    TagFilter,
)
from importer.sources.dynatrace.classic.common import Fail, finite, shown
from importer.sources.dynatrace.classic.metric_map import MapEntry, MetricMap, active_map
from importer.sources.dynatrace.classic.selector_query import (
    Aggregate,
    DimFilter,
    ENum,
    EOp,
    ERef,
    QExpr,
    SeriesSpec,
)

SIMPLE = {
    "average": Aggregation.AVG,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
    "sum": Aggregation.SUM,
}
OPS: dict[str, Literal["+", "-", "*", "/"]] = {"+": "+", "-": "-", "*": "*", "/": "/"}
WILDCARD_CHARS = "*%_"
LIKE = {"prefix": "{}*", "suffix": "*{}", "contains": "*{}*"}
STATUS_NOTES = {
    "unverified": (
        "metric name mapping is unverified for {key}: the New Relic name comes from the "
        "research note and has not been seen on live data"
    ),
    "documented": (
        "metric name mapping comes from documentation for {key} and has not been seen on live data"
    ),
}
ENTITY_ID = re.compile(r"[A-Z_]+-[0-9A-F]{16}")
ENTITY_PREFIX = "dt.entity."
ENTITY_ID_REASON = (
    "a filter on an entity id cannot be matched: New Relic has names, not Dynatrace ids; "
    "resolve the id to a name first"
)
ENTITY_SPLIT_NOTE = (
    "splitting by an entity dimension gives names in New Relic where Dynatrace shows entity "
    "ids, so the facet labels change from entity ids to names"
)
SCALE_REASON = (
    "arithmetic around a metric with a unit conversion is not supported (only multiplying or "
    "dividing by a constant keeps the unit)"
)
LIKE_NOTE = (
    "a LIKE filter is used for a prefix, suffix or contains test; whether it ignores case is "
    "not confirmed"
)


@dataclass
class Built:
    signal: MetricSignal
    factor: float  # multiply a Dynatrace threshold by this to get New Relic units
    notes: list[str]


def entry_for(key: str, mapping: MetricMap) -> MapEntry:
    row = mapping.find(key)
    if row is None or row.status == "none":
        why = f" ({row.evidence})" if row is not None and row.evidence else ""
        raise Fail(
            f"no New Relic signal is known for Dynatrace metric {shown(key)}{why}; to get "
            f"the data: {mapping.prerequisite(key)}"
        )
    return row


def _attribute(dimension: str, entries: list[MapEntry]) -> str:
    found = {e.dimensions.get(dimension) for e in entries}
    attributes = [a for a in found if a is not None]
    if len(attributes) != len(found):
        raise Fail(f"dimension {shown(dimension)} is not in the metric map for the metric used")
    if len(attributes) != 1:
        raise Fail(f"the metrics map dimension {shown(dimension)} to different attributes")
    return attributes[0]


def _check_entity_ids(filters: list[DimFilter]) -> None:
    for flt in filters:
        if flt.key.startswith(ENTITY_PREFIX) and any(ENTITY_ID.fullmatch(v) for v in flt.values):
            raise Fail(ENTITY_ID_REASON)


def _threshold_factor(entries: list[MapEntry], expression: QExpr | None) -> float:
    """How a Dynatrace threshold becomes a New Relic one, given the unit conversion."""
    factor = entries[0].unit_factor
    if expression is None or all(e.unit_factor == 1 for e in entries):
        return factor
    if len(entries) > 1 or not isinstance(expression, EOp):
        raise Fail(SCALE_REASON)
    left, right = expression.left, expression.right
    if expression.op in "*/" and isinstance(left, ERef) and isinstance(right, ENum):
        return factor  # metric * k, metric / k
    if expression.op == "*" and isinstance(left, ENum) and isinstance(right, ERef):
        return factor  # k * metric
    if expression.op == "/" and isinstance(left, ENum) and isinstance(right, ERef):
        return 1 / factor  # k / metric
    raise Fail(SCALE_REASON)


def _safe_value(value: str) -> str:
    try:
        quote_string(value)
    except ValueError:
        raise Fail("a filter value has a control character that NRQL cannot carry") from None
    return value


def _like_value(kind: str, value: str) -> str:
    if any(c in value for c in WILDCARD_CHARS):
        raise Fail("a filter value with *, % or _ cannot be matched as text in a LIKE filter")
    return LIKE[kind].format(_safe_value(value))


def tag_filter(flt: DimFilter, attribute: str) -> TagFilter:
    """One dimension test as a New Relic scope filter on `attribute`."""
    if flt.kind == "exists":  # IS NOT NULL is a negated absent test
        return TagFilter(key=attribute, absent=True, negate=not flt.negate)
    if flt.kind == "eq":
        values = [_safe_value(v) for v in flt.values]
        if len(values) == 1:
            return TagFilter(key=attribute, value=values[0], negate=flt.negate)
        return TagFilter(key=attribute, values=values, negate=flt.negate)
    return TagFilter(
        key=attribute, value=_like_value(flt.kind, flt.values[0]), wildcard=True, negate=flt.negate
    )


def _term_target(
    entry: MapEntry, spec: SeriesSpec, *, alone: bool
) -> tuple[NrTarget, Aggregation, list[str]]:
    agg = spec.aggregate or _default(entry)
    notes: list[str] = []
    if entry.select is not None:
        if spec.aggregate and entry.default_aggregation not in ("", spec.aggregate.func):
            notes.append(
                f"the metric map gives a fixed expression for {shown(spec.key)}, so the "
                f"Dynatrace aggregation {spec.aggregate.func} is not applied"
            )
        return NrTarget(event=entry.event, select=entry.select), Aggregation.AVG, notes
    name = identifier(entry.attribute or "")  # a row without a select has an attribute
    if agg.func in SIMPLE and alone:
        return NrTarget(event=entry.event, attribute=entry.attribute), SIMPLE[agg.func], notes
    return NrTarget(event=entry.event, select=_select(agg, name)), Aggregation.AVG, notes


def _default(entry: MapEntry) -> Aggregate:
    func = entry.default_aggregation
    if not func:
        raise Fail(
            "the selector gives no aggregation and the metric map has no default for this metric"
        )
    return Aggregate(func)


def _select(agg: Aggregate, name: str) -> str:
    if agg.func == "percentile" and agg.percentile is not None:
        return f"percentile({name}, {agg.percentile:g})"
    if agg.func in ("average", "min", "max", "sum", "count", "latest"):
        return f"{agg.func}({name})"
    raise Fail(f"aggregation {shown(agg.func)} is not supported")


def _expression(node: QExpr) -> Expr:
    if isinstance(node, ENum):
        return Num(value=finite(node.value, "a number in the selector"))
    if isinstance(node, ERef):
        return Ref(name=_name(node.index))
    return BinOp(op=OPS[node.op], left=_expression(node.left), right=_expression(node.right))


def _name(index: int) -> str:
    return chr(ord("a") + index)


def _status_notes(entries: list[MapEntry], keys: list[str], mapping: MetricMap) -> list[str]:
    notes: list[str] = []
    for entry, key in zip(entries, keys, strict=True):
        template = STATUS_NOTES.get(entry.status)
        found = [template.format(key=shown(key))] if template else []
        found.append(f"data prerequisite: {mapping.prerequisite(key)}")
        notes += [n for n in found if n not in notes]
    return notes


def build_signal(
    terms: list[SeriesSpec],
    expression: QExpr | None,
    extra: list[DimFilter],
    mapping: MetricMap | None = None,
) -> Built:
    """The metric signal for one or more terms that share a split and filters."""
    mapping = mapping or active_map()
    entries = [entry_for(t.key, mapping) for t in terms]
    if len({e.event for e in entries}) > 1:
        raise Fail("the metrics live on different New Relic events and cannot share one condition")
    first = terms[0]
    _check_entity_ids([*first.filters, *extra])
    group_by = [_attribute(d, entries) for d in first.group_by]
    scope = [tag_filter(f, _attribute(f.key, entries)) for f in [*first.filters, *extra]]
    metric_terms: dict[str, MetricTerm] = {}
    notes: list[str] = []
    aggregation = Aggregation.AVG
    for index, (entry, spec) in enumerate(zip(entries, terms, strict=True)):
        target, aggregation, more = _term_target(entry, spec, alone=len(terms) == 1)
        notes += more + spec.notes
        space = aggregation if len(terms) == 1 else Aggregation.AVG
        metric_terms[_name(index)] = MetricTerm(
            metric=spec.key, space_aggregation=space, nr_target=target
        )
    if len(terms) > 1 and any(e.unit_factor != 1 for e in entries):
        raise Fail("arithmetic over metrics with a unit conversion is not supported")
    factor = _threshold_factor(entries, expression)
    if any(d.startswith(ENTITY_PREFIX) for d in first.group_by):
        notes.append(ENTITY_SPLIT_NOTE)
    if any(f.kind != "eq" for f in [*first.filters, *extra]):
        notes.append(LIKE_NOTE)
    notes += _status_notes(entries, [t.key for t in terms], mapping)
    signal = MetricSignal(
        terms=metric_terms,
        expression=None if expression is None else _expression(expression),
        aggregation=aggregation,
        scope=scope,
        group_by=group_by,
    )
    return Built(signal, factor, notes)
