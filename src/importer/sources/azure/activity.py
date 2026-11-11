"""Activity log alert rules (Microsoft.Insights/activityLogAlerts) to log-count conditions.

Azure fires an activity log alert for every activity event that matches its condition. The
conversion counts the matching records among the forwarded Azure activity logs (`Log` events with
`plugin.type = 'azure'`, filled by the Event Hub forwarder when the matching
`forward<Category>AzureActivityLogs` flag is on) and opens when the count in a 60 second window is
at least 1. The data path is `needs-log-forwarding`.

Every attribute name is UNVERIFIED (docs/research/azure-datapath.md section 4): the rule's field
names are used as they are, except the few where the Event Hub record is known to use another name
(`status` is `resultType`, `subStatus` is `resultSignature`). A `name-map.json` entry wins.
Rules that need an OR across fields, several prefixes, or a field the record does not carry stay
manual with the condition written out.
"""

from typing import Any

from importer.core.emit.nrql import build_nrql, field_identifier
from importer.core.ir import (
    Confidence,
    CountFilter,
    CountSignal,
    EmitOptions,
    IRMonitor,
    Occurrences,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.azure import reasons as R
from importer.sources.azure.common import Fail, base_fields, key_of, manual
from importer.sources.azure.models import AzureAlert
from importer.sources.azure.names import RESOURCE_SEGMENTS, log_attribute

NEEDS_LOG_FORWARDING = "needs-log-forwarding"  # same word as the Google source's data_path
FORWARDED = ("plugin.type", "azure")
TABLE = "AzureActivity"
WINDOW = 60
# rule field (lower case) to the attribute of the forwarded record, all UNVERIFIED
EVENT_HUB_NAMES = {
    "category": "category",
    "operationname": "operationName",
    "level": "level",
    "resourceid": "resourceId",
    "status": "resultType",
    "substatus": "resultSignature",
}
# fields a rule can use that the Event Hub record has no counterpart for
NO_COUNTERPART = ("resourcegroup", "resourcetype", "resourceprovider", "caller", "anomalies")
# categories of the forwarder template (flag forward<Category>AzureActivityLogs)
CATEGORY_FLAGS = {
    "administrative": "Administrative",
    "alert": "Alert",
    "autoscale": "Autoscale",
    "policy": "Policy",
    "recommendation": "Recommendation",
    "resourcehealth": "ResourceHealth",
    "security": "Security",
    "servicehealth": "ServiceHealth",
}


def _attribute(alert: AzureAlert, field: object) -> tuple[str, bool]:
    """(attribute, written from a guess) for a rule field; raises Fail when none exists."""
    name = str(field or "").strip()
    lowered = name.lower()
    mapped = log_attribute(alert.names, TABLE, name)
    if mapped is not None and mapped[1]:
        return mapped[0], False
    if lowered in NO_COUNTERPART:
        raise Fail(f"{R.M_ACTIVITY_COND}: field {R.shown(name, 40)} has no attribute in the record")
    if lowered in EVENT_HUB_NAMES:
        return EVENT_HUB_NAMES[lowered], True
    if lowered.startswith("properties."):
        try:
            field_identifier(name)
        except ValueError:
            raise Fail(
                f"{R.M_ACTIVITY_COND}: field {R.shown(name, 40)} is not a plain name"
            ) from None
        return name, True
    raise Fail(f"{R.M_ACTIVITY_COND}: field {R.shown(name, 40)} is not one Azure documents")


def _leaf(alert: AzureAlert, leaf: dict[str, Any]) -> CountFilter:
    attr, _ = _attribute(alert, leaf.get("field"))
    if isinstance(leaf.get("equals"), str) and leaf["equals"]:
        return CountFilter(key=attr, value=leaf["equals"])
    contains = leaf.get("containsAny")
    if isinstance(contains, list) and contains and all(isinstance(v, str) and v for v in contains):
        if len(contains) > 1:
            raise Fail(f"{R.M_ACTIVITY_COND}: containsAny with several values needs OR of LIKE")
        return CountFilter(key=attr, value=f"*{contains[0]}*", wildcard=True)
    raise Fail(f"{R.M_ACTIVITY_COND}: a condition has neither equals nor containsAny")


def _any_of(alert: AzureAlert, parts: list[Any]) -> CountFilter:
    leaves = [p for p in parts if isinstance(p, dict)]
    if not leaves or len(leaves) != len(parts) or any(("equals" not in p) for p in leaves):
        raise Fail(f"{R.M_ACTIVITY_COND}: anyOf must list equals tests")
    attrs = {_attribute(alert, p.get("field"))[0] for p in leaves}
    if len(attrs) != 1:
        raise Fail(f"{R.M_ACTIVITY_COND}: anyOf across different fields needs OR")
    values = [p["equals"] for p in leaves]
    if not all(isinstance(v, str) and v for v in values):
        raise Fail(f"{R.M_ACTIVITY_COND}: anyOf values must be text")
    return CountFilter(key=attrs.pop(), value="", values=values)


def _scope(scopes: object) -> tuple[CountFilter, bool]:
    """The resourceId filter and whether the rule covers more than one resource."""
    ids = [s for s in scopes if isinstance(s, str) and s] if isinstance(scopes, list) else []
    if not ids or len(ids) != len(scopes or []):
        raise Fail(f"{R.M_ACTIVITY_COND}: scope is not a list of resource ids")
    plain = [i for i in ids if RESOURCE_SEGMENTS.search(i)]
    key = "resourceId"
    if len(plain) == len(ids):
        if len(ids) == 1:
            return CountFilter(key=key, value=ids[0]), False
        return CountFilter(key=key, value="", values=sorted(ids)), True
    if len(ids) == 1:
        prefix = "/" + ids[0].strip("/")
        depth = prefix.lower().count("/")
        if depth == 2:  # a subscription: service health records may carry the bare id
            return CountFilter(key=key, value=prefix + "*", wildcard=True), True
        if depth == 4:  # a resource group
            return CountFilter(key=key, value=prefix + "/*", wildcard=True), True
    raise Fail(f"{R.M_ACTIVITY_COND}: scope mixes or lists several prefixes (one condition each)")


def _categories(filters: list[CountFilter]) -> list[str]:
    found: list[str] = []
    for f in filters:
        if f.key == "category":
            for value in f.values or [f.value]:
                flag = CATEGORY_FLAGS.get(value.lower())
                if flag and flag not in found:
                    found.append(flag)
    return found


def _convert(alert: AzureAlert) -> tuple[IRMonitor, bool]:
    props = alert.properties
    base = base_fields(alert, key_of(alert), "activity log alert", alert.name)
    if props.get("tenantScope"):
        raise Fail(f"{R.M_ACTIVITY_COND}: the rule is tenant or management group scoped")
    condition = props.get("condition")
    leaves = condition.get("allOf") if isinstance(condition, dict) else None
    if not isinstance(leaves, list) or not leaves:
        raise Fail(f"{R.M_ACTIVITY_COND}: the rule has no readable condition")
    scope, many = _scope(props.get("scopes"))
    filters: list[CountFilter] = []
    for leaf in leaves:
        if not isinstance(leaf, dict):
            raise Fail(f"{R.M_ACTIVITY_COND}: a condition is not an object")
        any_of = leaf.get("anyOf")
        filters.append(_any_of(alert, any_of) if isinstance(any_of, list) else _leaf(alert, leaf))
    categories = _categories(filters)
    health = bool(set(categories) & {"ServiceHealth", "ResourceHealth"})
    facet = ["resourceId"] if many and "ServiceHealth" not in categories else []
    names = sorted({f.key for f in filters} | {scope.key})
    notes = [
        R.N_ACTIVITY_NO_SEVERITY,
        R.N_ACTIVITY_FORWARD.format(flags=_flags(categories)),
        R.N_ACTIVITY_ATTRIBUTES.format(names=", ".join(names)),
        R.N_ACTIVITY_EVENTS,
        R.N_RESOURCE_ID_LOG,
    ]
    if health:
        notes.append(R.N_ACTIVITY_HEALTH)
    if props.get("enabled") is False:
        notes.append(R.N_DISABLED)
    signal = Signal(
        kind=SignalKind.LOG,
        raw_query=alert.name,
        window_seconds=WINDOW,
        aggregation_window_seconds=WINDOW,
        occurrences=Occurrences.ALL,
        count=CountSignal(
            filters=[CountFilter(key=FORWARDED[0], value=FORWARDED[1]), scope, *filters],
            group_by=facet,
        ),
    )
    monitor = IRMonitor(
        **base,
        signal=signal,
        thresholds=[
            Threshold(severity=Severity.WARNING, operator=Operator.ABOVE_OR_EQUALS, value=1)
        ],
        confidence=Confidence.APPROXIMATE,
        reasons=notes,
        unmapped={"description_note": "Azure activity log alert (no severity)"},
        emit=EmitOptions(null_safe_count=False),
        data_path=NEEDS_LOG_FORWARDING,
    )
    try:
        build_nrql(monitor)
    except ValueError as exc:
        raise Fail(f"{R.M_ACTIVITY_COND}: {R.shown(exc, 120)}") from None
    return monitor, health


def _flags(categories: list[str]) -> str:
    if not categories:
        return "the forward<Category>AzureActivityLogs flags of the categories the rule matches"
    return ", ".join(f"forward{c}AzureActivityLogs" for c in categories)


def _condition_lines(condition: object) -> list[str]:
    lines: list[str] = []
    leaves = condition.get("allOf") if isinstance(condition, dict) else None
    for leaf in leaves if isinstance(leaves, list) else []:
        if not isinstance(leaf, dict):
            continue
        any_of = leaf.get("anyOf")
        parts: list[Any] = any_of if isinstance(any_of, list) else [leaf]
        texts = []
        for p in parts:
            if isinstance(p, dict):
                wanted = p.get("equals", p.get("containsAny", ""))
                texts.append(f"{R.shown(p.get('field'), 40)} = {R.shown(wanted, 80)}")
        if texts:
            lines.append(" or ".join(texts))
    return lines


def translate_activity_log_alert(alert: AzureAlert) -> IRMonitor:
    try:
        return _convert(alert)[0]
    except Fail as fail:
        base = base_fields(alert, key_of(alert), "activity log alert", alert.name)
        lines = _condition_lines(alert.properties.get("condition"))
        steps = [f"The Azure rule matches events where {' and '.join(lines)}."] if lines else []
        return manual(base, [fail.reason], steps)
