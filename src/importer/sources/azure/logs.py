"""Log based Azure alerts: scheduled query rules (KQL), activity log alerts, smart detectors.

Scheduled query rules convert only the KQL patterns in `kql.py`: a table, `where` terms on
columns, and a count or one aggregate of a column. The table has no counterpart in New Relic, so
the condition counts or aggregates forwarded Azure logs (`plugin.type = 'azure'`) and the column
names are used as attribute names, both UNVERIFIED (docs/research/azure-monitor-alerting.md 4.5).
Activity log, service health and smart detector rules are manual (sections 4.6 and 4.8).
"""

import math
import re
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
from importer.sources.azure.kql import KqlError, Query, Term, parse
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
KQL_FUNCTIONS = {
    "avg": Aggregation.AVG,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
    "sum": Aggregation.SUM,
}
# tables filled by an agent or service whose data New Relic does not receive from Azure
AGENT_TABLES = re.compile(
    r"(Perf|InsightsMetrics|Heartbeat|Event|Syslog|Update.*|ConfigurationData|Container.*|Kube.*"
    r"|Alert|Usage|Operation|App[A-Z].*|AzureActivity|SigninLogs|AuditLogs|SecurityEvent"
    r"|SecurityAlert|SecurityIncident|ADX.*|Windows.*)",
)
BAD_SCOPE_TYPES = ("microsoft.insights/components", "microsoft.kusto/clusters")
MEASURE_NAMES = {"", "count_", "aggregatedvalue"}
FORWARDED = ("plugin.type", "azure")  # the attribute New Relic's forwarder documents
MAX_PERIODS = 24
SERVICE_HEALTH_WORDS = ("servicehealth", "resourcehealth")


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_PERIODS:
        return value
    if value is None:
        return default
    raise Fail(f"{R.M_LOG_RANGE}: failingPeriods is not a small whole number")


def _filter_args(term: Term, attribute: str) -> dict[str, Any]:
    if term.op == "in":
        return {"key": attribute, "value": "", "values": list(term.values), "negate": term.negate}
    text = term.values[0]
    if term.op == "eq":
        return {"key": attribute, "value": text, "negate": term.negate}
    pattern = {
        "contains": f"*{text}*",
        "has": f"*{text}*",
        "startswith": f"{text}*",
        "endswith": f"*{text}",
    }[term.op]
    return {"key": attribute, "value": pattern, "wildcard": True, "negate": term.negate}


def _columns(query: Query, alert: AzureAlert) -> tuple[dict[str, str], bool]:
    """(column -> attribute, any name came from the map's absence)."""
    found: dict[str, str] = {}
    derived = False
    for column in [t.column for t in query.terms]:
        attr = log_attribute(alert.names, query.table, column)
        if attr is None:
            raise Fail(f"{R.M_KQL}: column {R.shown(column, 40)} is not a plain name")
        found[column] = attr[0]
        derived = derived or not attr[1]
    return found, derived


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


def _measure(
    query: Query, cond: dict[str, Any], alert: AzureAlert
) -> tuple[str, str | None, Aggregation | None]:
    """('count' or 'column', attribute, aggregation) for what the rule measures."""
    column = cond.get("metricMeasureColumn")
    time_agg = str(cond.get("timeAggregation", "")).lower()
    summary = query.summarize
    if summary is None and not column and time_agg in ("count", ""):
        return "count", None, None
    if summary is not None and summary.function == "count":
        if time_agg not in AGGREGATIONS or str(column or "").lower() not in MEASURE_NAMES:
            raise Fail(f"{R.M_LOG_MEASURE}: count() with time aggregation {time_agg or 'none'}")
        return "count", None, None
    measured = summary.column if summary is not None else column
    if not isinstance(measured, str) or time_agg not in AGGREGATIONS:
        raise Fail(f"{R.M_LOG_MEASURE}: no column or time aggregation New Relic can take")
    if summary is not None and summary.function in KQL_FUNCTIONS:
        agg = KQL_FUNCTIONS[summary.function]
    else:
        agg = AGGREGATIONS[time_agg]
    attr = log_attribute(alert.names, query.table, measured)
    if attr is None:
        raise Fail(f"{R.M_LOG_MEASURE}: column {R.shown(measured, 40)} is not a plain name")
    return "column", attr[0], agg


def _log_timing(
    props: dict[str, Any], cond: dict[str, Any], query: Query, simple: bool
) -> tuple[int, int, EmitOptions, list[str]]:
    """(aggregation window, breach duration, emit options, notes) of a log alert."""
    if simple:  # a SimpleLogAlert has no window: every matching record counts, over a minute
        return 60, 60, EmitOptions(), []
    timed = timing(props, logs=True)
    override = iso_seconds(props.get("overrideQueryTimeRange"))
    if props.get("overrideQueryTimeRange") and override != timed.window:
        raise Fail(R.M_LOG_RANGE)
    summary = query.summarize
    if summary is not None and summary.bin_seconds not in (None, timed.window):
        raise Fail(f"{R.M_KQL}: the summarize bin size differs from the window")
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


