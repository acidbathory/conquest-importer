"""PromQL rewrites for alerts, read as a tree (docs/research/promql-engine-rules.md).

The forms in `promql.py` read text with regular expressions and give up on anything beyond one
aggregate or one ratio. This module runs after them, only on expressions they refused, so no rule
that converted before changes. It reads the expression with `promql_ast` and converts a shape only
when the rewrite can be proved from the rules registry:

- ARITH: `+ - * /`, numbers and unary minus between aggregates that share one grouping become one
  query with math on the aggregates (cookbook "Multi-query math in alerts"). Operands may differ in
  their label filters (each side then gets its own `filter()`), not in grouping, window or kind.
- RATIO-BARE: the same for plain selectors (`used / limit`); the test stays per series, as for a
  single bare selector.
- OVER-TIME: `avg|max|min|sum|count_over_time(x[w])`, alone or inside the same aggregate, becomes
  the aggregation over a window of `w` that slides by the evaluation interval (cookbook
  "_over_time functions", method 3).
- GAUGE-SUM: `sum by (labels) (gauge)` when the hints file says those labels name one series: the
  sum is then one value per group, so the average of the gauge is exact.
- TOPK: `topk(k, x) > n` and `bottomk(k, x) < n` fire when any series passes the bound, which is
  what the condition on every series does; only the number of incidents differs.

Anything else returns None (or raises `Fail`) and the older refusal keeps its reason.
"""

import re
from dataclasses import dataclass, replace

from importer.core.ir import (
    Aggregation,
    BinOp,
    Expr,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Num,
    Operator,
    Ref,
    SignalKind,
)
from importer.sources.grafana.dashboard_convert.name_map import (
    NameMap,
    NameMapError,
    active_name_map,
)
from importer.sources.grafana.promql_ast import (
    Aggregate,
    Binary,
    Call,
    Node,
    Number,
    Paren,
    PromQLSyntaxError,
    Selector,
    Unary,
    format_expr,
    parse,
    variables_in,
    walk,
)
from importer.sources.grafana.translate import (
    COUNTER_SUFFIXES,
    EMPTY_SENSITIVE,
    EXACT_AGGREGATES,
    MAX_EXPRESSION,
    N_COUNTER,
    N_COUNTER_EMPTY,
    N_METRIC,
    N_NEGATION,
    N_PER_SERIES,
    N_RATE_DIVISION,
    N_RATE_SAMPLES,
    N_RATIO_ZERO,
    PROM_COUNTER,
    PROM_MATCHER,
    PROM_NOT_SIMPLE,
    R_AGG_MIX,
    R_COUNTER_REDUCER,
    RANGE_SECONDS,
    REDUCERS,
    Built,
    Ctx,
    Fail,
    Match,
    looked_back,
    nrql_attribute,
    parse_matchers,
    register_prom_fallback,
    tag_filter,
    time_window,
)

MIN_RANGE = 60
MAX_FACETS = 5
COUNT_SUFFIXES = ("_total", "_count", "_bucket")  # what New Relic stores as a delta count
SIMPLE_RANGE = re.compile(r"(\d+)([smh])")
ARITH_OPS = frozenset({"+", "-", "*", "/"})
COUNTER_FUNCTIONS = frozenset({"rate", "increase"})
OVER_TIME = {
    "avg_over_time": ("avg", Aggregation.AVG),
    "max_over_time": ("max", Aggregation.MAX),
    "min_over_time": ("min", Aggregation.MIN),
    "sum_over_time": ("sum", Aggregation.SUM),
    "count_over_time": ("sum", Aggregation.COUNT),  # (the outer aggregator that stays exact)
}
GAUGE_AGGREGATORS = frozenset({"avg", "sum", "min", "max"})

