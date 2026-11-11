"""Log based Azure alerts: scheduled query rules (KQL), activity log alerts, smart detectors.

Scheduled query rules convert the KQL subset in `kql_parse.py` (lowered by `kql_lower.py`). The
table has no counterpart in New Relic, so the condition reads the `Log` event: forwarded Azure
logs (`plugin.type = 'azure'`) for diagnostic tables, and nothing documented for agent tables.
`kql_tables.py` names the data path of each table (`IRMonitor.data_path`); the column names are
used as attribute names and are UNVERIFIED (docs/research/azure-monitor-alerting.md 4.5).
Activity log, service health and smart detector rules are manual (sections 4.6 and 4.8).
"""

import math
from typing import Any

from importer.core.emit.nrql import build_nrql
from importer.core.ir import (
    Aggregation,
    Confidence,
    CountFilter,
    CountSignal,
    EmitOptions,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Occurrences,
    Operator,
    Signal,
    SignalKind,
    TagFilter,
    Threshold,
)
from importer.sources.azure import reasons as R
from importer.sources.azure.common import (
    Fail,
    Level,
    base_fields,
    common_notes,
    key_of,
    manual,
    severity_level,
    timing,
)
from importer.sources.azure.kql_lex import KqlSyntax, KqlUnsupported
from importer.sources.azure.kql_lower import Lowered, is_empty_sensitive, lower
from importer.sources.azure.kql_parse import Query, parse
from importer.sources.azure.models import AzureAlert, iso_seconds
from importer.sources.azure.names import log_attribute

OPERATORS = {
    "greaterthan": Operator.ABOVE,
    "greaterthanorequal": Operator.ABOVE_OR_EQUALS,
    "lessthan": Operator.BELOW,
    "lessthanorequal": Operator.BELOW_OR_EQUALS,
    "equals": Operator.EQUALS,
}
AGGREGATIONS = {
    "average": Aggregation.AVG,
    "minimum": Aggregation.MIN,
    "maximum": Aggregation.MAX,
    "total": Aggregation.SUM,
}
BAD_SCOPE_TYPES = ("microsoft.insights/components", "microsoft.kusto/clusters")
FORWARDED = ("plugin.type", "azure")  # the attribute New Relic's forwarder documents
MAX_PERIODS = 24


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PERIODS:
        return value
    if value is None:
        return default
    raise Fail(f"{R.M_LOG_RANGE}: failingPeriods is not a small whole number")


def _dimensions(
    raw: object, alert: AzureAlert, table: str
) -> tuple[list[tuple[str, list[str], bool]], list[str]]:
    """[(attribute, values, negate)] and the facet attributes, from the rule's dimensions."""
    found: list[tuple[str, list[str], bool]] = []
    facets: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        values = item.get("values") if isinstance(item, dict) else None
        attr = (
            log_attribute(alert.names, table, str(item.get("name", "")))
            if isinstance(item, dict)
            else None
        )
        op = str(item.get("operator", "Include")).lower() if isinstance(item, dict) else ""
        if attr is None or op not in ("include", "exclude") or not isinstance(values, list):
            raise Fail(f"{R.M_KQL}: a rule dimension cannot be written as an attribute")
        facets.append(attr[0])
        if "*" not in values or op == "exclude":
            found.append((attr[0], [str(v) for v in values], op == "exclude"))
    return found, facets


def _operator_and_value(cond: dict[str, Any]) -> tuple[Operator, float]:
    operator = OPERATORS.get(str(cond.get("operator", "")).lower())
    if operator is None:
        raise Fail(f"{R.M_OPERATOR}: {R.shown(cond.get('operator'), 30)}")
    value = cond.get("threshold")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise Fail(R.M_THRESHOLD)
    return operator, float(value)


def _check_scopes(scopes: object) -> None:
    if not isinstance(scopes, list):
        return
    for scope in scopes:
        if isinstance(scope, str) and any(t in scope.lower() for t in BAD_SCOPE_TYPES):
            raise Fail(f"{R.M_LOG_SCOPE}: Application Insights or Azure Data Explorer")


