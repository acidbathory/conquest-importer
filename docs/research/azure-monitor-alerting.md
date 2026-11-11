# Azure Monitor alerting: objects, API, and mapping to New Relic NRQL conditions

Research for an Azure source. Written 2026-11-11 from public sources only. No Azure tenant, no New
Relic account and no API of either was called. Everything below was fetched with `curl` from public
URLs on 2026-11-11.

Evidence marks on every claim:

- SWAGGER f: read in the OpenAPI file `f` of Azure/azure-rest-api-specs (key below).
- EXAMPLE: read in an official example file in the same repository (copied to `tests/fixtures/azure/`).
- LEARN k: read on a Microsoft Learn page (key below, HTML fetched and stripped to text).
- NR docs: read in `newrelic/docs-website` MDX or in this repository's New Relic reference.
- REPO file: read in this repository.
- UNVERIFIED: written from memory or inferred; not confirmed in a fetched source. Do not treat as fact.

## 0. Sources

All swagger and example URLs are pinned to commit
`939d1a81ec5159e61394cd8ca2589c2c0ce49ccd` of Azure/azure-rest-api-specs (the `main` branch head on
2026-11-11). Base: `https://raw.githubusercontent.com/Azure/azure-rest-api-specs/939d1a81ec5159e61394cd8ca2589c2c0ce49ccd/specification/`.

| Key | File under the base | api-version |
|---|---|---|
| S-MA | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2026-01-01/metricAlert.json` | 2026-01-01 |
| S-ALA | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2026-01-01/activityLogAlerts_API.json` | 2026-01-01 |
| S-SQR | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2026-03-01/scheduledQueryRule_API.json` | 2026-03-01 |
| S-SQR23 | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2023-12-01/scheduledQueryRule_API.json` | 2023-12-01 |
| S-SQR18 | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2018-04-16/scheduledQueryRule_API.json` | 2018-04-16 (legacy shape) |
| S-AG | `monitor/resource-manager/Microsoft.Insights/Insights/stable/2023-01-01/actionGroups_API.json` | 2023-01-01 |
| S-APR | `alertsmanagement/resource-manager/Microsoft.AlertsManagement/AlertProcessingRules/stable/2021-08-08/AlertProcessingRules.json` | 2021-08-08 |
| S-AR | `alertsmanagement/resource-manager/Microsoft.AlertsManagement/AlertProcessingRules/preview/2019-05-05-preview/ActionRules.json` | 2019-05-05-preview (old name) |
| S-PRG | `alertsmanagement/resource-manager/Microsoft.AlertsManagement/PrometheusRuleGroups/stable/2023-03-01/PrometheusRuleGroups.json` | 2023-03-01 |
| S-SD | `alertsmanagement/resource-manager/Microsoft.AlertsManagement/Legacy/stable/2021-04-01/SmartDetectorAlertRulesApi.json` | 2021-04-01 |
| S-DASH | `portal/resource-manager/Microsoft.Portal/Portal/preview/2025-04-01-preview/portal.json` | 2025-04-01-preview (no stable version) |
| S-WB | `applicationinsights/resource-manager/Microsoft.Insights/ApplicationInsights/stable/2023-06-01/workbooks_API.json` | 2023-06-01 |

"Latest stable" was decided by listing the `stable/` folders on GitHub at that commit. Folder names
are the api-versions. The latest folders that hold each file are 2026-01-01 (metric alerts, activity
log alerts), 2026-03-01 (scheduled query rules), 2023-01-01 (action groups) and the versions in the
table. Two further stable action group folders exist (2021-09-01 and 2023-01-01); the preview
folders were not read for action groups (their file names differ and were not located). That makes
"2023-01-01 is the latest stable action group API" true for the folders listed, and UNVERIFIED for
preview additions.

| Key | Learn page (fetched 2026-11-11) |
|---|---|
| L-TYPES | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-types |
| L-METRIC | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-metric-alert-rule |
| L-LOG | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-create-log-alert-rule |
| L-DYN | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-dynamic-thresholds |
| L-APR | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-processing-rules |
| L-SCHEMA | https://learn.microsoft.com/en-us/azure/azure-monitor/alerts/alerts-common-schema |
| L-RG | https://learn.microsoft.com/en-us/azure/governance/resource-graph/first-query-rest-api |
| L-PRICE | https://azure.microsoft.com/en-us/pricing/details/monitor/ (price figures absent from the HTML) |

New Relic sources: NR-AM `src/content/docs/infrastructure/microsoft-azure-integrations/azure-integrations-list/azure-monitor.mdx`,
NR-MET `.../get-started/azure-integration-metrics.mdx`, NR-LOG `src/content/docs/logs/forward-logs/azure-log-forwarding.mdx`,
all in `newrelic/docs-website` at commit `1e2c595ef12c92dfef455ef25dd6d819065dad52`
(`https://raw.githubusercontent.com/newrelic/docs-website/1e2c595ef12c92dfef455ef25dd6d819065dad52/<path>`).
New Relic reference in this repository: `docs/research/newrelic-notifications-and-nrql-reference.md` (NRREF).

## 1. Answers first

- Seven alert-related resource types matter. Their list calls work at subscription scope with one
  GET each (section 2), so the extractor does not need to walk resource groups.
- Every list is paged with `nextLink` except action groups, whose swagger declares no next link
  (SWAGGER S-AG, `x-ms-pageable` has no `nextLinkName`). Still follow `nextLink` if one ever appears.
- A metric alert fires when all its criteria are true (LEARN L-TYPES). New Relic has no composite
  condition (REPO `docs/what-migrates.md`). Multi-criteria metric alerts are therefore manual.
- Azure evaluates every `evaluationFrequency` over a trailing `windowSize`. New Relic evaluates
  consecutive aggregation windows. Equal frequency and window map cleanly. Frequency smaller than
  the window needs `slide_by` (extra cost) and is approximate.
