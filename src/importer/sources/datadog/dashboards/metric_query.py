"""One plain Datadog metric query as a New Relic series, reusing the monitor translator.

A dashboard query has no time aggregator, window or threshold, so the monitor parser is given a
fixed head and a dummy comparison (`avg(last_5m):<query> > 0`) and only the series is used. The
scope, tag and metric-name rules, the name map and the NRQL builders are the monitor ones.
What a dashboard can do and an alert cannot is done here for the functions with a safe NRQL form:
top() (FACET, ORDER BY, LIMIT), as_count(), as_rate() and per_second() (rate(sum(x), 1 second)),
fill() (not carried) and rollup() (avg, min, max; sum only as daily). Anything else (timeshift,
weighted, derivative, formulas) is a placeholder with a reason.
"""

from dataclasses import dataclass, replace

from importer.core.dashboard_ir import Series
from importer.core.emit.nrql import MAX_ALIAS, identifier, quote_string, widget_nrql
from importer.core.ir import BinOp, Expr, MetricSignal, Num, Ref, TagFilter
from importer.core.text import printable
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.formulas import parse_formula, refs_in, top_formula
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.dashboards.variables import (
    Resolver,
    Scope,
    TemplateVar,
    UsedVariable,
    with_variables,
)
from importer.sources.datadog.metric import AGGREGATIONS
from importer.sources.datadog.metric import _Terms as Terms  # shared with the monitor path
from importer.sources.datadog.names import NameMap, Resolved, resolve_metric
from importer.sources.datadog.query import Bin, Call, MetricQuery, Node, Word, parse_metric_query
from importer.sources.datadog.query import Num as QueryNum
from importer.sources.datadog.query import Series as QuerySeries

PERIOD_SECONDS = 300  # unused by TIMESERIES AUTO and by single-value widgets; the IR needs one
HEAD = "avg(last_5m):"
TAIL = " > 0"


@dataclass(frozen=True)
class Converted:
    series: Series | None  # structured, when the query holds no variable
    nrql: str | None  # the finished query, when it does
    notes: list[str]
    data_path: str
    variables: dict[str, UsedVariable]
    limited: bool = False  # the query sets its own LIMIT


def _several(text: str) -> bool:
    """True when a comma outside braces and parentheses separates queries (`a{*}, b{*}`)."""
    depth = 0
    for ch in text:
        depth += ch in "({"
        depth -= ch in ")}"
        if ch == "," and depth == 0:
            return True
    return False


def split_queries(text: str) -> list[str]:
    """The queries of `a{*}, b{*}`: split at each comma outside braces and parentheses."""
    parts: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        depth += ch in "({"
        depth -= ch in ")}"
        if ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


@dataclass(frozen=True)
class Top:
    limit: int
    rank: str  # a New Relic aggregate: average, max, min, sum or latest
    direction: str  # DESC or ASC


@dataclass(frozen=True)
class Shape:
    """One plain series and the functions around it that this converter carries over."""

    series: QuerySeries
    top: Top | None = None
    rate: bool = False
    as_count: bool = False
    fill: bool = False
    rollup_method: str = ""  # avg, min, max or sum; empty when rollup() names none
    rollup_seconds: int | None = None
    rollup_daily: bool = False


MAX_LIMIT = 5000  # the most groups NRQL returns for a FACET
RANKS = {"mean": "average", "avg": "average", "max": "max", "min": "min", "sum": "sum"}
ROLLUP_METHODS = ("avg", "min", "max", "sum")


