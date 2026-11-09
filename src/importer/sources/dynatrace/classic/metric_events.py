"""Metric events (`builtin:anomaly-detection.metric-events`) to New Relic conditions.

A static threshold becomes a static NRQL condition and the two baseline models become a baseline
condition. The metric key or selector goes through the metric map, so a metric with no New Relic
signal stays manual and says what to install. Windows follow `windows.py`.
"""

from typing import Literal

from importer.core.ir import (
    BaselineSpec,
    Confidence,
    IRMonitor,
    Occurrences,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.dynatrace.classic.common import Fail, finite, monitor, shown
from importer.sources.dynatrace.classic.models import (
    DimensionFilter,
    EntityCondition,
    EventTemplate,
    MetricEventValue,
    ModelProperties,
    QueryDefinition,
    SettingsObject,
)
from importer.sources.dynatrace.classic.selector import SelectorError
from importer.sources.dynatrace.classic.selector_query import (
    Aggregate,
    DimFilter,
    QExpr,
    SeriesSpec,
    to_query,
)
from importer.sources.dynatrace.classic.signals import build_signal
from importer.sources.dynatrace.classic.windows import SampleWindow, sample_window

SOURCE_TYPE = "metric event"
STATIC = "STATIC_THRESHOLD"
BASELINES = {"AUTO_ADAPTIVE_THRESHOLD", "SEASONAL_BASELINE"}
OPERATORS = {"ABOVE": Operator.ABOVE, "BELOW": Operator.BELOW}
DIRECTIONS: dict[str, Literal["upper_only", "lower_only", "upper_and_lower"]] = {
    "ABOVE": "upper_only",
    "BELOW": "lower_only",
    "OUTSIDE": "upper_and_lower",
}
BASELINE_DEVIATIONS = 3.0  # chosen: neither Dynatrace sensitivity maps to a standard deviation
KEY_AGGREGATES = {
    "AVG": Aggregate("average"),
    "MIN": Aggregate("min"),
    "MAX": Aggregate("max"),
    "SUM": Aggregate("sum"),
    "COUNT": Aggregate("count"),
    "MEDIAN": Aggregate("percentile", 50.0),
    "PERCENTILE90": Aggregate("percentile", 90.0),
    "VALUE": Aggregate("latest"),
}
# operator -> (filter kind, negated, ignores case)
FILTER_OPERATORS = {
    "EQUALS": ("eq", False, False),
    "DOES_NOT_EQUAL": ("eq", True, False),
    "STARTS_WITH": ("prefix", False, False),
    "DOES_NOT_START_WITH": ("prefix", True, False),
    "CONTAINS_CASE_SENSITIVE": ("contains", False, False),
    "DOES_NOT_CONTAIN_CASE_SENSITIVE": ("contains", True, False),
    "CONTAINS_CASE_INSENSITIVE": ("contains", False, True),
    "DOES_NOT_CONTAIN_CASE_INSENSITIVE": ("contains", True, True),
}
ENTITY_NOTE = (
    "entity filter conditions are read as all of them must match (how Dynatrace combines "
    "several conditions is not documented), and a name test uses the name attribute from the "
    "metric map"
)
CASE_NOTE = "a case-insensitive Dynatrace test becomes a LIKE filter, which may be case sensitive"
BASELINE_NOTES = (
    "the Dynatrace model (a 99th percentile plus interquartile range over 7 days, or a seasonal "
    "band learned over 14 days) differs from the New Relic baseline; both learn a normal range",
    "sensitivity (signalFluctuation or tolerance) has no principled conversion; the band is "
    f"set to {BASELINE_DEVIATIONS:g} standard deviations, a chosen value to tune with the "
    "NrAiSignal histogram",
    "New Relic needs one to four weeks of its own data before a baseline is reliable, and a "
    "faceted query starts learning from scratch",
)


def _dimension_filter(item: DimensionFilter) -> DimFilter:
    kind = FILTER_OPERATORS.get(item.operator)
    if kind is None or kind[2] or not item.dimensionKey:
        raise Fail(f"dimension filter operator {shown(item.operator)} is not supported")
    return DimFilter(item.dimensionKey, kind[0], (item.dimensionValue,), kind[1])


def _entity_filter(condition: EntityCondition, dimension: str | None) -> tuple[DimFilter, bool]:
    """The test and whether Dynatrace ignores case in it."""
    key = {"NAME": dimension, "HOST_NAME": "dt.entity.host"}.get(condition.type)
    kind = FILTER_OPERATORS.get(condition.operator)
    if condition.type not in ("NAME", "HOST_NAME"):
        raise Fail(f"an entity filter on {shown(condition.type)} cannot be expressed in New Relic")
    if not key or kind is None:
        raise Fail("an entity filter has no dimension key or an operator that is not supported")
    return DimFilter(key, kind[0], (condition.value,), kind[1]), kind[2]


def _filters(qd: QueryDefinition) -> tuple[list[DimFilter], list[str]]:
    if qd.managementZone:
        raise Fail("the event is limited to a management zone, which New Relic does not offer")
    entity = qd.entityFilter
    pairs = [_entity_filter(c, entity.dimensionKey) for c in entity.conditions]
    found = [flt for flt, _ in pairs] + [_dimension_filter(i) for i in qd.dimensionFilter]
    notes = [ENTITY_NOTE] if entity.conditions else []
    return found, notes + ([CASE_NOTE] if any(ci for _, ci in pairs) else [])


def _terms(qd: QueryDefinition) -> tuple[list[SeriesSpec], QExpr | None]:
    if qd.type == "METRIC_KEY":
        aggregate = KEY_AGGREGATES.get(qd.aggregation)
        if not qd.metricKey or aggregate is None:
            raise Fail("the event has no metric key or its aggregation is not supported")
        return [SeriesSpec(qd.metricKey, aggregate=aggregate, aggregated=True)], None
    if qd.type == "METRIC_SELECTOR":
        if not qd.metricSelector:
            raise Fail("the event has no metric selector")
        try:
            query = to_query(qd.metricSelector)
        except SelectorError as exc:
            raise Fail(f"metric selector not converted: {exc}") from None
        return query.terms, query.expression
    raise Fail(f"query type {shown(qd.type)} is not supported")


def _window(mp: ModelProperties, qd: QueryDefinition) -> SampleWindow:
    return sample_window(
        mp.violatingSamples, mp.samples, mp.dealertingSamples, qd.queryOffset, mp.alertOnNoData
    )


NO_PROBLEM_TYPES = frozenset(
    {"INFO", "CUSTOM_ANNOTATION", "CUSTOM_DEPLOYMENT", "CUSTOM_CONFIGURATION"}
)
MERGE_NOTE = (
    "davisMerge is ignored: Davis merges events into problems, and New Relic correlates "
    "alert events by a different method"
)


def _event_type(template: EventTemplate) -> list[str]:
    """Notes about the event template; manual for a type that opens no problem."""
    if template.eventType in NO_PROBLEM_TYPES:
        raise Fail(
            f"the event type {template.eventType} does not open a problem in Dynatrace "
            "(it is informational), so it is not a paging condition"
        )
    return [MERGE_NOTE] if template.davisMerge is not None else []


def _model(mp: ModelProperties) -> tuple[Operator | None, BaselineSpec | None]:
    """The static operator or the baseline for the model type; manual when neither applies."""
    if mp.type in BASELINES:
        direction = DIRECTIONS.get(mp.alertCondition)
        if direction is None:
            raise Fail(f"alert condition {shown(mp.alertCondition)} is not supported")
        return None, BaselineSpec(direction=direction, deviations=BASELINE_DEVIATIONS)
    if mp.type != STATIC:
        raise Fail(f"model type {shown(mp.type)} is not supported")
    operator = OPERATORS.get(mp.alertCondition)
    if operator is None:
        raise Fail(
            f"alert condition {shown(mp.alertCondition)} needs a baseline model; a static "
            "threshold is ABOVE or BELOW"
        )
    if mp.threshold is None:
        raise Fail("a static threshold event has no threshold")
    return operator, None


def _convert(value: MetricEventValue, key: str, name: str) -> IRMonitor:
    mp, qd = value.modelProperties, value.queryDefinition
    template_notes = _event_type(value.eventTemplate)
    operator, baseline = _model(mp)
    window = _window(mp, qd)
    terms, expression = _terms(qd)
    extra, filter_notes = _filters(qd)
    built = build_signal(terms, expression, extra)
    reasons = [] if value.enabled else [DISABLED]
    reasons += window.notes
    if operator is not None:
        scaled = finite(float(mp.threshold or 0) * built.factor, "the converted threshold")
        thresholds = [Threshold(severity=Severity.CRITICAL, operator=operator, value=scaled)]
    else:
        thresholds = [
            Threshold(
                severity=Severity.CRITICAL, operator=Operator.ABOVE, value=BASELINE_DEVIATIONS
            )
        ]
        reasons += BASELINE_NOTES
        if qd.type == "METRIC_KEY":
            reasons.append(KEY_BASELINE_NOTE)
    reasons += filter_notes + template_notes + built.notes
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=name,
        window_seconds=window.window_seconds,
        aggregation_window_seconds=60,
        aggregation_delay_seconds=window.delay_seconds,
        occurrences=Occurrences.ALL,
        metric=built.signal,
        baseline=baseline,
    )
    result = monitor(
        key=key,
        name=name,
        source_type=SOURCE_TYPE,
        confidence=Confidence.APPROXIMATE,
        reasons=reasons,
        signal=signal,
    )
    result.thresholds = thresholds
    result.no_data = window.no_data
    if not value.enabled:
        result.unmapped["enabled"] = False
    return result


KEY_BASELINE_NOTE = (
    "Dynatrace allows a baseline model only for a metric selector; this event uses a metric "
    "key, so check what it was meant to do"
)
DISABLED = (
    "the Dynatrace metric event was disabled; the New Relic condition is created disabled too"
)


def translate_metric_event(obj: SettingsObject, key: str) -> IRMonitor:
    """One metric event as IR; never raises."""
    value = MetricEventValue.model_validate(obj.value)
    name = value.summary.strip() or value.eventTemplate.title.strip() or "Metric event"
    try:
        return _convert(value, key, name)
    except Fail as fail:
        return monitor(
            key=key,
            name=name,
            source_type=SOURCE_TYPE,
            confidence=Confidence.MANUAL,
            reasons=[fail.reason],
        )
