"""`query alert` monitors: plain thresholds on metrics, with arithmetic between series.

Converted: `time_aggr(last_Nm):space:metric{scope} [by {tag}] op N`, the same with `+ - * /` between
series and numbers, and `.as_count()` on a sum. Everything else is a precise manual reason.
Metric and tag names come from the name map and are UNVERIFIED unless the map says otherwise
(docs/research/datadog-verified.md section 13).
"""

from dataclasses import dataclass, replace
from typing import Literal, cast

from importer.core.emit.nrql import identifier
from importer.core.ir import (
    Aggregation,
    BinOp,
    Confidence,
    EmitOptions,
    Expr,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Num,
    Occurrences,
    Ref,
    Signal,
    SignalKind,
    TagFilter,
)
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import (
    Fail,
    Thresholds,
    Timing,
    base_fields,
    manual,
    message_parts,
    options_for,
    priority_note,
    thresholds,
    timing,
)
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.names import NameMap, resolve_attribute, resolve_metric
from importer.sources.datadog.query import (
    Bin,
    BoolOp,
    Call,
    InSet,
    MetricQuery,
    Node,
    Not,
    ScopeNode,
    Series,
    Star,
    Tag,
    Window,
    parse_metric_query,
)
from importer.sources.datadog.query import Num as QNum
from importer.sources.datadog.sanitize import shown

AGGREGATIONS = {
    "avg": Aggregation.AVG,
    "sum": Aggregation.SUM,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
}
NRQL_FUNCTIONS = {"avg": "average", "sum": "sum", "min": "min", "max": "max"}
ALGORITHMS = {
    "anomalies": R.M_ANOMALY,
    "outliers": R.M_OUTLIER,
    "forecast": R.M_FORECAST,
}
_D5_FILL = "empty-window handling, planned for milestone D5"
_D5_RATE = "rate functions are planned for milestone D5"
_NO_COMPARE = "a condition cannot compare two time ranges (COMPARE WITH is not allowed)"
_NO_LIMIT = "LIMIT is not allowed in a condition"
_BY_HAND = "arithmetic functions are not planned; write the expression in NRQL by hand"
_NESTED = "nested space aggregation after a rollup is not converted"
_NOT_PLANNED = "exclusion functions are not planned"
FUNCTION_HINTS = {
    "default_zero": _D5_FILL,
    "fill": _D5_FILL,
    "rollup": "the bucket size is fixed by the rollup; planned for milestone D5",
    "moving_rollup": "planned for milestone D5",
    "per_second": _D5_RATE,
    "per_minute": _D5_RATE,
    "per_hour": _D5_RATE,
    "derivative": _D5_RATE,
    "diff": _D5_RATE,
    "monotonic_diff": _D5_RATE,
    "dt": _D5_RATE,
    "timeshift": _NO_COMPARE,
    "calendar_shift": _NO_COMPARE,
    "hour_before": _NO_COMPARE,
    "day_before": _NO_COMPARE,
    "week_before": _NO_COMPARE,
    "month_before": _NO_COMPARE,
    "top": _NO_LIMIT,
    "bottom": _NO_LIMIT,
    "abs": _BY_HAND,
    "log2": _BY_HAND,
    "log10": _BY_HAND,
    "cumsum": "no NRQL equivalent",
    "integral": "no NRQL equivalent",
    "cutoff_min": _NOT_PLANNED,
    "cutoff_max": _NOT_PLANNED,
    "clamp_min": _NOT_PLANNED,
    "clamp_max": _NOT_PLANNED,
    "exclude_null": _NOT_PLANNED,
    "autosmooth": "smoothing functions have no condition equivalent",
    "count_nonzero": "no NRQL equivalent",
    "count_not_null": "no NRQL equivalent",
    "sum": _NESTED,
    "avg": _NESTED,
    "min": _NESTED,
    "max": _NESTED,
}


def function_reason(name: str) -> str:
    hint = FUNCTION_HINTS.get(name)
    if hint is None and name.startswith(("top", "bottom")):
        hint = _NO_LIMIT
    if hint is None and name.startswith(("ewma", "median")):
        hint = "smoothing functions have no condition equivalent"
    return f"{R.M_FUNCTION}: {shown(name, 40)}() is not converted ({hint or 'unknown function'})"


