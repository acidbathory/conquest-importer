"""Build NRQL from the IR. Shared by the NerdGraph and Terraform emitters.

NRQL is built only from validated identifiers, escaped string literals, and
`select` expressions from the trusted metric map. Never format untrusted text
into a query any other way.
"""

import re

from importer.core.dashboard_ir import Series
from importer.core.ir import (
    Aggregation,
    CountFilter,
    CountSignal,
    Expr,
    FilterBase,
    HeartbeatSignal,
    IRMonitor,
    MetricSignal,
    NrTarget,
    Num,
    Ref,
    SignalKind,
    TagFilter,
)
from importer.core.text import printable

NRQL_FUNCTIONS = {
    Aggregation.AVG: "average",
    Aggregation.MIN: "min",
    Aggregation.MAX: "max",
    Aggregation.SUM: "sum",
}
COUNT_EVENTS = {SignalKind.LOG: "Log", SignalKind.APM: "Transaction"}
PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}
MAX_ALIAS = 100
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def attribute_for(kind: SignalKind, key: str) -> str:  # noqa: ARG001 - kept for call-site clarity
    """Source attribute names are used as they are; each source maps its own names."""
    return key


def quote_string(value: str) -> str:
    """Return a single-quoted NRQL string literal with backslashes and quotes escaped."""
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("control characters are not allowed in NRQL strings")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def identifier(name: str) -> str:
    """A validated attribute or event name; backtick-quoted when NRQL needs it.

    NRQL rejects a dotted name when a segment starts with a digit (``aws.apigateway.5XXError``),
    so such names are wrapped in backticks. The pattern admits no backtick, so quoting is safe.
    """
    if len(name) > 2 and name[0] == name[-1] == "`":
        # Already quoted by an earlier pass: check what is inside and keep it.
        return identifier(name[1:-1])
    if not _IDENTIFIER.match(name):
        raise ValueError(f"invalid NRQL identifier: {name!r}")
    if any(part[:1].isdigit() for part in name.split(".")):
        return f"`{name}`"
    return name


def _facet(kind: SignalKind, group_by: list[str]) -> str:
    if not group_by:
        return ""
    return " FACET " + ", ".join(identifier(attribute_for(kind, g)) for g in group_by)


def _target_sql(target: NrTarget, func: str) -> str:
    if target.select is not None:
        return target.select
    if target.attribute is None:
        raise ValueError("target has neither attribute nor select")
    return f"{func}({identifier(target.attribute)})"


def _expr_sql(expr: Expr, signal: MetricSignal, func: str, parent_prec: int = 0) -> str:
    if isinstance(expr, Num):
        return f"{expr.value:g}"
    if isinstance(expr, Ref):
        target = signal.terms[expr.name].nr_target
        if target is None:
            raise ValueError("term has no New Relic target")
        return _target_sql(target, func)
    prec = PRECEDENCE[expr.op]
    left = _expr_sql(expr.left, signal, func, prec)
    # the right side of - and / needs parentheses even at equal precedence
    right = _expr_sql(expr.right, signal, func, prec + (1 if expr.op in "-/" else 0))
    text = f"{left} {expr.op} {right}"
    return f"({text})" if prec < parent_prec else text


def _match_clause(attr: str, f: FilterBase) -> str:
    """One WHERE term for a filter; `attr` is already a validated identifier."""
    if f.values:
        items = ", ".join(quote_string(v) for v in f.values)
        return f"{attr} {'NOT IN' if f.negate else 'IN'} ({items})"
    if f.wildcard:
        if "%" in f.value:  # a literal percent sign would act as a wildcard in LIKE
            raise ValueError("a % in a LIKE pattern cannot be escaped, so it cannot be emitted")
        op = "NOT LIKE" if f.negate else "LIKE"
        return f"{attr} {op} {quote_string(f.value.replace('*', '%'))}"
    return f"{attr} {'!=' if f.negate else '='} {quote_string(f.value)}"


def _tag_clause(f: TagFilter) -> str:
    attr = identifier(attribute_for(SignalKind.METRIC, f.key))
    if f.absent:
        return f"{attr} IS NOT NULL" if f.negate else f"{attr} IS NULL"
    return _match_clause(attr, f)


def _metric_nrql(signal: MetricSignal, alias: str | None = None) -> str:
    func = NRQL_FUNCTIONS[signal.aggregation]
    targets = [t.nr_target for t in signal.terms.values()]
    if any(t is None for t in targets):
        raise ValueError("term has no New Relic target")
    events = {t.event for t in targets if t}
    if len(events) != 1:
        raise ValueError("metrics live on different New Relic events and cannot share one query")
    if signal.expression is None:
        (target,) = (t for t in targets if t is not None)
        select = _target_sql(target, func)
    else:
        select = _expr_sql(signal.expression, signal, func)
    if alias:
        select += f" AS {quote_string(printable(alias, MAX_ALIAS))}"
    query = f"SELECT {select} FROM {identifier(events.pop())}"  # noqa: S608 - validated
    if signal.scope:
        query += " WHERE " + " AND ".join(_tag_clause(f) for f in signal.scope)
    return query + _facet(SignalKind.METRIC, signal.group_by)


def widget_nrql(series: Series, timeseries: bool) -> str:
    """NRQL for one dashboard series; `timeseries` adds the bucket size for charts over time."""
    query = _metric_nrql(series.signal, series.label)
    if timeseries:
        query += f" TIMESERIES {int(series.period_seconds)} SECONDS"
    return query


def _filter_clause(kind: SignalKind, f: CountFilter) -> str:
    return _match_clause(identifier(attribute_for(kind, f.key)), f)


def _count_nrql(kind: SignalKind, signal: CountSignal) -> str:
    query = f"SELECT count(*) FROM {COUNT_EVENTS[kind]}"  # noqa: S608 - constant event names
    if signal.filters:
        query += " WHERE " + " AND ".join(_filter_clause(kind, f) for f in signal.filters)
    return query + _facet(kind, signal.group_by)


def _heartbeat_nrql(signal: HeartbeatSignal) -> str:
    target = signal.nr_target
    query = f"SELECT count(*) FROM {identifier(target.event)}"  # noqa: S608
    clauses = [_tag_clause(f) for f in signal.scope]
    if target.event == "Metric" and target.attribute:
        # a metric arrives as rows of the Metric event named by metricName
        clauses.insert(0, f"metricName = {quote_string(target.attribute)}")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    return query + _facet(SignalKind.HEARTBEAT, signal.group_by)


def build_nrql(monitor: IRMonitor) -> str:
    sig = monitor.signal
    if sig.kind == SignalKind.METRIC and sig.metric:
        return _metric_nrql(sig.metric)
    if sig.kind in COUNT_EVENTS and sig.count:
        return _count_nrql(sig.kind, sig.count)
    if sig.kind == SignalKind.HEARTBEAT and sig.heartbeat:
        return _heartbeat_nrql(sig.heartbeat)
    raise ValueError(f"cannot build NRQL for signal kind {sig.kind.value}")