R_MIX = (
    "promql arithmetic mixes operands that one New Relic query cannot share: counters, gauges and "
    "range functions, or different groupings, windows or aggregations, need separate queries"
)
R_ZERO_DIVISOR = "promql divides by the literal 0"
R_ARITH_OPERATOR = "promql arithmetic uses an operator other than + - * /"
R_OVER_TIME_AGG = (
    "promql {agg}() across series of {function}(): the two layers are not one New Relic "
    "aggregation, so only the same operation (max of max_over_time and so on) converts"
)
R_WITHOUT_UNKNOWN = (
    "promql without() needs the labels of the metric (the name map key labels, from keyset()); "
    "none are listed for it"
)
R_WITHOUT_WIDE = "promql without() leaves {count} labels, and a New Relic FACET takes at most 5"
N_WITHOUT = (
    "without() is written as the remaining labels listed in the name map (labels); a label the "
    "metric gains later is not in the grouping"
)
R_PER_SERIES_MATH = (
    "promql arithmetic between plain selectors tests each series; without the series labels "
    "(from grafana-prom-hints) one New Relic query would combine every series first"
)
R_TOPK_DIRECTION = (
    "promql {function}() with a threshold that keeps the other end of the ranking: only "
    "topk with above and bottomk with below can be tested on every series"
)
N_OVER_TIME = (
    "{function} becomes the {aggregation} of the samples in the window; Prometheus weighs every "
    "sample the same, so does New Relic, but a different scrape interval gives a different count"
)
N_GAUGE_SUM = (
    "sum() of the gauge {name} is one series per group (the hints say {labels} name a series), "
    "so the average of its samples in the window is the value Prometheus sums"
)
N_TOPK = (
    "{function}({k}) is dropped: the condition tests every series, so it opens one incident per "
    "series that passes the bound, where Prometheus reports at most {k}"
)
N_ARITH_SCOPE = (
    "the operands have different label filters, so each aggregate carries its own filter(); a "
    "series that matches neither filter is ignored in both"
)


@dataclass(frozen=True)
class Leaf:
    """One operand of the arithmetic: [agg [by (..)]] ( [function] ( selector [range] ) )."""

    kind: str  # counter, over_time or gauge
    aggregator: str | None
    group_by: tuple[str, ...]
    name: str
    matches: tuple[Match, ...]
    function: str | None
    seconds: int | None
    aggregation: Aggregation
    note: str = ""
    without: bool = False


def _hints() -> NameMap:
    try:
        return active_name_map()
    except NameMapError:
        return NameMap()


def _unwrap(node: Node) -> Node:
    while isinstance(node, Paren):
        node = node.expr
    return node


def _range_seconds(text: str | None) -> int:
    found = SIMPLE_RANGE.fullmatch(text or "")
    if found is None:
        raise Fail(PROM_NOT_SIMPLE)
    return int(found[1]) * RANGE_SECONDS[found[2]]


def _selector(node: Node, *, ranged: bool) -> tuple[str, tuple[Match, ...], int | None]:
    node = _unwrap(node)
    if not isinstance(node, Selector) or (node.range is not None) != ranged:
        raise Fail(PROM_NOT_SIMPLE)
    if node.offset or node.at or not node.name or node.variables:
        raise Fail(PROM_NOT_SIMPLE)
    body = ", ".join(f'{m.label}{m.op}"{m.value}"' for m in node.matchers)
    matches = tuple(parse_matchers(body, PROM_MATCHER, PROM_NOT_SIMPLE, absent_ok=True))
    seconds = _range_seconds(node.range) if ranged else None
    return node.name, matches, seconds


def _stored_as_count(name: str, hints: NameMap) -> bool:
    """New Relic keeps the metric as a delta count (the hinted type, else its name suffix)."""
    kind = hints.types.get(name)
    if kind is None:
        return name.endswith(COUNT_SUFFIXES)
    return kind != "gauge"


def _looks_like_counter(name: str, hints: NameMap) -> bool:
    kind = hints.types.get(name)
    return name.endswith(COUNTER_SUFFIXES) if kind is None else kind != "gauge"


def _gauge_sum_is_one_series(
    name: str, group_by: tuple[str, ...], matches: tuple[Match, ...], hints: NameMap
) -> bool:
    """The hints name the labels of one series and the query groups by or pins all of them."""
    needed = hints.series.get(name)
    if needed is None:
        return False
    pinned = {m.key for m in matches if m.kind == "eq" and not m.negate and len(m.values) == 1}
    return set(needed) <= pinned | set(group_by)