def _log_timing(
    props: dict[str, Any], cond: dict[str, Any], simple: bool
) -> tuple[int, int, EmitOptions, list[str]]:
    """(aggregation window, breach duration, emit options, notes) of a log alert."""
    if simple:  # a SimpleLogAlert has no window: every matching record counts, over a minute
        return 60, 60, EmitOptions(), []
    timed = timing(props, logs=True)
    override = iso_seconds(props.get("overrideQueryTimeRange"))
    if props.get("overrideQueryTimeRange") and override != timed.window:
        raise Fail(R.M_LOG_RANGE)
    raw_failing = cond.get("failingPeriods")
    failing: dict[str, Any] = raw_failing if isinstance(raw_failing, dict) else {}
    n = _positive_int(failing.get("numberOfEvaluationPeriods"), 1)
    m = _positive_int(failing.get("minFailingPeriodsToAlert"), 1)
    if m > n:
        raise Fail(f"{R.M_LOG_RANGE}: more failing periods than evaluation periods")
    notes = list(timed.notes)
    if m < n:
        notes.append(R.N_FAILING.format(m=m, n=n))
    return timed.window, timed.window * m, timed.options, notes


def _parse_query(text: str) -> Query:
    try:
        return parse(text)
    except KqlUnsupported as exc:
        raise Fail(f"{R.M_KQL}: {exc}") from None
    except KqlSyntax as exc:
        raise Fail(f"{R.M_KQL}: {exc}") from None


def _signal(
    low: Lowered,
    filters: list[dict[str, Any]],
    opts: EmitOptions,
    operator: Operator,
    value: float,
    window: int,
    window_seconds: int,
    group_by: list[str],
    name: str,
) -> tuple[Signal, EmitOptions, list[str]]:
    """The IR signal for a lowered query; a plain count keeps the filters as a CountSignal."""
    notes: list[str] = []
    if low.count:
        signal = Signal(
            kind=SignalKind.LOG,
            raw_query=name,
            window_seconds=window_seconds,
            aggregation_window_seconds=window,
            occurrences=Occurrences.ALL,
            count=CountSignal(filters=[CountFilter(**f) for f in filters], group_by=group_by),
        )
        scope = [FORWARDED[0]] if low.info.azure_filter else []
        inner = [f for f in filters if f["key"] not in scope]
        if is_empty_sensitive(operator, value) and inner:
            opts = opts.model_copy(update={"null_safe_count": True, "null_safe_scope": scope})
            notes.append(R.N_LOG_NULL)
        elif is_empty_sensitive(operator, value):
            notes.append(R.N_LOG_NULL_OPEN)
        return signal, opts, notes
    if low.select is None and low.measured is None:
        raise Fail(R.M_LOG_MEASURE)
    target = NrTarget(event="Log", select=low.select, attribute=low.measured)
    label = low.measured or low.select or ""
    if low.needs_null_safe:
        notes.append(R.N_LOG_NULL)
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=name,
        window_seconds=window_seconds,
        aggregation_window_seconds=window,
        occurrences=Occurrences.ALL,
        metric=MetricSignal(
            terms={
                "a": MetricTerm(metric=label, space_aggregation=low.aggregation, nr_target=target)
            },
            aggregation=low.aggregation,
            scope=[TagFilter(**f) for f in filters],
            group_by=group_by,
        ),
    )
    return signal, opts, notes


