"""Metric alert rules (Microsoft.Insights/metricAlerts) to IR monitors.

What converts: one static-threshold criterion on a platform metric, for one resource, several
resources or a resource group or subscription (one signal per resource through FACET). What does
not: several criteria (Azure needs all true; New Relic has no composite condition), dynamic
thresholds, availability tests, custom and guest metrics, Application Insights metrics. Those
are manual with the reason, and the several-criteria case lists each criterion converted alone.
See docs/research/azure-monitor-alerting.md sections 4.2 to 4.4.
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from importer.core.emit.nrql import NRQL_FUNCTIONS, build_nrql, identifier
from importer.core.ir import (
    Aggregation,
    BaselineSpec,
    Confidence,
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
from importer.sources.azure.models import AzureAlert, NameMap
from importer.sources.azure.names import (
    PLATFORM_TYPE,
    dimension_attribute,
    metric_name,
    type_of_resource_id,
)
from importer.sources.azure.promql import convert_metric_promql

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
    "count": Aggregation.SUM,  # written with a count() select below
}
SINGLE_TYPES = (
    "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
    "Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria",
)
WEBTEST_TYPE = "Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria"
PROMQL_TYPE = "Microsoft.Azure.Monitor.PromQLCriteria"
RESOURCE_GROUP = re.compile(r"/?subscriptions/[^/]+/resourcegroups/[^/]+/?", re.IGNORECASE)
SUBSCRIPTION = re.compile(r"/?subscriptions/[^/]+/?", re.IGNORECASE)
MAX_SCOPES = 50
APP_INSIGHTS = "microsoft.insights/components"
COLLECTOR = TagFilter(key="collector.name", value="azure-monitor")


@dataclass
class Scope:
    filters: list[TagFilter]
    facet: bool
    resource_type: str | None  # when every scope is one resource of one type
    notes: list[str]


def _scope(scopes: object, multiple: bool) -> Scope:
    if not isinstance(scopes, list) or not scopes or len(scopes) > MAX_SCOPES:
        raise Fail(R.M_SCOPE)
    ids = [s for s in scopes if isinstance(s, str) and s and not set(s) & set("*%'\"\\\n\r")]
    if len(ids) != len(scopes):
        raise Fail(R.M_SCOPE)
    types = {type_of_resource_id(s) for s in ids}
    if any(t and t.lower() == APP_INSIGHTS for t in types):
        raise Fail(R.M_APP_INSIGHTS)
    if None not in types:  # resources
        one = len({t.lower() for t in types if t}) == 1
        if len(ids) == 1:
            flt = TagFilter(key="azure.resourceId", value=ids[0])
        else:
            flt = TagFilter(key="azure.resourceId", value="", values=sorted(ids))
        kind = next(iter(types)) if one else None
        return Scope([flt], multiple or len(ids) > 1, kind, [R.N_RESOURCE_ID])
    if len(ids) == 1 and (RESOURCE_GROUP.fullmatch(ids[0]) or SUBSCRIPTION.fullmatch(ids[0])):
        prefix = "/" + ids[0].strip("/") + "/"
        flt = TagFilter(key="azure.resourceId", value=prefix + "*", wildcard=True)
        return Scope([flt], True, None, [R.N_RESOURCE_ID])
    if len(ids) > 1 and all(RESOURCE_GROUP.fullmatch(i) or SUBSCRIPTION.fullmatch(i) for i in ids):
        raise Fail(
            R.M_SCOPE_SEVERAL, [f"Create one condition for each prefix: {p}" for p in ids[:10]]
        )
    raise Fail(R.M_SCOPE)


def _dimensions(
    raw: object, names: NameMap, prefix: str, metric: str = ""
) -> tuple[list[TagFilter], list[str], list[str]]:
    filters: list[TagFilter] = []
    facets: list[str] = []
    notes: list[str] = []
    if not isinstance(raw, list):
        return filters, facets, notes
    for item in raw:
        if not isinstance(item, dict):
            raise Fail(R.M_DIMENSION)
        operator = str(item.get("operator", "Include")).lower()
        values = item.get("values")
        label = R.shown(item.get("name", ""), 40)
        attr = dimension_attribute(names, prefix, str(item.get("name", "")), metric)
        ok = isinstance(values, list) and values and all(isinstance(v, str) for v in values)
        if attr is None or operator not in ("include", "exclude", "startswith") or not ok:
            raise Fail(f"{R.M_DIMENSION}: {label}")
        if not isinstance(values, list):
            raise Fail(f"{R.M_DIMENSION}: {label}")
        facets.append(attr)
        notes.append(R.N_DIM_SPLIT.format(name=label))
        if operator == "startswith":
            # a prefix is a LIKE; several prefixes would need OR, which one filter cannot hold
            if len(values) != 1 or set(values[0]) & set("*%"):
                raise Fail(f"{R.M_DIMENSION}: {label} (StartsWith needs one plain prefix)")
            filters.append(TagFilter(key=attr, value=values[0] + "*", wildcard=True))
            continue
        if "*" in values and operator == "include":
            continue
        filters.append(TagFilter(key=attr, value="", values=values, negate=operator == "exclude"))
    return filters, facets, notes


def _threshold(crit: dict[str, Any], level: Level) -> Threshold:
    operator = OPERATORS.get(str(crit.get("operator", "")).lower())
    if operator is None:
        raise Fail(f"{R.M_OPERATOR}: {R.shown(crit.get('operator'), 30)}")
    value = crit.get("threshold")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise Fail(R.M_THRESHOLD)
    return Threshold(severity=level.severity, operator=operator, value=float(value))


def _namespace(props: dict[str, Any], crit: dict[str, Any], scope: Scope) -> str:
    for candidate in (crit.get("metricNamespace"), props.get("targetResourceType")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return scope.resource_type or ""


def _metric_signal(
    alert: AzureAlert, crit: dict[str, Any], scope: Scope
) -> tuple[MetricSignal, list[str]]:
    props = alert.properties
    metric = crit.get("metricName")
    if not isinstance(metric, str) or not metric.strip():
        raise Fail(R.M_NO_NAME)
    namespace = _namespace(props, crit, scope)
    found = metric_name(alert.names, namespace, metric)
    if found is None:
        if namespace and not PLATFORM_TYPE.fullmatch(namespace.lower()):
            raise Fail(f"{R.M_CUSTOM_NAMESPACE}: {R.shown(namespace, 60)}")
        raise Fail(f"{R.M_NO_NAME}: {R.shown(metric, 60)}")
    agg_name = str(crit.get("timeAggregation", "")).lower()
    if agg_name not in AGGREGATIONS:
        raise Fail(f"{R.M_TIME_AGG}: {R.shown(crit.get('timeAggregation'), 30)}")
    try:
        select = f"count({identifier(found.attribute)})" if agg_name == "count" else None
        if select is None:
            identifier(found.attribute)
    except ValueError:
        raise Fail(f"{R.M_NO_NAME}: {R.shown(metric, 60)}") from None
    filters, facets, notes = _dimensions(crit.get("dimensions"), alert.names, found.prefix, metric)
    note = {"map": R.N_NAME_MAPPED, "table": R.N_NAME_TABLE}.get(found.basis, R.N_NAME_DERIVED)
    notes.insert(0, note.format(name=found.attribute))
    if facets:
        notes.append(R.N_ATTR_DERIVED.format(names=", ".join(facets)))
    if not facets:
        notes.append(R.N_DIM_DOUBLE)
    if agg_name == "count":
        notes.append(R.N_COUNT)
    elif agg_name == "total":
        notes.append(R.N_TOTAL)
    region = props.get("targetResourceRegion")
    if isinstance(region, str) and region:
        notes.append(R.N_REGION.format(region=R.shown(region, 40)))
    target = NrTarget(event="Metric", attribute=found.attribute, select=select)
    signal = MetricSignal(
        terms={
            "a": MetricTerm(
                metric=metric, space_aggregation=AGGREGATIONS[agg_name], nr_target=target
            )
        },
        aggregation=AGGREGATIONS[agg_name],
        scope=[COLLECTOR, *scope.filters, *filters],
        group_by=[*(["azure.resourceId"] if scope.facet else []), *facets],
    )
    return signal, notes


def _one(
    alert: AzureAlert, crit: object, multiple: bool, level: Level
) -> tuple[Signal, Threshold, list[str]]:
    """Convert one criterion on its own; raises Fail."""
    if not isinstance(crit, dict):
        raise Fail(R.M_NO_CRITERIA)
    kind = crit.get("criterionType")
    if kind == "DynamicThresholdCriterion":
        return _dynamic(alert, crit, multiple, level)
    if kind not in ("StaticThresholdCriterion", None) or ("threshold" not in crit and kind is None):
        raise Fail(f"{R.M_CRITERIA_TYPE}: {R.shown(kind, 40)}")
    props = alert.properties
    scope = _scope(props.get("scopes"), multiple)
    threshold = _threshold(crit, level)
    timed = timing(props)
    metric, notes = _metric_signal(alert, crit, scope)
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=alert.name,
        window_seconds=timed.window,
        aggregation_window_seconds=timed.window,
        occurrences=Occurrences.ALL,
        metric=metric,
    )
    return signal, threshold, [*notes, *scope.notes, *timed.notes]


OPERATOR_TEXT = {
    Operator.ABOVE: ">",
    Operator.ABOVE_OR_EQUALS: ">=",
    Operator.BELOW: "<",
    Operator.BELOW_OR_EQUALS: "<=",
    Operator.EQUALS: "=",
}
# Azure sensitivity to New Relic standard deviations. No document maps one to the other: Azure says
# Medium or Low reduce noise, New Relic says 2 is sensitive and 3 conservative (about 99.7 percent
# of normal variation). These values are a choice of this importer, not a fact.
SENSITIVITY = {"high": 2.0, "medium": 3.0, "low": 4.0}
DYNAMIC_DIRECTION: dict[str, Literal["upper_only", "lower_only", "upper_and_lower"]] = {
    "greaterthan": "upper_only",
    "lessthan": "lower_only",
    "greaterorlessthan": "upper_and_lower",
}


def _number(value: float) -> str:
    """A plain decimal for NRQL (no exponent)."""
    text = format(Decimal(repr(value)), "f")
    return text[:-2] if text.endswith(".0") else text


def _dynamic(
    alert: AzureAlert, crit: dict[str, Any], multiple: bool, level: Level
) -> tuple[Signal, Threshold, list[str]]:
    """A dynamic threshold as a baseline condition: an approximation, never an equivalent."""
    sensitivity = str(crit.get("alertSensitivity", "")).lower()
    direction = DYNAMIC_DIRECTION.get(str(crit.get("operator", "")).lower())
    if sensitivity not in SENSITIVITY or direction is None:
        raise Fail(f"{R.M_DYNAMIC}: sensitivity or operator is not one Azure documents")
    scope = _scope(alert.properties.get("scopes"), multiple)
    timed = timing(alert.properties)
    metric, notes = _metric_signal(alert, crit, scope)
    periods = crit.get("failingPeriods")
    need, out_of = 1, 1
    if isinstance(periods, dict):
        need = _small(periods.get("minFailingPeriodsToAlert"))
        out_of = _small(periods.get("numberOfEvaluationPeriods"))
    if need > out_of:
        raise Fail(f"{R.M_DYNAMIC}: failingPeriods is not readable")
    deviations = SENSITIVITY[sensitivity]
    notes.append(
        R.N_DYNAMIC.format(
            sensitivity=R.shown(crit.get("alertSensitivity"), 10),
            deviations=f"{deviations:g}",
            direction=direction.replace("_", " "),
        )
    )
    if need != out_of or out_of > 1:
        notes.append(R.N_FAILING.format(m=need, n=out_of))
    if crit.get("ignoreDataBefore"):
        notes.append(R.N_IGNORE_BEFORE.format(value=R.shown(crit["ignoreDataBefore"], 40)))
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=alert.name,
        window_seconds=timed.window * need,
        aggregation_window_seconds=timed.window,
        occurrences=Occurrences.ALL,
        metric=metric,
        baseline=BaselineSpec(direction=direction, deviations=deviations),
    )
    threshold = Threshold(severity=level.severity, operator=Operator.ABOVE, value=deviations)
    return signal, threshold, [*notes, *scope.notes, *timed.notes]


def _small(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 24:
        return value
    raise Fail(f"{R.M_DYNAMIC}: failingPeriods is not readable")


def _several(
    alert: AzureAlert, crits: list[Any], multiple: bool, level: Level
) -> tuple[Signal, Threshold, list[str]]:
    """Every criterion true: one query whose tests are joined with AND inside if(..., 1, 0).

    Needs the same scope, dimensions and window (the window belongs to the rule); the cookbook
    method is 'AND of two tests with if()' (VERIFIED-VALID).
    """
    parts = [_one(alert, crit, multiple, level) for crit in crits]
    first = parts[0][0].metric
    tests: list[str] = []
    notes: list[str] = []
    for signal, threshold, found in parts:
        sig = signal.metric
        if sig is None or first is None or signal.baseline is not None:
            raise Fail("a dynamic threshold cannot be joined with another test")
        if (sig.scope, sig.group_by) != (first.scope, first.group_by):
            raise Fail("the criteria do not share the same dimensions or scope")
        target = sig.terms["a"].nr_target
        if target is None or threshold.operator not in OPERATOR_TEXT:
            raise Fail(R.M_OPERATOR)
        func = NRQL_FUNCTIONS[sig.aggregation]
        body = target.select or f"{func}({identifier(target.attribute or '')})"
        tests.append(f"{body} {OPERATOR_TEXT[threshold.operator]} {_number(threshold.value)}")
        notes.extend(found)
    select = f"if({' AND '.join(tests)}, 1, 0)"
    target = NrTarget(event="Metric", select=select)
    term = MetricTerm(
        metric=" + ".join(c.get("name", "criterion") for c in crits if isinstance(c, dict)),
        space_aggregation=Aggregation.MAX,
        nr_target=target,
    )
    joined = MetricSignal(
        terms={"a": term},
        aggregation=Aggregation.MAX,
        scope=first.scope if first else [],
        group_by=first.group_by if first else [],
    )
    notes.append(R.N_COMPOSITE.format(count=len(crits)))
    base = parts[0][0]
    signal = base.model_copy(update={"metric": joined})
    return signal, Threshold(severity=level.severity, operator=Operator.ABOVE, value=0.0), notes


def _alone(alert: AzureAlert, crits: list[Any], multiple: bool, level: Level) -> list[str]:
    """Each criterion converted by itself, as manual steps."""
    steps: list[str] = []
    for index, crit in enumerate(crits[:10], start=1):
        label = R.shown(
            crit.get("name", f"criterion {index}") if isinstance(crit, dict) else "", 40
        )
        try:
            signal, threshold, _ = _one(alert, crit, multiple, level)
            probe = IRMonitor(
                **base_fields(alert, "probe", alert.type, alert.name),
                signal=signal,
                thresholds=[threshold],
                confidence=Confidence.APPROXIMATE,
            )
            steps.append(
                f"Criterion {label!r} alone: {build_nrql(probe)} with the threshold "
                f"{threshold.operator.value.replace('_', ' ')} {threshold.value:g} for "
                f"{signal.window_seconds}s. Create it as a separate condition only if one alone "
                "is worth alerting on; together they were an AND."
            )
        except (Fail, ValueError) as exc:
            steps.append(f"Criterion {label!r} could not be converted alone: {R.shown(exc, 150)}")
    return steps


def translate_metric_alert(alert: AzureAlert) -> IRMonitor:
    props = alert.properties
    key = key_of(alert)
    base = base_fields(alert, key, "metric alert", alert.name)
    level = severity_level(props.get("severity"))
    criteria = props.get("criteria")
    if not isinstance(criteria, dict):
        return manual(base, [R.M_NO_CRITERIA])
    odata = criteria.get("odata.type")
    notes = common_notes(props, level)
    extra = {"description_note": f"Azure severity {level.label}", "azure_severity": level.label}
    if odata == WEBTEST_TYPE:
        return manual(base, [R.M_WEBTEST], unmapped=extra)
    if odata == PROMQL_TYPE:
        return convert_metric_promql(alert, base, level, notes, extra)
    if odata not in SINGLE_TYPES:
        return manual(base, [f"{R.M_CRITERIA_TYPE}: {R.shown(odata, 60)}"], unmapped=extra)
    crits = criteria.get("allOf")
    if not isinstance(crits, list) or not crits:
        return manual(base, [R.M_NO_CRITERIA], unmapped=extra)
    multiple = odata == SINGLE_TYPES[1]
    try:
        if len(crits) > 1:
            signal, threshold, found = _several(alert, crits, multiple, level)
        else:
            signal, threshold, found = _one(alert, crits[0], multiple, level)
    except Fail as fail:
        if len(crits) > 1:
            steps = fail.steps or _alone(alert, crits, multiple, level)
            return manual(base, [f"{R.M_SEVERAL}: {fail.reason}"], steps, unmapped=extra)
        return manual(base, [fail.reason], fail.steps, unmapped=extra)
    return IRMonitor(
        **base,
        signal=signal,
        thresholds=[threshold],
        confidence=Confidence.APPROXIMATE,
        reasons=list(dict.fromkeys([*found, *notes])),
        unmapped=extra,
        emit=timing(props).options,
        data_path="native",
    )