def _log_condition(
    alert: AzureAlert, cond: dict[str, Any], level: Level, simple: bool
) -> tuple[IRMonitor, list[str]]:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "log alert", alert.name)
    if cond.get("criterionType") == "DynamicThresholdCriterion":
        raise Fail(R.M_DYNAMIC)
    text = str(cond.get("query", ""))
    head = re.match(r"\s*([A-Za-z_]\w*)\s*(?:\||$)", text)
    if head is not None and AGENT_TABLES.fullmatch(head[1]):  # said before any parse problem
        raise Fail(f"{R.M_KQL_TABLE}: {R.shown(head[1], 40)}")
    try:
        query = parse(text)
    except KqlError as exc:
        raise Fail(f"{R.M_KQL}: {exc}") from None
    _check_scopes(props.get("scopes"))
    operator, value = (
        _operator_and_value(cond)
        if not simple
        else (
            Operator.ABOVE_OR_EQUALS,
            float(_positive_int(cond.get("minRecurrenceCount"), 1)),
        )
    )
    window, window_seconds, opts, notes = _log_timing(props, cond, query, simple)
    attrs, derived = _columns(query, alert)
    dim_filters, facets = _dimensions(cond.get("dimensions"), alert, query.table)
    for column in query.summarize.by if query.summarize else ():
        mapped = log_attribute(alert.names, query.table, column)
        if mapped is None or mapped[0] not in facets:
            raise Fail(
                f"{R.M_KQL}: the summarize groups by {R.shown(column, 40)}, "
                "which is not a rule dimension"
            )
    group_by = facets
    kind, measured, aggregation = _measure(query, cond, alert)
    filters: list[dict[str, Any]] = [{"key": FORWARDED[0], "value": FORWARDED[1]}]
    filters += [_filter_args(t, attrs[t.column]) for t in query.terms]
    filters += [{"key": a, "value": "", "values": v, "negate": neg} for a, v, neg in dim_filters]
    names = sorted({*attrs.values(), *facets, *([measured] if measured else [])})
    notes += [R.N_LOG_FORWARD, R.N_LOG_TABLE.format(table=R.shown(query.table, 40))]
    if names and (derived or measured or facets):
        notes.append(R.N_LOG_ATTRIBUTES.format(names=", ".join(names)))
    if any(t.op in ("contains", "has") for t in query.terms):
        notes.append(R.N_FILTER_CASE)
    thresholds = [Threshold(severity=level.severity, operator=operator, value=value)]
    if kind == "count":
        signal = Signal(
            kind=SignalKind.LOG,
            raw_query=alert.name,
            window_seconds=window_seconds,
            aggregation_window_seconds=window,
            occurrences=Occurrences.ALL,
            count=CountSignal(filters=[CountFilter(**f) for f in filters], group_by=group_by),
        )
        empty_sensitive = operator in (
            Operator.BELOW,
            Operator.BELOW_OR_EQUALS,
            Operator.EQUALS,
        ) or (operator is Operator.ABOVE_OR_EQUALS and value <= 0)
        if empty_sensitive and len(filters) > 1:
            opts = opts.model_copy(
                update={"null_safe_count": True, "null_safe_scope": [FORWARDED[0]]}
            )
            notes.append(R.N_LOG_NULL)
        elif empty_sensitive:
            notes.append(R.N_LOG_NULL_OPEN)
    else:
        if measured is None or aggregation is None:
            raise Fail(R.M_LOG_MEASURE)
        target = NrTarget(event="Log", attribute=measured)
        signal = Signal(
            kind=SignalKind.METRIC,
            raw_query=alert.name,
            window_seconds=window_seconds,
            aggregation_window_seconds=window,
            occurrences=Occurrences.ALL,
            metric=MetricSignal(
                terms={
                    "a": MetricTerm(
                        metric=measured, space_aggregation=aggregation, nr_target=target
                    )
                },
                aggregation=aggregation,
                scope=[TagFilter(**f) for f in filters],
                group_by=group_by,
            ),
        )
    monitor = IRMonitor(
        **base,
        signal=signal,
        thresholds=thresholds,
        confidence=Confidence.APPROXIMATE,
        reasons=[],
        emit=opts if opts != EmitOptions() else None,
    )
    build_nrql(monitor)  # a name the emitter would refuse becomes a manual reason here
    return monitor, notes


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


def _leaf_text(leaf: dict[str, Any]) -> str:
    wanted = leaf.get("equals", leaf.get("containsAny", ""))
    return f"{R.shown(leaf.get('field'), 40)} = {R.shown(wanted, 80)}"


def _condition_lines(condition: object) -> list[str]:
    lines: list[str] = []
    leaves = condition.get("allOf") if isinstance(condition, dict) else None
    for leaf in leaves if isinstance(leaves, list) else []:
        if not isinstance(leaf, dict):
            continue
        any_of = leaf.get("anyOf")
        parts: list[Any] = any_of if isinstance(any_of, list) else [leaf]
        text = " or ".join(_leaf_text(p) for p in parts if isinstance(p, dict))
        if text:
            lines.append(text)
    return lines


def translate_activity_log_alert(alert: AzureAlert) -> IRMonitor:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "activity log alert", alert.name)
    lines = _condition_lines(props.get("condition"))
    health = any(w in " ".join(lines).lower().replace(" ", "") for w in SERVICE_HEALTH_WORDS)
    reason = R.M_SERVICE_HEALTH if health else R.M_ACTIVITY
    steps = [f"The Azure rule matches events where {' and '.join(lines)}."] if lines else []
    extra: dict[str, Any] = {}
    if props.get("tenantScope"):
        reason = f"{reason}; the rule is tenant or management group scoped"
    return manual(base, [reason], steps, unmapped=extra)


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
