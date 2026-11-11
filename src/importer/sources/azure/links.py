"""Tags and handles that link a converted rule to its action groups and to processing rules.

The notification side (action groups to workflows, alert processing rules to muting rules) finds the
conditions of an action group by the tag `azure.actionGroup:<subscription>/<resource group>/<name>`
(lower case) and by the action group ids in `notification.handles`. Processing rules that scope by
severity, resource type, resource group or resource need the matching `azure.*` tag, or they stay
manual. The tag names and the key format are the contract in docs/dev/azure-notification-link.md
(branch improve/azure-notify); this module writes them and imports nothing from that package.
"""

import re
from typing import Any

from importer.core.ir import IRMonitor
from importer.sources.azure.models import AzureAlert
from importer.sources.azure.names import type_of_resource_id

ACTION_GROUP_TAG = "azure.actionGroup"
SAFE_VALUE = re.compile(r"[A-Za-z0-9_.()/-]{1,200}")
GROUP_ID = re.compile(
    r"/subscriptions/(?P<sub>[^/]+)/resourcegroups/(?P<rg>[^/]+)"
    r"/providers/microsoft\.insights/actiongroups/(?P<name>[^/]+)/?",
    re.IGNORECASE,
)
RESOURCE_GROUP = re.compile(r"/subscriptions/[^/]+/resourcegroups/(?P<rg>[^/]+)", re.IGNORECASE)
MAX_GROUPS = 50
MAX_RESOURCES = 5


def _ids(value: object, key: str) -> list[str]:
    found: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = item.get(key) if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            found.append(text.strip())
    return found


def action_group_ids(alert: AzureAlert, rule: dict[str, Any] | None = None) -> list[str]:
    """The action group resource ids a rule points at."""
    props = alert.properties
    if alert.family == "metricAlerts":
        return _ids(props.get("actions"), "actionGroupId")
    if alert.family == "scheduledQueryRules":
        actions = props.get("actions")
        return _ids(actions.get("actionGroups") if isinstance(actions, dict) else None, "")
    if alert.family == "activityLogAlerts":
        actions = props.get("actions")
        groups = actions.get("actionGroups") if isinstance(actions, dict) else None
        return _ids(groups, "actionGroupId")
    if alert.family == "prometheusRuleGroups" and rule is not None:
        return _ids(rule.get("actions"), "actionGroupId")
    if alert.family == "smartDetectorAlertRules":
        groups = props.get("actionGroups")
        return _ids(groups.get("groupIds") if isinstance(groups, dict) else None, "")
    return []


def group_key(resource_id: str) -> str | None:
    found = GROUP_ID.fullmatch(resource_id)
    if found is None:
        return None
    key = f"{found['sub']}/{found['rg']}/{found['name']}".lower()
    return key if SAFE_VALUE.fullmatch(key) else None


def _scope_tags(alert: AzureAlert) -> list[str]:
    scopes = alert.properties.get("scopes", alert.properties.get("scope"))
    tags: list[str] = []
    for scope in (
        [s for s in scopes if isinstance(s, str)][:MAX_RESOURCES]
        if isinstance(scopes, list)
        else []
    ):
        group = RESOURCE_GROUP.match(scope if scope.startswith("/") else "/" + scope)
        kind = type_of_resource_id(scope)
        if SAFE_VALUE.fullmatch(scope):
            if kind:
                tags.append(f"azure.resourceId:{scope}")
                tags.append(f"azure.resourceType:{kind.lower()}")
        if group and SAFE_VALUE.fullmatch(group["rg"]):
            tags.append(f"azure.resourceGroup:{group['rg']}")
    return tags


def add_links(
    alert: AzureAlert, monitor: IRMonitor, rule: dict[str, Any] | None = None
) -> IRMonitor:
    """The monitor with action group tags and handles and the scoping tags added."""
    ids = list(dict.fromkeys(action_group_ids(alert, rule)))[:MAX_GROUPS]
    tags = list(monitor.tags)
    for resource_id in ids:
        key = group_key(resource_id)
        if key:
            tags.append(f"{ACTION_GROUP_TAG}:{key}")
    label = monitor.unmapped.get("azure_severity")
    if isinstance(label, str) and re.fullmatch(r"Sev[0-4]", label):
        tags.append(f"azure.severity:{label}")
    tags += _scope_tags(alert)
    handles = [i for i in ids if SAFE_VALUE.fullmatch(i)]
    monitor.tags = list(dict.fromkeys(tags))
    monitor.notification.handles = list(dict.fromkeys([*monitor.notification.handles, *handles]))
    return monitor