def _letters(i: int) -> str:
    return chr(ord("a") + i) if i < 26 else f"q{i}"


# -- scope ---------------------------------------------------------------------------------------
def _tag_filter(tag: Tag, names: NameMap) -> TagFilter:
    if "$" in tag.value or "$" in (tag.key or ""):
        raise Fail(f"{R.M_TEMPLATE_VAR}: {shown(f'{tag.key}:{tag.value}', 60)}")
    if tag.key is None:
        raise Fail(f"{R.M_SCOPE}: tag {shown(tag.value, 40)!r} has no key:value form")
    attr = resolve_attribute(names, tag.key)
    if attr is None:
        raise Fail(f"{R.M_TAG}: {shown(tag.key, 40)}")
    if tag.value == "*":
        return TagFilter(key=attr, absent=True, negate=not tag.negate)  # key:* means "has the tag"
    if tag.wildcard and "%" in tag.value:
        raise Fail(f"{R.M_SCOPE}: a % in a wildcard value cannot be escaped")
    return TagFilter(key=attr, value=tag.value, negate=tag.negate, wildcard=tag.wildcard)


def scope_filters(node: ScopeNode, names: NameMap) -> list[TagFilter]:
    """A scope as ANDed filters. OR is allowed only between plain values of one tag (an IN)."""
    if isinstance(node, Star):
        return []
    if isinstance(node, Tag):
        return [_tag_filter(node, names)]
    if isinstance(node, InSet):
        attr = resolve_attribute(names, node.key)
        if attr is None:
            raise Fail(f"{R.M_TAG}: {shown(node.key, 40)}")
        if any("$" in v for v in node.values):
            raise Fail(f"{R.M_TEMPLATE_VAR}: {shown(node.key, 40)}")
        if not node.values:
            raise Fail(f"{R.M_SCOPE}: an empty IN list")
        return [TagFilter(key=attr, values=list(node.values), negate=node.negate)]
    if isinstance(node, Not):
        raise Fail(f"{R.M_SCOPE}: NOT over a group of tags")
    if node.op == "and":
        out: list[TagFilter] = []
        for item in node.items:
            out += scope_filters(item, names)
        return out
    return [_or_values(node, names)]


def _or_values(node: BoolOp, names: NameMap) -> TagFilter:
    tags = [i for i in node.items if isinstance(i, Tag)]
    plain = len(tags) == len(node.items) and len({t.key for t in tags}) == 1
    if not plain or tags[0].key is None or any(t.negate or t.wildcard for t in tags):
        raise Fail(f"{R.M_SCOPE}: OR is converted only between plain values of one tag")
    first = _tag_filter(tags[0], names)
    return TagFilter(key=first.key, values=[t.value for t in tags])


