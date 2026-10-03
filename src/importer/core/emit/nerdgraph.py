"""Emit NerdGraph (GraphQL) payloads for static NRQL alert conditions.

The GraphQL document is a constant. All monitor-derived values travel as
variables, never formatted into the query text.
"""

from typing import Any

from importer.core.emit.common import (
    aggregation_delay,
    aggregation_window,
    condition_name,
    emittable_window,
    nrql_for,
    source_description,
)
from importer.core.ir import IRMonitor

POLICY_PLACEHOLDER = "POLICY_ID_PLACEHOLDER"
ACCOUNT_PLACEHOLDER = "ACCOUNT_ID_PLACEHOLDER"

MUTATION = (
    "mutation CreateCondition("
    "$accountId: Int!, $policyId: ID!, $condition: AlertsNrqlConditionStaticInput!) {\n"
    "  alertsNrqlConditionStaticCreate("
    "accountId: $accountId, policyId: $policyId, condition: $condition) {\n"
    "    id\n    name\n  }\n}"
)


def nerdgraph_payload(monitor: IRMonitor) -> dict[str, Any]:
    window = emittable_window(monitor)
    condition: dict[str, Any] = {
        "name": condition_name(monitor.name),
        "description": source_description(monitor),
        "enabled": False,  # imported disabled; a human enables after review
        "nrql": {"query": nrql_for(monitor)},
        "signal": {
            "aggregationWindow": aggregation_window(monitor),
            "aggregationMethod": "EVENT_FLOW",
            "aggregationDelay": aggregation_delay(monitor),
        },
        "valueFunction": "SINGLE_VALUE",
        "terms": [
            {
                "priority": t.severity.value.upper(),
                "operator": t.operator.value.upper(),
                "threshold": t.value,
                "thresholdDuration": window,
                "thresholdOccurrences": monitor.signal.occurrences.value.upper(),
            }
            for t in monitor.thresholds
        ],
    }
    if monitor.no_data.notify and monitor.no_data.timeout_minutes:
        condition["expiration"] = {
            "expirationDuration": monitor.no_data.timeout_minutes * 60,
            "openViolationOnExpiration": True,
            "closeViolationsOnExpiration": False,
        }
    return {
        "query": MUTATION,
        "variables": {
            "accountId": ACCOUNT_PLACEHOLDER,
            "policyId": POLICY_PLACEHOLDER,
            "condition": condition,
        },
    }
