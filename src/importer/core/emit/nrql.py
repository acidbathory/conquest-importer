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
# Datadog tag/attribute -> New Relic attribute, per data type. Anything not listed is used
# as-is and flagged as unverified by the translator.
TAG_ATTRIBUTES = {
    SignalKind.METRIC: {"host": "hostname", "service": "appName"},
    SignalKind.LOG: {"host": "hostname", "status": "level"},
    SignalKind.APM: {"service": "appName", "host": "host"},
    SignalKind.HEARTBEAT: {"host": "hostname"},
}
# Search terms that become a fixed predicate instead of an equality test.
SPECIAL_PREDICATES = {
    (SignalKind.APM, "status", "error"): ("error IS true", "error IS NOT true"),
}
COUNT_EVENTS = {SignalKind.LOG: "Log", SignalKind.APM: "Transaction"}
PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2}
MAX_ALIAS = 100
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


def attribute_for(kind: SignalKind, key: str) -> str:
    return TAG_ATTRIBUTES[kind].get(key, key)


def quote_string(value: str) -> str:
    """Return a single-quoted NRQL string literal with backslashes and quotes escaped."""
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("control characters are not allowed in NRQL strings")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"invalid NRQL identifier: {name!r}")
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


def _tag_clause(f: TagFilter) -> str:
    attr = identifier(attribute_for(SignalKind.METRIC, f.key))
    if f.absent:
        return f"{attr} IS NULL"
    return f"{attr} = {quote_string(f.value)}"


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
    special = SPECIAL_PREDICATES.get((kind, f.key, f.value))
    if special:
        return special[1] if f.negate else special[0]
    attr = identifier(attribute_for(kind, f.key))
    if f.wildcard:
        op = "NOT LIKE" if f.negate else "LIKE"
        return f"{attr} {op} {quote_string(f.value.replace('*', '%'))}"
    return f"{attr} {'!=' if f.negate else '='} {quote_string(f.value)}"


def _count_nrql(kind: SignalKind, signal: CountSignal) -> str:
    query = f"SELECT count(*) FROM {COUNT_EVENTS[kind]}"  # noqa: S608 - constant event names
    if signal.filters:
        query += " WHERE " + " AND ".join(_filter_clause(kind, f) for f in signal.filters)
    return query + _facet(kind, signal.group_by)


def _heartbeat_nrql(signal: HeartbeatSignal) -> str:
    query = f"SELECT count(*) FROM {identifier(signal.nr_target.event)}"  # noqa: S608
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