# -- the query -----------------------------------------------------------------------------------
class _Terms:
    def __init__(self, time_aggr: str, names: NameMap) -> None:
        self.time = time_aggr
        self.names = names
        self.terms: dict[str, MetricTerm] = {}
        self.scopes: dict[str, list[TagFilter]] = {}
        self.groups: list[tuple[str, ...]] = []
        self.scales: dict[str, float] = {}
        self.notes: list[str] = []
        self.from_map: list[bool] = []
        self.events: set[str] = set()
        self.tag_keys: list[str] = []

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def series(self, s: Series) -> Ref:
        as_count = any(fn.name == "as_count" for fn in s.functions)
        if s.aggr not in AGGREGATIONS:
            raise Fail(
                f"{R.M_FUNCTION}: space aggregator {shown(s.aggr, 20)} is not converted "
                "(percentiles are a different statistic on distribution metrics)"
            )
        found = resolve_metric(self.names, s.metric)
        if found is None:
            raise Fail(f"{R.M_METRIC_NAME}: {shown(s.metric, 60)}")
        if as_count:
            if found.kind in ("gauge", "rate"):
                raise Fail(R.M_AS_COUNT_KIND.format(kind=found.kind))
            if self.time != "sum":
                raise Fail(
                    f"{R.M_FUNCTION}: as_count() with the time aggregator {self.time} is not "
                    "converted; only a sum of counts is"
                )
            if found.kind is None:
                self.note(R.N_AS_COUNT)
        elif found.kind in ("count", "rate"):
            self.note(R.N_KIND.format(kind=found.kind))
        func = "sum" if as_count else self.time
        if s.aggr != func and not as_count:
            self.note(R.N_TIME_AGGR.format(time=self.time, space=s.aggr))
        name = _letters(len(self.terms))
        groups = tuple(self._attribute(g) for g in s.group_by)
        filters = scope_filters(s.scope, self.names)
        self.tag_keys += [f.key for f in filters if f.key not in self.tag_keys]
        select = f"{NRQL_FUNCTIONS[func]}({identifier(found.attribute)})"
        self.terms[name] = MetricTerm(
            metric=shown(s.metric, 200),
            space_aggregation=AGGREGATIONS[func],
            nr_target=NrTarget(event=found.event, attribute=found.attribute, select=select),
        )
        self.scopes[name] = filters
        self.groups.append(groups)
        self.scales[name] = found.unit_scale
        self.from_map.append(found.from_map)
        self.events.add(found.event)
        return Ref(name=name)

    def _attribute(self, tag: str) -> str:
        attr = resolve_attribute(self.names, tag)
        if attr is None:
            raise Fail(f"{R.M_TAG}: {shown(tag, 40)}")
        if attr not in self.tag_keys:
            self.tag_keys.append(attr)
        return attr

    def expr(self, node: Node) -> Expr:
        if isinstance(node, Series):
            return self.series(node)
        if isinstance(node, QNum):
            return Num(value=node.value)
        if isinstance(node, Bin):
            op = cast(Literal["+", "-", "*", "/"], node.op)
            return BinOp(op=op, left=self.expr(node.left), right=self.expr(node.right))
        raise Fail(f"{R.M_UNREADABLE}: part of the formula is not a series, a number or arithmetic")


def _check_calls(query: MetricQuery) -> None:
    for call in query.calls():
        if call.name == "as_count":
            continue
        if call.name == "as_rate":
            raise Fail(R.M_AS_RATE)
        raise Fail(function_reason(call.name))
    if isinstance(query.expr, Call):  # a top-level function the walk above has not named
        raise Fail(function_reason(query.expr.name))


def zero_default(query: MetricQuery) -> Series | None:
    """The series inside `default(<one series>, 0)`, the only default() form converted.

    A New Relic signal can fill an empty window with a static value, which is what Datadog does
    to a gap in one existing series. A formula inside default() is not converted: the fill would
    have to apply before the arithmetic, and a signal fills only the whole query's result.
    """
    expr = query.expr
    if not (isinstance(expr, Call) and expr.name == "default" and len(expr.args) == 2):
        return None
    inner, value = expr.args
    if isinstance(inner, Series) and isinstance(value, QNum) and value.value == 0.0:
        return inner
    return None


def classify_query(query: MetricQuery) -> tuple[Node, Window]:
    """The expression and window of a plain threshold query, or Fail naming the family."""
    if not query.ok or query.expr is None or query.window is None:
        first = query.diagnostics[0] if query.diagnostics else None
        detail = f": {shown(first.code, 40)}: {shown(first.message, 100)}" if first else ""
        raise Fail(f"{R.M_UNREADABLE}{detail}")
    for call in query.calls():
        if call.name in ALGORITHMS:
            raise Fail(ALGORITHMS[call.name])
    if query.change is not None:
        raise Fail(R.M_CHANGE)
    if query.window.kind == "next":
        raise Fail(R.M_FORECAST)
    if query.window.kind == "current":
        raise Fail(R.M_CUMULATIVE)
    if not query.lossless:
        detail = query.diagnostics[0].message if query.diagnostics else ""
        raise Fail(f"{R.M_NOT_LOSSLESS}: {shown(detail, 100)}")
    inner = zero_default(query)
    _check_calls(replace(query, expr=inner) if inner is not None else query)
    return (inner if inner is not None else query.expr), query.window


