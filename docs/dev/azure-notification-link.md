# Azure: how notifications link to converted alerts

Two packages build the Azure source in parallel: the alert converter (rules to conditions) and the
notification side (`src/importer/sources/azure/notifications/`, action groups and alert processing
rules). They never import each other. This note is the whole contract between them.

## The tag

A converted condition carries the tag `azure.actionGroup`, one value per action group its Azure
alert rule pointed at. The value is the key `subscription id/resource group/action group name`,
lower case, built by `action_group_key()` in `azure/notifications/link.py`; the constant is
`ACTION_GROUP_TAG`. In the saved IR (`ir/*.json`, `IRMonitor.tags`) the entry is spelled
`azure.actionGroup:<key>`. The alert converter can import the constant and the function.

The IR may also keep the action group resource ids in `notification.handles`. The notification
side reads both and merges them, so either is enough.

The workflow for an action group filters issues with `accumulations.tag.azure.actionGroup
EXACTLY_MATCHES <key>`. Two things are UNVERIFIED until a live test: that the tag of a condition
reaches the issue as `accumulations.tag.*`, and that an issue keeps all values of a multi-valued
tag. `convert-azure-notifications --route-by name` filters on the converted condition names
instead and needs no tag.

## Tags for alert processing rules (optional)

A suppression rule scoped by severity, monitor service, resource type, resource group or resource
becomes a muting rule only when converted conditions carry the matching tag; otherwise it stays
manual, because a rule that muted more than the Azure rule would be wrong in the quiet direction.

| Tag | Azure field | Value |
|---|---|---|
| `azure.severity` | Severity | `Sev0` to `Sev4` |
| `azure.monitorService` | MonitorService | for example `Platform`, `Application Insights` |
| `azure.resourceType` | TargetResourceType | for example `microsoft.compute/virtualmachines` |
| `azure.resourceGroup` | TargetResourceGroup / resource group scope | resource group name |
| `azure.resourceId` | TargetResource / resource scope | the resource id |

Constants are in `link.py` (`SEVERITY_TAG` and so on). Values are compared without regard to case.
