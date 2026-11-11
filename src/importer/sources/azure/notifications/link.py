"""How the notification side finds the converted conditions that used an action group.

The alert converter and this package agree on two things, written down in
`docs/dev/azure-notification-link.md`:

- a converted condition carries the tag `azure.actionGroup` (the constant below), one tag value
  per action group the Azure alert rule pointed at, spelled `action_group_key(resource id)`;
- the saved IR may also keep the action group resource ids in `notification.handles`. Either
  source is enough; both are read and merged.

Nothing here imports the alert converter, so the two sides can be built and tested alone.
"""

import re

from importer.core.ir import IRMonitor

ACTION_GROUP_TAG = "azure.actionGroup"
# Tags the alert converter may write so that alert processing rules can be scoped. A rule that
# needs one of them is converted only when converted conditions really carry it.
SEVERITY_TAG = "azure.severity"
SERVICE_TAG = "azure.monitorService"
RESOURCE_TYPE_TAG = "azure.resourceType"
RESOURCE_GROUP_TAG = "azure.resourceGroup"
RESOURCE_TAG = "azure.resourceId"

_ID = re.compile(
    r"^/subscriptions/(?P<sub>[^/]+)/resourcegroups/(?P<rg>[^/]+)"
    r"/providers/microsoft\.insights/actiongroups/(?P<name>[^/]+)/?$",
    re.IGNORECASE,
)
MAX_KEY = 200


def action_group_key(resource_id: str) -> str:
    """`subscription/resource group/name`, lower case: Azure ids are case-insensitive."""
    found = _ID.match(resource_id.strip())
    if found:
        return f"{found['sub']}/{found['rg']}/{found['name']}".lower()
    return resource_id.strip().lower()[:MAX_KEY]


def tag_values(monitor: IRMonitor, tag: str) -> list[str]:
    """Values of the `name:value` entries of the monitor's tags for one tag name."""
    start = tag + ":"
    return [t[len(start) :] for t in monitor.tags if t.startswith(start)]


def monitor_action_groups(monitor: IRMonitor) -> list[str]:
    """Sorted action group keys that a converted monitor points at."""
    keys = {action_group_key(h) for h in monitor.notification.handles if h.strip()}
    keys |= {
        v.strip().lower()[:MAX_KEY] for v in tag_values(monitor, ACTION_GROUP_TAG) if v.strip()
    }
    return sorted(keys)