def _top(call: Call) -> tuple[Top, Node]:
    name = "top"
    args = call.args
    if len(args) != 4 or not (
        isinstance(args[1], QueryNum) and isinstance(args[2], Word) and isinstance(args[3], Word)
    ):
        raise Fail(DR.W_FUNC_ARGS.format(name=name, why="is read only as top(query, n, by, dir)"))
    limit = args[1].value
    if limit != int(limit) or not 1 <= limit <= MAX_LIMIT:
        raise Fail(
            DR.W_FUNC_ARGS.format(name=name, why=f"limit must be a whole number to {MAX_LIMIT}")
        )
    by, direction = args[2].text.lower(), args[3].text.lower()
    if by not in (*RANKS, "last"):
        raise Fail(
            DR.W_FUNC_ARGS.format(
                name=name, why=f"ranks by {shown(by, 20)}, which has no New Relic aggregate"
            )
        )
    if direction not in ("asc", "desc"):
        raise Fail(DR.W_FUNC_ARGS.format(name=name, why="direction is not asc or desc"))
    return Top(int(limit), RANKS.get(by, "latest"), direction.upper()), args[0]


def _rollup(call: Call, shape: Shape) -> Shape:
    if shape.rollup_method or shape.rollup_seconds or shape.rollup_daily:
        raise Fail(DR.W_FUNC_ARGS.format(name="rollup", why="appears twice"))
    method, seconds, daily = "", None, False
    for arg in call.args:
        if isinstance(arg, Word) and arg.text.lower() in ROLLUP_METHODS and not method:
            method = arg.text.lower()
        elif isinstance(arg, Word) and arg.text.lower() == "daily" and not daily:
            daily = True
        elif isinstance(arg, QueryNum) and arg.value == int(arg.value) and arg.value >= 1:
            seconds = int(arg.value)
        else:
            what = shown(arg.text, 20) if isinstance(arg, Word) else "this argument"
            raise Fail(
                DR.W_FUNC_ARGS.format(name="rollup", why=f"has {what}, which is not converted")
            )
    return replace(shape, rollup_method=method, rollup_seconds=seconds, rollup_daily=daily)


def _chain(shape: Shape) -> Shape:
    """Apply the `.fn()` chain of the series to the shape, or Fail on one with no safe form."""
    for call in shape.series.functions:
        if call.name == "as_count":
            shape = replace(shape, as_count=True)
        elif call.name == "as_rate":
            shape = replace(shape, rate=True)
        elif call.name == "fill":
            shape = replace(shape, fill=True)
        elif call.name == "rollup":
            shape = _rollup(call, shape)
        elif call.name == "weighted":
            why = "it weights an average by how long each series reported, which NRQL cannot do"
            raise Fail(DR.W_NO_FORM.format(name="weighted", why=why))
        else:
            raise Fail(DR.W_FUNCTION.format(name=shown(call.name, 40)))
    return shape


def read_shape(text: str) -> Shape:
    """The single series in a dashboard query and its supported functions, or Fail with why not."""
    if _several(text):
        raise Fail(DR.W_SEVERAL)
    query: MetricQuery = parse_metric_query(HEAD + text + TAIL)
    if not query.ok or query.expr is None or not query.lossless:
        first = query.diagnostics[0] if query.diagnostics else None
        detail = f": {shown(first.code, 40)}: {shown(first.message, 100)}" if first else ""
        raise Fail(f"{DR.W_UNREADABLE}{detail}")
    expr = query.expr
    top: Top | None = None
    rate = False
    if isinstance(expr, Call) and expr.name == "top":
        top, inner = _top(expr)
        expr = inner
    if isinstance(expr, Call) and expr.name == "per_second" and len(expr.args) == 1:
        if top is not None:
            raise Fail(DR.W_FUNC_ARGS.format(name="top", why="over a rate is not converted"))
        rate, expr = True, expr.args[0]
    if isinstance(expr, Call):
        raise Fail(DR.W_FUNCTION.format(name=shown(expr.name, 40)))
    if isinstance(expr, Bin):
        raise Fail(DR.W_ARITHMETIC)
    if not isinstance(expr, QuerySeries):
        raise Fail(DR.W_UNREADABLE)
    shape = _chain(Shape(expr, top=top, rate=rate))
    if top is not None and shape.rate:
        raise Fail(DR.W_FUNC_ARGS.format(name="top", why="over a rate is not converted"))
    if top is not None and not expr.group_by:
        raise Fail(DR.W_FUNC_ARGS.format(name="top", why="needs a group by to rank"))
    return shape