- Azure data reaches New Relic only for what the data path carries. Metric alerts on platform metrics
  need the New Relic Azure Monitor integration (`collector.name = 'azure-monitor'`). Custom metrics are
  not supported by that integration (NR-AM). Activity log alerts and KQL log alerts need log
  forwarding through an Event Hub (NR-LOG). Without data, no converted condition can fire.
- The New Relic Azure integrations cannot read Azure Government resources (NR activate page,
  quoted in `docs/research/azure-datapath.md`).
- Action groups map well to New Relic only for email and webhook shaped receivers. SMS, voice,
  Azure app push, Azure role e-mail and Event Hub have no destination type.
- Receiver URLs hold secrets: `serviceUri`, `callbackUrl`, `httpTriggerUrl` (section 3). The
  swagger does not mark them `x-ms-secret`; list responses in the official examples return them in
  clear (EXAMPLE `action_groups/2023-01-01__getActionGroup.json`). Whether a real tenant masks them
  is UNVERIFIED. The extractor must treat them as secrets either way.

## 2. Resource types, paths, api-versions

`{sub}` is the subscription id. Resource-group and single-resource paths follow
`/subscriptions/{sub}/resourceGroups/{rg}/providers/<provider>/<type>/{name}`.

| Resource type | List at subscription scope (GET) | api-version | Swagger | Paging |
|---|---|---|---|---|
| metricAlerts | `/subscriptions/{sub}/providers/Microsoft.Insights/metricAlerts` | 2026-01-01 | S-MA | `nextLink` |
| scheduledQueryRules | `/subscriptions/{sub}/providers/Microsoft.Insights/scheduledQueryRules` | 2026-03-01 | S-SQR | `nextLink` |
| activityLogAlerts | `/subscriptions/{sub}/providers/Microsoft.Insights/activityLogAlerts` | 2026-01-01 | S-ALA | `nextLink` |
| alertProcessingRules | `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/actionRules` | 2021-08-08 | S-APR | `nextLink` |
| prometheusRuleGroups | `/subscriptions/{sub}/providers/Microsoft.AlertsManagement/prometheusRuleGroups` | 2023-03-01 | S-PRG | `nextLink` |
| smartDetectorAlertRules | `/subscriptions/{sub}/providers/microsoft.alertsManagement/smartDetectorAlertRules` | 2021-04-01 | S-SD | `nextLink` |
| actionGroups | `/subscriptions/{sub}/providers/Microsoft.Insights/actionGroups` | 2023-01-01 | S-AG | none declared |
| portal dashboards | `/subscriptions/{sub}/providers/Microsoft.Portal/dashboards` | 2025-04-01-preview (preview only) | S-DASH | `nextLink` |
| workbooks | `/subscriptions/{sub}/providers/Microsoft.Insights/workbooks` (query `category`, `tags`, `canFetchContent`) | 2023-06-01 | S-WB | `nextLink` |

Notes:

- Alert processing rules live under the resource type name `actionRules`. The 2021-08-08 swagger
  names the resource `AlertProcessingRules` but keeps the path `actionRules` (SWAGGER S-APR). The
  2019-05-05-preview swagger describes the earlier "action rules" (S-AR); it is only a fixture source.
- A `GET` of one workbook with `canFetchContent=true` is needed to read `serializedData`; the list
  returns no content unless that query flag is set (SWAGGER S-WB parameters). Which of the two the
  server honours on list is UNVERIFIED.
- Older api-versions for each type exist. A converter should ask for the latest stable above. If a
  tenant rejects it, falling back to the previous stable folder is UNVERIFIED (no response was seen).
  The 2026-01-01 metric alert API adds `PromQLCriteria` (SWAGGER S-MA), which older versions do not
  describe.
- Legacy log alert rules (`isLegacyLogAnalyticsRule`, `createdWithApiVersion`) are read-only flags in
  the current API (SWAGGER S-SQR). The 2018-04-16 shape (`source`, `schedule`, `action`, `trigger`) is
  what such rules had (SWAGGER S-SQR18). Whether the 2026-03-01 list returns them in the new shape is
  UNVERIFIED; the fixtures include the 2021-08-01 and 2022-06-15 shapes.
- Classic metric alert rules (`microsoft.insights/alertrules`) are not covered here. They were not
  fetched. UNVERIFIED whether any tenant still has them.
- Inventory by Resource Graph is available: `POST https://management.azure.com/providers/Microsoft.ResourceGraph/resources?api-version=2022-10-01`
  with body `{"subscriptions": ["{sub}"], "query": "Resources | project name, type | limit 5"}`
  (LEARN L-RG). Whether alert rule types appear in the `Resources` table is UNVERIFIED. Use it only
  for counting, never as the source of rule bodies.
- Portal dashboards have no stable API. Only preview versions exist at the pinned commit
  (`.../Portal/preview/2015-08-01-preview` to `2025-04-01-preview`; the `stable` folder is absent).

Request headers: `Authorization: Bearer <token>` (LEARN, REST pages). Reads need no body.

## 3. Fields the converter must read

Field names are as in the swagger definition. `*` means required in the swagger. `(ro)` read-only.
All resources share `id`, `name`, `type`, `location`, `tags`, and for tracked resources `identity`,
`systemData`, `etag` (SWAGGER).

### 3.1 metricAlerts (S-MA, definition `MetricAlertResource`)

