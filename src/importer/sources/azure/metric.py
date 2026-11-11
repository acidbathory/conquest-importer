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
from typing import Any

from importer.core.emit.nrql import build_nrql, identifier
from importer.core.ir import (
    Aggregation,
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
        if attr is None or operator not in ("include", "exclude") or not ok:
            raise Fail(f"{R.M_DIMENSION}: {label}")
        if not isinstance(values, list):
            raise Fail(f"{R.M_DIMENSION}: {label}")
        facets.append(attr)
        notes.append(R.N_DIM_SPLIT.format(name=label))
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
        raise Fail(R.M_DYNAMIC)
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
    if len(crits) > 1:
        return manual(base, [R.M_SEVERAL], _alone(alert, crits, multiple, level), unmapped=extra)
    try:
        signal, threshold, found = _one(alert, crits[0], multiple, level)
    except Fail as fail:
        return manual(base, [fail.reason], fail.steps, unmapped=extra)
    return IRMonitor(
        **base,
        signal=signal,
        thresholds=[threshold],
        confidence=Confidence.APPROXIMATE,
        reasons=list(dict.fromkeys([*found, *notes])),
        unmapped=extra,
        emit=timing(props).options,
    )