def _group_by(series: QuerySeries, variables: dict[str, TemplateVar]) -> None:
    for tag in series.group_by:
        if "$" in tag:
            name = tag.removeprefix("$")
            if name not in variables:
                raise Fail(DR.V_UNDEFINED.format(name=shown(name, 40)))
            raise Fail(DR.V_FORM.format(what="a variable in group by"))


def _expression(scale: float) -> Expr | None:
    return None if scale == 1.0 else BinOp(op="*", left=Ref(name="a"), right=Num(value=scale))


def _select(query: str) -> tuple[str, str]:
    """The expression after SELECT and the text from FROM on. Only identifiers precede FROM."""
    head, _, tail = query.partition(" FROM ")
    return head.removeprefix("SELECT "), " FROM " + tail


def _ranking(top: Top, attribute: str) -> str:
    return f"{top.rank}({identifier(attribute)})"


def _raw_query(
    out: Series,
    shape: Shape,
    scale: float,
    timeseries: bool,
    filters: list[TagFilter],
    attribute: str,
) -> str:
    """The query as text, for the forms the structured series cannot say: rate(), ORDER BY and
    LIMIT, a fixed daily bucket, and template variables in the filter."""
    signal = out.signal
    if shape.rate:
        signal = signal.model_copy(update={"expression": None})
    label = out.label
    change = {"signal": signal, "auto_period": False, "label": None if shape.rate else label}
    query = widget_nrql(out.model_copy(update=change), False)
    if shape.rate:
        select, rest = _select(query)
        times = "" if scale == 1.0 else f" * {scale:g}"
        alias = f" AS {quote_string(printable(label, MAX_ALIAS))}" if label else ""
        query = f"SELECT rate({select}, 1 second){times}{alias}{rest}"
    query = with_variables(query, filters)
    if shape.top is not None:
        select, _ = _select(query)
        ranking = _ranking(shape.top, attribute)
        if ranking != select or shape.top.direction != "DESC":
            query += f" ORDER BY {ranking} {shape.top.direction}"
        query += f" LIMIT {shape.top.limit}"
    if timeseries:
        query += " TIMESERIES 1 day" if shape.rollup_daily else " TIMESERIES AUTO"
    return query


def _function_notes(shape: Shape, aggr: str, timeseries: bool) -> list[str]:
    notes: list[str] = []
    if shape.rate:
        notes.append(DR.N_RATE)
    if (shape.rate or shape.as_count) and aggr != "sum":
        notes.append(DR.N_SPACE_SUM.format(aggr=shown(aggr, 20)))
    if shape.fill:
        notes.append(DR.N_FILL)
    if shape.top is not None:
        notes.append(DR.N_TOP)
    if timeseries and shape.rollup_daily:
        notes.append(DR.N_DAILY)
    elif timeseries and shape.rollup_seconds:
        notes.append(DR.N_ROLLUP_AUTO)
    return notes


def _time_function(shape: Shape, time_aggr: str, timeseries: bool) -> str:
    """The function applied over a bucket (or the whole window), or Fail if the form is unsafe."""
    series = shape.series
    if shape.rate:
        if time_aggr not in ("", "avg", "sum"):
            raise Fail(
                DR.W_FUNC_ARGS.format(name="as_rate", why=f"cannot be combined with {time_aggr}")
            )
        return "sum"
    if shape.as_count:
        return time_aggr or "sum"
    func = time_aggr or shape.rollup_method or series.aggr
    # A sum over a fixed bucket depends on the bucket; New Relic's automatic bucket differs.
    sums = (shape.rollup_method or series.aggr) == "sum"
    if timeseries and shape.rollup_seconds and sums and not shape.rollup_daily:
        why = "of sum over a fixed bucket changes with New Relic's automatic bucket size"
        raise Fail(DR.W_FUNC_ARGS.format(name="rollup", why=why))
    return func


