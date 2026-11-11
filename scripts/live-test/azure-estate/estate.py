"""Create a tiny ckg- Azure Monitor test estate in ONE resource group through ARM REST PUTs.

Everything is named ckg- (the storage account ckgstore + 6 random characters), alert rules are
created disabled, and every receiver address is on an unroutable .invalid domain. The manifest
(out/live-azure/manifest.json) is written BEFORE each PUT so a half-created object is still
cleaned up. Previews unless --yes. Secrets come from the environment or .env, never printed.

    python estate.py            # preview: names and counts only
    python estate.py --yes      # create (stops at the first permission or policy error)
"""

import argparse
import json
import secrets
import string
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arm import Arm, ArmError, load_env

RG_DEFAULT = "azure-signup-rg"
LOCATION = "eastus"
MANIFEST = Path("out/live-azure/manifest.json")
INSIGHTS = "Microsoft.Insights"
API_AG, API_MA, API_SQR, API_ALA, API_APR = (
    "2023-01-01",
    "2026-01-01",
    "2026-03-01",
    "2026-01-01",
    "2021-08-08",
)
API_STORAGE, API_WORKSPACE = "2023-05-01", "2022-10-01"
EMAIL = "ckg-test@example.invalid"
HOOK = "https://example.invalid/hook"
SAFE_GROUPS = ("ckg-ag-email", "ckg-ag-webhook")


@dataclass
class Item:
    kind: str
    name: str
    path: str
    api: str
    body: dict[str, Any]
    wait: bool = False
    probe: bool = False
    note: str = field(default="", compare=False)


