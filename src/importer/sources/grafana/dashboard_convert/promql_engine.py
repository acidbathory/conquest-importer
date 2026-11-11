"""PromQL rewrites for dashboard widgets that the regular-expression reader refuses.

`convert_query` (promql.py) tries its own shapes first. A query it refuses comes here, and only
the rewrites of docs/research/promql-engine-rules.md are written; anything else returns None and
the original refusal stands. The expression is read as a tree (`promql_ast`); the part under a
comparison or an `_over_time` function goes back through the original reader, so names, matchers,
variables and the name map are handled in one place.

- CMP-FILTER: `x > n` on a plain selector tests the sample value in a WHERE (cookbook,
  "Comparison filters", method 1); with the hinted series labels and a widget that shows one
  value it is the newest value of each series, then the test (method 2). `agg(...) > n` on a
  widget that shows one value is the nested query over the aggregate. A chart of an aggregate
  has no such form and stays refused.
- CMP-BOOL: `x > bool n` is `if(<aggregate> > n, 1, 0)`.
- OVER-TIME: `avg|max|min|sum|count_over_time(x[w])` is the aggregate over a window `w`, sliding
  by the step on a chart (`TIMESERIES w SLIDE BY step`), or `SINCE w ago` on a single value.
- GAUGE-SUM-NESTED: `sum by (l) (gauge)` on a single-value widget, when the hints name the labels
  of one series, is the sum of the newest value of each series.
"""

import re
from collections.abc import Callable
from dataclasses import replace

from importer.core.dashboard_nrql import check_raw_nrql
from importer.core.emit.nrql import identifier
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.legend import Legend
from importer.sources.grafana.dashboard_convert.nrql import interval_text
from importer.sources.grafana.dashboard_convert.promql import (
    UNIT_SECONDS,
    Converted,
    QueryOptions,
    Statement,
    convert_classic,
    tokenize,
)
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.promql_ast import (
    Aggregate,
    Binary,
    Call,
    Node,
    Number,
    Paren,
    PromQLSyntaxError,
    Selector,
    format_expr,
    parse,
)

MAX_LENGTH = 10_000
NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
WINDOW = re.compile(r"(?:\d+[smhdwy])+")
SELECT_ARGUMENT = re.compile(r"(?:latest|average)\((.+)\)")
OVER_TIME = {
    "avg_over_time": ("avg", "average"),
    "max_over_time": ("max", "max"),
    "min_over_time": ("min", "min"),
    "sum_over_time": ("sum", "sum"),
    "count_over_time": ("sum", "count"),  # the outer aggregator that adds per-series counts
}
FLIP = {">": "<", "<": ">", ">=": "<=", "<=": ">=", "==": "==", "!=": "!="}
SLIDE_STEP = 60

N_FILTER_SAMPLES = (
    f"{r.NOTE} the comparison tests every sample in the time range, not only the newest one, so "
    "a series that matched earlier still shows"
)
N_FILTER_NEWEST = (
    f"{r.NOTE} the comparison is applied to the newest value of each series over the time range, "
    "and the widget shows the series that pass, not a chart"
)
N_FILTER_AGGREGATE = (
    f"{r.NOTE} the comparison is applied to the aggregate over the whole time range, and the "
    "widget shows the groups that pass, not a chart"
)
N_BOOL = f"{r.NOTE} the comparison with bool is 1 or 0 for each group and bucket"
N_OVER_TIME_SLIDE = (
    f"{r.NOTE} the window of {{window}} slides by {{step}}; Grafana slides it at every point"
)
N_OVER_TIME_FIXED = (
    f"{r.NOTE} the window of {{window}} is not a whole number of minutes, so it is not slid"
)
N_OVER_TIME_BUCKET = (
    f"{r.NOTE} the window of the query follows a dashboard variable, so each New Relic bucket is "
    "the window and the windows do not overlap"
)
N_OVER_TIME_RANGE = (
    f"{r.NOTE} a single value over the dashboard time range; the window of {{window}} in the "
    "query cannot be set on a widget, so a longer picker gives a different number"
)
N_COUNT_SERIES = (
    f"{r.NOTE} the count is of series whose newest value in the time range passes the test (the "
    "name map lists the labels of one series)"
)
N_NESTED_SUM = (
    f"{r.NOTE} the sum adds the newest value of each series (the name map lists the labels of one "
    "series), over the whole time range"
)


