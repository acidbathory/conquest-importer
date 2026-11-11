"""Builders for Azure alert rule resources used by the Azure tests."""

from typing import Any

from importer.sources.azure.models import AzureAlert, NameMap

SUB = "/subscriptions/00000000-0000-0000-0000-000000000001"
VM = f"{SUB}/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm1"
VM2 = f"{SUB}/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm2"
WORKSPACE = f"{SUB}/resourceGroups/rg1/providers/Microsoft.OperationalInsights/workspaces/ws1"


def make(
    family: str,
    props: dict[str, Any],
    *,
    name: str = "rule",
    kind: str = "",
    names: NameMap | None = None,
) -> AzureAlert:
    types = {
        "metricAlerts": "Microsoft.Insights/metricAlerts",
        "scheduledQueryRules": "Microsoft.Insights/scheduledQueryRules",
        "activityLogAlerts": "Microsoft.Insights/activityLogAlerts",
        "prometheusRuleGroups": "Microsoft.AlertsManagement/prometheusRuleGroups",
        "smartDetectorAlertRules": "Microsoft.AlertsManagement/smartDetectorAlertRules",
    }
    return AzureAlert(
        id=f"{SUB}/resourceGroups/rg1/providers/x/{name}",
        name=name,
        type=types[family],
        kind=kind,
        family=family,
        properties=props,
        names=names or NameMap(),
    )


def criterion(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "criterionType": "StaticThresholdCriterion",
        "name": "c1",
        "metricName": "Percentage CPU",
        "metricNamespace": "Microsoft.Compute/virtualMachines",
        "timeAggregation": "Average",
        "operator": "GreaterThan",
        "threshold": 80,
        "dimensions": [],
    }
    return base | over


def metric_alert(*crits: dict[str, Any], **over: Any) -> AzureAlert:
    props: dict[str, Any] = {
        "severity": 2,
        "enabled": True,
        "scopes": [VM],
        "evaluationFrequency": "PT5M",
        "windowSize": "PT5M",
        "criteria": {
            "odata.type": "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
            "allOf": list(crits) or [criterion()],
        },
    }
    names = over.pop("names", None)
    name = over.pop("name", "rule")
    return make("metricAlerts", props | over, name=name, names=names)


def log_rule(query: str, **cond: Any) -> AzureAlert:
    condition: dict[str, Any] = {
        "query": query,
        "timeAggregation": "Count",
        "operator": "GreaterThan",
        "threshold": 5,
        "failingPeriods": {"numberOfEvaluationPeriods": 1, "minFailingPeriodsToAlert": 1},
    }
    props: dict[str, Any] = {
        "severity": 1,
        "enabled": True,
        "scopes": [WORKSPACE],
        "evaluationFrequency": "PT5M",
        "windowSize": "PT5M",
        "criteria": {"allOf": [condition | cond]},
    }
    return make("scheduledQueryRules", props, kind="LogAlert")