def _log_condition(
    alert: AzureAlert, cond: dict[str, Any], level: Level, simple: bool
) -> tuple[IRMonitor, list[str]]:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "log alert", alert.name)
    if cond.get("criterionType") == "DynamicThresholdCriterion":
        raise Fail(R.M_DYNAMIC)
    query = _parse_query(str(cond.get("query", "")))
    _check_scopes(props.get("scopes"))
    operator, value = (
        _operator_and_value(cond)
        if not simple
        else (
            Operator.ABOVE_OR_EQUALS,
            float(_positive_int(cond.get("minRecurrenceCount"), 1)),
        )
    )
    window, window_seconds, opts, notes = _log_timing(props, cond, simple)
    dim_filters, rule_facets = _dimensions(cond.get("dimensions"), alert, query.table)
    low = lower(query, cond, alert, rule_facets, window)
    if low.threshold is not None:
        operator, value = low.threshold
    notes += low.notes
    filters: list[dict[str, Any]] = (
        [{"key": FORWARDED[0], "value": FORWARDED[1]}] if low.info.azure_filter else []
    )
    filters += low.filters
    filters += [{"key": a, "value": "", "values": v, "negate": neg} for a, v, neg in dim_filters]
    if low.info.azure_filter:
        notes += [R.N_LOG_FORWARD, R.N_LOG_TABLE.format(table=R.shown(query.table, 40))]
    if low.attributes and (low.derived or low.measured or low.facets or low.select):
        notes.append(R.N_LOG_ATTRIBUTES.format(names=", ".join(low.attributes)))
    if any(f.get("wildcard") for f in low.filters):
        notes.append(R.N_FILTER_CASE)
    thresholds = [Threshold(severity=level.severity, operator=operator, value=value)]
    signal, opts, more = _signal(
        low, filters, opts, operator, value, window, window_seconds, low.facets, alert.name
    )
    monitor = IRMonitor(
        **base,
        signal=signal,
        thresholds=thresholds,
        confidence=Confidence.APPROXIMATE,
        reasons=[],
        emit=opts if opts != EmitOptions() else None,
        data_path=low.info.path,
    )
    build_nrql(monitor)  # a name the emitter would refuse becomes a manual reason here
    return monitor, [*notes, *more]


def translate_scheduled_query_rule(alert: AzureAlert) -> IRMonitor:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "log alert", alert.name)
    level = severity_level(props.get("severity"))
    extra = {"description_note": f"Azure severity {level.label}", "azure_severity": level.label}
    notes = common_notes(props, level)
    kind = alert.kind or "LogAlert"
    if kind == "LogToMetric":
        return manual(base, [R.M_LOG_TO_METRIC], unmapped=extra, confidence=Confidence.UNSUPPORTED)
    if kind not in ("LogAlert", "SimpleLogAlert"):
        return manual(base, [f"{R.M_LOG_KIND}: {R.shown(kind, 40)}"], unmapped=extra)
    criteria = props.get("criteria")
    conditions = criteria.get("allOf") if isinstance(criteria, dict) else None
    if not isinstance(conditions, list) or not conditions:
        legacy = any(k in props for k in ("source", "action", "trigger"))
        return manual(base, [R.M_LOG_LEGACY if legacy else R.M_LOG_NO_CRITERIA], unmapped=extra)
    if len(conditions) > 1:
        return manual(base, [R.M_LOG_SEVERAL], unmapped=extra)
    cond = conditions[0]
    if not isinstance(cond, dict):
        return manual(base, [R.M_LOG_NO_CRITERIA], unmapped=extra)
    try:
        monitor, found = _log_condition(alert, cond, level, kind == "SimpleLogAlert")
    except (Fail, ValueError) as exc:
        reason = exc.reason if isinstance(exc, Fail) else f"{R.M_KQL}: {R.shown(exc, 150)}"
        return manual(base, [reason], getattr(exc, "steps", []), unmapped=extra)
    monitor.reasons = list(dict.fromkeys([*found, *notes]))
    monitor.unmapped = extra
    return monitor


def translate_smart_detector(alert: AzureAlert) -> IRMonitor:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "smart detector alert rule", alert.name)
    detector = props.get("detector")
    detector_id = detector.get("id") if isinstance(detector, dict) else None
    reason = f"{R.M_SMART}: detector {R.shown(detector_id, 60)}"
    return manual(
        base,
        [reason],
        unmapped={"azure_severity": R.shown(props.get("severity"), 10)},
        confidence=Confidence.UNSUPPORTED,
    )