def _unwrap(node: Node) -> Node:
    while isinstance(node, Paren):
        node = node.expr
    return node


def read(expr: str) -> Node | None:
    if len(expr) > MAX_LENGTH:
        return None
    try:
        return _unwrap(parse(expr))
    except (PromQLSyntaxError, IndexError, RecursionError):
        return None


def comparison_parts(tree: Node) -> tuple[Node, str, str, bool] | None:
    """(left side, operator, number, bool) for `x OP number` or `number OP x`."""
    if not isinstance(tree, Binary) or tree.op not in FLIP or tree.matching is not None:
        return None
    lhs, rhs = _unwrap(tree.lhs), _unwrap(tree.rhs)
    if isinstance(rhs, Number) and NUMBER.fullmatch(rhs.text) and not isinstance(lhs, Number):
        return lhs, tree.op, rhs.text, tree.bool_modifier
    if isinstance(lhs, Number) and NUMBER.fullmatch(lhs.text) and not isinstance(rhs, Number):
        return rhs, FLIP[tree.op], lhs.text, tree.bool_modifier
    return None


def over_time_parts(tree: Node) -> tuple[str, str, Selector, str | None, tuple[str, ...]] | None:
    """(function, NRQL function, selector, outer aggregator, by labels) for an _over_time shape."""
    outer: str | None = None
    labels: tuple[str, ...] = ()
    node = tree
    if isinstance(node, Aggregate) and node.param is None and node.grouping != "without":
        outer, labels, node = node.op, tuple(node.labels), _unwrap(node.expr)
    if not isinstance(node, Call) or node.func not in OVER_TIME or len(node.args) != 1:
        return None
    selector = _unwrap(node.args[0])
    if not isinstance(selector, Selector) or not selector.range or selector.offset or selector.at:
        return None
    expected, nrql_function = OVER_TIME[node.func]
    if outer is not None and outer != expected:
        return None
    return node.func, nrql_function, selector, outer, labels


def reads_inner(expr: str, readable: Callable[[str], bool]) -> bool:
    """Whether the engine has a rewrite for `expr`, given `readable(text)`, the planner's answer
    for the plain query under the comparison or the _over_time function."""
    tree = read(" ".join(expr.split()))
    if tree is None:
        return False
    parts = comparison_parts(tree)
    if parts is not None:
        # a plain selector (a WHERE on the value) and bool (an if()) work on every widget; a
        # filter on an aggregate has no chart form, so the planner leaves it to the placeholder
        lhs, _, _, bool_modifier = parts
        return (bool_modifier or isinstance(lhs, Selector)) and readable(format_expr(lhs))
    time_parts = over_time_parts(tree)
    if time_parts is None:
        return False
    _, _, selector, outer, labels = time_parts
    text = format_expr(replace(selector, range=None))
    if outer is not None:
        text = f"avg{f' by ({chr(44).join(labels)})' if labels else ''}({text})"
    return readable(text)


# --- comparison ---


def _condition(attribute: str, operator: str, number: str) -> list[str]:
    if operator == "==":  # raw equality on a float matched nothing in the live test
        return [f"{attribute} >= {number}", f"{attribute} <= {number}"]
    return [f"{attribute} {operator} {number}"]


def _comparison(
    parts: tuple[Node, str, str, bool],
    opts: QueryOptions,
    legend: Legend | None,
    repeat: str | None,
) -> Converted | None:
    lhs, operator, number, bool_modifier = parts
    inner = convert_classic(format_expr(lhs), "prometheus", opts, legend, repeat)
    statement = inner.statement
    if statement is None:
        return None
    notes = [*inner.notes]
    if bool_modifier:
        select = f"if({statement.select} {'=' if operator == '==' else operator} {number}, 1, 0)"
        rewritten = replace(statement, select=select)
        notes.append(N_BOOL)
        return _finish(inner, rewritten, notes)
    if isinstance(lhs, Selector):
        found = SELECT_ARGUMENT.fullmatch(statement.select)
        if found is None:
            return None
        attribute = found[1]
        series = _series_labels(lhs, opts)
        if series is not None and not opts.timeseries:
            return _nested(inner, statement, "latest", attribute, series, operator, number)
        where = (*statement.where, *_condition(attribute, operator, number))
        notes.append(N_FILTER_SAMPLES)
        return _finish(inner, replace(statement, where=where), notes)
    if opts.timeseries:
        return None  # a chart of an aggregate has no filtered form
    return _nested_aggregate(inner, statement, operator, number)