def _leaf(node: Node, hints: NameMap) -> Leaf:
    node = _unwrap(node)
    aggregator: str | None = None
    group_by: tuple[str, ...] = ()
    without = False
    if isinstance(node, Aggregate):
        if node.op not in GAUGE_AGGREGATORS or node.param is not None:
            raise Fail(PROM_NOT_SIMPLE)
        aggregator, group_by = node.op, tuple(node.labels)
        without = node.grouping == "without"
        node = _unwrap(node.expr)
    if isinstance(node, Call):
        return replace(_call_leaf(node, aggregator, group_by, hints, without), without=without)
    name, matches, _ = _selector(node, ranged=False)
    group_by = _grouping(name, group_by, without, hints)
    if _looks_like_counter(name, hints):
        raise Fail(PROM_COUNTER)
    attribute = nrql_attribute(name)  # validates the name
    del attribute
    if aggregator == "sum":
        if not _gauge_sum_is_one_series(name, group_by, matches, hints):
            raise Fail(R_AGG_MIX.format(agg="sum", reducer="last"))
        note = N_GAUGE_SUM.format(name=name, labels=", ".join(hints.series[name]) or "no label")
        return Leaf("gauge", "avg", group_by, name, matches, None, None, Aggregation.AVG, note)
    aggregation = {"avg": Aggregation.AVG, "min": Aggregation.MIN, "max": Aggregation.MAX}.get(
        aggregator or "", Aggregation.AVG
    )
    return Leaf(
        "gauge", aggregator, group_by, name, matches, None, None, aggregation, without=without
    )


def _grouping(name: str, labels: tuple[str, ...], without: bool, hints: NameMap) -> tuple[str, ...]:
    """The `by` labels; for `without (..)` every label the hints list for the metric but those."""
    if not without:
        return labels
    known = hints.labels.get(name)
    if known is None:
        raise Fail(R_WITHOUT_UNKNOWN)
    kept = tuple(label for label in known if label not in labels and label != "__name__")
    if len(kept) > MAX_FACETS:
        raise Fail(R_WITHOUT_WIDE.format(count=len(kept)))
    return kept


def _call_leaf(
    call: Call, aggregator: str | None, group_by: tuple[str, ...], hints: NameMap, without: bool
) -> Leaf:
    if len(call.args) != 1:
        raise Fail(PROM_NOT_SIMPLE)
    name, matches, seconds = _selector(call.args[0], ranged=True)
    nrql_attribute(name)
    group_by = _grouping(name, group_by, without, hints)
    if call.func in COUNTER_FUNCTIONS:
        if aggregator not in (None, "sum"):
            raise Fail(PROM_NOT_SIMPLE)
        if not _stored_as_count(name, hints):
            raise Fail(PROM_COUNTER)
        # no aggregator: a per-series rate, which needs the series labels (hints) to be a FACET
        return Leaf(
            "counter", aggregator, group_by, name, matches, call.func, seconds, Aggregation.SUM
        )
    if call.func in OVER_TIME:
        if _looks_like_counter(name, hints):
            raise Fail(PROM_COUNTER)  # New Relic keeps a counter as per-interval changes
        outer, aggregation = OVER_TIME[call.func]
        if aggregator is not None and aggregator != outer:
            raise Fail(R_OVER_TIME_AGG.format(agg=aggregator, function=call.func))
        return Leaf(
            "over_time", aggregator, group_by, name, matches, call.func, seconds, aggregation
        )
    raise Fail(PROM_NOT_SIMPLE)


# --- the arithmetic tree ---


def _tree_leaves(node: Node, hints: NameMap) -> list[Leaf]:
    """The operands of an arithmetic tree, left to right; raise `Fail` for any other operator."""
    node = _unwrap(node)
    if isinstance(node, Number):
        return []
    if isinstance(node, Unary):
        return _tree_leaves(node.expr, hints)
    if isinstance(node, Binary):
        if node.op not in ARITH_OPS:
            raise Fail(R_ARITH_OPERATOR)
        if node.matching is not None or node.bool_modifier:
            raise Fail(PROM_NOT_SIMPLE)
        divisor = _unwrap(node.rhs)
        if node.op == "/" and isinstance(divisor, Number) and float(divisor.text) == 0:
            raise Fail(R_ZERO_DIVISOR)
        return _tree_leaves(node.lhs, hints) + _tree_leaves(node.rhs, hints)
    return [_leaf(node, hints)]