@dataclass
class _Query:
    """One query read and resolved, before it is placed in a series."""

    shape: Shape
    found: Resolved
    scope: Scope
    func: str


def _prepare(
    text: str,
    time_aggr: str,
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar],
    committed: dict[str, UsedVariable],
) -> _Query:
    shape = read_shape(text)
    series = shape.series
    _group_by(series, variables)
    if series.group_by and not grouped:
        raise Fail(DR.W_QV_GROUP)
    found = resolve_metric(names, series.metric)
    if found is None:
        raise Fail(f"{R.M_METRIC_NAME}: {shown(series.metric, 60)}")
    if shape.rate and found.kind == "gauge":
        why = "on a gauge needs an interval conversion that New Relic does not do"
        raise Fail(DR.W_FUNC_ARGS.format(name="as_rate", why=why))
    scope = Resolver(variables, names, committed, found.event, found.attribute).split(series.scope)
    func = _time_function(shape, time_aggr, timeseries)
    return _Query(shape, found, scope, func)


def _plain_in_formula(shape: Shape) -> None:
    what = (
        "a rate"
        if shape.rate
        else "top()"
        if shape.top
        else "a daily rollup"
        if shape.rollup_daily
        else ""
    )
    if what:
        raise Fail(DR.W_FORMULA_QUERY.format(what=what))


def _build(
    queries: dict[str, _Query],
    formula: Expr | None,
    timeseries: bool,
    label: str | None,
    names: NameMap,
) -> Converted:
    """One series from one query, or from several combined by `formula` into one SELECT."""
    first = next(iter(queries.values()))
    built = Terms(first.func, names)
    refs: dict[str, Ref] = {}
    for key, q in queries.items():
        built.time = q.func
        series = replace(
            q.shape.series,
            scope=q.scope.fixed,
            functions=tuple(f for f in q.shape.series.functions if f.name == "as_count"),
        )
        ref = built.series(series)
        extra = [*q.scope.defaults, *q.scope.variable_filters]
        built.scopes[ref.name] = [*built.scopes[ref.name], *extra]
        built.tag_keys += [f.key for f in extra if f.key not in built.tag_keys]
        refs[key] = ref
    many = len(queries) > 1
    if many and len({frozenset(g) for g in built.groups}) > 1:
        raise Fail(DR.W_FORMULA_GROUP)
    if many and len(built.events) > 1:
        raise Fail(DR.W_FORMULA_EVENTS)
    shape = first.shape
    if shape.rate:  # the monitor note about a missing as_count() or as_rate() does not apply
        built.notes = [n for n in built.notes if n != R.N_KIND.format(kind=first.found.kind)]
    used: dict[str, UsedVariable] = {}
    filters: list[TagFilter] = []
    notes = list(built.notes)
    for q in queries.values():
        used.update(q.scope.used)
        filters += [f for f in q.scope.variable_filters if f not in filters]
        notes += q.scope.notes
    notes += _function_notes(shape, first.shape.series.aggr, timeseries)
    if many:
        notes.append(DR.N_FORMULA)
    for q in queries.values():
        if q.func == "sum" and q.found.kind != "count" and not (q.shape.rate or q.shape.as_count):
            notes.append(DR.N_SUM_GAUGE)
        if q.found.unit_scale != 1.0:
            notes.append(R.N_SCALE.format(scale=f"{q.found.unit_scale:g}"))
        notes.append(R.N_NAMES_MAPPED if q.found.from_map else R.N_NAMES_UNVERIFIED)
    if built.tag_keys:
        notes.append(R.N_ATTRS_UNVERIFIED.format(names=shown(", ".join(built.tag_keys[:8]), 120)))
    scopes = list(built.scopes.values())
    shared = all(s == scopes[0] for s in scopes)
    terms = built.terms
    if not shared:
        terms = {k: v.model_copy(update={"scope": built.scopes[k]}) for k, v in terms.items()}
    if formula is None:
        expression = _expression(first.found.unit_scale)
    else:
        expression = _scaled(formula, {k: queries[k].found.unit_scale for k in queries}, refs)
    signal = MetricSignal(
        terms=terms,
        expression=expression,
        aggregation=AGGREGATIONS[first.func],
        scope=scopes[0] if shared else [],
        group_by=list(built.groups[0]),
    )
    out = Series(signal=signal, period_seconds=PERIOD_SECONDS, auto_period=timeseries, label=label)
    path = "name-map" if all(q.found.from_map for q in queries.values()) else "datadog-same-names"
    notes = list(dict.fromkeys(notes))
    structured = not (shape.rate or shape.top or filters or (timeseries and shape.rollup_daily))
    if structured:
        return Converted(out, None, notes, path, used)
    query = _raw_query(
        out, shape, first.found.unit_scale, timeseries, filters, first.found.attribute
    )
    return Converted(None, query, notes, path, used, limited=shape.top is not None)


