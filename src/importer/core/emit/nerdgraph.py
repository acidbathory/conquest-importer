"""Emit NerdGraph (GraphQL) payloads for static NRQL alert conditions.

The GraphQL document is a constant. All monitor-derived values travel as
variables, never formatted into the query text.
"""

from typing import Any

from importer.core.emit.common import (
    aggregation_delay,
    aggregation_timer,
    aggregation_window,
    condition_name,
    emittable_window,
    expiration_actions,
    fill_settings,
    nrql_for,
    source_description,
    streaming_method,
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


BASELINE_MUTATION = (
    "mutation CreateBaselineCondition("
    "$accountId: Int!, $policyId: ID!, $condition: AlertsNrqlConditionBaselineInput!) {\n"
    "  alertsNrqlConditionBaselineCreate("
    "accountId: $accountId, policyId: $policyId, condition: $condition) {\n"
    "    id\n    name\n  }\n}"
)


def _signal(monitor: IRMonitor) -> dict[str, Any]:
    method = streaming_method(monitor)
    signal: dict[str, Any] = {
        "aggregationWindow": aggregation_window(monitor),
        "aggregationMethod": method,
    }
    if method == "EVENT_TIMER":
        signal["aggregationTimer"] = aggregation_timer(monitor)  # the timer replaces the delay
    else:
        signal["aggregationDelay"] = aggregation_delay(monitor)
    fill = fill_settings(monitor)
    if fill is not None:
        signal["fillOption"] = fill[0]
        if fill[1] is not None:
            signal["fillValue"] = fill[1]
    options = monitor.emit
    if options is not None and options.slide_by_seconds is not None:
        signal["slideBy"] = options.slide_by_seconds
    return signal


def _text_settings(monitor: IRMonitor) -> dict[str, Any]:
    """Settings that are present only when the monitor asks for them."""
    options = monitor.emit
    if options is None:
        return {}
    wanted = {
        "violationTimeLimitSeconds": options.violation_time_limit_seconds,
        "titleTemplate": options.title_template,
        "runbookUrl": options.runbook_url,
    }
    return {key: value for key, value in wanted.items() if value is not None}


def nerdgraph_payload(monitor: IRMonitor) -> dict[str, Any]:
    window = emittable_window(monitor)
    condition: dict[str, Any] = {
        "name": condition_name(monitor.name),
        "description": source_description(monitor),
        "enabled": False,  # imported disabled; a human enables after review
        "nrql": {"query": nrql_for(monitor)},
        "signal": _signal(monitor),
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
    options = monitor.emit
    if options is not None and options.predict_by_seconds is not None:
        for term in condition["terms"]:
            term["prediction"] = {"predictBy": options.predict_by_seconds}
    baseline = monitor.signal.baseline
    if baseline is not None:
        # A baseline condition has no value function; its terms are in standard deviations.
        del condition["valueFunction"]
        condition["baselineDirection"] = baseline.direction.upper()
        for term in condition["terms"]:
            term["operator"] = "ABOVE"
    condition.update(_text_settings(monitor))
    if monitor.no_data.notify and monitor.no_data.timeout_minutes:
        condition["expiration"] = {
            "expirationDuration": monitor.no_data.timeout_minutes * 60,
            "openViolationOnExpiration": expiration_actions(monitor)[0],
            "closeViolationsOnExpiration": expiration_actions(monitor)[1],
        }
        options = monitor.emit
        if options is not None and options.ignore_on_expected_termination is not None:
            condition["expiration"]["ignoreOnExpectedTermination"] = (
                options.ignore_on_expected_termination
            )
    return {
        "query": BASELINE_MUTATION if baseline is not None else MUTATION,
        "variables": {
            "accountId": ACCOUNT_PLACEHOLDER,
            "policyId": POLICY_PLACEHOLDER,
            "condition": condition,
        },
    }