def random_suffix(n: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def _rg(sub: str, rg: str) -> str:
    return f"/subscriptions/{sub}/resourceGroups/{rg}"


def _ag_receivers(sub: str, rg: str, tenant: str) -> dict[str, dict[str, Any]]:
    """One action group per receiver type; nothing can reach a real person."""
    zero = "00000000-0000-0000-0000-000000000000"
    acct = f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Automation/automationAccounts/ckg-auto"
    return {
        "email": {
            "emailReceivers": [
                {"name": "ckg-email", "emailAddress": EMAIL, "useCommonAlertSchema": True}
            ]
        },
        "webhook": {
            "webhookReceivers": [
                {"name": "ckg-hook-common", "serviceUri": HOOK, "useCommonAlertSchema": True},
                {
                    "name": "ckg-hook-legacy",
                    "serviceUri": HOOK + "2",
                    "useCommonAlertSchema": False,
                },
            ]
        },
        "securewebhook": {
            "webhookReceivers": [
                {
                    "name": "ckg-hook-aad",
                    "serviceUri": HOOK + "3",
                    "useCommonAlertSchema": True,
                    "useAadAuth": True,
                    "objectId": zero,
                    "identifierUri": "api://ckg-test.invalid",
                    "tenantId": tenant,
                }
            ]
        },
        "logicapp": {
            "logicAppReceivers": [
                {
                    "name": "ckg-logic",
                    "resourceId": f"{_rg(sub, rg)}/providers/Microsoft.Logic/workflows/ckg-logic-placeholder",
                    "callbackUrl": "https://ckg-logic.invalid/workflows/placeholder/triggers/manual/paths/invoke?api-version=2016-10-01&sig=PLACEHOLDER-NOT-A-REAL-SIGNATURE",
                    "useCommonAlertSchema": True,
                }
            ]
        },
        "function": {
            "azureFunctionReceivers": [
                {
                    "name": "ckg-func",
                    "functionAppResourceId": f"{_rg(sub, rg)}/providers/Microsoft.Web/sites/ckg-func-placeholder",
                    "functionName": "ckgHttpTrigger",
                    "httpTriggerUrl": "https://ckg-func.invalid/api/ckgHttpTrigger?code=PLACEHOLDER",
                    "useCommonAlertSchema": True,
                }
            ]
        },
        "runbook": {
            "automationRunbookReceivers": [
                {
                    "name": "ckg-runbook",
                    "automationAccountId": acct,
                    "runbookName": "ckg-runbook-placeholder",
                    "webhookResourceId": f"{acct}/webhooks/ckg-hook",
                    "isGlobalRunbook": False,
                    "serviceUri": "https://ckg-runbook.invalid/webhooks?token=PLACEHOLDER",
                    "useCommonAlertSchema": True,
                }
            ]
        },
        "itsm": {
            "itsmReceivers": [
                {
                    "name": "ckg-itsm",
                    "workspaceId": f"{zero}|{zero}",
                    "connectionId": zero,
                    "ticketConfiguration": '{"PayloadRevision":0,"WorkItemType":"Incident","UseTemplate":false,"WorkItemData":"{}","CreateOneWIPerCI":false}',
                    "region": "eastus",
                }
            ]
        },
        "eventhub": {
            "eventHubReceivers": [
                {
                    "name": "ckg-eventhub",
                    "eventHubNameSpace": "ckg-ns-placeholder",
                    "eventHubName": "ckg-hub",
                    "subscriptionId": sub,
                    "tenantId": tenant,
                    "useCommonAlertSchema": True,
                }
            ]
        },
        "armrole": {
            # Monitoring Contributor built-in role id; attached to no rule.
            "armRoleReceivers": [
                {
                    "name": "ckg-armrole",
                    "roleId": "749f88d5-cbae-40b8-bcfc-e573ddc772fa",
                    "useCommonAlertSchema": True,
                }
            ]
        },
        "apppush": {"azureAppPushReceivers": [{"name": "ckg-push", "emailAddress": EMAIL}]},
    }


def _crit(
    name: str, metric: str, agg: str, op: str, threshold: float, **extra: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "criterionType": "StaticThresholdCriterion",
        "metricName": metric,
        "metricNamespace": "Microsoft.Storage/storageAccounts",
        "timeAggregation": agg,
        "operator": op,
        "threshold": threshold,
        **extra,
    }


def _dyn(name: str, metric: str, agg: str, op: str, sens: str, n: int, m: int) -> dict[str, Any]:
    return {
        "name": name,
        "criterionType": "DynamicThresholdCriterion",
        "metricName": metric,
        "metricNamespace": "Microsoft.Storage/storageAccounts",
        "timeAggregation": agg,
        "operator": op,
        "alertSensitivity": sens,
        "failingPeriods": {"numberOfEvaluationPeriods": n, "minFailingPeriodsToAlert": m},
        "ignoreDataBefore": "2026-11-01T00:00:00.000Z",
        "dimensions": [],
    }


SINGLE = "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria"
MULTI = "Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria"


def _metric_alerts(scope: str, ag: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    def rule(
        sev: int,
        freq: str,
        win: str,
        odata: str,
        crits: list[dict[str, Any]],
        mitigate: bool,
        group: str = "email",
        **extra: Any,
    ) -> dict[str, Any]:
        return {
            "description": "ckg test metric alert (disabled)",
            "severity": sev,
            "enabled": False,
            "scopes": [scope],
            "evaluationFrequency": freq,
            "windowSize": win,
            "autoMitigate": mitigate,
            "criteria": {"odata.type": odata, "allOf": crits},
            "actions": [{"actionGroupId": ag(group)}],
            **extra,
        }

    hook = {"actionGroupId": ag("webhook"), "webHookProperties": {"ckg": "test"}}
    return [
        (
            "ckg-ma-static-txn",
            rule(
                0,
                "PT1M",
                "PT5M",
                SINGLE,
                [_crit("txn", "Transactions", "Total", "GreaterThan", 1000)],
                True,
            ),
        ),
        (
            "ckg-ma-static-avail",
            rule(
                1,
                "PT5M",
                "PT15M",
                SINGLE,
                [_crit("avail", "Availability", "Average", "LessThan", 99)],
                False,
            ),
        ),
        (
            "ckg-ma-multi-cond",
            rule(
                2,
                "PT15M",
                "PT1H",
                SINGLE,
                [
                    _crit("ingress", "Ingress", "Total", "GreaterThan", 1e9),
                    _crit("egress", "Egress", "Total", "GreaterThanOrEqual", 1e9),
                ],
                True,
                "webhook",
            ),
        ),
        (
            "ckg-ma-dims",
            rule(
                3,
                "PT30M",
                "PT1H",
                MULTI,
                [
                    _crit(
                        "txn-dims",
                        "Transactions",
                        "Total",
                        "GreaterThan",
                        50,
                        dimensions=[
                            {
                                "name": "ApiName",
                                "operator": "Include",
                                "values": ["GetBlob", "PutBlob"],
                            },
                            {"name": "ResponseType", "operator": "Include", "values": ["*"]},
                        ],
                    )
                ],
                True,
            ),
        ),
        (
            "ckg-ma-latency-max",
            rule(
                4,
                "PT1H",
                "P1D",
                SINGLE,
                [_crit("lat", "SuccessE2ELatency", "Maximum", "GreaterThan", 5000)],
                False,
                "webhook",
            ),
        ),
        (
            "ckg-ma-dyn-txn",
            rule(
                2,
                "PT5M",
                "PT15M",
                MULTI,
                [_dyn("dtxn", "Transactions", "Total", "GreaterThan", "Medium", 4, 3)],
                True,
            ),
        ),
        (
            "ckg-ma-dyn-avail",
            rule(
                3,
                "PT15M",
                "PT1H",
                MULTI,
                [_dyn("davail", "Availability", "Average", "LessThan", "High", 4, 4)],
                True,
                "webhook",
            ),
        ),
        (
            "ckg-ma-capacity-min",
            rule(
                1,
                "PT1H",
                "PT6H",
                SINGLE,
                [_crit("cap", "UsedCapacity", "Minimum", "LessThan", 1)],
                True,
            ),
        ),
        (
            "ckg-ma-count",
            {
                **rule(
                    2,
                    "PT5M",
                    "PT5M",
                    SINGLE,
                    [_crit("cnt", "Transactions", "Count", "GreaterThanOrEqual", 5)],
                    True,
                ),
                "actions": [hook],
            },
        ),
    ]


def _sqr(ws: str, ag: Callable[[str], str]) -> list[tuple[str, dict[str, Any]]]:
    def cond(
        query: str, agg: str, op: str, thr: float, n: int = 1, m: int = 1, **extra: Any
    ) -> dict[str, Any]:
        return {
            "query": query,
            "timeAggregation": agg,
            "operator": op,
            "threshold": thr,
            "failingPeriods": {"numberOfEvaluationPeriods": n, "minFailingPeriodsToAlert": m},
            **extra,
        }

    def rule(
        sev: int, freq: str, win: str, c: dict[str, Any], group: str = "email", **extra: Any
    ) -> dict[str, Any]:
        return {
            "location": LOCATION,
            "properties": {
                "description": "ckg test log alert (disabled)",
                "severity": sev,
                "enabled": False,
                "scopes": [ws],
                "evaluationFrequency": freq,
                "windowSize": win,
                "criteria": {"allOf": [c]},
                "actions": {"actionGroups": [ag(group)]},
                **extra,
            },
        }

    usage_sum = "Usage | summarize AggregatedValue = sum(Quantity) by DataType"
    dyn = {
        "criterionType": "DynamicThresholdCriterion",
        "query": "Usage | summarize AggregatedValue = sum(Quantity) by bin(TimeGenerated, 5m)",
        "timeAggregation": "Total",
        "metricMeasureColumn": "AggregatedValue",
        "operator": "GreaterOrLessThan",
        "alertSensitivity": "Medium",
        "failingPeriods": {"numberOfEvaluationPeriods": 4, "minFailingPeriodsToAlert": 3},
        "ignoreDataBefore": "2026-11-01T00:00:00.000Z",
    }
    simple = rule(
        4, "PT5M", "PT5M", {"query": "Usage | where IsBillable == true"}, autoMitigate=False
    )
    for k in ("evaluationFrequency", "windowSize"):
        simple["properties"].pop(k)
    simple["kind"] = "SimpleLogAlert"
    return [
        (
            "ckg-sqr-usage-total",
            rule(
                2,
                "PT15M",
                "PT1H",
                cond(
                    usage_sum,
                    "Total",
                    "GreaterThan",
                    1000,
                    metricMeasureColumn="AggregatedValue",
                    dimensions=[{"name": "DataType", "operator": "Include", "values": ["*"]}],
                ),
                muteActionsDuration="PT1H",
            ),
        ),
        (
            "ckg-sqr-usage-count",
            rule(
                3,
                "PT5M",
                "PT5M",
                cond("Usage | where IsBillable == true", "Count", "GreaterThan", 0),
            ),
        ),
        (
            "ckg-sqr-activity-fail",
            rule(
                1,
                "PT15M",
                "PT15M",
                cond(
                    'AzureActivity | where CategoryValue == "Administrative" | where ActivityStatusValue == "Failure"',
                    "Count",
                    "GreaterThan",
                    5,
                    3,
                    2,
                ),
                "webhook",
                muteActionsDuration="PT30M",
            ),
        ),
        (
            "ckg-sqr-heartbeat-silent",
            rule(
                0,
                "PT5M",
                "PT30M",
                cond(
                    "Heartbeat | summarize LastSeen = max(TimeGenerated) by Computer | where LastSeen < ago(10m)",
                    "Count",
                    "GreaterThan",
                    0,
                ),
                autoMitigate=False,
            ),
        ),
        (
            "ckg-sqr-usage-avg",
            rule(
                3,
                "PT10M",
                "PT30M",
                cond(
                    "Usage | where DataType == 'Operation'",
                    "Average",
                    "GreaterThan",
                    100,
                    3,
                    2,
                    metricMeasureColumn="Quantity",
                ),
                "webhook",
                resolveConfiguration={"autoResolved": True, "timeToResolve": "PT10M"},
            ),
        ),
        ("ckg-sqr-dynamic", rule(2, "PT5M", "PT15M", dyn)),
        ("ckg-sqr-simple", simple),
    ]


def build_plan(sub: str, rg: str, suffix: str, tenant: str) -> list[Item]:
    base = _rg(sub, rg)
    ins = f"{base}/providers/{INSIGHTS}"
    storage = f"ckgstore{suffix}"
    st_id = f"{base}/providers/Microsoft.Storage/storageAccounts/{storage}"
    ws_name = "ckg-law"
    ws_id = f"{base}/providers/Microsoft.OperationalInsights/workspaces/{ws_name}"

    def ag(key: str) -> str:
        return f"{ins}/actionGroups/ckg-ag-{key}"

    items = [
        Item(
            "storage",
            storage,
            st_id,
            API_STORAGE,
            {
                "location": LOCATION,
                "sku": {"name": "Standard_LRS"},
                "kind": "StorageV2",
                "tags": {"ckg_test": "true"},
                "properties": {
                    "minimumTlsVersion": "TLS1_2",
                    "allowBlobPublicAccess": False,
                    "supportsHttpsTrafficOnly": True,
                },
            },
            wait=True,
        ),
        Item(
            "workspace",
            ws_name,
            ws_id,
            API_WORKSPACE,
            {
                "location": LOCATION,
                "tags": {"ckg_test": "true"},
                "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30},
            },
            wait=True,
        ),
    ]
    for key, receivers in _ag_receivers(sub, rg, tenant).items():
        name = f"ckg-ag-{key}"
        items.append(
            Item(
                "actionGroup",
                name,
                f"{ins}/actionGroups/{name}",
                API_AG,
                {
                    "location": "Global",
                    "tags": {"ckg_test": "true"},
                    "properties": {
                        "groupShortName": f"ckg{key}"[:12],
                        "enabled": True,
                        **receivers,
                    },
                },
            )
        )
    for name, props in _metric_alerts(st_id, ag):
        items.append(
            Item(
                "metricAlert",
                name,
                f"{ins}/metricAlerts/{name}",
                API_MA,
                {"location": "global", "tags": {"ckg_test": "true"}, "properties": props},
            )
        )
    for name, body in _sqr(ws_id, ag):
        body["tags"] = {"ckg_test": "true"}
        items.append(
            Item("scheduledQueryRule", name, f"{ins}/scheduledQueryRules/{name}", API_SQR, body)
        )

    def ala(cond: dict[str, Any], scope: str, group: str = "email") -> dict[str, Any]:
        return {
            "location": "Global",
            "tags": {"ckg_test": "true"},
            "properties": {
                "description": "ckg test activity log alert (disabled)",
                "enabled": False,
                "scopes": [scope],
                "condition": cond,
                "actions": {"actionGroups": [{"actionGroupId": ag(group)}]},
            },
        }

    sub_scope = f"/subscriptions/{sub}"
    ala_rules = [
        (
            "ckg-ala-admin-rg",
            ala(
                {
                    "allOf": [
                        {"field": "category", "equals": "Administrative"},
                        {
                            "field": "operationName",
                            "equals": "Microsoft.Storage/storageAccounts/delete",
                        },
                    ]
                },
                base,
            ),
        ),
        (
            "ckg-ala-service-health",
            ala(
                {
                    "allOf": [
                        {"field": "category", "equals": "ServiceHealth"},
                        {
                            "anyOf": [
                                {"field": "properties.incidentType", "equals": "Incident"},
                                {"field": "properties.incidentType", "equals": "Maintenance"},
                            ]
                        },
                    ]
                },
                sub_scope,
                "webhook",
            ),
        ),
        (
            "ckg-ala-resource-health",
            ala(
                {
                    "allOf": [
                        {"field": "category", "equals": "ResourceHealth"},
                        {"field": "properties.currentHealthStatus", "equals": "Unavailable"},
                    ]
                },
                st_id,
            ),
        ),
    ]
    for name, body in ala_rules:
        items.append(
            Item("activityLogAlert", name, f"{ins}/activityLogAlerts/{name}", API_ALA, body)
        )

    am = f"{base}/providers/Microsoft.AlertsManagement/actionRules"
    items.append(
        Item(
            "alertProcessingRule",
            "ckg-apr-suppress-weekend",
            f"{am}/ckg-apr-suppress-weekend",
            API_APR,
            {
                "location": "Global",
                "tags": {"ckg_test": "true"},
                "properties": {
                    "description": "ckg test suppression during a weekly window (disabled)",
                    "enabled": False,
                    "scopes": [base],
                    "conditions": [
                        {"field": "Severity", "operator": "Equals", "values": ["Sev3", "Sev4"]}
                    ],
                    "actions": [{"actionType": "RemoveAllActionGroups"}],
                    "schedule": {
                        "timeZone": "UTC",
                        "effectiveFrom": "2026-11-12T00:00:00",
                        "effectiveUntil": "2027-11-12T00:00:00",
                        "recurrences": [
                            {
                                "recurrenceType": "Weekly",
                                "daysOfWeek": ["Saturday", "Sunday"],
                                "startTime": "22:00:00",
                                "endTime": "06:00:00",
                            }
                        ],
                    },
                },
            },
        )
    )
    items.append(
        Item(
            "alertProcessingRule",
            "ckg-apr-add-ag",
            f"{am}/ckg-apr-add-ag",
            API_APR,
            {
                "location": "Global",
                "tags": {"ckg_test": "true"},
                "properties": {
                    "description": "ckg test: add an action group to all alerts in the resource group (disabled)",
                    "enabled": False,
                    "scopes": [base],
                    "actions": [{"actionType": "AddActionGroups", "actionGroupIds": [ag("email")]}],
                },
            },
        )
    )
    return items


def probe_items(sub: str, rg: str) -> list[Item]:
    """Create requests the API is expected to REJECT (documented in the live-vs-official note)."""
    base = _rg(sub, rg)
    ws = f"{base}/providers/Microsoft.OperationalInsights/workspaces/ckg-law"
    name = "ckg-sqr-probe-union-1m"
    return [
        Item(
            "scheduledQueryRule",
            name,
            f"{base}/providers/{INSIGHTS}/scheduledQueryRules/{name}",
            API_SQR,
            {
                "location": LOCATION,
                "properties": {
                    "enabled": False,
                    "severity": 4,
                    "scopes": [ws],
                    "evaluationFrequency": "PT1M",
                    "windowSize": "PT5M",
                    "criteria": {
                        "allOf": [
                            {
                                "query": "union Usage, Heartbeat | take 5",
                                "timeAggregation": "Count",
                                "operator": "GreaterThan",
                                "threshold": 0,
                            }
                        ]
                    },
                },
            },
            probe=True,
        )
    ]


def _write(manifest: Path, data: dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data, indent=2))


def _wait(arm: Arm, item: Item, sleep: Callable[[float], None]) -> None:
    for _ in range(60):
        state = arm.get(item.path, item.api).get("properties", {}).get("provisioningState")
        if state in (None, "Succeeded"):
            return
        if state in ("Failed", "Canceled"):
            raise ArmError("GET", item.path, 200, "ProvisioningFailed", str(state))
        sleep(10)
    raise ArmError("GET", item.path, 200, "ProvisioningTimeout", item.name)


def run(
    arm: Arm, items: list[Item], manifest: Path, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(manifest.read_text()) if manifest.exists() else {}
    data.setdefault("resources", [])
    data.setdefault("rejected", [])
    data["subscription_placeholder"] = "SUBSCRIPTION_ID"
    for it in items:
        entry = {
            "kind": it.kind,
            "name": it.name,
            "id": it.path,
            "api": it.api,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        data["resources"].append(
            entry
        )  # recorded before the PUT: a half-created object is cleaned up
        _write(manifest, data)
        try:
            arm.put(it.path, it.api, it.body)
            if it.wait:
                _wait(arm, it, sleep)
        except ArmError as err:
            if err.status in (400, 409, 422) and "Policy" not in err.code:
                data["resources"].remove(entry)
                data["rejected"].append(
                    {**entry, "status": err.status, "code": err.code, "error": str(err)}
                )
                _write(manifest, data)
                print(f"REJECTED {it.kind} {it.name}: {err.status} {err.code}")
                continue
            print(f"STOP {it.kind} {it.name}: {err}")
            raise
        print(f"created {it.kind} {it.name}")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--yes", action="store_true", help="really create (default: preview)")
    ap.add_argument(
        "--probe", action="store_true", help="also send the requests expected to be rejected"
    )
    ap.add_argument("--rg", default=RG_DEFAULT)
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    a = ap.parse_args(argv)
    env = load_env(a.env_file)
    arm = Arm(env)
    try:
        prior = json.loads(a.manifest.read_text()) if a.manifest.exists() else {}
        suffix = prior.get("storage_suffix") or random_suffix()
        items = build_plan(arm.subscription, a.rg, suffix, env["AZURE_TENANT_ID"])
        if a.probe:
            items += probe_items(arm.subscription, a.rg)
        counts: dict[str, int] = {}
        for it in items:
            counts[it.kind] = counts.get(it.kind, 0) + 1
            print(f"{'create' if a.yes else 'would create'} {it.kind:20} {it.name}")
        print(counts)
        if not a.yes:
            print("preview only; pass --yes to create")
            return 0
        a.manifest.parent.mkdir(parents=True, exist_ok=True)
        if not prior:
            _write(a.manifest, {"storage_suffix": suffix, "resources": [], "rejected": []})
        elif "storage_suffix" not in prior:
            _write(a.manifest, {**prior, "storage_suffix": suffix})
        run(arm, items, a.manifest)
    finally:
        arm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