def _series_labels(selector: Selector, opts: QueryOptions) -> tuple[str, ...] | None:
    labels = opts.name_map.series.get(selector.name)
    if not labels:
        return None
    try:
        return tuple(identifier(label) for label in labels)
    except ValueError:
        return None


def _nested(
    inner: Converted,
    statement: Statement,
    function: str,
    attribute: str,
    facet: tuple[str, ...],
    operator: str,
    number: str,
) -> Converted:
    source = replace(
        statement, select=f"{function}({attribute})", alias=" AS v", facet=facet, limit=None
    )
    test = " AND ".join(_condition("v", operator, number))
    nrql = f"SELECT * FROM ({source.render()}) WHERE {test}"  # noqa: S608
    notes = [n for n in inner.notes if n != r.N_MERGED]
    return _checked(inner, nrql, [*notes, N_FILTER_NEWEST], list(facet), source)


def _nested_aggregate(
    inner: Converted, statement: Statement, operator: str, number: str
) -> Converted:
    source = replace(statement, alias=" AS v", timeseries="")
    test = " AND ".join(_condition("v", operator, number))
    columns = "*" if statement.facet else "v"
    nrql = f"SELECT {columns} FROM ({source.render()}) WHERE {test}"  # noqa: S608
    return _checked(inner, nrql, [*inner.notes, N_FILTER_AGGREGATE], list(statement.facet), source)


def _finish(inner: Converted, statement: Statement, notes: list[str]) -> Converted:
    return _checked(inner, statement.render(), notes, list(statement.facet), statement)


def _checked(
    inner: Converted, nrql: str, notes: list[str], facet: list[str], statement: Statement | None
) -> Converted:
    try:
        check_raw_nrql(nrql)
    except ValueError:
        raise Unconvertible(r.C_NRQL) from None
    return replace(
        inner, nrql=nrql, notes=list(dict.fromkeys(notes)), facet=facet, statement=statement
    )


# --- _over_time ---


def _seconds(window: str) -> int:
    return sum(int(n) * UNIT_SECONDS[u] for n, u in re.findall(r"(\d+)([smhdwy])", window))


def _over_time(
    parts: tuple[str, str, Selector, str | None, tuple[str, ...]],
    opts: QueryOptions,
    legend: Legend | None,
    repeat: str | None,
) -> Converted | None:
    _, function, selector, outer, labels = parts
    window, _names = tokenize(selector.range or "", opts)
    dynamic = not WINDOW.fullmatch(window)  # a variable or built-in: the bucket is the window
    seconds = 0 if dynamic else _seconds(window)
    plain = replace(selector, range=None)
    text = format_expr(plain)
    if outer is not None:
        by = f" by ({', '.join(labels)})" if labels else ""
        text = f"avg{by}({text})"  # the reader writes the facet and filters; the function is ours
    inner = convert_classic(text, "prometheus", opts, legend, repeat)
    statement = inner.statement
    if statement is None:
        return None
    found = SELECT_ARGUMENT.fullmatch(statement.select)
    if found is None:
        return None
    notes = [n for n in inner.notes if n != r.N_INSTANT]
    rewritten = replace(statement, select=f"{function}({found[1]})", timeseries="")
    if not opts.timeseries:
        shown = "the query" if dynamic else interval_text(seconds)
        notes.append(N_OVER_TIME_RANGE.format(window=shown))
        return _finish(inner, rewritten, notes)
    if dynamic:
        bucket = opts.bucket_seconds
        series = f" TIMESERIES {bucket} SECONDS" if bucket else " TIMESERIES AUTO"
        notes.append(N_OVER_TIME_BUCKET)
        return _finish(inner, replace(rewritten, timeseries=series), notes)
    step = _step(opts, seconds)
    series = f" TIMESERIES {interval_text(seconds)}"
    if step is None:
        notes.append(N_OVER_TIME_FIXED.format(window=interval_text(seconds)))
    else:
        series += f" SLIDE BY {interval_text(step)}"
        notes.append(
            N_OVER_TIME_SLIDE.format(window=interval_text(seconds), step=interval_text(step))
        )
    return _finish(inner, replace(rewritten, timeseries=series), notes)


