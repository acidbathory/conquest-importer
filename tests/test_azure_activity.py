"""Azure activity log alerts as counts of forwarded activity log records.

The expected NRQL is written by hand. Every attribute name is UNVERIFIED (docs/research/
azure-datapath.md section 4); the tests pin the mapping, not the truth of the names.
"""

from typing import Any

from azure_helpers import SUB, VM, make
from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.ir import Confidence, Operator
from importer.sources.azure.models import NameMap
from importer.sources.azure.translate import translate_alert

RG = f"{SUB}/resourceGroups/rg1"
BASE = "SELECT count(*) FROM Log WHERE plugin.type = 'azure'"


def one(alert: Any) -> Any:
    (monitor,) = translate_alert(alert)
    return monitor


def alert(scopes: list[str], *leaves: dict[str, Any], **props: Any) -> Any:
    return make(
        "activityLogAlerts", {"scopes": scopes, "condition": {"allOf": list(leaves)}} | props
    )


def reasons(monitor: Any) -> str:
    return " | ".join(monitor.reasons)


ADMIN = {"field": "category", "equals": "Administrative"}


def test_an_administrative_delete_in_a_resource_group() -> None:
    m = one(
        alert(
            [RG],
            ADMIN,
            {"field": "operationName", "equals": "Microsoft.Storage/storageAccounts/delete"},
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert m.data_path == "needs-log-forwarding"
    assert nrql_for(m) == (
        f"{BASE} AND resourceId LIKE '{RG}/%' AND category = 'Administrative' "
        "AND operationName = 'Microsoft.Storage/storageAccounts/delete' FACET resourceId"
    )
    assert [(t.operator, t.value) for t in m.thresholds] == [(Operator.ABOVE_OR_EQUALS, 1.0)]
    assert m.signal.window_seconds == m.signal.aggregation_window_seconds == 60
    text = reasons(m)
    assert "UNVERIFIED" in text and "forwardAdministrativeAzureActivityLogs" in text
    assert "no severity" in text


def test_a_single_resource_and_an_any_of_on_one_field_become_in() -> None:
    m = one(
        alert(
            [VM],
            ADMIN,
            {
                "anyOf": [
                    {"field": "level", "equals": "Error"},
                    {"field": "level", "equals": "Critical"},
                ]
            },
        )
    )
    assert nrql_for(m) == (
        f"{BASE} AND resourceId = '{VM}' AND category = 'Administrative' "
        "AND level IN ('Error', 'Critical')"
    )


def test_resource_health_filters_on_the_current_health_status() -> None:
    m = one(
        alert(
            [VM],
            {"field": "category", "equals": "ResourceHealth"},
            {"field": "properties.currentHealthStatus", "equals": "Unavailable"},
        )
    )
    assert nrql_for(m) == (
        f"{BASE} AND resourceId = '{VM}' AND category = 'ResourceHealth' "
        "AND properties.currentHealthStatus = 'Unavailable'"
    )
    assert "forwardResourceHealthAzureActivityLogs" in reasons(m)


def test_service_health_on_a_subscription_has_no_facet() -> None:
    m = one(
        alert(
            [SUB],
            {"field": "category", "equals": "ServiceHealth"},
            {
                "anyOf": [
                    {"field": "properties.incidentType", "equals": "Incident"},
                    {"field": "properties.incidentType", "equals": "Maintenance"},
                ]
            },
        )
    )
    assert nrql_for(m) == (
        f"{BASE} AND resourceId LIKE '{SUB}%' AND category = 'ServiceHealth' "
        "AND properties.incidentType IN ('Incident', 'Maintenance')"
    )
    assert "forwardServiceHealthAzureActivityLogs" in reasons(m)


def test_status_and_substatus_use_the_event_hub_names_and_the_map_wins() -> None:
    m = one(alert([VM], ADMIN, {"field": "status", "equals": "Failed"}))
    assert "resultType = 'Failed'" in nrql_for(m)
    names = NameMap(attributes={"status": "result.status"})
    mapped = one(
        make(
            "activityLogAlerts",
            {
                "scopes": [VM],
                "condition": {"allOf": [ADMIN, {"field": "status", "equals": "Failed"}]},
            },
            names=names,
        )
    )
    assert "result.status = 'Failed'" in nrql_for(mapped)


def test_contains_any_with_one_value_is_a_like() -> None:
    m = one(alert([VM], {"field": "operationName", "containsAny": ["/delete"]}))
    assert "operationName LIKE '%/delete%'" in nrql_for(m)


def test_several_resources_use_in_and_a_facet() -> None:
    vm2 = VM.replace("vm1", "vm2")
    m = one(alert([VM, vm2], ADMIN))
    assert f"resourceId IN ('{VM}', '{vm2}')" in nrql_for(m)
    assert nrql_for(m).endswith("FACET resourceId")


def test_what_has_no_attribute_stays_manual_with_the_reason() -> None:
    cases = [
        alert([VM], {"field": "level"}),
        alert([VM], {"field": "operationName", "containsAny": ["a", "b"]}),
        alert([VM], {"field": "resourceGroup", "equals": "rg1"}),
        alert(
            [VM],
            {
                "anyOf": [
                    {"field": "level", "equals": "Error"},
                    {"field": "status", "equals": "Failed"},
                ]
            },
        ),
        alert([VM], {"field": "properties.impactedServices[*].ServiceName", "equals": "x"}),
        alert([RG, f"{SUB}/resourceGroups/rg2"], ADMIN),
        alert([VM], ADMIN, tenantScope="t"),
        make("activityLogAlerts", {}),
    ]
    for rule in cases:
        m = one(rule)
        assert m.confidence is Confidence.MANUAL, rule.properties
        assert m.reasons[0].startswith("activity log alert"), m.reasons


def test_the_condition_is_still_listed_in_the_steps_when_manual() -> None:
    m = one(alert([VM], {"field": "resourceGroup", "equals": "rg1"}))
    assert "resourceGroup = rg1" in m.remediation[0]


def test_disabled_is_noted_and_the_condition_emits() -> None:
    m = one(alert([VM], ADMIN, enabled=False))
    assert "disabled" in reasons(m)
    condition = nerdgraph_payload(m)["variables"]["condition"]
    assert condition["terms"][0]["operator"] == "ABOVE_OR_EQUALS"
    assert condition["terms"][0]["threshold"] == 1.0