class _Terms:
    """Terms of the signal, one per distinct operand."""

    def __init__(self, shared: tuple[Match, ...]) -> None:
        self.shared = shared
        self.terms: dict[str, MetricTerm] = {}
        self.keys: dict[tuple[str, tuple[Match, ...], str | None], str] = {}

    def ref(self, leaf: Leaf) -> Expr:
        key = (leaf.name, leaf.matches, leaf.function)
        if key not in self.keys:
            name = f"t{len(self.keys)}"
            attribute = nrql_attribute(leaf.name)
            own = [m for m in leaf.matches if m not in self.shared]
            self.terms[name] = MetricTerm(
                metric=attribute,
                space_aggregation=leaf.aggregation,
                nr_target=NrTarget(event="Metric", attribute=attribute),
                scope=[tag_filter(m) for m in own],
            )
            self.keys[key] = name
        ref: Expr = Ref(name=self.keys[key])
        if leaf.function == "rate":
            ref = BinOp(op="/", left=ref, right=Num(value=float(leaf.seconds or 1)))
        return ref


def _expression(node: Node, leaves: list[Leaf], terms: _Terms) -> Expr:
    """The IR expression of the tree; `leaves` is consumed in order."""
    node = _unwrap(node)
    if isinstance(node, Number):
        return Num(value=float(node.text))
    if isinstance(node, Unary):
        inner = _expression(node.expr, leaves, terms)
        return inner if node.op == "+" else BinOp(op="-", left=Num(value=0.0), right=inner)
    if isinstance(node, Binary):
        left = _expression(node.lhs, leaves, terms)
        right = _expression(node.rhs, leaves, terms)
        op = node.op
        if op not in ARITH_OPS:
            raise Fail(R_ARITH_OPERATOR)
        return BinOp(op=op, left=left, right=right)
    return terms.ref(leaves.pop(0))


def _shared_matches(leaves: list[Leaf]) -> tuple[Match, ...]:
    first = leaves[0].matches
    return tuple(m for m in first if all(m in leaf.matches for leaf in leaves[1:]))


def _check_counter_window(seconds: int, ctx: Ctx) -> None:
    if looked_back(ctx) != "last":
        raise Fail(R_COUNTER_REDUCER.format(reducer=looked_back(ctx)))
    if seconds < MIN_RANGE or seconds % 60:
        raise Fail(PROM_NOT_SIMPLE)


def _window_plan(leaves: list[Leaf], ctx: Ctx) -> tuple[Aggregation, int | None, bool, list[str]]:
    """Aggregation, fixed window, sliding or not, and notes for operands of one kind."""
    first = leaves[0]
    notes = [N_METRIC]
    if first.kind in ("counter", "over_time"):
        _check_counter_window(first.seconds or 0, ctx)
    if first.kind == "counter":
        notes += [N_COUNTER, N_RATE_SAMPLES]
        if ctx.threshold is not None and ctx.threshold.operator in EMPTY_SENSITIVE:
            notes.append(N_COUNTER_EMPTY)
        if any(leaf.function == "rate" for leaf in leaves):
            notes.append(N_RATE_DIVISION.format(seconds=first.seconds))
        return first.aggregation, first.seconds, False, notes
    if first.kind == "over_time":
        notes.append(
            N_OVER_TIME.format(function=first.function, aggregation=first.aggregation.value)
        )
        return first.aggregation, first.seconds, True, notes
    reducer = looked_back(ctx)
    aggregation = first.aggregation
    if first.aggregator is None:
        aggregation = Aggregation.AVG if reducer in ("last", "mean") else REDUCERS[reducer]
    elif (first.aggregator, reducer) not in EXACT_AGGREGATES:
        raise Fail(R_AGG_MIX.format(agg=first.aggregator, reducer=reducer))
    fixed, window_notes = time_window(ctx, reducer)
    return aggregation, fixed, False, notes + window_notes


def _read(expr: str) -> Node | None:
    """The tree, or None when the text is too long or not PromQL this parser reads."""
    if len(expr) > MAX_EXPRESSION:
        return None
    try:
        return _unwrap(parse(expr))
    except (PromQLSyntaxError, IndexError, RecursionError):
        return None