def _scaled(expr: Expr, scales: dict[str, float]) -> Expr:
    """Fold each metric's unit scale into a formula; a single term is scaled in the thresholds."""
    if isinstance(expr, Ref) and scales[expr.name] != 1.0:
        return BinOp(op="*", left=expr, right=Num(value=scales[expr.name]))
    if isinstance(expr, BinOp):
        return BinOp(op=expr.op, left=_scaled(expr.left, scales), right=_scaled(expr.right, scales))
    return expr


@dataclass
class _Plan:
    built: _Terms
    expr: Expr
    timed: Timing
    limits: Thresholds
    single: bool
    scale: float
    window: int
    fill_zero: bool = False


def _plan(m: DatadogMonitor, query: MetricQuery) -> _Plan:
    node, window = classify_query(query)
    timed = timing(window.seconds)
    built = _Terms(query.time_aggr, m.names)
    expr = built.expr(node)
    if not built.terms:
        raise Fail(R.M_NO_METRIC)
    if len({frozenset(g) for g in built.groups}) > 1:
        raise Fail(R.M_GROUP_MISMATCH)
    if len(built.events) > 1:
        raise Fail(R.M_MIXED_EVENTS)
    single = len(built.terms) == 1 and isinstance(expr, Ref)
    scale = built.scales["a"] if single else 1.0
    if query.threshold is None:
        raise Fail(R.M_THRESHOLD)
    limits = thresholds(m, query.op, query.threshold, scale, m.priority_level)
    return _Plan(
        built, expr, timed, limits, single, scale, window.seconds, zero_default(query) is not None
    )


def translate_metric(m: DatadogMonitor) -> IRMonitor:
    base = base_fields(m)
    query = parse_metric_query(m.query_text)
    try:
        plan = _plan(m, query)
    except Fail as fail:
        return manual(base, [fail.reason], fail.steps)
    built = plan.built
    options = options_for(m, plan.window, event_style=False)
    note, description, message_notes = message_parts(m)
    scopes = list(built.scopes.values())
    shared = all(s == scopes[0] for s in scopes)
    terms = built.terms
    if not shared:
        terms = {k: v.model_copy(update={"scope": built.scopes[k]}) for k, v in terms.items()}
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=shown(m.query_text, 400),
        window_seconds=plan.window,
        aggregation_window_seconds=plan.window,
        aggregation_delay_seconds=options.delay,
        occurrences=Occurrences.ALL,
        metric=MetricSignal(
            terms=terms,
            expression=None if plan.single else _scaled(plan.expr, built.scales),
            aggregation=AGGREGATIONS[query.time_aggr],
            scope=scopes[0] if shared else [],
            group_by=list(built.groups[0]),
        ),
    )
    return IRMonitor(
        **base,
        signal=signal,
        thresholds=plan.limits.terms,
        no_data=options.no_data,
        notification=note,
        confidence=Confidence.APPROXIMATE,
        reasons=_reasons(plan, options.notes, message_notes),
        unmapped={**options.unmapped, **priority_note(m.priority_level)},
        emit=EmitOptions(
            slide_by_seconds=plan.timed.slide,
            description_note=description or None,
            **({"fill_option": "STATIC", "fill_value": 0.0} if plan.fill_zero else {}),
        ),
        data_path="name-map" if all(built.from_map) else "datadog-same-names",
    )


def _reasons(plan: _Plan, options: list[str], message: list[str]) -> list[str]:
    built = plan.built
    out: list[str] = list(built.notes)
    if plan.fill_zero:
        out.append(R.N_DEFAULT_ZERO)
    if len(built.terms) > 1:
        out.append(R.N_RATIO)
    out.append(R.N_NAMES_UNVERIFIED if not all(built.from_map) else R.N_NAMES_MAPPED)
    if built.tag_keys:
        out.append(R.N_ATTRS_UNVERIFIED.format(names=shown(", ".join(built.tag_keys[:8]), 120)))
    if plan.scale != 1.0:
        out.append(R.N_SCALE.format(scale=f"{plan.scale:g}"))
    return list(dict.fromkeys([*out, *plan.timed.notes, *plan.limits.notes, *options, *message]))