def _step(opts: QueryOptions, seconds: int) -> int | None:
    bucket = opts.bucket_seconds
    if bucket and bucket < seconds and seconds % bucket == 0:
        return bucket
    if seconds > SLIDE_STEP and seconds % SLIDE_STEP == 0:
        return SLIDE_STEP
    return None


# --- gauge sum, nested ---


def _gauge_sum(
    tree: Node, opts: QueryOptions, legend: Legend | None, repeat: str | None
) -> Converted | None:
    if opts.timeseries or not isinstance(tree, Aggregate) or tree.op != "sum":
        return None
    if tree.param is not None or tree.grouping == "without":
        return None
    selector = _unwrap(tree.expr)
    if not isinstance(selector, Selector) or selector.range or selector.offset or selector.at:
        return None
    series = _series_labels(selector, opts)
    if series is None:
        return None
    by = tuple(identifier(label) for label in tree.labels)
    if set(by) >= set(series):
        return None  # the reader already writes it (one series per group)
    inner = convert_classic(format_expr(selector), "prometheus", opts, legend, repeat)
    statement = inner.statement
    if statement is None:
        return None
    found = SELECT_ARGUMENT.fullmatch(statement.select)
    if found is None:
        return None
    source = replace(
        statement,
        select=f"latest({found[1]})",
        alias=" AS v",
        facet=tuple(dict.fromkeys((*series, *by))),
        limit=None,
        timeseries="",
    )
    outer = f"SELECT sum(v) FROM ({source.render()})"  # noqa: S608
    if by:
        outer += " FACET " + ", ".join(by)
    outer += " LIMIT MAX" if by else ""
    notes = [n for n in inner.notes if n != r.N_MERGED]
    return _checked(inner, outer, [*notes, N_NESTED_SUM], list(by), source)


def count_parts(tree: Node) -> tuple[Selector, str, str, tuple[str, ...]] | None:
    """(selector, operator, number, by labels) for `count [by (..)] (selector OP number)`."""
    if not isinstance(tree, Aggregate) or tree.op != "count" or tree.param is not None:
        return None
    if tree.grouping == "without":
        return None
    parts = comparison_parts(_unwrap(tree.expr))
    if parts is None or parts[3] or not isinstance(parts[0], Selector):
        return None
    return parts[0], parts[1], parts[2], tuple(tree.labels)


def _count(
    parts: tuple[Selector, str, str, tuple[str, ...]],
    opts: QueryOptions,
    legend: Legend | None,
    repeat: str | None,
) -> Converted | None:
    selector, operator, number, labels = parts
    series = _series_labels(selector, opts)
    if series is None or opts.timeseries:
        return None  # the series are not known, or a chart cannot hold a nested query
    inner = convert_classic(format_expr(selector), "prometheus", opts, legend, repeat)
    statement = inner.statement
    found = SELECT_ARGUMENT.fullmatch(statement.select) if statement else None
    if statement is None or found is None:
        return None
    by = tuple(identifier(label) for label in labels)
    source = replace(
        statement,
        select=f"latest({found[1]})",
        alias=" AS v",
        facet=tuple(dict.fromkeys((*series, *by))),
        limit=None,
        timeseries="",
    )
    test = " AND ".join(_condition("v", operator, number))
    nrql = f"SELECT count(*) FROM ({source.render()}) WHERE {test}"  # noqa: S608
    if by:
        nrql += " FACET " + ", ".join(by) + " LIMIT MAX"
    notes = [n for n in inner.notes if n != r.N_MERGED]
    return _checked(inner, nrql, [*notes, N_COUNT_SERIES], list(by), source)


def convert_with_engine(
    expr: str, opts: QueryOptions, legend: Legend | None, repeat: str | None
) -> Converted | None:
    """The engine's NRQL for `expr`, or None when no rewrite applies (the caller re-raises)."""
    tree = read(" ".join(expr.split()))
    if tree is None:
        return None
    try:
        parts = comparison_parts(tree)
        if parts is not None:
            return _comparison(parts, opts, legend, repeat)
        counted = count_parts(tree)
        if counted is not None:
            return _count(counted, opts, legend, repeat)
        time_parts = over_time_parts(tree)
        if time_parts is not None:
            return _over_time(time_parts, opts, legend, repeat)
        return _gauge_sum(tree, opts, legend, repeat)
    except Unconvertible:
        return None