def _arith_form(expr: str, ctx: Ctx) -> Built | None:
    tree = _read(expr)
    if tree is None:
        return None
    if variables_in(expr) or any(isinstance(n, Selector) and n.variables for n in walk(tree)):
        return None
    if isinstance(tree, (Number, Selector)) or (isinstance(tree, Aggregate) and tree.param):
        return None
    hints = _hints()
    leaves = _tree_leaves(tree, hints)
    if not leaves:
        return None
    kinds = {leaf.kind for leaf in leaves}
    groups = {tuple(sorted(leaf.group_by)) for leaf in leaves}
    aggregations = {leaf.aggregation for leaf in leaves}
    windows = {leaf.seconds for leaf in leaves}
    bare = {leaf.aggregator is None for leaf in leaves}
    if len(kinds) > 1 or len(groups) > 1 or len(aggregations) > 1 or len(windows) > 1:
        raise Fail(R_MIX)
    if len(bare) > 1:
        raise Fail(R_MIX)
    first = leaves[0]
    aggregation, fixed, slide, notes = _window_plan(leaves, ctx)
    notes += [leaf.note for leaf in leaves if leaf.note]
    if any(leaf.without for leaf in leaves):
        notes.append(N_WITHOUT)
    shared = _shared_matches(leaves)
    terms = _Terms(shared)
    expression = _expression(tree, list(leaves), terms)
    if "/" in _operators(tree):
        notes.append(N_RATIO_ZERO)
    if any(leaf.matches != first.matches for leaf in leaves):
        notes.append(N_ARITH_SCOPE)
    if any(m.negate for leaf in leaves for m in leaf.matches):
        notes.append(N_NEGATION)
    group_by = list(first.group_by)
    per_series = first.aggregator is None
    if per_series:
        notes.append(N_PER_SERIES)
        series = _series_labels(leaves, hints)
        if series is not None:
            group_by, per_series = series, False
        elif not _any_series_is_exact(tree, aggregation, ctx):
            raise Fail(R_PER_SERIES_MATH)  # a maximum of a ratio is not a ratio of maxima
    metric = MetricSignal(
        terms=terms.terms,
        expression=None if isinstance(expression, Ref) else expression,
        aggregation=aggregation,
        scope=[tag_filter(m) for m in shared],
        group_by=[nrql_attribute(g) for g in group_by],
    )
    return Built(
        SignalKind.METRIC, fixed, metric=metric, notes=notes, per_series=per_series, slide_by=slide
    )


def _any_series_is_exact(tree: Node, aggregation: Aggregation, ctx: Ctx) -> bool:
    """A plain bare max_over_time/min_over_time: the extreme of all samples is the extreme of the
    per-series extremes, so 'any series passes the bound' needs no series labels."""
    if not isinstance(tree, Call) or ctx.threshold is None:
        return False
    above = (Operator.ABOVE, Operator.ABOVE_OR_EQUALS)
    below = (Operator.BELOW, Operator.BELOW_OR_EQUALS)
    return (aggregation is Aggregation.MAX and ctx.threshold.operator in above) or (
        aggregation is Aggregation.MIN and ctx.threshold.operator in below
    )


def _operators(node: Node) -> set[str]:
    return {n.op for n in walk(node) if isinstance(n, Binary)}


def _series_labels(leaves: list[Leaf], hints: NameMap) -> list[str] | None:
    """The labels that name one series, when the hints give the same list for every operand."""
    if any(leaf.name not in hints.series for leaf in leaves):
        return None
    found = {tuple(hints.series[leaf.name]) for leaf in leaves}
    return list(found.pop()) if len(found) == 1 else None


# --- topk / bottomk ---


def _topk_form(expr: str, ctx: Ctx) -> Built | None:
    tree = _read(expr)
    if tree is None:
        return None
    if not (isinstance(tree, Aggregate) and tree.op in ("topk", "bottomk")):
        return None
    if not isinstance(tree.param, Number) or ctx.threshold is None:
        return None
    wanted = (
        (Operator.ABOVE, Operator.ABOVE_OR_EQUALS)
        if tree.op == "topk"
        else (Operator.BELOW, Operator.BELOW_OR_EQUALS)
    )
    if ctx.threshold.operator not in wanted:
        raise Fail(R_TOPK_DIRECTION.format(function=tree.op))
    inner = format_expr(tree.expr)
    from importer.sources.grafana.translate import _prometheus  # the whole chain, for the inner

    built = _prometheus(inner, ctx)
    built.notes.append(N_TOPK.format(function=tree.op, k=tree.param.text))
    return built


def _engine(expr: str, ctx: Ctx) -> Built | None:
    return _arith_form(expr, ctx) or _topk_form(expr, ctx)


register_prom_fallback(_engine)
