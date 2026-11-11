"""One plain Datadog metric query as a New Relic series, reusing the monitor translator.

A dashboard query has no time aggregator, window or threshold, so the monitor parser is given a
fixed head and a dummy comparison (`avg(last_5m):<query> > 0`) and only the series is used. The
scope, tag and metric-name rules, the name map and the NRQL builders are the monitor ones.
What a dashboard could do and an alert cannot (top, rollup, as_rate, timeshift, formulas) is not
done here: those queries are placeholders with a reason until the function table exists.
"""

from dataclasses import dataclass, replace

from importer.core.dashboard_ir import Series
from importer.core.emit.nrql import widget_nrql
from importer.core.ir import BinOp, Expr, MetricSignal, Num, Ref
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.dashboards.variables import (
    Resolver,
    TemplateVar,
    UsedVariable,
    with_variables,
)
from importer.sources.datadog.metric import AGGREGATIONS
from importer.sources.datadog.metric import _Terms as Terms  # shared with the monitor path
from importer.sources.datadog.names import NameMap, resolve_metric
from importer.sources.datadog.query import Bin, Call, MetricQuery, parse_metric_query
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


def _several(text: str) -> bool:
    """True when a comma outside braces and parentheses separates queries (`a{*}, b{*}`)."""
    depth = 0
    for ch in text:
        depth += ch in "({"
        depth -= ch in ")}"
        if ch == "," and depth == 0:
            return True
    return False


def read_series(text: str) -> QuerySeries:
    """The single plain series in a dashboard query, or Fail with the reason it is not one."""
    if _several(text):
        raise Fail(DR.W_SEVERAL)
    query: MetricQuery = parse_metric_query(HEAD + text + TAIL)
    if not query.ok or query.expr is None or not query.lossless:
        first = query.diagnostics[0] if query.diagnostics else None
        detail = f": {shown(first.code, 40)}: {shown(first.message, 100)}" if first else ""
        raise Fail(f"{DR.W_UNREADABLE}{detail}")
    calls = query.calls()
    if calls:
        raise Fail(DR.W_FUNCTION.format(name=shown(calls[0].name, 40)))
    if isinstance(query.expr, Call):
        raise Fail(DR.W_FUNCTION.format(name=shown(query.expr.name, 40)))
    if isinstance(query.expr, Bin):
        raise Fail(DR.W_ARITHMETIC)
    if not isinstance(query.expr, QuerySeries):
        raise Fail(DR.W_UNREADABLE)
    return query.expr


def _group_by(series: QuerySeries, variables: dict[str, TemplateVar]) -> None:
    for tag in series.group_by:
        if "$" in tag:
            name = tag.removeprefix("$")
            if name not in variables:
                raise Fail(DR.V_UNDEFINED.format(name=shown(name, 40)))
            raise Fail(DR.V_FORM.format(what="a variable in group by"))


def _expression(scale: float) -> Expr | None:
    return None if scale == 1.0 else BinOp(op="*", left=Ref(name="a"), right=Num(value=scale))


def convert_series(
    text: str,
    time_aggr: str,
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar],
    committed: dict[str, UsedVariable],
) -> Converted:
    """The series for one query. `time_aggr` is the function applied over each bucket (or over the
    whole window for a single value); the series' own space aggregator is used when it is empty."""
    series = read_series(text)
    _group_by(series, variables)
    if series.group_by and not grouped:
        raise Fail(DR.W_QV_GROUP)
    found = resolve_metric(names, series.metric)
    if found is None:
        raise Fail(f"{R.M_METRIC_NAME}: {shown(series.metric, 60)}")
    scope = Resolver(variables, names, committed, found.event, found.attribute).split(series.scope)
    func = time_aggr or series.aggr
    built = Terms(func, names)
    built.series(replace(series, scope=scope.fixed))
    extra = [*scope.defaults, *scope.variable_filters]
    built.scopes["a"] = [*built.scopes["a"], *extra]
    built.tag_keys += [f.key for f in extra if f.key not in built.tag_keys]
    notes = [*built.notes, *scope.notes]
    if func == "sum" and found.kind != "count":
        notes.append(DR.N_SUM_GAUGE)
    if found.unit_scale != 1.0:
        notes.append(R.N_SCALE.format(scale=f"{found.unit_scale:g}"))
    notes.append(R.N_NAMES_MAPPED if found.from_map else R.N_NAMES_UNVERIFIED)
    if built.tag_keys:
        notes.append(R.N_ATTRS_UNVERIFIED.format(names=shown(", ".join(built.tag_keys[:8]), 120)))
    signal = MetricSignal(
        terms=built.terms,
        expression=_expression(found.unit_scale),
        aggregation=AGGREGATIONS[func],
        scope=built.scopes["a"],
        group_by=list(built.groups[0]),
    )
    out = Series(signal=signal, period_seconds=PERIOD_SECONDS, auto_period=timeseries)
    path = "name-map" if found.from_map else "datadog-same-names"
    notes = list(dict.fromkeys(notes))
    if not scope.variable_filters:
        return Converted(out, None, notes, path, scope.used)
    flat = out.model_copy(update={"auto_period": False})
    query = with_variables(widget_nrql(flat, False), scope.variable_filters)
    return Converted(
        None, query + (" TIMESERIES AUTO" if timeseries else ""), notes, path, scope.used
    )