def _scaled(formula: Expr, scales: dict[str, float], refs: dict[str, Ref]) -> Expr:
    """The formula over the terms' own names, each metric's unit scale folded in."""
    if isinstance(formula, Ref):
        ref = refs[formula.name]
        scale = scales[formula.name]
        return ref if scale == 1.0 else BinOp(op="*", left=ref, right=Num(value=scale))
    if isinstance(formula, BinOp):
        return BinOp(
            op=formula.op,
            left=_scaled(formula.left, scales, refs),
            right=_scaled(formula.right, scales, refs),
        )
    return formula


def convert_series(
    text: str,
    time_aggr: str,
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar],
    committed: dict[str, UsedVariable],
    label: str | None = None,
) -> Converted:
    """The series for one query. `time_aggr` is the function applied over each bucket (or over the
    whole window for a single value); the series' own space aggregator is used when it is empty."""
    query = _prepare(text, time_aggr, timeseries, grouped, names, variables, committed)
    return _build({"q": query}, None, timeseries, label, names)


def convert_formula(
    formula: str,
    queries: dict[str, str],
    time_aggr: str,
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar],
    committed: dict[str, UsedVariable],
    label: str | None = None,
) -> Converted:
    """One series for an arithmetic formula over named queries, all in one SELECT. The queries
    must group by the same tags and live on one event; NRQL cannot join anything else."""
    ranked = top_formula(formula)
    if ranked is not None:
        if ranked.name not in queries:
            raise Fail(DR.W_FORMULA_NAME.format(name=shown(ranked.name, 40)))
        text = f"top({queries[ranked.name]}, {ranked.limit}, '{ranked.by}', '{ranked.direction}')"
        only = _prepare(text, time_aggr, timeseries, grouped, names, variables, committed)
        scaled = (
            None
            if ranked.factor is None
            else BinOp(op="*", left=Ref(name="q"), right=Num(value=ranked.factor))
        )
        return _build({"q": only}, scaled, timeseries, label, names)
    expr = parse_formula(formula, set(queries))
    seen = dict(committed)
    prepared: dict[str, _Query] = {}
    for name in refs_in(expr):
        q = _prepare(queries[name], time_aggr, timeseries, grouped, names, variables, seen)
        _plain_in_formula(q.shape)
        seen.update(q.scope.used)
        prepared[name] = q
    if not prepared:
        raise Fail(DR.W_FORMULA_UNREADABLE.format(why="it names no query"))
    return _build(prepared, expr, timeseries, label, names)


def as_nrql(done: Converted, timeseries: bool) -> str:
    """The finished query text of a converted series, for a widget that mixes both forms."""
    if done.nrql is not None:
        return done.nrql
    if done.series is None:
        raise ValueError("a converted series holds neither a series nor a query")
    flat = done.series.model_copy(update={"auto_period": False})
    return widget_nrql(flat, False) + (" TIMESERIES AUTO" if timeseries else "")