`properties`: `description`, `severity`* (0..4), `enabled`*, `scopes`* (resource ids; "you cannot
change the scope of a metric rule based on logs"), `evaluationFrequency`* (ISO 8601 duration),
`windowSize` (ISO 8601), `targetResourceType`, `targetResourceRegion` (used when the scope is a
subscription or resource group), `criteria`* (discriminator `odata.type`), `autoMitigate` (default
true), `resolveConfiguration` {`autoResolved`*, `timeToResolve`}, `actions[]` {`actionGroupId`,
`webHookProperties`}, `customProperties`, `actionProperties`, `lastUpdatedTime` (ro), `isMigrated`
(ro).

`criteria.odata.type` values: `Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria`,
`Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria`,
`Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria`, `Microsoft.Azure.Monitor.PromQLCriteria`.
The swagger notes "previously undocumented values might be returned", so unknown values must degrade
to manual, not fail.

- Single and multiple resource shapes: `allOf[]` of `MultiMetricCriteria`, discriminator
  `criterionType` = `StaticThresholdCriterion` or `DynamicThresholdCriterion`. Common fields: `name`*,
  `metricName`*, `metricNamespace`, `timeAggregation`* (Average, Count, Minimum, Maximum, Total),
  `dimensions[]` {`name`*, `operator`* (a free string; examples use `Include`), `values[]`*},
  `skipMetricValidation`.
  - Static: `operator`* (Equals, GreaterThan, GreaterThanOrEqual, LessThan, LessThanOrEqual), `threshold`* (number).
  - Dynamic: `operator`* (GreaterThan, LessThan, GreaterOrLessThan), `alertSensitivity`* (Low, Medium,
    High), `failingPeriods`* {`numberOfEvaluationPeriods`*, `minFailingPeriodsToAlert`*},
    `ignoreDataBefore` (date-time).
- Web test: `webTestId`*, `componentId`*, `failedLocationCount`*.
- PromQL: `failingPeriods` {`for`* duration}, `allOf[]` of `StaticPromQLCriteria` or
  `DynamicPromQLCriteria` with `name`*, `query`*; dynamic adds `operator`, `alertSensitivity`,
  `ignoreDataBefore`.

Secrets: none in the rule. `actions[].webHookProperties` and `customProperties` are free key/value
maps and may carry tokens (UNVERIFIED that any do; treat as sensitive).

### 3.2 scheduledQueryRules (S-SQR)

Top level: `kind` (`LogAlert` default, `SimpleLogAlert`, `LogToMetric`), `identity`, `properties`*.
`properties`: `displayName`, `description`, `severity` (0..4), `enabled`, `scopes[]` (workspace,
Application Insights component, resource ids), `evaluationFrequency`, `windowSize`,
`overrideQueryTimeRange`, `targetResourceTypes[]`, `criteria.allOf[]` of `Condition`,
`muteActionsDuration`, `actions` {`actionGroups[]` (ids), `customProperties`, `actionProperties`},
`autoMitigate`, `resolveConfiguration` {`autoResolved`, `timeToResolve`}, `skipQueryValidation`,
`checkWorkspaceAlertsStorageConfigured`, `isWorkspaceAlertsStorageConfigured` (ro),
`createdWithApiVersion` (ro), `isLegacyLogAnalyticsRule` (ro).

`Condition`: `query` (the KQL), `timeAggregation` (Count, Average, Minimum, Maximum, Total),
`metricMeasureColumn`, `resourceIdColumn`, `dimensions[]` {`name`*, `operator`* Include|Exclude,
`values[]`*}, `operator` (Equals, GreaterThan, GreaterThanOrEqual, LessThan, LessThanOrEqual,
GreaterOrLessThan), `threshold`, `criterionType` (static or dynamic), `alertSensitivity`,
`ignoreDataBefore`, `failingPeriods` {`numberOfEvaluationPeriods` default 1, `minFailingPeriodsToAlert`
default 1}, `minRecurrenceCount` (SimpleLogAlert only), `metricName` (LogToMetric only).

Semantics (SWAGGER S-SQR descriptions; LEARN L-LOG): `evaluationFrequency`, `windowSize`,
`overrideQueryTimeRange`, `muteActionsDuration`, `targetResourceTypes` and `failingPeriods` apply to
kind `LogAlert`. Evaluation frequency runs "from one minute to one day". The alert time range is
capped at two days even if the query says `ago(7d)`. A one-minute frequency rejects queries with
`search`, `union`, `take`, `ingestion_time()`, the `adx` pattern, functions calling other tables, and
`AzureDiagnostics` with a Category that has a resource-specific table (LEARN L-LOG).

Secrets: none in the rule. The KQL itself may hold literals (tokens, hostnames, user names); treat
`query` as untrusted text for output escaping (CLAUDE.md rule on untrusted text).

### 3.3 activityLogAlerts (S-ALA)

`properties`: `tenantScope` (tenant GUID, for tenant and management group rules), `scopes[]`
(resource id prefixes), `condition.allOf[]` of `AlertRuleAnyOfOrLeafCondition` (leaf: `field`,
`equals`, `containsAny[]`; or `anyOf[]` of leaves), `actions.actionGroups[]` {`actionGroupId`*,
`webhookProperties`, `actionProperties`}, `enabled`, `description`. Location is `Global`.
`field` is case-insensitive: `resourceId`, `category`, `caller`, `level`, `operationName`,
`resourceGroup`, `resourceProvider`, `status`, `subStatus`, `resourceType`, or anything beginning with
`properties` (SWAGGER S-ALA). There is no severity field and no threshold; the rule fires per event.

### 3.4 alertProcessingRules (S-APR)

`properties`: `scopes[]`*, `conditions[]` {`field` (Severity, MonitorService, MonitorCondition,
SignalType, TargetResourceType, TargetResource, TargetResourceGroup, AlertRuleId, AlertRuleName,
Description, AlertContext), `operator` (Equals, NotEquals, Contains, DoesNotContain), `values[]`},
`schedule` {`effectiveFrom`, `effectiveUntil` (ISO 8601, no zone suffix), `timeZone`, `recurrences[]`
(discriminator `recurrenceType` Daily, Weekly with `daysOfWeek[]`, Monthly with `daysOfMonth[]`; each
has `startTime`, `endTime`)}, `actions[]`* (discriminator `actionType`: `AddActionGroups` with
`actionGroupIds[]`*, or `RemoveAllActionGroups`), `description`, `enabled`.
LEARN L-APR adds: up to five values per filter; all filters are ANDed; values in one filter are ORed;
suppression removes action groups but the fired alert stays visible.

### 3.5 prometheusRuleGroups (S-PRG)

`properties`: `description`, `enabled`, `clusterName`, `scopes[]`* (the Azure Monitor workspace, and
optionally a cluster), `interval` (duration), `rules[]`* {`record`, `alert`, `enabled`,
`expression`* (PromQL), `labels`, `severity` (integer), `for` (duration), `annotations`,
`actions[]` {`actionGroupId`, `actionProperties`}, `resolveConfiguration` {`autoResolved`,
`timeToResolve`}}.

### 3.6 smartDetectorAlertRules (S-SD)

`properties`: `description`, `state`* (Enabled|Disabled), `severity`* (strings `Sev0` to `Sev4`,
unlike the integers elsewhere), `frequency`*, `detector`* {`id`*, `parameters`}, `scope[]`*,
`actionGroups`* {`customEmailSubject`, `customWebhookPayload`, `groupIds[]`*}, `throttling` {`duration`}.
`detector.name`, `description`, `supportedResourceTypes`, `parameterDefinitions` are read-only.

### 3.7 actionGroups (S-AG, definition `ActionGroup`)

`properties`: `groupShortName`*, `enabled`*, and eleven receiver arrays. Secret fields in bold.

| Array | Fields |
|---|---|
| `emailReceivers` | `name`*, `emailAddress`*, `useCommonAlertSchema`, `status` (ro) |
| `smsReceivers` | `name`*, `countryCode`*, `phoneNumber`*, `status` (ro) |
| `voiceReceivers` | `name`*, `countryCode`*, `phoneNumber`* |
| `webhookReceivers` | `name`*, **`serviceUri`***, `useCommonAlertSchema`, `useAadAuth`, `objectId`, `identifierUri`, `tenantId` |
| `itsmReceivers` | `name`*, `workspaceId`*, `connectionId`*, `ticketConfiguration`* (a string, JSON inside), `region`* |
| `azureAppPushReceivers` | `name`*, `emailAddress`* |
| `automationRunbookReceivers` | `automationAccountId`*, `runbookName`*, `webhookResourceId`*, `isGlobalRunbook`*, `name`, **`serviceUri`**, `useCommonAlertSchema` |
| `logicAppReceivers` | `name`*, `resourceId`*, **`callbackUrl`***, `useCommonAlertSchema` |
| `azureFunctionReceivers` | `name`*, `functionAppResourceId`*, `functionName`*, **`httpTriggerUrl`***, `useCommonAlertSchema` |
| `armRoleReceivers` | `name`*, `roleId`*, `useCommonAlertSchema` |
| `eventHubReceivers` | `name`*, `eventHubNameSpace`*, `eventHubName`*, `subscriptionId`*, `tenantId`, `useCommonAlertSchema` |

The webhook `serviceUri` may itself carry a token in its query string (the Logic App `callbackUrl`
carries a `sig=` signature; the official example has one, replaced in the fixtures, see
`tests/fixtures/azure/SOURCES.md`). Basic-auth credentials inside a URL (`user:pass@host`) are
possible (SYNTHETIC fixture). Email addresses and phone numbers are personal data.

### 3.8 Dashboards and workbooks

Portal dashboard (S-DASH, preview): tracked resource with `properties.lenses[].parts[]` (position and
`metadata` with part `type`, `inputs`, `settings`) and `properties.metadata`. Workbook (S-WB):
`properties` {`displayName`*, `serializedData`* (a string holding JSON), `version`, `category`*,
`tags[]`, `sourceId`, `storageUri`, `description`, `revision` (ro)}, `kind` (shared or user), `identity`.
Dashboards and workbooks are the subject of a later task; they are listed so the extractor allow-list
is complete. Dashboard JSON is not an alert object and is not mapped here.

## 4. Mapping to New Relic NRQL conditions

### 4.1 New Relic facts used (NRREF unless marked)

- A NRQL condition has at most two terms (CRITICAL and an optional WARNING). Operators: above,
  above_or_equals, below, below_or_equals, equals, not_equals.
- Signal: `aggregation_window` seconds (this repo keeps 30..3600; docs pages disagree on the upper
  bound, 2 hours or 6 hours); `aggregation_method` EVENT_FLOW, EVENT_TIMER or CADENCE; `slide_by`
  must divide the window and costs extra data points; `fill_option` NONE, STATIC, LAST_VALUE.
- Cloud integration data should use EVENT_TIMER (NRREF and NR-AM, which also gives the exact UI path).
- Loss of signal: `expiration_duration` 30..172800 s, with `open_violation_on_expiration` and
  `close_violations_on_expiration`. It needs an existing signal and does not work with nested
  aggregation.
- Threshold: `threshold_duration` (multiple of the window) with `threshold_occurrences` ALL or
  AT_LEAST_ONCE. There is no "M of N" rule.
- `count(*)` returns NULL, not 0, on an empty window; `below N` count rules do not evaluate there.
- Baseline conditions: direction UPPER_ONLY, LOWER_ONLY, UPPER_AND_LOWER; threshold in standard
  deviations 1..1000; operator above.
- Not usable in a condition query: `SINCE`, `UNTIL`, `TIMESERIES`, `COMPARE WITH`, `LIMIT`, subqueries,
  joins, several aggregations in one query.
- Chart limit, not a condition limit: `TIMESERIES` makes at most 366 buckets (REPO
  `docs/research/grafana-dashboards.md`, citing the NRQL docs). It matters to dashboard conversion, not to the
  rules below, because a condition's windows come from `aggregation_window`.
- Azure Monitor integration data (NR-MET, NR-AM): dimensional `Metric` data. Name rule: resource
  type lowercased, `/` becomes `.`, `Microsoft.` becomes `azure.`; metric name keeps its casing with
  whitespace removed and is appended: `Microsoft.Compute/virtualMachines` + `Percentage CPU` gives
  `azure.compute.virtualmachines.PercentageCPU`. Every point carries `collector.name = 'azure-monitor'`,
  `instrumentation.provider = 'azure'`, `azure.region`, `azure.resourceId`, `azure.resourceType`.
  Dimensions appear as attributes prefixed with the resource type, for example
  `azure.cache.redis.ShardId`. The same metric name can occur with and without a dimension, so
  `sum()` and `count()` can double count; filter with `IS NULL` or `IS NOT NULL`.

### 4.2 Time settings

| Azure | New Relic | Verdict |
|---|---|---|
| `windowSize` = `evaluationFrequency` (for example 5 min and 5 min) | `aggregation_window` = windowSize in seconds | exact for the arithmetic; window boundaries are aligned differently (Azure evaluates on the rule's own clock, New Relic on clock-aligned windows) |
| `windowSize` > `evaluationFrequency` (for example 5 min every 1 min) | `aggregation_window` = windowSize and `slide_by` = frequency when frequency divides the window and is at least 30 s | approximate: costs extra data points, and the first full window delays alerts |
| frequency below 30 s | not possible | manual (Azure's smallest is 1 minute, so this should not occur) |
| `windowSize` above 3600 s (PT2H, P1D) | window above this repo's 3600 s cap | manual: pages disagree on the maximum (NRREF 2.1); needs the live test listed in NRREF |
| ISO 8601 durations such as `PT15M`, `PT1H`, `P1D` | parse to seconds | exact; reject months and years (`P1M` is ambiguous) |
| `failingPeriods` M of N with M = N | `threshold_duration` = N x window, `threshold_occurrences` ALL | exact |
| `failingPeriods` M of N with M < N | `threshold_duration` = M x window, ALL | approximate and stricter: Azure counts non-consecutive violations (LEARN L-LOG: "three failures happened in the last hour"); New Relic needs them consecutive. Same trade the Dynatrace research chose |
| `overrideQueryTimeRange` (up to 2 days) | none | manual: the evaluation range and the bin size are independent in Azure, a single window in New Relic |
| `muteActionsDuration` | workflow notification handling | none in the condition; report it, optional manual (a muting rule cannot be created from this field) |
| `enabled: false` | condition `enabled` false | exact |
| `autoMitigate: true` (default) and `resolveConfiguration.autoResolved: true` | New Relic closes an event when the signal recovers | exact for the default case |
| `autoMitigate: false` (stateless metric alert) | none | approximate: Azure re-notifies on every evaluation (LEARN L-METRIC: between the frequency and double it); New Relic opens one event and notifies on its transitions. Report it |
| `resolveConfiguration.timeToResolve` | none | approximate: New Relic closes at the first healthy window; Azure keeps the alert open for the given time (UNVERIFIED exact semantics for metric alerts) |
| no data in the window | `expiration_duration` is off by default | exact when Azure also stays silent on no data (metric alerts: UNVERIFIED); to alert on silence use loss of signal with EVENT_TIMER |
| data delay from Azure Monitor | EVENT_TIMER, timer at least the window length | needed: NR-AM says so explicitly |

### 4.3 Severity

Azure severity is an integer 0..4 (smart detector rules use `Sev0`..`Sev4`; the common alert schema
says "Possible values are Sev0, Sev1, Sev2, Sev3, or Sev4", LEARN L-SCHEMA). The textual names
Critical, Error, Warning, Informational and Verbose are UNVERIFIED (not found on the fetched pages).

| Azure | Proposed New Relic | Verdict |
|---|---|---|
| 0, 1 | CRITICAL term | approximate: a proposal, owner decides |
| 2 | WARNING term only, or CRITICAL on a low-urgency policy | approximate: whether a workflow should page on WARNING is a setup choice |
| 3, 4 | WARNING term on a policy with no paging destination | approximate |

New Relic has only two priorities, so five levels collapse to two. Keep the original severity in the
condition name or description (`[Sev2] name`) and, if the owner wants, as a workflow filter on the
condition name. The collapse is lossy and the report must say so.

### 4.4 Metric alert criteria

Aggregation: Average to `average`, Minimum to `min`, Maximum to `max`, Total to `sum`, Count to
`count` (for a gauge in Azure "Count" is the number of samples in the grain; whether New Relic's
`count` over `Metric` equals it is UNVERIFIED, and the metric type that the integration stores,
"summary, gauge or count" per NR-AM, decides).
Operator: GreaterThan to above, GreaterThanOrEqual to above_or_equals, LessThan to below,
LessThanOrEqual to below_or_equals, Equals to equals.

| Shape | NRQL condition | Verdict |
|---|---|---|
| Static, one criterion, one resource | `FROM Metric SELECT <agg>(azure.<type>.<MetricName>) WHERE collector.name = 'azure-monitor' AND azure.resourceId = '<id>'` (id casing: UNVERIFIED), EVENT_TIMER | approximate: exact only if the metric arrives in New Relic with the same grain and the name rule above yields the real name; verify with a data check. The name is deterministic from the rule, but the repo rule is never to guess a name: mark "not confirmed" until seen arriving |
| Static, several resources (`MultipleResourceMultipleMetricCriteria`, scope subscription or resource group, `targetResourceType`, `targetResourceRegion`) | same query without the id filter, plus `WHERE azure.resourceId LIKE '<scope prefix>%'` and `FACET azure.resourceId`; one signal per facet | approximate: each Azure alert instance is per resource, each New Relic facet is its own signal, matching; facet limit 20,000 values; scope prefix filtering is UNVERIFIED |
| Static, dimensions Include | `AND <dim attribute> IN (...)` | approximate: attribute name is `<resource type prefix>.<DimensionName>` as in the NR-MET examples; unverified for each service |
| Static, dimension `*` (split) | `FACET <dim attribute>` in addition | approximate |
| Static, dimension Exclude | `AND <dim attribute> NOT IN (...)` | approximate |
| Static, metric has a dimension variant and a no-dimension variant | add `<dim attribute> IS NULL` | required to avoid double counting (NR-MET) |
| Two or more criteria in `allOf` | none: Azure fires only when all criteria are true (LEARN L-TYPES); a condition per criterion would become OR | manual: report each criterion converted alone, state the AND is lost |
| Dynamic threshold | baseline condition on the same query; GreaterThan to UPPER_ONLY, LessThan to LOWER_ONLY, GreaterOrLessThan to UPPER_AND_LOWER | approximate and different: Azure's model is machine learning with seasonality and needs three days and 30 samples before alerting (LEARN L-DYN); New Relic's baseline is its own model, default 3 standard deviations. `alertSensitivity` High/Medium/Low has no published equivalent in standard deviations. A guess would be invented data; leave a clearly marked default and ask the owner |
| Dynamic `ignoreDataBefore` | none | manual note |
| Dynamic `failingPeriods` | `threshold_duration` as in 4.2 | same verdicts as 4.2 |
| `WebtestLocationAvailabilityCriteria` | none | manual: availability test failures by location; New Relic Synthetics has its own conditions, no mapping from `webTestId` |
| `PromQLCriteria` (static or dynamic), `failingPeriods.for` | PromQL to NRQL with the repo's PromQL work; `for` to `threshold_duration` | approximate: only if the Prometheus data is in New Relic (section 4.7) |
| Custom metric (`metricNamespace` not a platform namespace), guest OS metric | none | none: the Azure Monitor integration does not support custom metrics (NR-AM) |
| Application Insights standard metric (scope is a `microsoft.insights/components` id) | none | manual: data comes from an APM agent or OpenTelemetry, not from this integration (the Azure Application Insights integration page exists in NR docs but was not read) |
| Unknown `odata.type` or `criterionType` | none | manual with reason "unknown criteria type" |
| Scope is not a resource id (for example a Log Analytics workspace for log-based metric alerts) | none | manual |

### 4.5 Scheduled query rules (KQL)

By kind:

| Kind | Verdict |
|---|---|
| `LogAlert` | see the pattern table |
| `SimpleLogAlert` (every matching record fires, `minRecurrenceCount`) | approximate: `SELECT count(*) FROM Log WHERE ...` above `minRecurrenceCount - 1` over the smallest window; New Relic aggregates per window, Azure fires per record |
| `LogToMetric` | none: no alert; it publishes a metric |
| unknown kind | manual |

The data must exist in New Relic. Azure logs reach New Relic through the Event Hub forwarder as `Log`
events (`SELECT * FROM Log WHERE plugin.type = 'azure'`, NR-LOG). Whether the table (`AzureDiagnostics`,
`AppRequests`, `Heartbeat`) maps to attributes of `Log` is UNVERIFIED and depends on what resources
stream to the hub. Application Insights tables and `Perf`/`Heartbeat` come from agents that New Relic
does not read.

| KQL pattern | NRQL | Verdict |
|---|---|---|
| `T | where <simple predicates> | summarize count() by bin(TimeGenerated, W)` with threshold on the count | `SELECT count(*) FROM Log WHERE <predicates>` window W | approximate: predicate translation limited to `==`, `!=`, `contains`, `has`, `startswith`, `in`, `and`, `or`; attribute names of the forwarded record are UNVERIFIED; empty window gives NULL, so use `below` rules with care |
| `... | summarize avg(x)|sum|min|max by bin(), col` | `SELECT average(x) ... FACET col` | approximate |
| `dcount(x)` | `uniqueCount(x)` | approximate (dcount is approximate in both) |
| `percentile(x, 95)` | `percentile(x, 95)` | approximate |
| number-of-results alert (`operator`, `threshold` on `Count` of rows, no summarize) | `count(*)` over window | approximate, same NULL caveat |
| `where cnt > N` after summarize | threshold of the condition | exact when the summarize is the last aggregation |
| `Heartbeat | summarize arg_max(TimeGenerated, *) by Computer | where TimeGenerated < ago(5m)` (absence) | loss of signal on an infrastructure agent signal | manual: the Azure Monitor agent heartbeat is not data New Relic receives; re-point at an infrastructure agent heartbeat |
| `Perf` counters, `InsightsMetrics`, Container Insights tables | New Relic infrastructure or Kubernetes data | manual: different agent, different attribute names |
| `AppRequests`, `AppExceptions`, `AppDependencies` | APM or OpenTelemetry data | manual |
| `AzureActivity`, `SigninLogs`, `SecurityEvent` | `Log` if forwarded | manual: attribute names unknown |
| `join`, `union`, `let`, `mv-expand`, `parse`, `extract`, regex, `make-series`, `series_*`, `arg_max`/`top`, `externaldata`, `evaluate`, `render`, cross-workspace `workspace('x')` | none | manual: a condition query cannot contain subqueries or joins (NRREF 2.3) |
| `dimensions` (split by columns, Include/Exclude) | `FACET` and `WHERE ... IN/NOT IN` | approximate |
| `metricMeasureColumn`, `resourceIdColumn` | `SELECT <agg>(<attribute>)`, `FACET <resource attribute>` | approximate |
| Dynamic threshold on a log rule | baseline condition | approximate, as for metric alerts |
| scope is Application Insights, Azure Data Explorer, Resource Graph | none | manual: the data is not in New Relic |
| `skipQueryValidation: true` | none | note only |

The conversion takes the KQL as untrusted text: strings from it go into NRQL only through the repo's
escaping functions, and the original KQL must be kept verbatim in the condition description or
the report (not as an executable fragment).

### 4.6 Activity log alerts

There is no threshold and no window: the rule fires for every matching event. The New Relic
equivalent is a count over a short window, above 0.

| Azure | New Relic | Verdict |
|---|---|---|
| `category` Administrative, ServiceHealth, ResourceHealth, Alert, Autoscale, Policy, Recommendation, Security | `SELECT count(*) FROM Log WHERE plugin.type = 'azure' AND <category filter>` above 0, window 60 s, EVENT_TIMER | approximate: the Event Hub template forwards exactly these subscription activity log categories (NR-LOG lists Administrative, Alert, Autoscale, Policy, Recommendation, Resource Health, Security, Service Health). Attribute names in the forwarded record are UNVERIFIED; events arrive minutes late; NULL on empty windows is harmless for an `above 0` rule |
| `operationName`, `status`, `subStatus`, `caller`, `level`, `resourceType`, `resourceGroup`, `resourceProvider`, `resourceId` with `equals` | `WHERE` equality on the matching log attribute | approximate (names UNVERIFIED) |
| `containsAny[]` | `WHERE attr IN (...)` for equality or an OR of `LIKE` | approximate: Azure's `containsAny` is substring |
| `anyOf` | OR group | approximate |
| `properties.*` fields (Service Health: incident type, impacted services and regions) | `WHERE` on a flattened attribute | manual: the nested path to attribute name mapping is unknown |
| `scopes[]` prefixes | `resourceId LIKE '<prefix>%'` | approximate |
| `tenantScope` (tenant or management group rules) | none | manual: the forwarder is per subscription |
| no `condition` or unknown field | none | manual |
| severity | none (the rule has none) | use a fixed default and say so |

### 4.7 Prometheus rule groups

| Azure | New Relic | Verdict |
|---|---|---|
| `alert` rule, `expression` PromQL | PromQL to NRQL translation in the repo (see `docs/research/promql-to-nrql-census.md`), condition per rule | approximate: only for expressions the translator supports; the data must reach New Relic by remote write or OTLP, which managed Prometheus does not do by itself (see `azure-datapath.md`) |
| `for` | `threshold_duration` (rounded up to a multiple of the window) | exact when it divides evenly, otherwise approximate |
| group `interval` | `aggregation_window` | approximate |
| `record` rule | none: no stored recording rules | approximate: expand the recorded expression inline where it is used; otherwise manual |
| `labels`, `annotations` | description text, never executed | note |
| `severity` (integer) | section 4.3 | approximate |
| `actions[].actionGroupId` | workflow, section 5 | as action groups |
| `resolveConfiguration` | section 4.2 | approximate |
| `enabled` false on rule or group | `enabled` false | exact |

### 4.8 Smart detector alert rules

`detector.id` names a machine-learning detector (for example failure anomalies on an Application
Insights resource). New Relic has no equivalent detector. Verdict: none. The report should say which
detector was configured and suggest a baseline error-rate condition on APM or Log data as a manual
replacement. The fixtures have the shape (`state`, `severity` as `SevN`, `frequency`, `throttling`).

### 4.9 Heartbeat and availability

Azure metric alerts do not alert on silence by default. For a heartbeat style rule the New Relic
shape is loss of signal; the Azure side shows up only as a scheduled query rule on `Heartbeat`
(section 4.5, manual).

## 5. Action group receivers to New Relic destinations

New Relic has 14 destination types: WEBHOOK, EMAIL, SERVICE_NOW, SERVICE_NOW_APP,
PAGERDUTY_ACCOUNT_INTEGRATION, PAGERDUTY_SERVICE_INTEGRATION, JIRA, SLACK, SLACK_COLLABORATION,
SLACK_LEGACY, MOBILE_PUSH, EVENT_BRIDGE, MICROSOFT_TEAMS, WORKFLOW_AUTOMATION (NRREF 1.1).
Azure has eleven receiver arrays.

| Azure receiver | New Relic destination | Verdict | What the person must supply |
|---|---|---|---|
| `emailReceivers` | EMAIL (one destination per group, comma separated; 5 addresses free, 150 paid per destination, NRREF) | exact for delivery; the message format differs | confirm recipients; UNVERIFIED whether the API creates a destination without recipient verification |
| `webhookReceivers` | WEBHOOK, URL in `secure_url` (prefix plus secret suffix) | approximate: the body is New Relic's payload template, not the Azure common alert schema (`useCommonAlertSchema`, LEARN L-SCHEMA); a receiver that parses the Azure schema breaks | the secret part of the URL; the payload template |
| `webhookReceivers` with `useAadAuth` | WEBHOOK | manual: New Relic OAuth2 is UI only (NRREF) | an authentication decision |
| `logicAppReceivers` | WEBHOOK to `callbackUrl` | approximate: the URL carries the `sig=` secret; the Logic App expects the Azure schema | the callback URL |
| `azureFunctionReceivers` | WEBHOOK to `httpTriggerUrl` | approximate: the URL may carry a function key (`code=`) | the trigger URL and key |
| `automationRunbookReceivers` | WEBHOOK to the runbook webhook `serviceUri` | approximate: the URL embeds a token that Azure shows once, so the exported value may be absent | the webhook URL (UNVERIFIED whether the list returns it) |
| `itsmReceivers` | SERVICE_NOW or SERVICE_NOW_APP if the ITSM connection is ServiceNow | manual: `connectionId` and `workspaceId` point at an ITSM connector in a Log Analytics workspace; its product is not in the action group (UNVERIFIED how to read it) | ServiceNow URL and credentials; the app must be installed on the ServiceNow side |
| `smsReceivers`, `voiceReceivers` | none | none: New Relic has no SMS or voice destination; route through PagerDuty, a webhook to an SMS gateway, or MOBILE_PUSH for people with the app | a replacement channel |
| `azureAppPushReceivers` | MOBILE_PUSH | approximate at best: New Relic mobile push is only for the user who owns the key and one per user (NRREF) | each person sets up their own |
| `armRoleReceivers` | none | none: Azure mails everyone who holds an RBAC role; resolving that needs the role assignment list and Entra lookups | a list of recipients, then EMAIL |
| `eventHubReceivers` | none (EVENT_BRIDGE is AWS) | none | a webhook to a forwarder, owner's design |
| (none; via webhook host) a Slack incoming webhook URL | SLACK | manual: SLACK needs browser OAuth (NRREF) | authorise Slack |
| (none; via webhook host) a Teams connector URL | MICROSOFT_TEAMS | manual: needs the New Relic Teams app and a security code; the Office 365 connectors are being retired (NRREF 1.1) | install the app |
| (none) PagerDuty, Opsgenie | PAGERDUTY_* | not an Azure receiver type: reached by webhook or ITSM; manual | integration key |

The webhook host heuristics for Slack and Teams are UNVERIFIED hostnames and should be a hint in the
report only. `groupShortName` (at most 12 characters in the portal; the swagger gives no limit here,
UNVERIFIED) is used by SMS and email subjects only; it is not needed in New Relic.

Workflow shape: one New Relic workflow per Azure action group set used by a rule; the workflow filter
selects the converted conditions by condition id or by policy; the destination list is the mapped
receivers. A rule that points at several action groups becomes a workflow with the union of
destinations. Create order and ids come from the existing New Relic emitters, not from this note.

## 6. Alert processing rules to muting rules

Azure's suppression (`RemoveAllActionGroups`) removes action groups from fired alerts, the alerts stay
visible (LEARN L-APR). A New Relic muting rule withholds notifications while the event is still
recorded (NRREF 1.3). That is the same behaviour.

| Azure | New Relic muting rule | Verdict |
|---|---|---|
| `RemoveAllActionGroups`, no schedule | enabled rule, always active; schedule not possible for "always" (a window and repeat are needed) | approximate: needs a very long window, or leave disabled for the owner to turn on |
| `schedule.effectiveFrom`/`effectiveUntil` with no recurrences (one-off) | `schedule.startTime`, `endTime`, `timeZone`, no repeat | exact |
| `Daily` recurrence with `startTime`/`endTime` | `repeat: DAILY`, window times from the recurrence, end date from `effectiveUntil` | exact; a window that crosses midnight (22:00 to 06:00) is a start and end datetime, which is fine |
| `Weekly` with `daysOfWeek` | `repeat: WEEKLY`, `weeklyRepeatDays` | exact |
| `Monthly` with `daysOfMonth` | `repeat: MONTHLY` repeats on the start day only | approximate for one day, manual for several days (one rule per day) |
| several recurrences in one rule (LEARN L-APR: daily evenings plus weekend days) | one muting rule per recurrence | approximate: several objects for one |
| `effectiveUntil` | `endRepeat` date | exact |
| `timeZone` | `timeZone` IANA name | exact when the Windows-style names Azure accepts are mapped to IANA names (UNVERIFIED which names Azure returns) |
| condition `AlertRuleName` Equals/Contains | `conditionName` EQUALS/CONTAINS | exact if the converted condition keeps the Azure name |
| condition `AlertRuleId` | `conditionId` | manual: the New Relic id exists only after creation |
| condition `TargetResource`, `TargetResourceGroup` | `tags.<facet attribute>` for faceted conditions | approximate: depends on the facet used in 4.4 |
| condition `TargetResourceType` | tag on the faceted attribute, or by policy | approximate |
| condition `Severity` | none unless severity went into the condition name or a policy | approximate |
| conditions `MonitorService`, `MonitorCondition` (Fired/Resolved), `SignalType`, `Description`, `AlertContext` | none | none |
| operators Equals, NotEquals, Contains, DoesNotContain | EQUALS, NOT_EQUALS, CONTAINS, NOT_CONTAINS (provider spellings, NRREF 1.3; the two New Relic pages disagree) | exact |
| `scopes` (subscription, resource groups, resources) | filter by policy, condition name list (at most 20 sub-conditions and 500 values), or facet tags | approximate |
| `AddActionGroups` | adds destinations to matching alerts | manual: a workflow with a filter for the same alerts; no muting rule involved |
| more than five values in a filter | at most five in Azure; fine in New Relic | exact |
| `enabled: false` | `enabled` false | exact |

Window-end behaviour: Azure suppression is permanent for the alerts it hid (they do not notify at the
end of the window, LEARN L-APR). New Relic's default `CLOSE_ISSUES_ON_INACTIVE` closes the muted event
so a new un-muted one can open if the problem continues; `DO_NOTHING` keeps the muted event open.
The closest match to Azure is `DO_NOTHING` (UNVERIFIED mapping, decide with the owner).

## 7. The five hardest constructs

1. Multi-criteria metric alerts (AND of several metrics). No composite condition in New Relic.
2. Dynamic thresholds. Different models, no sensitivity equivalence, `ignoreDataBefore`, 3 days of learning.
3. KQL log alerts. Needs the logs in New Relic with unknown attribute names; most KQL operators have no NRQL form; one-minute frequency rules and `arg_max` heartbeat patterns.
4. Action group receivers without a destination (SMS, voice, app push, Azure role, Event Hub) and webhook bodies in the Azure common alert schema.
5. Alert processing rules with `AddActionGroups`, monthly recurrences and conditions on fields New Relic does not carry (MonitorCondition, SignalType, AlertContext).

## 8. UNVERIFIED and not read

- Whether real list responses mask webhook `serviceUri`, `callbackUrl`, `httpTriggerUrl`.
- Attribute names of forwarded activity logs and diagnostic logs in the New Relic `Log` event.
- Exact severity names (Critical, Error, Warning, Informational, Verbose).
- Resource casing of `azure.resourceId`, and the `count` aggregation equality.
- Whether older api-versions are accepted as a fallback.
- The classic alert rules (`microsoft.insights/alertrules`), the preview-only action group APIs, Service Health alert category fields beyond the Learn text.
- Price per rule: see `docs/user/azure-credentials.md` ("not checked").
- All live behaviour: nothing was called.

## 9. Fixtures

`tests/fixtures/azure/` holds the official examples and SYNTHETIC hostile cases; the mapping of every
file to its source URL is in `tests/fixtures/azure/SOURCES.md`. Example files wrap a request and its
responses; the resource is in `responses.200.body` or in `parameters`.
