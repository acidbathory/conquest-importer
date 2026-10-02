"""Emit NerdGraph (GraphQL) payloads for static NRQL alert conditions.

The GraphQL document is a constant. All monitor-derived values travel as
variables, never formatted into the query text.
"""

from typing import Any

from importer.emit.common import (
    AGGREGATION_DELAY_SECONDS,
    AGGREGATION_WINDOW_SECONDS,
    emittable_window,
    nrql_for,
)
from importer.models.ir import IRMonitor

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
        "name": monitor.name,
        "description": f"Imported from Datadog monitor {monitor.source_id}",
        "enabled": False,  # imported disabled; a human enables after review
        "nrql": {"query": nrql_for(monitor)},
        "signal": {
            "aggregationWindow": AGGREGATION_WINDOW_SECONDS,
            "aggregationMethod": "EVENT_FLOW",
            "aggregationDelay": AGGREGATION_DELAY_SECONDS,
        },
        "valueFunction": "SINGLE_VALUE",
        "terms": [
            {
                "priority": t.severity.value.upper(),
                "operator": t.operator.value.upper(),
                "threshold": t.value,
                "thresholdDuration": window,
                "thresholdOccurrences": "ALL",
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
