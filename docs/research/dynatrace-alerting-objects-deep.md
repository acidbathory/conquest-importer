# Dynatrace alerting objects (classic and hybrid tenants): exact shapes and how they map to New Relic

Written 2026-11-09 from public documentation and public Dynatrace repositories only. No Dynatrace tenant,
token or API was called. No account data was sent anywhere. This note extends
`docs/research/dynatrace-native-coverage.md` (called "the earlier note" below); where the two disagree,
this one is newer and says why.

Evidence marks on every claim:

- DOCS Kn: read on a public Dynatrace or New Relic documentation page on 2026-11-09; the URL is in the Sources table in section 11.
- REPO path: read in this repository, or in a public Dynatrace GitHub repository (marked REPO-DT with the file).
- UNVERIFIED: from memory or inferred. Do not treat it as fact. The full list is in section 10.

## 0. Corrections to the earlier note, and the five findings that matter most

1. The metric event sample in section 4.2 of the earlier note used the keys `violationType`,
   `violationWindowInMinutes` and `severity`. Those keys are not in the schema table. The real keys are
   `modelProperties.alertCondition`, `violatingSamples`, `samples` (the sliding window), `dealertingSamples`,
   `alertOnNoData`, and `eventTemplate.eventType` (DOCS K1a; REPO-DT terraform-provider-dynatrace
   `metricevents/settings/model_properties.go`, which gives the JSON tags). The sample came from a Dynatrace
   AI-skills repository whose own text says it shows "an older API representation" (DOCS K22). Do not build
   fixtures from that sample.
2. The sliding window key is `samples` in a metric event but `slidingWindow` in a Davis anomaly detector input
   list. Two schemas, two names, same meaning (DOCS K1a, K4).
3. Davis anomaly detectors are Settings 2.0 objects of schema `builtin:davis.anomaly-detectors` (DOCS K4, K1b).
   They are not stored by the Anomaly Detection app; the app "has moved to Settings" and only keeps user
   preferences in app state (DOCS K6). The owner's 404 on this schema is therefore not explained by a different
   storage location. Section 3 lists the three likeliest causes and the probes that separate them.
4. The Davis detector API tutorial uses the host `https://{env}.apps.dynatrace.com/platform/classic/environment-api/v2/settings/objects`
   with a platform token or OAuth client, and states that "Anomaly Detection doesn't work on classic endpoints"
   (DOCS K4). A tenant that is only classic (`*.live.dynatrace.com` or Managed) cannot hold Davis detectors at all.
   That is the main reason classic and hybrid customers have metric events and not detectors.
5. The Settings API masks every property whose type is `secret` in GET responses (DOCS K2). The webhook `url`
   is a plain text property, not a secret, so it is returned in full unless the author ticked
   `urlContainsSecret`, in which case the value lives in `secretUrl` (DOCS K1c). A webhook URL can therefore
   carry a token in clear text. Treat every webhook `url`, every header `value` where `secret` is false, and
   every Trello `applicationKey` as sensitive even though the schema does not call them secrets.

## 1. Access model: which API, host, token and scope reads each object

### 1.1 Hosts and tokens

| Tenant kind | Host | Header | Notes | Evidence |
|---|---|---|---|---|
| SaaS classic | `https://{env}.live.dynatrace.com/api/v2/settings/...` | `Authorization: Api-Token <dt0c01...>` | scope `settings.read` | DOCS K3 |
| SaaS classic or hybrid, platform token | same path on `.live.` host | `Authorization: Bearer <dt0s16...>` | scope `settings:objects:read` (page lists "Platform Token / OAuth: Required scope: settings:objects:read") | DOCS K3 |
| Managed | `https://{domain}/e/{env}/api/v2/settings/...` | `Api-Token` | same scope | DOCS K1a (schema page URLs) |
| Environment ActiveGate | `https://{activegate}/e/{env}/api/v2/settings/...` | `Api-Token` | same scope | DOCS K1a |
| Platform (`.apps.`) | `https://{env}.apps.dynatrace.com/platform/classic/environment-api/v2/settings/objects` | `Bearer` | scopes `settings:schemas:read`, `settings:objects:read` | DOCS K4 |
| Platform native settings service | `https://{env}.apps.dynatrace.com/platform/settings/v1/objects` | `Bearer` | used by the live read in the earlier note | REPO docs/research/dynatrace-native-coverage.md |

The two Platform settings paths are different services (the second is named "Settings service" in the earlier
note, the first is a proxy to the classic Environment API). Whether both expose the same schemas is not
documented. On the owner's tenant the second path listed 313 schemas (REPO earlier note); the docs index lists
about 360 builtin schema pages (DOCS K21, count of `builtin-*` links in the sitemap-linked index). The
difference may be tenant release, or may be the path. This is question Q1 in section 9.

Read call shape for any object (DOCS K3):

- `GET /api/v2/settings/objects?schemaIds=<id>[,<id>]&fields=objectId,schemaId,scope,summary,modified,value&pageSize=500`
- At least one of `schemaIds` or `scopes` is required on the first page. Default page size 100, maximum 500.
- When `nextPageKey` is sent, every other query parameter must be omitted.
- `scope` of an object is a string; `environment` means tenant-wide. A scope of `HOST-...` or `HOST_GROUP-...` means
  the object overrides one entity (relevant for anomaly detection schemas; `scopes=environment` does not match them).
- `adminAccess=true` makes the call act as owner of all objects and needs `settings:objects:admin` (DOCS K3).
  Newer schemas carry `owner` and `resourceContext` (permissions); a normal user may not see objects owned by others
  (DOCS K3 response fields; the visibility rule itself is UNVERIFIED).

### 1.2 One table for every object

| Object | Settings schemaId | Classic API (deprecated or alternative) | Read scope | Evidence |
|---|---|---|---|---|
| Metric events ("Custom events for alerting") | `builtin:anomaly-detection.metric-events` | none found in the current Config API list | `settings.read` / `settings:objects:read` | DOCS K1a, K13, K26 |
| Davis anomaly detector (Platform) | `builtin:davis.anomaly-detectors` | none; Platform only | `settings:schemas:read`, `settings:objects:read` (+ Grail read scopes to preview) | DOCS K4, K6 |
| Alerting profile | `builtin:alerting.profile` | Config API alerting profiles, "deprecated, use Settings API" | `settings.read`; classic `ReadConfig` | DOCS K15, REPO-DT TF doc |
| Problem notification | `builtin:problem.notifications` | `GET /api/config/v1/notifications[/{id}]`, scope `ReadConfig`, "deprecated" | `settings.read` | DOCS K14 |
| Maintenance window, classic | `builtin:alerting.maintenance-window` | Config API maintenance windows, "deprecated" | `settings.read`; classic `ReadConfig` | DOCS K16 |
| Maintenance window, newer | `builtin:maintenance-windows` (DQL filter) | none | `settings.read` | DOCS K1d |
| Management zone | `builtin:management-zones` | Config API management zones | `settings.read` | DOCS K1e |
| Auto tag | `builtin:tags.auto-tagging` | Config API automatically applied tags | `settings.read` | DOCS K1f |
| Anomaly detection, services | `builtin:anomaly-detection.services` | Config API anomaly detection, Services | `settings.read` | DOCS K1g, K27 |
| Anomaly detection, hosts / disks / VMware / AWS / databases | `builtin:anomaly-detection.infrastructure-hosts`, `.infrastructure-disks`, `.infrastructure-disks.per-disk-override`, `.disk-rules`, `.infrastructure-vmware`, `.infrastructure-aws`, `.databases` | Config API anomaly detection (Hosts, VMware, AWS, Disk events, Database services) | `settings.read` | DOCS K1h, K27 |
| Anomaly detection, Kubernetes | `builtin:anomaly-detection.kubernetes-cluster`, `-node`, `-namespace`, `-workload`, `-pvc` | none | `settings.read` | DOCS K1i |
| Anomaly detection, RUM | `builtin:anomaly-detection.rum-web`, `rum-mobile`, `rum-custom` (+ crash-rate variants) | Config API anomaly detection, Applications | `settings.read` | DOCS K1j, K27 |
| Other built-in detection settings | `builtin:anomaly-detection.holiday-aware-baseline`, `.frequent-issues`, `builtin:alerting.connectivity-alerts`, `builtin:availability.process-group-alerting`, `builtin:infrastructure.disk.edge.anomaly-detectors` | various | `settings.read` | DOCS K1k |
| Log event (alert from a log query) | `builtin:logmonitoring.log-events` | none | `settings.read` | DOCS K1l |
| Log metric | `builtin:logmonitoring.schemaless-log-metric` | none | `settings.read` | DOCS K1m |
| Calculated service metric | no Settings schema found | Config API calculated metrics, Service; `ReadConfig` | `ReadConfig` | DOCS K17, REPO-DT TF doc |
| SLO (classic) | `builtin:monitoring.slo` | `GET /api/v2/slo[/{id}]` | `slo.read`; platform token `settings:objects:read` | DOCS K1n, K18 |
| SLO (Platform) | not a settings object | `GET /platform/slo/v1/slos` | `slo:slos:read` | REPO earlier note; DOCS K18b |
| Davis analyzer definitions | not alert storage | `GET /platform/davis/analyzers/v1/analyzers` | `davis:analyzers:read` | DOCS K19 |

Deprecation: the three Config API pages for notifications, alerting profiles and maintenance windows each open
with "This API is deprecated. Use the Settings API instead" (DOCS K14, K15, K16). The extractor should read the
Settings schemas and use Config API only as a fallback on old Managed clusters (fallback need: UNVERIFIED).

Secret masking: "The API masks properties of type secret in GET responses" (DOCS K2). The classic v1 API
behaves the same way: Slack `url` is returned as `null`, and an `Authorization` header value is returned as `null`
(DOCS T1 notification doc text: "GET requests return this field with the null value"). What a masked value
looks like in the Settings JSON (null, empty string, or a placeholder string) is not documented: UNVERIFIED.
The loader must accept all three and never treat any of them as a credential.

## 2. Object by object: the full value shape

Every Settings object comes back wrapped. The wrapper (DOCS K3 response fields) has `objectId`, `schemaId`,
`schemaVersion`, `scope`, `summary`, `created`, `modified`, `createdBy`, `modifiedBy`, `author` (deprecated),
`externalId`, `owner`, `resourceContext`, `updateToken`, `value`. The shapes below are the `value` object.
Field names are quoted from the schema table pages (DOCS K1) and, for metric events and detectors, confirmed
against the JSON tags in the Terraform provider source (REPO-DT).

### 2.1 Metric events (`builtin:anomaly-detection.metric-events`)

DOCS K1a, K13, T1 `metric_events.md`, REPO-DT `metricevents/settings/*.go`.

Top level:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `enabled` | boolean | yes | Active or not. |
| `summary` | text | yes | Display name of the event. This is the human name; there is no `name` field. |
| `queryDefinition` | object | yes | What to measure. |
| `modelProperties` | object | yes | Monitoring strategy and sample counts. |
| `eventTemplate` | object | yes | What event is raised. |
| `eventEntityDimensionKey` | text | no | Preferred entity type for the raised event, for example `dt.entity.host`. |
| `legacyId` | text | no | Id of the config in the old Config API. |

`queryDefinition`:

| Key | Type | Required | Meaning |
|---|---|---|---|
| `type` | enum `METRIC_KEY` or `METRIC_SELECTOR` | yes | Key mode allows only static thresholds ("Metric-key-based query definitions only support static thresholds", DOCS K1a). Selector mode allows all three strategies (DOCS K13). |
| `metricKey` | text | key mode | For example `builtin:host.disk.usedPct`. |
| `aggregation` | enum `MIN MAX SUM COUNT AVG VALUE MEDIAN PERCENTILE90` | key mode | Time aggregation per one-minute sample. `PERCENTILE90` is the only percentile offered. |
| `metricSelector` | text | selector mode | A full metric selector (section 5). Contains its own aggregation. |
| `managementZone` | text | no | Id of a management zone ("legacy_id"); limits data to that zone (DOCS K13). |
| `queryOffset` | integer | no | "Minute offset of sliding evaluation window for metrics with latency". |
| `entityFilter` | object | yes | `{ "dimensionKey": "dt.entity.host", "conditions": [ ... ] }`; `dimensionKey` is optional. |
| `dimensionFilter` | array | yes (may be empty) | Items `{ "dimensionKey", "operator", "dimensionValue" }`. Operator enum: `EQUALS DOES_NOT_EQUAL STARTS_WITH DOES_NOT_START_WITH CONTAINS_CASE_SENSITIVE DOES_NOT_CONTAIN_CASE_SENSITIVE`. |

`entityFilter.conditions[]` items: `{ "type", "operator", "value" }`.
- `type` enum: `NAME ENTITY_ID MANAGEMENT_ZONE TAG HOST_NAME HOST_GROUP_NAME PROCESS_GROUP_NAME PROCESS_GROUP_ID CUSTOM_DEVICE_GROUP_NAME`.
- `operator` enum: `EQUALS DOES_NOT_EQUAL STARTS_WITH DOES_NOT_START_WITH CONTAINS_CASE_SENSITIVE DOES_NOT_CONTAIN_CASE_SENSITIVE CONTAINS_CASE_INSENSITIVE DOES_NOT_CONTAIN_CASE_INSENSITIVE`.
- How several conditions combine (AND or OR) is not stated on the schema page: UNVERIFIED. Assume AND and flag it.

`modelProperties`:

| Key | Type | Used by | Meaning |
|---|---|---|---|
| `type` | enum `STATIC_THRESHOLD AUTO_ADAPTIVE_THRESHOLD SEASONAL_BASELINE` | all | Strategy. |
| `threshold` | float | static | Raise when violated. In the unit of the metric (base unit). |
| `signalFluctuation` | float | auto-adaptive | "How many times the signal fluctuation is added to the baseline" (DOCS K1a). In the Davis detector the same idea is `numberOfSignalFluctuations`, default 1 (DOCS K4). |
| `tolerance` | float | seasonal | Band width; higher means fewer events. Range 0.1 to 10, default 4 (DOCS K8). |
| `alertCondition` | enum `ABOVE BELOW OUTSIDE` | all | `OUTSIDE` only for the two baseline strategies (DOCS K4 states this for detectors; same rule for events is UNVERIFIED but the Terraform enum is shared). |
| `alertOnNoData` | boolean | all | Missing samples count as violating samples (see 6.4). |
| `violatingSamples` | integer | all | How many one-minute samples in the window must violate. Maximum 60 (DOCS K4 for detectors; events: UNVERIFIED). |
| `samples` | integer | all | Size of the sliding window in one-minute samples. This is the sliding window. |
| `dealertingSamples` | integer | all | How many samples must be back to normal to close the event. |

Fields not relevant to the chosen `type` are omitted in the Terraform struct (`omitempty`); whether the API sends
them as `null` or leaves them out is UNVERIFIED. Models must treat both as absent.

`eventTemplate`:

| Key | Type | Meaning |
|---|---|---|
| `title` | text | Event title; supports `{` placeholders. |
| `description` | text | Event description; supports placeholders. |
| `eventType` | enum | `INFO ERROR AVAILABILITY SLOWDOWN RESOURCE CUSTOM_ALERT CUSTOM_ANNOTATION CUSTOM_CONFIGURATION CUSTOM_DEPLOYMENT MARKED_FOR_TERMINATION WARNING` (DOCS K1a). |
| `davisMerge` | boolean | Davis may merge the event into an existing problem. |
| `metadata` | array | Items `{ "metadataKey", "metadataValue" }`; extra event properties, key hint prefix `dt.`. |

Limits: up to 10,000 metric event configurations (DOCS K13); auto-adaptive events limited to 100 per environment
(DOCS K7: "limit of 100 metric event configurations per environment"; the page is about auto-adaptive thresholds).

Defaults the UI uses: 3 of 5 one-minute samples to open, 5 samples to close (DOCS K5, K9). Whether the API
fills defaults is UNVERIFIED; the fields are marked Required, so exports should carry them.

### 2.2 Alerting profiles (`builtin:alerting.profile`)

DOCS K1o, T1 `alerting.md`.

| Key | Type | Meaning |
|---|---|---|
| `name` | text, required | Profile name. |
| `managementZone` | setting reference, optional | One management zone id. Docs recommend manual tags instead. Empty string or absent means all. |
| `severityRules` | set, required, max 100 | Rules, see below. |
| `eventFilters` | set, required, max 100 | Filters, see below. |

`severityRules[]`: `severityLevel` enum `AVAILABILITY CUSTOM_ALERT ERRORS MONITORING_UNAVAILABLE PERFORMANCE RESOURCE_CONTENTION`;
`delayInMinutes` integer ("Send a notification if a problem remains open longer than X minutes"); `tagFilterIncludeMode`
enum `NONE INCLUDE_ANY INCLUDE_ALL`; `tagFilter` set of tag strings (Terraform examples write them as `key:value`,
for example `EnvironmentA:production`, DOCS T1; the context prefix form `[Context]key:value` is UNVERIFIED here
but is the entity selector tag form, DOCS K12).

`eventFilters[]`: `source` enum `PREDEFINED CUSTOM`; `predefinedFilter` `{ "eventType": <one of ~70 enum values>, "negate": bool }`;
`customFilter` `{ "titleFilter", "descriptionFilter", "metadataFilter" }` where a text filter is
`{ "operator": BEGINS_WITH|ENDS_WITH|CONTAINS|REGEX_MATCHES|STRING_EQUALS, "value", "negate", "enabled", "caseSensitive" }`
and `metadataFilter.metadataFilterItems[]` is `{ "metadataKey", "metadataValue", "negate" }`, max 20.

Semantics (DOCS T1 alerting resource description): across event filters, all negated filters use AND, all
non-negated filters use OR, and the two groups are combined with AND. If a profile has both severity rules and
event filters, AND applies between them. Severity rules are independent of each other.

Important: severity levels here are problem categories, not priorities. A metric event of `eventType` `SLOWDOWN`
opens a PERFORMANCE problem. The mapping event type to category is only documented in a Dynatrace AI-skills
repository for Davis detectors (REPO-DT, DOCS K22): `AVAILABILITY_EVENT` to AVAILABILITY, `ERROR_EVENT` to ERROR,
`PERFORMANCE_EVENT` to SLOWDOWN, `RESOURCE_CONTENTION_EVENT` to RESOURCE_CONTENTION, `CUSTOM_ALERT` to CUSTOM_ALERT,
`CUSTOM_INFO` to INFO and opens no problem. The classic enum names differ (`ERRORS` in profiles, `ERROR` in
notification placeholders); an alias table is needed. The classic event type to category mapping for metric
events (`SLOWDOWN`, `RESOURCE`, `ERROR`) is inferred by name only: UNVERIFIED.

### 2.3 Problem notifications (`builtin:problem.notifications`)

DOCS K1c. One object per integration. Shared keys: `enabled`, `type` (enum `EMAIL SLACK JIRA ANSIBLETOWER OPS_GENIE PAGER_DUTY VICTOROPS WEBHOOK XMATTERS TRELLO SERVICE_NOW`),
`displayName`, `alertingProfile` (object id of the profile), and exactly one nested object named after the type.
Nested object names: `emailNotification`, `slackNotification`, `jiraNotification`, `ansibleTowerNotification`,
`opsGenieNotification`, `pagerDutyNotification`, `victorOpsNotification`, `webHookNotification`,
`xMattersNotification`, `trelloNotification`, `serviceNowNotification`. The other nested objects are present or
null: UNVERIFIED which.

| Type | Fields (type: s = secret, t = text, b = boolean) | Secret fields (masked in GET) | Sensitive but returned |
|---|---|---|---|
| EMAIL | `toRecipients` set, `ccRecipients` set, `bccRecipients` set, `subject` t, `body` t, `notifyClosedProblems` b | none | recipient addresses (personal data) |
| SLACK | `url` s, `channel` t, `message` t | `url` | channel name |
| JIRA | `url` t, `username` t, `apiToken` s, `projectKey` t, `issueType` t, `summary` t, `description` t | `apiToken` | `url`, `username` |
| ANSIBLETOWER | `jobTemplateURL` t, `acceptAnyCertificate` b, `username` t, `password` s, `customMessage` t | `password` | `username`, URL |
| OPS_GENIE | `apiKey` s, `domain` t, `message` t | `apiKey` | `domain` (region host) |
| PAGER_DUTY | `account` t, `serviceName` t, `serviceApiKey` s | `serviceApiKey` | account and service names |
| VICTOROPS | `apiKey` s, `routingKey` t, `message` t | `apiKey` | `routingKey` is text, not secret |
| WEBHOOK | `urlContainsSecret` b (optional), `url` t, `secretUrl` s, `acceptAnyCertificate` b, `notifyEventMergesEnabled` b, `notifyClosedProblems` b, `useOAuth2` b (optional), `oAuth2Credentials` {`accessTokenUrl` t, `clientId` t, `clientSecret` s, `scope` t, `authenticateViaRequestHeader` b}, `timeoutInSeconds` int (optional), `headers[]` {`name` t, `secret` b, `value` t, `secretValue` s}, `payload` t | `secretUrl`, `clientSecret`, each `headers[].secretValue` | `url` (when not flagged secret), `clientId`, header `value` when `secret` is false |
| XMATTERS | `url` t, `acceptAnyCertificate` b, `headers[]` as webhook, `payload` t | header `secretValue` | `url` (an xMatters trigger URL commonly embeds a key; treat as sensitive) |
| TRELLO | `applicationKey` t, `authorizationToken` s, `boardId` t, `listId` t, `resolvedListId` t, `text` t, `description` t | `authorizationToken` | `applicationKey` is text, not secret |
| SERVICE_NOW | `instanceName` t or `url` t (mutually exclusive), `username` t, `password` s, `message` t, `sendIncidents` b, `sendEvents` b, `formatProblemDetailsAsText` b (optional), `timeoutInSeconds` int (optional) | `password` | `username` |

(DOCS K1c for the field list and the secret type; the "sensitive but returned" column is my classification and is
UNVERIFIED as to what the API returns.) The old v1 API also lists `HIPCHAT` in the type enum (DOCS T1 notification doc); it is a retired product.

Placeholders in message templates (DOCS K1c): `{ImpactedEntity}`, `{ImpactedEntityNames}`, `{NamesOfImpactedEntities}`,
`{PID}`, `{ProblemID}`, `{ProblemImpact}`, `{ProblemSeverity}`, `{ProblemTitle}`, `{ProblemURL}`, `{State}`,
`{Tags}` and `{Tags[key]}`, `{ImpactedEntities}`, `{ProblemDetailsText|HTML|Markdown|JSON|JSONv2}`. Which
placeholders are valid depends on the type (OpsGenie has only a handful).

Delivery semantics that have no New Relic twin: `notifyClosedProblems` (send on resolve), `notifyEventMergesEnabled`
(send when new events merge into an existing problem), `useOAuth2`, `timeoutInSeconds`, `acceptAnyCertificate`.

### 2.4 Maintenance windows

Two schemas can exist at once.

Classic `builtin:alerting.maintenance-window` (DOCS K1p):

- `enabled`; `generalProperties` { `name`, `description`, `maintenanceType` PLANNED|UNPLANNED, `suppression`
  DETECT_PROBLEMS_AND_ALERT | DETECT_PROBLEMS_DONT_ALERT | DONT_DETECT_PROBLEMS, `disableSyntheticMonitorExecution` b }.
- `schedule` { `scheduleType` ONCE|DAILY|WEEKLY|MONTHLY, and one of `onceRecurrence` {`startTime`, `endTime`, `timeZone`}
  (local date time + zone), `dailyRecurrence` / `weeklyRecurrence` {`dayOfWeek`} / `monthlyRecurrence` {`dayOfMonth`}
  each with `timeWindow` {`startTime`, `endTime`, `timeZone`} and `recurrenceRange` {`scheduleStartDate`, `scheduleEndDate`} }.
  If `dayOfMonth` does not exist in a month, the window runs on the last day of the month (DOCS K1p).
- `filters[]` (each filter is OR-ed with the others): `entityType` t, `entityId` t, `entityTags` set (all must
  match), `managementZones` set (all must match). No filters means the whole environment (DOCS K1p).

Older Config API shape (DOCS T1 `maintenance_window.md`, deprecated): `type`, `suppression`, `name`, `schedule`
{`recurrenceType`, `start`, `end` in `yyyy-MM-dd HH:mm`, `zoneId`}, `suppressSynthMonExec`, `scope.matches[]`
{`tagCombination`, `tags[]` {`key`, `context`}}.

Newer `builtin:maintenance-windows` (DOCS K1d): `name`, `enabled`, `description`, `filter` (a DQL filter text,
required), `autoDelete` b, `schedule` {`trigger` {`type` `time`|`once`, `recurring` {`time`, `earliestStart`, `until`, `rule`}
or `once` {`date`}}, `timezone`, `duration` minutes}, `objectScopes` {`syntheticMonitors` {`disableSyntheticMonitors`,
`disableSyntheticMonitorFilter`}, `suppressProblems` b}. `rule` is "Reference to rule which specifies on which days
instances should be created"; its text format (an RRULE?) is UNVERIFIED. The owner's tenant has this schema with 0
objects (REPO earlier note).

### 2.5 Management zones (`builtin:management-zones`)

DOCS K1e, T1 `management_zone_v2.md`. `name`, `description`, `rules[]`. A rule has `enabled`, `type`
(`ME` attribute rule, `DIMENSION` metric dimension rule, `SELECTOR` entity selector rule), `entitySelector` (for
`SELECTOR`), `attributeRule`, `dimensionRule`. `attributeRule`: `entityType` (about 35 values: SERVICE, PROCESS_GROUP,
HOST, CUSTOM_DEVICE, ... KUBERNETES_CLUSTER, CLOUD_APPLICATION_NAMESPACE, AZURE, ...), `conditions[]`
(`key`, `operator`, plus a typed value such as `stringValue`, `enumValue`, `entityId`, `caseSensitive`), and
propagation flags `serviceToHostPropagation`, `serviceToPGPropagation`, `pgToHostPropagation`,
`pgToServicePropagation`, `hostToPGPropagation`, `customDeviceGroupToCustomDevicePropagation`,
`azureToServicePropagation` and more. A zone is a rule-driven set of entities that is applied by background jobs
that "might be delayed" (DOCS K1e). Dimension rules are not supported for metric events (DOCS K13).

### 2.6 Auto tags (`builtin:tags.auto-tagging`)

DOCS K1f, T1 `autotag_v2.md`. `name`, `description`, `rules[]`. A rule: `enabled`, `valueFormat` (with placeholders such
as `{ProcessGroup:Environment:keptn_stage}`), `valueNormalization` (`Leave text as-is`, `To upper case`, `To lower case`),
`type` (`ME` or `SELECTOR`), `entitySelector`, `attributeRule` {`entityType`, `conditions[]`, propagation flags}. Tags
are applied by periodic background jobs, "removed automatically once an entity no longer matches" (DOCS K1f).

### 2.7 Anomaly detection for built-in entity types

These are tuning settings for Davis' automatic detection, not user alerts. They are always present with defaults,
and an export contains one object per scope (environment, plus overrides per host, disk, service). The converter
should read them only to report non-default tuning.

Services (`builtin:anomaly-detection.services`, DOCS K1g, K20): four detectors, each with `enabled`.
- `responseTime`: `detectionMode` `auto` or `fixed`. Auto: `responseTimeAll` {`degradationMilliseconds`,
  `degradationPercent`}, `responseTimeSlowest` {`slowestDegradationMilliseconds`, `slowestDegradationPercent`},
  `overAlertingProtection` {`requestsPerMinute`, `minutesAbnormalState`}. Fixed: `responseTimeAll`
  {`degradationMilliseconds`}, `responseTimeSlowest` {`slowestDegradationMilliseconds`}, `sensitivity` `low|medium|high`.
- `failureRate`: auto {`absoluteIncrease`, `relativeIncrease`, `overAlertingProtection`}; fixed {`threshold`, `overAlertingProtection`, `sensitivity`}.
- `loadDrops`: `loadDropPercent`, `minutesAbnormalState`. `loadSpikes`: `loadSpikePercent`, `minutesAbnormalState`.
- Baselining: "the default reference period is the past 7 days" (DOCS K20). Load detection learns "over an observation
  period of one week". The numeric defaults for each field are not on the pages I read: UNVERIFIED.

Hosts (`builtin:anomaly-detection.infrastructure-hosts`, DOCS K1h): `host` {`connectionLostDetection`,
`highCpuSaturationDetection`, `highSystemLoadDetection` (AIX only), `highMemoryDetection`, `highGcActivityDetection`,
`outOfMemoryDetection`, `outOfThreadsDetection`} and `network` {dropped packets, errors, high network, TCP problems,
retransmission}. Each has `enabled`, `detectionMode` `auto|custom`, `customThresholds` (for example
`highCpuSaturationDetection.customThresholds.cpuSaturation` integer percent, `highMemoryDetection.customThresholds`
`usedMemoryPercentageWindows`, `usedMemoryPercentageNonWindows`, `pageFaultsPerSecondWindows`,
`pageFaultsPerSecondNonWindows`) and `eventThresholds` {`violatingSamples`, `violatingEvaluationWindow`,
`dealertingSamples`, `dealertingEvaluationWindow`}. These samples are 10-second samples, not one-minute samples
(DOCS K1h: "The number of 10-second samples ..."). That is a trap when comparing with metric events.

Disks (`builtin:anomaly-detection.infrastructure-disks.per-disk-override`, DOCS K1h2): `diskLowSpaceDetection`
{`enabled`, `detectionMode`, `customThresholds.freeSpacePercentage`}, `diskSlowWritesAndReadsDetection`
(`writeAndReadTime`), `diskLowInodesDetection` (`freeInodesPercentage`), each "in 3 out of 5 samples", with
`override*` booleans. Disk events with OneAgent edge detection use `builtin:infrastructure.disk.edge.anomaly-detectors`
(DOCS K1k, K22): the agent detects locally, there is no query.

VMware, AWS, databases, Kubernetes, RUM: same pattern (`enabled`, `detectionMode`, `customThresholds`),
for example AWS has `ec2CandidateHighCpuDetection`, `rdsHighCpuDetection`, `rdsLowStorageDetection`,
`elbHighConnectionErrorsDetection`, `lambdaHighErrorRateDetection` (DOCS K1h3, "Alert if the condition is met in 3 out
of 5 samples").

Auto-adaptive defaults: the "auto" mode learns a baseline from the previous seven days (DOCS K7, K20); no numeric
default is documented on the pages read.

### 2.8 Custom events for alerting

"Custom events for alerting" is the UI name of metric events: the settings page path is
`#settings/anomalydetection/metricevents` (DOCS K1q, the `builtin:custom-metrics` page text "To create a custom event
based on a custom metric, go to Custom events for alerting"). Do not confuse it with custom events pushed by the Events API
v2 (`POST /api/v2/events/ingest`, event types such as `CUSTOM_ALERT`), which are runtime data that an alerting profile
event filter can match, not configuration (DOCS K22 category 5).

Log events (`builtin:logmonitoring.log-events`, DOCS K1l): `enabled`, `summary`, `query` (the matcher, a log query
text), `eventTemplate` (same shape as metric events: `title`, `description`, `eventType`, `davisMerge`, `metadata`).
This is a log-pattern alert, and is the one classic alert type that can go to a New Relic condition on `Log`
(section 4).

### 2.9 Calculated service metrics and log metrics

Calculated service metrics (Config API only, scope `ReadConfig`, DOCS K17, T1 `calculated_service_metric.md`). Terraform shape:
`name`, `enabled`, `metric_key` (`calc:service.<name>`), `unit` (for example `MILLI_SECOND_PER_MINUTE`), `management_zones`,
`conditions` (attribute and comparison pairs such as HTTP method in [POST, GET]), `metric_definition`
{`metric` (`REQUEST_ATTRIBUTE`, `RESPONSE_TIME`, ...), `request_attribute`}, optional `dimension_definition`.
JSON key names of the API (camelCase) are UNVERIFIED; the page I fetched for the endpoint returned 404. These metrics
only exist because OneAgent traces requests in Dynatrace, so New Relic has nothing to read.

Log metrics (`builtin:logmonitoring.schemaless-log-metric`, DOCS K1m, T1 `log_metrics.md`): `enabled`, `key` (the metric
key), `query` (matcher), `measure` (`OCCURRENCE` or `ATTRIBUTE`), `measureAttribute`, `dimensions` set. Example (T1):
`key log.x`, `query matchesPhrase(content, "terratest")`, `measure ATTRIBUTE`, `measureAttribute dt.entity.host`.
A metric on `OCCURRENCE` is a count of matching lines per minute, so an alert on it is a log-count alert. Converting
means reading the metric's query, not the metric key.

### 2.10 SLOs

Classic `builtin:monitoring.slo` and `GET /api/v2/slo` (DOCS K1n, K18): `enabled`, `name`, `customDescription`
(`description` in the v2 API), `metricName`, `metricExpression` (a percentage-valued metric expression, see section 5),
`evaluationType` `AGGREGATE`, `filter` (an entity selector, max 1,000 characters in the v2 API), `evaluationWindow`
(`-1h`, `-1w`, `-2d to now`, `-1d/d to now/d`), `targetSuccess`, `targetWarning`, `errorBudgetBurnRate`
{`burnRateVisualizationEnabled`, `fastBurnThreshold`}. The v2 API response also has computed fields
(`evaluatedPercentage`, `errorBudget`, `status`) that must not be treated as configuration. Deprecated fields
`metricNumerator`, `metricDenominator`, `useRateMetric` can appear on old SLOs (DOCS K18).
Platform SLOs: `/platform/slo/v1/slos`, already read by the importer (REPO src/importer/sources/dynatrace/extract.py, `SLOS_PATH`).

## 3. How Davis anomaly detectors are stored on Platform tenants

### 3.1 What the documentation says

- Storage: Settings 2.0, schema `builtin:davis.anomaly-detectors`, scope `environment` in the schema table (DOCS K1b)
  and in the API tutorial (DOCS K4). A Dynatrace AI-skills repository writes `"scope": "tenant"` in its examples
  (REPO-DT, DOCS K22); the schema page says `environment`. The extractor should read by `schemaIds` and never filter by scope.
- Read: `GET https://{env}.apps.dynatrace.com/platform/classic/environment-api/v2/settings/objects?schemaIds=builtin:davis.anomaly-detectors`
  with `Authorization: Bearer <platform token>` and scopes `settings:schemas:read` and `settings:objects:read` (DOCS K4).
  Running a query preview needs Grail read scopes such as `storage:metrics:read` (DOCS K4); reading the object does not.
- Write needs `settings:objects:write`; `iam:service-users:use` for service users; without a service user, `davis:analyzers:execute` (DOCS K4).
- The Anomaly Detection app lists "all anomaly detection configurations" and, from Dynatrace version 1.344, "custom alerts
  have moved to Settings" (DOCS K6). Its extra permissions are `davis:analyzers:read` (list analyzers), `davis:analyzers:execute`
  (threshold suggestion), `state:user-app-states:*` (user settings, not alerts), `storage:events:read` (DOCS K6).
- Classic endpoints cannot hold them: "Anomaly Detection doesn't work on classic endpoints" and "Classic authentication
  methods like username and password won't work" (DOCS K4).
- Davis Analyzers (`/platform/davis/analyzers/v1/...`, scope `davis:analyzers:read`) are an execution API: list, document,
  validate, execute, poll, cancel forecast and causal analyzers (DOCS K19). They store no alert definitions. A workflow task
  that runs a Davis analyzer is stored inside the workflow (read with `automation:workflows:read`, REPO earlier note), so
  "Davis analyzer based alerts" that are not detectors are Workflow content.
- Object permissions: newer Settings objects have an `owner` and `resourceContext`; `adminAccess=true` with
  `settings:objects:admin` reads all (DOCS K3). The Anomaly Detection app also says a detector edited by a different actor is
  treated as new with the new actor's permission profile (DOCS K6).

### 3.2 Why the owner's tenant returned 404 (hypotheses, none verified)

The documented 404 for the objects call is "The specified schema or scope is not found" (DOCS K3), so the schema id was
unknown to the service that answered. In order of likelihood:

1. The call went to `/platform/settings/v1/objects`, a different service from the `/platform/classic/environment-api/v2/settings/objects`
   proxy that the detector tutorial uses (DOCS K4), and that service does not list this schema. Evidence for: the owner's list
   had 313 schemas, the docs index shows about 360 builtin pages. Evidence against: none found.
2. The tenant has no detectors and the schema is not registered until a first use or a feature release. Evidence: none; UNVERIFIED.
3. The owner's token user lacks visibility. This would normally give 403 or an empty list, not 404, so it is the least likely.

Probes that separate them (each a read-only GET, owner approval needed per call type, none made by me):

- P1 `GET /platform/classic/environment-api/v2/settings/schemas/builtin:davis.anomaly-detectors` with `settings:schemas:read`.
- P2 `GET /platform/classic/environment-api/v2/settings/objects?schemaIds=builtin:davis.anomaly-detectors&fields=objectId,summary,scope,schemaVersion&pageSize=5`.
- P3 `GET /platform/classic/environment-api/v2/settings/schemas` and filter for `davis`; compare the count with the 313 from the other path.

### 3.3 What the converter should do

Read detectors only through the proxy path above plus the `.live.` path for hybrid tenants, treat a 404 on the schema as
"this tenant has none" with a counted note, and never as an error. Model `analyzer.input` as a list of `{key, value}` string
pairs, convert to a dict, and keep unknown keys.

## 4. Decision table: New Relic target, verdict, and why

Verdicts: CONVERTS (the importer can produce the New Relic object; owner reviews), PARTIAL (produced with named
losses), MANUAL (report text only), NO EQUIVALENT. Every converted condition also carries the data prerequisite from
section 4.2, because Dynatrace metrics do not exist in New Relic.

### 4.1 Table

| Dynatrace object | New Relic target | Verdict | Why | Evidence |
|---|---|---|---|---|
| Metric event, METRIC_KEY, static | NRQL static condition | CONVERTS if the metric is in the map | One aggregation, one threshold, one window. Threshold unit must match. | DOCS K1a, N1 |
| Metric event, METRIC_SELECTOR, static, simple chain | NRQL static condition | PARTIAL | Selector parse needed; unsupported functions fall back to MANUAL (section 5). | DOCS K10, N1 |
| Metric event, AUTO_ADAPTIVE_THRESHOLD or SEASONAL_BASELINE | NRQL baseline condition (`alertsNrqlConditionBaselineCreate`) | PARTIAL | The two models differ (p99 plus IQR vs a learned forecast band). Sensitivity cannot be converted numerically; New Relic needs 1 to 4 weeks of its own data. | DOCS K7, K8, N2, N4 |
| `OUTSIDE` condition | baseline `UPPER_AND_LOWER` | PARTIAL | Direction maps; static `OUTSIDE` does not exist in the Dynatrace schema for static strategy. | DOCS K4, N4 |
| `alertOnNoData` true | loss-of-signal threshold with "open new alert event" | PARTIAL | Different clock and needs a prior signal. | DOCS N1 |
| `queryOffset` | `aggregationDelay` (event flow) or `aggregationTimer` | PARTIAL | Do not map to "evaluation delay", which is a different feature. | DOCS N1, N3 |
| Entity filter NAME, HOST_NAME | `WHERE` on a name attribute | PARTIAL | Needs the attribute for that entity type in New Relic. | DOCS K1a |
| Entity filter ENTITY_ID | `WHERE hostname = ...` after id to name lookup | MANUAL unless names are exported | Ids do not exist in New Relic. | DOCS K1a |
| Entity filter TAG | `WHERE` on a tag or label attribute | MANUAL | New Relic must have the same tag on the entity. | DOCS K1a, K12 |
| Entity filter MANAGEMENT_ZONE, `managementZone` | none | MANUAL | A zone is a rule-built entity set (2.5). | DOCS K1e |
| Dimension filter | `WHERE` (operators map to `=`, `!=`, `LIKE 'x%'`, `LIKE '%x%'`) | PARTIAL | Dimension keys are Dynatrace names, need a dimension map. | DOCS K1a |
| `eventTemplate.eventType` | condition tag `dt_category` and priority | PARTIAL | Event type is a problem category; New Relic has priority only. | DOCS K1o, N8 |
| `eventTemplate.title/description` | condition name and description | CONVERTS | `{dims:...}` placeholders need a New Relic variable table (UNVERIFIED). | DOCS K5 |
| `davisMerge` | none | NO EQUIVALENT | Davis merges events into problems; New Relic correlation is a different method. | DOCS K1a |
| Davis detector, timeseries, static | NRQL static condition | PARTIAL | DQL `timeseries` subset (REPO src/importer/sources/dynatrace/dql.py handles only log counts today). | DOCS K4 |
| Davis detector, timeseries, auto-adaptive / seasonal | NRQL baseline | PARTIAL | As above. | DOCS K4 |
| Davis detector, records mode | NRQL condition only for the log-count shape (`fetch logs`, `filter`, `summarize count()`) | MANUAL otherwise | Arbitrary DQL has no NRQL pipeline equivalent. | DOCS K5, REPO dql.py |
| Built-in Davis detection (services, hosts, k8s) | none | NO EQUIVALENT | Automatic, no user object. Non-default tuning is reported only. | DOCS K20 |
| Edge alerts (disk, process availability) | infrastructure conditions | MANUAL | Agent-side; no query. | DOCS K22 |
| Alerting profile | workflow issue filter | PARTIAL | Category, tag filter and delay need New Relic attributes; delay has no workflow field (UNVERIFIED). Pair with its notification. | DOCS K1o, N8 |
| Problem notification, EMAIL | email destination + channel in a workflow | CONVERTS | Recipients are complete data. | DOCS K1c, N6 |
| Problem notification, SLACK | Slack destination | PARTIAL | Webhook url is masked; New Relic Slack destination is OAuth based; re-authorise. | DOCS K2, N6 |
| Problem notification, PAGER_DUTY | PagerDuty destination | PARTIAL | `serviceApiKey` is masked; re-enter. | DOCS K1c, N6 |
| Problem notification, WEBHOOK, XMATTERS | webhook destination | PARTIAL | Payload placeholders must be rewritten to New Relic variables; secrets and OAuth re-entered. | DOCS K1c, N6 |
| Problem notification, JIRA, SERVICE_NOW | Jira / ServiceNow destination | PARTIAL | Secret masked; field mapping differs. | DOCS K1c, N6 |
| Problem notification, OPS_GENIE, VICTOROPS, TRELLO, ANSIBLE_TOWER | webhook destination | MANUAL | No native destination in the list read. | DOCS N6 |
| Maintenance window | muting rule with a schedule | PARTIAL | Time part converts; entity scope depends on tags; suppression modes differ (section 4.3). | DOCS K1p, N5 |
| Management zone | account/tag scope | MANUAL | Rule-built set. | DOCS K1e |
| Auto tag | entity tags or nothing | MANUAL (NO EQUIVALENT as a rule engine) | New Relic tags entities directly; no rule-driven tagging was found. Use tag rules as filter hints only. | DOCS K1f |
| Anomaly detection tuning for built-ins | none | NO EQUIVALENT | Report non-default values only. | DOCS K1g |
| Log event (`builtin:logmonitoring.log-events`) | NRQL condition on `Log` | PARTIAL | Matcher syntax is a log query; simple matches only. | DOCS K1l |
| Log metric (`...schemaless-log-metric`) | NRQL on `Log`, or a condition if an alert uses it | PARTIAL | `OCCURRENCE` is a count. `ATTRIBUTE` is a sum or other measure. | DOCS K1m |
| Calculated service metric | none | NO EQUIVALENT | Derived from OneAgent request data. | DOCS K17 |
| SLO (classic or Platform) | New Relic service level | PARTIAL | Needs a good/valid-event NRQL; `metricExpression` data does not exist in New Relic. | DOCS K1n |
| Davis analyzer inside a workflow | none | NO EQUIVALENT | Forecast or causal tasks. | DOCS K19 |

### 4.2 The data prerequisite: what the converter must produce so the data exists

Dynatrace-collected metrics are not exported to New Relic by any documented route (REPO earlier note section 6). A converted
condition on `builtin:host.cpu.usage` therefore watches nothing until New Relic receives an equivalent signal. The converter
must emit, per condition:

1. A mapping from the Dynatrace metric key to a New Relic signal: event type, attribute, aggregation, unit factor. This is a
   hand-built file, like `src/importer/sources/gcp/gcp_verified.json` (REPO), verified on live New Relic data. A starter set,
   all UNVERIFIED as equivalences except where a doc is cited:

| Dynatrace metric key | New Relic signal | Notes |
|---|---|---|
| `builtin:host.cpu.usage` (percent, DOCS K10 descriptor example) | `SystemSample.cpuPercent` | `cpuPercent` appears in a New Relic query example (DOCS N9); equivalence of definition is UNVERIFIED. |
| `builtin:host.mem.usage` | `SystemSample.memoryUsedPercent` | UNVERIFIED |
| `builtin:host.disk.usedPct` | `StorageSample.diskUsedPercent` | StorageSample exists (DOCS N9); attribute name UNVERIFIED. |
| `builtin:service.response.time` | `Transaction.duration` | unit: Dynatrace microseconds vs New Relic seconds, UNVERIFIED both. |
| `builtin:service.requestCount.server` | `count(*)` on `Transaction` | needs a New Relic APM agent. |
| `builtin:service.errors.total.rate` | `percentage(count(*), WHERE error IS true)` on `Transaction` | UNVERIFIED |
| custom metric ingested into Dynatrace via OTLP or Micrometer | same OpenTelemetry metric sent to New Relic | best case: the metric name is the one the customer's code emits, so dual-shipping works; the Dynatrace key may be a normalised form of the OTel name (UNVERIFIED). |

2. A data prerequisite line for the report: which New Relic agent, integration or exporter must be installed (infrastructure
   agent for host metrics, APM agent for service metrics, cloud integration for cloud metrics, OTLP exporter for custom
   metrics). OneAgent-only metrics (`builtin:host.*`, `builtin:process.*`, `builtin:service.*`, `builtin:tech.*`, RUM) are
   re-instrumentation items (REPO earlier note section 6, UNVERIFIED family list).
3. A check command, the equivalent of `check-gcp-data` (REPO src/importer/sources/gcp/data_check.py), that runs
   `SELECT count(*) FROM <event> WHERE <filter> SINCE 1 hour ago` per converted condition and marks "no data".
4. A dimension map: Dynatrace dimension keys (`dt.entity.host`, `dt.entity.disk`, `dt.entity.service`) are entity ids; New Relic
   attributes are names (`hostname`, `diskName`, `appName`: UNVERIFIED). A lookup from id to name must be exported at extract time
   (Entities API v2 read; scope `entities.read`: UNVERIFIED), or the condition cannot be scoped.

### 4.3 Maintenance window to muting rule, in detail

New Relic muting rules mute notifications at the point just before sending; alert events are still recorded (DOCS N5). That
matches `DETECT_PROBLEMS_DONT_ALERT` closely. `DONT_DETECT_PROBLEMS` (no problems at all) cannot be matched: New Relic still opens
the alert event and the issue, only silences the notification. `DETECT_PROBLEMS_AND_ALERT` needs no muting rule (the window then
only disables synthetic monitors).

Rule attributes available (DOCS N5): `accountId`, `compoundAlertId`, `compoundAlertName`, `conditionId`, `conditionName`,
`conditionType`, `entity.guid`, `nrqlEventType`, `nrqlQuery`, `policyId`, `policyName`, `product`, `runbookUrl`,
`tags.<NAME>`, `targetName`. Schedule: start and end, a time zone, repeat daily, weekly (with weekdays) or monthly, end by date or
count; notify-or-suppress behaviour after the window (DOCS N5). Dynatrace `ONCE` maps to a single start/end; `DAILY`, `WEEKLY`,
`MONTHLY` map to the three repeats; `recurrenceRange.scheduleEndDate` maps to "end by date". `MONTHLY` with a day past month end
runs on the last day in Dynatrace (DOCS K1p); how New Relic treats day 31 is UNVERIFIED. Entity tags map to `tags.<key>` only if
the converted conditions carry the same tag; a window with no filters mutes everything (a muting rule with no condition is not
documented: UNVERIFIED, so emit a rule on `policyName` set).

## 5. Metric selector to NRQL

A metric event of type METRIC_SELECTOR, a classic SLO `metricExpression` and a calculated metric all use the selector grammar (DOCS K10, K11).
Restrictions of an NRQL alert condition that decide each verdict (DOCS N1): the query must return one number; one aggregation per
condition; `SINCE`, `UNTIL`, `TIMESERIES`, `COMPARE WITH`, `LIMIT`, subqueries and joins are not allowed; `histogram()`,
`bytecountestimate()` and `cardinality()` are not supported; `FACET` is allowed (up to 20,000 values for static and baseline); nested
aggregation needs a faceted inner query and the same window at every level; loss of signal does not work with nested queries.
Supported aggregators listed: `apdex average count latest max min percentage percentile sum uniqueCount` (DOCS N1). `filter()` inside
`SELECT` is shown in the docs as the way to get a zero instead of null (DOCS N1).

Grammar facts (DOCS K10): a selector starts with a metric key, then `:transformations` left to right. Up to 10 metrics per query.
Quote and tilde escaping uses `~`. Dimensions are referenced by key. A selector can be several comma-separated metric keys; a condition
can hold only one value, so a multi-metric selector becomes several conditions or MANUAL.

### 5.1 Function table

"Alert verdict" is the verdict for using that function inside a New Relic alert condition.

| Dynatrace | Meaning (DOCS K10) | NRQL shape | Alert verdict |
|---|---|---|---|
| `:auto` (or none) | default aggregation of the metric (from descriptor `defaultAggregation`) | whatever the default is, usually `average(attr)` | PARTIAL: needs the descriptor or a table. |
| `:avg` | mean of the slot, nulls ignored | `average(attr)` | CONVERTS |
| `:min`, `:max`, `:sum` | | `min(attr)`, `max(attr)`, `sum(attr)` | CONVERTS |
| `:count` | count of non-null values in the slot | `count(attr)` (events with the attribute) | PARTIAL: counts events, not slot values; fine for event streams. |
| `:median` | | `percentile(attr, 50)` | PARTIAL: a `median` alias is not confirmed (UNVERIFIED); percentile is documented (DOCS N1). |
| `:percentile(n)` | n from 0 to 100 | `percentile(attr, n)` | CONVERTS. Large faceted percentile conditions can error (DOCS N1 tip). |
| `:value` | single value as is | `latest(attr)` | PARTIAL |
| `:histogram` | buckets as dimension `le` | none | MANUAL (`histogram()` not allowed in alerts, DOCS N1). |
| `:splitBy("d1","d2")` | keep these dimensions, merge the rest | `FACET d1, d2` | CONVERTS if the dimension map exists. Merge happens over time slots first in Dynatrace; New Relic aggregates all events in the window once. Equal for sums, may differ for averages of unevenly sampled series. |
| `:splitBy()` | merge all dimensions | no `FACET` | CONVERTS |
| `:merge("d")` | remove dimension d | `FACET` on the remaining dimensions | PARTIAL: needs the full dimension list from the descriptor. |
| `:filter(eq("d","v"))` | dimension equals | `WHERE d = 'v'` | CONVERTS (escape the value). |
| `:filter(ne(...))` | | `WHERE d != 'v'` | CONVERTS |
| `:filter(prefix/suffix/contains(...))` | | `WHERE d LIKE 'v%'`, `'%v'`, `'%v%'` | CONVERTS; escape `%`, `_` and quotes; case rules of LIKE unconfirmed (REPO dql.py note). |
| `:filter(existsKey("d"))` | | `WHERE d IS NOT NULL` | CONVERTS |
| `:filter(and/or/not(...))` | | `AND`, `OR`, `NOT` | CONVERTS |
| `:filter(in("d", entitySelector("...")))` | dimension is one of the entities chosen by an entity selector | list of names after lookup | MANUAL (needs entity resolution; selector syntax in DOCS K12). |
| `:filter(series(avg, gt(10)))` | keep series whose aggregate over the time frame passes | none | MANUAL: post-aggregation series filter; a nested faceted query with an outer filter is the only candidate and has strict limits (DOCS N1). |
| `:filter(remainder("d"))` | the remainder bucket | none | NO EQUIVALENT |
| `:fold(agg)` | collapse the time series to one point | none | MANUAL: an alert evaluates a stream; a one-point result has no time axis. |
| `:last`, `:lastReal` | most recent point | `latest(attr)` | PARTIAL |
| `:rollup(agg, Nm)` | smooth by aggregating the trailing N minutes (max 60) | aggregation window N minutes with sliding window (slide-by 1 minute) | PARTIAL: sliding windows are supported (DOCS N1) but raise matched data points and cost (DOCS N7, N3). |
| `:smooth(skipfirst)` | null the first point after a gap | none | MANUAL (drop with note; effect is small). |
| `:rate(5m)` | convert a count to a per-interval rate | `sum(attr) / <window minutes>` for per-minute; `rate()` exists in NRQL but allowed in alerts is UNVERIFIED | PARTIAL |
| `:delta` | difference to the previous point, negative clamped to 0 | `derivative()` in NRQL, alert use UNVERIFIED | MANUAL until verified. |
| `:default(n)` | fill null slots with n | gap filling "custom static value" n (`signal.fillOption` STATIC, `fillValue`) (DOCS N1) | PARTIAL |
| `:default(n, always)` | also fill when the series is empty | none: gap fill applies only to actively reporting signals, history dropped after 2 hours or the threshold duration (DOCS N1) | PARTIAL: an entirely absent signal needs loss of signal instead. |
| `:timeshift(-1d)` | compare with the past | none (`COMPARE WITH` is incompatible with alerting, DOCS N1) | MANUAL: use a baseline condition for the same intent. |
| `:sort(...)` , `:limit(n)` | order and keep top n series | none (`LIMIT` not allowed, DOCS N1) | PARTIAL: New Relic evaluates every facet. Scope grows from top n to all; say so. |
| `:names`, `:parents` | add names or parent entity to the dimensions | drop; use name attributes | PARTIAL |
| `:partition("d", ...)` | split into new dimension by value or dimension criteria | `FACET cases(...)` exists in NRQL; alert use UNVERIFIED | MANUAL |
| `:setUnit(u)` | metadata only | no effect | CONVERTS (ignore, but record the unit) |
| `:toUnit(a,b)` | convert values | multiply by a constant in `SELECT` | PARTIAL: needs a unit table. |
| `entitySelector(...)` | select entities by type, name, tag, zone, health, relationships (DOCS K12) | `WHERE` on name or tag attributes | MANUAL for relationships and zones; PARTIAL for `entityName.startsWith/equals`. |
| `a / b * 100` | arithmetic between metrics (DOCS K11) | `SELECT 100 * sum(a) / sum(b)` | CONVERTS when both metrics are in the same New Relic event type; the repo already converts arithmetic over metrics (REPO docs/what-migrates.md alarms section, IR `BinOp`). |
| `(a):default(0) / (b)` | null-safe ratio | arithmetic plus gap fill or `filter()` | PARTIAL: Dynatrace returns null if either side is null (DOCS K11). |
| two or more metrics in one selector | up to 10 | one condition each | PARTIAL |

Two traps worth a test each:

- Counting zeros. A Dynatrace count slot with no events is null unless `:default(0)` is applied, and New Relic `count(*)` returns no
  data (null) when nothing matches (DOCS N1 "null value returned"). Use `SELECT filter(count(*), WHERE <failure>) FROM <type> WHERE <entity>`
  so the window yields 0, and add loss of signal for the no-events-at-all case (DOCS N1).
- Dimension values in `WHERE` come from untrusted export text (a dimension value, a tag value, a metric key). They may reach NRQL only through the
  existing quoting function (`quote_string`/`identifier`, REPO CLAUDE.md), including the `~` unescape.

## 6. Violating samples, sliding window, dealerting samples to New Relic

### 6.1 Dynatrace semantics

- Samples are one-minute samples for metric events and detectors (DOCS K1a, K4). Built-in host/infrastructure detection uses 10-second samples (DOCS K1h).
- "Violating samples" out of "sliding window" samples: the event opens when `violatingSamples` samples in the last `slidingWindow` samples violate; they do not have to be consecutive (DOCS K7, K9). Default 3 of 5. Maximum 60 for each (DOCS K4: `violatingSamples` max 60, `slidingWindow` max 60, `slidingWindow >= violatingSamples`).
- The event stays open until both hold: the number of violating samples falls below the opening count, and the number of normal samples is at least `dealertingSamples` (DOCS K5). Default 5. Maximum 60 (DOCS K4).
- `queryOffset` shifts the evaluation window back to avoid latest-minute latency (DOCS K1a).

### 6.2 New Relic fields (DOCS N4, N1, N3)

| NerdGraph field | Meaning | Range or default |
|---|---|---|
| `signal.aggregationWindow` | seconds per evaluated data point | the NRQL page says 30 seconds to 120 minutes in 15 s steps (DOCS N1); the streaming page says 30 seconds to 6 hours (DOCS N3). The two pages disagree. Use 60 s. |
| `signal.aggregationMethod` | `EVENT_FLOW` (default), `EVENT_TIMER`, `CADENCE` | event timer recommended for cloud-integration data (DOCS N1). |
| `signal.aggregationDelay` | seconds to wait (flow, cadence) | default 120 s (DOCS N3). |
| `signal.aggregationTimer` | seconds (timer) | default 60 s, 5 s to 20 min (DOCS N3). |
| `signal.slideBy` | sliding window step | must divide the window; costs more data points (DOCS N1, N7). |
| `signal.fillOption`, `fillValue` | gap fill NONE, STATIC (value), LAST_VALUE | (DOCS N1) |
| `expiration.expirationDuration`, `openViolationOnExpiration`, `closeViolationsOnExpiration`, `ignoreOnExpectedTermination` | loss of signal | 30 s to 48 h (DOCS N1) |
| `terms.threshold`, `operator`, `priority` | threshold, ABOVE/BELOW/..., CRITICAL or WARNING | one critical and one warning per condition (DOCS N7) |
| `terms.thresholdDuration` | seconds the threshold must hold | duration for an alert event 30 s to 2 h (DOCS N7) |
| `terms.thresholdOccurrences` | `ALL` ("for at least") or `AT_LEAST_ONCE` ("at least once in") | (DOCS N4, N2) |
| `violationTimeLimitSeconds` | auto-close an open alert event after this time | example 86400 (DOCS N4); default 3 days for conditions to expire is stated for lost-signal events (DOCS N1) |
| `baselineDirection` | `UPPER_ONLY`, `LOWER_ONLY`, `UPPER_AND_LOWER` | baseline conditions only (DOCS N4) |

### 6.3 Mapping and what changes meaning

| Dynatrace | New Relic | Maps? | What changes |
|---|---|---|---|
| one-minute sample | `aggregationWindow = 60` | yes | none |
| `violatingSamples = slidingWindow = n` (all consecutive) | `thresholdDuration = n*60`, `thresholdOccurrences = ALL` | exact | none |
| `violatingSamples = n` of `slidingWindow = m`, n < m | `thresholdDuration = n*60`, `ALL` (the repo's choice for AWS: "3 of 5 becomes 3 consecutive breaches", REPO docs/what-migrates.md) | approximate, stricter | A flapping series that breaks every second minute opens in Dynatrace and never in New Relic. State this in the note. |
| same | `thresholdDuration = m*60`, `AT_LEAST_ONCE` | approximate, looser | One breaching minute opens an alert event. Reject this mapping. |
| same, tried with sliding window aggregation | none | no | New Relic's sliding window smooths values before the threshold; it does not count violating minutes. Do not map `slidingWindow` to `slideBy`. Use `slideBy` only for `:rollup`. |
| `n = 1`, `m = 1` | `thresholdDuration = 60`, `ALL` | exact | |
| `dealertingSamples = k` | recovery: UNVERIFIED | unknown | New Relic closes an alert event when data points stop violating; a configurable recovery duration for static conditions is not in the pages read. Anomaly conditions close only after the signal stays inside the band for the duration (DOCS N2). Record the loss: Dynatrace needs k normal samples. |
| `queryOffset = q` minutes | `aggregationDelay = max(120, q*60)` or timer | approximate | New Relic waits to aggregate; Dynatrace evaluates older data. Detection latency is similar, window timestamps differ. |
| `slidingWindow` up to 60 | `thresholdDuration` up to 2 h | fine | |
| no Dynatrace auto-close | `violationTimeLimitSeconds` | n/a | Set a long value (86400) and say so. |
| `warning` level | none in Dynatrace metric events | n/a | Dynatrace has one level; use CRITICAL only. |

### 6.4 `alertOnNoData`

- `true`: "missing data samples will be treated as violating samples" (DOCS K1a). In effect, after `violatingSamples` missing minutes (with a window of
  `samples`) the event opens. New Relic: `expiration.expirationDuration = violatingSamples*60`, `openViolationOnExpiration = true`. It only works after
  a signal has been seen (DOCS N1 "requires a signal to be present"), does not work with nested queries, and fires immediately at expiry (independent of the
  threshold duration, DOCS N1). A missing metric is also affected by how a New Relic query returns null for zero events (section 5).
- `false`: missing data "is not treated as a violation but will still contribute to dealerting" (DOCS K1a). New Relic default gap filling NONE: "an empty
  aggregation window will reset the threshold duration timer" (DOCS N1). That is stricter for "all consecutive" conditions: one empty minute resets the count.
  Use gap fill STATIC 0 only where the metric is a counter that should read zero.

## 7. Auto-adaptive and seasonal baselines to New Relic baseline conditions

### 7.1 Dynatrace models

| Property | Auto-adaptive threshold | Seasonal baseline |
|---|---|---|
| Training data | the previous 7 days, per minute (DOCS K7) | the previous 14 days, per minute (DOCS K8) |
| Method | baseline = 99th percentile of the 7-day values; signal fluctuation = interquartile range (p25 to p75); threshold = baseline + n x IQR (DOCS K7) | quantile regression on autoregressive, time-of-day/day features and Fourier seasonal features; one-step-ahead forecast of a confidence band each minute (DOCS K8) |
| Parameter | `numberOfSignalFluctuations` n (event key `signalFluctuation`), default 1 (DOCS K4) | `tolerance` 0.1 to 10, default 4 (DOCS K8) |
| Direction | `ABOVE`, `BELOW`, `OUTSIDE` (DOCS K4) | same |
| Update | thresholds recomputed daily (DOCS K7) | model updated daily; falls back to a "simple model" with constant band when validation fails (DOCS K8) |
| Scope | each entity (each dimension tuple) has its own threshold (DOCS K7, K8) | same |
| Availability | metric selector events only (DOCS K13) | metric selector events only |
| Limit | 100 per environment (DOCS K7) | |

### 7.2 New Relic baseline condition (DOCS N2, N4)

- NerdGraph: `alertsNrqlConditionBaselineCreate` with `baselineDirection` (`UPPER_ONLY`, `LOWER_ONLY`, `UPPER_AND_LOWER`), `nrql.query`, `signal`
  (window, method, delay), `terms` { `threshold` (the number of standard deviations), `thresholdDuration`, `thresholdOccurrences`, `operator`, `priority` }.
  Example in DOCS N4: `baselineDirection: UPPER_ONLY`, `threshold: 13`, `thresholdDuration: 180`, `thresholdOccurrences: ALL`, `operator: ABOVE`.
- Learns 1 to 4 weeks of history; a faceted query "isn't trained on stored data and starts learning from scratch" (DOCS N2). So a converted baseline
  condition has weak signal for the first days.
- Seasonality: UI choices None, Hourly, Daily, Weekly, "New Relic calculation" (DOCS N2). The NerdGraph field name for seasonality is not in the pages I read: UNVERIFIED.
- Up to 20,000 signals per condition; each evaluated against its own baseline (DOCS N2).
- Tuning aid: every evaluation writes `NrAiSignal` with `numberOfDeviations`; histogram it to choose a threshold (DOCS N2).
- Separate from baseline: "Outlier detection" compares entities with their peers using DBSCAN clustering with parameters Epsilon, Min Points, Evaluation Groups (DOCS N10).
  Dynatrace has no equivalent of this, so "outlier" is not a target for any Dynatrace object.

### 7.3 Mapping and loss

| Item | Maps? | Notes |
|---|---|---|
| `ABOVE` / `BELOW` / `OUTSIDE` | yes | `UPPER_ONLY` / `LOWER_ONLY` / `UPPER_AND_LOWER`. |
| `numberOfSignalFluctuations`, `tolerance` to `terms.threshold` (deviations) | no principled conversion | Dynatrace n is in IQR units above the p99; New Relic is standard deviations of forecast error. Choose a fixed default (the repo uses a band width from the source where one exists, REPO `BaselineSpec.deviations`), flag the number as chosen, and point to the `NrAiSignal` histogram. A calibration table needs live data on both sides (Q8). |
| Training window (7 or 14 days) | no | New Relic decides 1 to 4 weeks. |
| p99 baseline plus IQR | no | different statistics. |
| Daily threshold update | no | New Relic adapts continuously and gives recent data more weight (DOCS N2). |
| Per-entity thresholds | yes with `FACET` | resets learning for faceted queries (DOCS N2). |
| Simple-model fallback | no | |
| Holiday-aware baseline (`builtin:anomaly-detection.holiday-aware-baseline`) | no | Applies to Davis' automatic service and application baselines (DOCS K1k). |
| `violatingSamples` etc. | as section 6 | baseline conditions use `thresholdDuration` and `thresholdOccurrences` the same way (DOCS N4). |
| Metric key type | static only | Auto-adaptive and seasonal exist only for selector events (DOCS K13); a METRIC_KEY with a baseline model is invalid input and must be a warning, not a crash. |

## 8. Fixtures for `tests/fixtures/dynatrace-classic/`

All JSON below is docs-derived, not live. Sources: schema tables (DOCS K1), the Settings API tutorial (DOCS K4), Terraform provider
docs and source (REPO-DT), and a Dynatrace AI-skills repository (REPO-DT, DOCS K22). Where I composed a value from a schema table
because no example exists, it is marked INVENTED. Object ids, user ids and names are invented. Wrapper fields are the ones listed
in DOCS K3. Real exports will also carry `summary`, `created`, `modified`, `owner` and `resourceContext`; tests should include
at least one wrapper with those and one without.

File layout suggestion: one file per object, each file an `items` list in the `GET /objects` response shape (`items`, `nextPageKey`, `pageSize`, `totalCount`).

### 8.1 Metric events

Easy: metric key, static, host disk. Quoted shape from DOCS T1 `metric_events.md` example, converted to the JSON tags of REPO-DT.

```json
{
  "objectId": "INVENTED-metric-event-0001",
  "schemaId": "builtin:anomaly-detection.metric-events",
  "scope": "environment",
  "value": {
    "enabled": true,
    "summary": "Disk usage above 90 percent",
    "queryDefinition": {
      "type": "METRIC_KEY",
      "metricKey": "builtin:host.disk.usedPct",
      "aggregation": "AVG",
      "entityFilter": { "dimensionKey": "dt.entity.host", "conditions": [] },
      "dimensionFilter": []
    },
    "modelProperties": {
      "type": "STATIC_THRESHOLD",
      "threshold": 90,
      "alertCondition": "ABOVE",
      "alertOnNoData": false,
      "violatingSamples": 3,
      "samples": 5,
      "dealertingSamples": 5
    },
    "eventTemplate": {
      "title": "Disk usage above 90 percent",
      "description": "The {metricname} on {entityname} is {alert_condition} {threshold}.",
      "eventType": "RESOURCE",
      "davisMerge": true,
      "metadata": []
    },
    "eventEntityDimensionKey": "dt.entity.host"
  }
}
```

Hard: selector with ratio and arithmetic, default, filter with `~` escapes, auto-adaptive, management zone, offset, tag and zone entity conditions
(INVENTED values, shape per DOCS K1a; selector grammar per DOCS K10, K11).

```json
{
  "objectId": "INVENTED-metric-event-0002",
  "schemaId": "builtin:anomaly-detection.metric-events",
  "scope": "environment",
  "value": {
    "enabled": true,
    "summary": "Error ratio above learned range, checkout services",
    "queryDefinition": {
      "type": "METRIC_SELECTOR",
      "metricSelector": "(builtin:service.errors.total.count:splitBy(\"dt.entity.service\"):value:default(0)) / (builtin:service.requestCount.server:splitBy(\"dt.entity.service\"):value) * 100",
      "managementZone": "-189204438944455158",
      "queryOffset": 2,
      "entityFilter": {
        "dimensionKey": "dt.entity.service",
        "conditions": [
          { "type": "TAG", "operator": "EQUALS", "value": "env:production" },
          { "type": "NAME", "operator": "STARTS_WITH", "value": "checkout-" }
        ]
      },
      "dimensionFilter": [
        { "dimensionKey": "k8s.cluster.name", "operator": "DOES_NOT_EQUAL", "dimensionValue": "sandbox" }
      ]
    },
    "modelProperties": {
      "type": "AUTO_ADAPTIVE_THRESHOLD",
      "signalFluctuation": 1,
      "alertCondition": "ABOVE",
      "alertOnNoData": true,
      "violatingSamples": 3,
      "samples": 5,
      "dealertingSamples": 5
    },
    "eventTemplate": {
      "title": "Checkout error ratio high on {dims:dt.entity.service}",
      "description": "Baseline {baseline}, observed above it.",
      "eventType": "ERROR",
      "davisMerge": false,
      "metadata": [ { "metadataKey": "dt.owner", "metadataValue": "payments" } ]
    }
  }
}
```

Hostile: untrusted text in every string field (quotes, newline, NRQL and HCL and Markdown injection, tilde escapes, a very long name, a
`BELOW` seasonal event on a metric key that the schema forbids). The converter must still escape or reject, never pass text through.

```json
{
  "objectId": "INVENTED-metric-event-0003",
  "schemaId": "builtin:anomaly-detection.metric-events",
  "scope": "environment",
  "value": {
    "enabled": true,
    "summary": "x' OR 1=1 --\n\"quoted\" ${var.secret} %{ if true } | col | `code` [link](http://x) <img src=x>",
    "queryDefinition": {
      "type": "METRIC_KEY",
      "metricKey": "custom.\"5xx_errors\"",
      "aggregation": "PERCENTILE90",
      "entityFilter": {
        "dimensionKey": "dt.entity.host",
        "conditions": [ { "type": "NAME", "operator": "CONTAINS_CASE_INSENSITIVE", "value": "a'; DROP TABLE x; --" } ]
      },
      "dimensionFilter": [
        { "dimensionKey": "tilde~key", "operator": "STARTS_WITH", "dimensionValue": "100%_done\\\"" }
      ]
    },
    "modelProperties": {
      "type": "SEASONAL_BASELINE",
      "tolerance": 4,
      "alertCondition": "OUTSIDE",
      "alertOnNoData": false,
      "violatingSamples": 60,
      "samples": 60,
      "dealertingSamples": 60
    },
    "eventTemplate": {
      "title": "{dims:dt.entity.host} } { unbalanced",
      "description": "line1\r\nline2\u0000",
      "eventType": "CUSTOM_ALERT",
      "davisMerge": true,
      "metadata": []
    }
  }
}
```

### 8.2 Davis anomaly detectors

Easy: quoted from the Dynatrace documentation tutorial (DOCS K4), with `actor` null as in the docs.

```json
{
  "objectId": "INVENTED-detector-0001",
  "schemaId": "builtin:davis.anomaly-detectors",
  "scope": "environment",
  "value": {
    "enabled": true,
    "title": "Low disk space alert",
    "description": "",
    "source": "Rest-API",
    "executionSettings": { "actor": null, "queryOffset": null },
    "analyzer": {
      "name": "dt.statistics.ui.anomaly_detection.StaticThresholdAnomalyDetectionAnalyzer",
      "input": [
        { "key": "query", "value": "timeseries avg(dt.host.disk.free), by:{dt.entity.host, dt.entity.disk}" },
        { "key": "threshold", "value": "10" },
        { "key": "alertCondition", "value": "BELOW" },
        { "key": "alertOnMissingData", "value": "false" },
        { "key": "violatingSamples", "value": "3" },
        { "key": "slidingWindow", "value": "5" },
        { "key": "dealertingSamples", "value": "5" }
      ]
    },
    "eventTemplate": {
      "properties": [
        { "key": "dt.source_entity", "value": "{dims:dt.entity.host}" },
        { "key": "event.type", "value": "CUSTOM_ALERT" },
        { "key": "event.description", "value": "The disk {dims:dt.entity.disk.name} runs out of space. Free up space or resize disk." },
        { "key": "event.name", "value": "Low amount of disk space available on host {dims:dt.entity.host.name}" }
      ]
    }
  }
}
```

Hard: adaptive baseline with `query.expression` and a Smartscape filter, then a records-mode detector with `alertIdentityFields[n]` keys.
Both quoted from REPO-DT (the Dynatrace AI-skills repository, DOCS K22); `actor` is a placeholder user id.

```json
{
  "objectId": "INVENTED-detector-0002",
  "schemaId": "builtin:davis.anomaly-detectors",
  "scope": "environment",
  "value": {
    "title": "Abnormal latency increase, JourneyService",
    "description": "",
    "enabled": true,
    "source": "Davis Anomaly Detection",
    "analyzer": {
      "name": "dt.statistics.ui.anomaly_detection.AutoAdaptiveAnomalyDetectionAnalyzer",
      "input": [
        { "key": "query.expression", "value": "timeseries avg_latency = avg(dt.service.request.response_time), by: {dt.smartscape.service} | filter dt.smartscape.service == toSmartscapeId(\"SERVICE-18AA85290DF3D5D2\")" },
        { "key": "numberOfSignalFluctuations", "value": "1" },
        { "key": "alertCondition", "value": "ABOVE" },
        { "key": "alertOnMissingData", "value": "false" },
        { "key": "violatingSamples", "value": "3" },
        { "key": "slidingWindow", "value": "5" },
        { "key": "dealertingSamples", "value": "5" }
      ]
    },
    "eventTemplate": {
      "properties": [
        { "key": "dt.source_entity", "value": "{dims:dt.smartscape.service}" },
        { "key": "event.type", "value": "PERFORMANCE_EVENT" },
        { "key": "event.name", "value": "Abnormal latency increase on JourneyService" },
        { "key": "event.description", "value": "Detected {violating_samples} violation samples within the evaluation window." },
        { "key": "dt.alert_group", "value": "my-routing-group" }
      ]
    },
    "executionSettings": { "actor": "00000000-0000-0000-0000-000000000000" }
  }
}
```

```json
{
  "objectId": "INVENTED-detector-0003",
  "schemaId": "builtin:davis.anomaly-detectors",
  "scope": "environment",
  "value": {
    "title": "High CPU usage, per host (records mode)",
    "description": "",
    "enabled": true,
    "source": "Davis Anomaly Detection",
    "analyzer": {
      "name": "dt.statistics.anomaly_detection.RecordAnomalyDetectionAnalyzer",
      "input": [
        { "key": "query.expression", "value": "timeseries cpu=avg(dt.host.cpu.usage), by: { dt.smartscape.host }\n| fieldsAdd max_cpu = arrayMax(cpu)\n| fieldsRemove cpu\n| filter max_cpu > 80" },
        { "key": "alertIdentityFields[0]", "value": "dt.smartscape.host" }
      ]
    },
    "eventTemplate": {
      "properties": [
        { "key": "dt.source_entity", "value": "{dims:dt.smartscape.host}" },
        { "key": "event.type", "value": "RESOURCE_CONTENTION_EVENT" },
        { "key": "event.name", "value": "High CPU usage on {dims:dt.smartscape.host}: {max_cpu}%" }
      ]
    },
    "executionSettings": { "actor": "00000000-0000-0000-0000-000000000000" }
  }
}
```

Hostile: numeric values given as JSON numbers instead of strings, an unknown analyzer, duplicate keys, a DQL string with a trailing comment and
backtick, an `event.name` with HCL and Markdown, and an `input` that is missing (the model must not crash).

```json
{
  "objectId": "INVENTED-detector-0004",
  "schemaId": "builtin:davis.anomaly-detectors",
  "scope": "tenant",
  "value": {
    "title": "${var.x} \"detector\" | cell",
    "enabled": true,
    "source": "Davis Anomaly Detection",
    "analyzer": {
      "name": "dt.statistics.ui.anomaly_detection.SomethingNewAnalyzer",
      "input": [
        { "key": "threshold", "value": 80 },
        { "key": "threshold", "value": "81" },
        { "key": "query", "value": "fetch logs | filter content == \"a\\\"b\" // `x`\n| summarize c = count()" }
      ]
    },
    "eventTemplate": { "properties": [ { "key": "event.name", "value": "%{ for }[x](http://e) {dims:" } ] },
    "executionSettings": { "actor": null }
  }
}
```

### 8.3 Alerting profiles

Easy (INVENTED from the schema table, DOCS K1o): one severity rule, no filters, no zone.

```json
{
  "objectId": "INVENTED-profile-0001",
  "schemaId": "builtin:alerting.profile",
  "scope": "environment",
  "value": {
    "name": "Default",
    "severityRules": [
      { "severityLevel": "AVAILABILITY", "delayInMinutes": 0, "tagFilterIncludeMode": "NONE", "tagFilter": [] },
      { "severityLevel": "ERRORS", "delayInMinutes": 0, "tagFilterIncludeMode": "NONE", "tagFilter": [] },
      { "severityLevel": "PERFORMANCE", "delayInMinutes": 30, "tagFilterIncludeMode": "NONE", "tagFilter": [] }
    ],
    "eventFilters": []
  }
}
```

Hard (rule structure from DOCS T1 `alerting.md`, filters from the schema table): tag filters, a zone, predefined filter with negate, custom filters
with regex and metadata.

```json
{
  "objectId": "INVENTED-profile-0002",
  "schemaId": "builtin:alerting.profile",
  "scope": "environment",
  "value": {
    "name": "Prod platform team",
    "managementZone": "-189204438944455158",
    "severityRules": [
      { "severityLevel": "RESOURCE_CONTENTION", "delayInMinutes": 15, "tagFilterIncludeMode": "INCLUDE_ALL", "tagFilter": ["EnvironmentA:production", "Team:platform"] },
      { "severityLevel": "CUSTOM_ALERT", "delayInMinutes": 0, "tagFilterIncludeMode": "INCLUDE_ANY", "tagFilter": ["Team:platform", "Team:sre"] }
    ],
    "eventFilters": [
      { "source": "PREDEFINED", "predefinedFilter": { "eventType": "OSI_HIGH_CPU", "negate": true } },
      { "source": "CUSTOM", "customFilter": {
          "titleFilter": { "operator": "REGEX_MATCHES", "value": "^(db|cache)-.*", "negate": false, "enabled": true, "caseSensitive": false },
          "metadataFilter": { "metadataFilterItems": [ { "metadataKey": "dt.owner", "metadataValue": "platform", "negate": false } ] } } }
    ]
  }
}
```

Hostile: tags with brackets and colons and Unicode, a regex that backtracks, an event filter with both bodies set, 100 rules (size cap test), and an empty `severityRules`.

```json
{
  "objectId": "INVENTED-profile-0003",
  "schemaId": "builtin:alerting.profile",
  "scope": "environment",
  "value": {
    "name": "p\"r|o}{f' -- é中",
    "severityRules": [
      { "severityLevel": "ERRORS", "delayInMinutes": 99999, "tagFilterIncludeMode": "INCLUDE_ANY", "tagFilter": ["[Kubernetes]app:a:b", "kéy:v'al", "", "x\"y"] }
    ],
    "eventFilters": [
      { "source": "CUSTOM",
        "predefinedFilter": { "eventType": "SERVICE_SLOWDOWN", "negate": false },
        "customFilter": { "descriptionFilter": { "operator": "REGEX_MATCHES", "value": "(a+)+$", "negate": true, "enabled": true, "caseSensitive": true } } }
    ]
  }
}
```

### 8.4 Problem notifications

Secret values below are the literal word `MASKED`; a real response may show `null`, an empty string or another placeholder (UNVERIFIED). The test
must assert that none of these values reach any output.

Easy, email:

```json
{
  "objectId": "INVENTED-notification-0001",
  "schemaId": "builtin:problem.notifications",
  "scope": "environment",
  "value": {
    "enabled": true,
    "type": "EMAIL",
    "displayName": "Ops email",
    "alertingProfile": "INVENTED-profile-0001",
    "emailNotification": {
      "toRecipients": ["ops@example.invalid"],
      "ccRecipients": [],
      "bccRecipients": [],
      "subject": "{State} {ProblemSeverity}: {ProblemTitle}",
      "body": "{ProblemDetailsText}\n{ProblemURL}",
      "notifyClosedProblems": true
    }
  }
}
```

Hard, webhook with headers, a secret URL and OAuth (DOCS K1c field names; example values in the style of DOCS T1 `webhook_notification.md`):

```json
{
  "objectId": "INVENTED-notification-0002",
  "schemaId": "builtin:problem.notifications",
  "scope": "environment",
  "value": {
    "enabled": true,
    "type": "WEBHOOK",
    "displayName": "Incident bridge",
    "alertingProfile": "INVENTED-profile-0002",
    "webHookNotification": {
      "urlContainsSecret": true,
      "url": "",
      "secretUrl": "MASKED",
      "acceptAnyCertificate": false,
      "notifyEventMergesEnabled": true,
      "notifyClosedProblems": true,
      "useOAuth2": true,
      "oAuth2Credentials": {
        "accessTokenUrl": "https://idp.example.invalid/oauth/token",
        "clientId": "dynatrace-bridge",
        "clientSecret": "MASKED",
        "scope": "incidents.write",
        "authenticateViaRequestHeader": true
      },
      "timeoutInSeconds": 30,
      "headers": [
        { "name": "X-Team", "secret": false, "value": "platform", "secretValue": null },
        { "name": "Authorization", "secret": true, "value": null, "secretValue": "MASKED" }
      ],
      "payload": "{\"title\":\"{ProblemTitle}\",\"state\":\"{State}\",\"tags\":\"{Tags}\",\"details\":{ProblemDetailsJSONv2}}"
    }
  }
}
```

Hard, Slack and PagerDuty (secrets masked):

```json
[
  { "objectId": "INVENTED-notification-0003", "schemaId": "builtin:problem.notifications", "scope": "environment",
    "value": { "enabled": true, "type": "SLACK", "displayName": "Slack critical", "alertingProfile": "INVENTED-profile-0002",
      "slackNotification": { "url": "MASKED", "channel": "#dynatrace-critical", "message": "{ProblemTitle} ({State})" } } },
  { "objectId": "INVENTED-notification-0004", "schemaId": "builtin:problem.notifications", "scope": "environment",
    "value": { "enabled": false, "type": "PAGER_DUTY", "displayName": "PagerDuty platform", "alertingProfile": "INVENTED-profile-0002",
      "pagerDutyNotification": { "account": "acme", "serviceName": "platform", "serviceApiKey": "MASKED" } } }
]
```

Hostile: webhook whose plain `url` embeds a token, a header whose name and value contain CRLF, a placeholder-only payload that is not JSON, recipients with
display names and a 5,000-address list, and a `type` that does not match the populated nested object.

```json
{
  "objectId": "INVENTED-notification-0005",
  "schemaId": "builtin:problem.notifications",
  "scope": "environment",
  "value": {
    "enabled": true,
    "type": "WEBHOOK",
    "displayName": "<script>alert(1)</script> | `x` ${y}",
    "alertingProfile": "does-not-exist",
    "webHookNotification": {
      "urlContainsSecret": false,
      "url": "https://hooks.example.invalid/in?token=SHOULD-NEVER-BE-PRINTED",
      "secretUrl": null,
      "acceptAnyCertificate": true,
      "notifyEventMergesEnabled": false,
      "notifyClosedProblems": false,
      "headers": [ { "name": "X-A\r\nX-Injected", "secret": false, "value": "v\r\nX-B: c", "secretValue": null } ],
      "payload": "{ProblemTitle} {Tags[env]} {Unknown}"
    },
    "emailNotification": { "toRecipients": ["Jane Q. <jane@example.invalid>"], "ccRecipients": [], "bccRecipients": [], "subject": "", "body": "", "notifyClosedProblems": false }
  }
}
```

### 8.5 Maintenance windows

Easy, classic weekly (shape per DOCS K1p; INVENTED values):

```json
{
  "objectId": "INVENTED-maint-0001",
  "schemaId": "builtin:alerting.maintenance-window",
  "scope": "environment",
  "value": {
    "enabled": true,
    "generalProperties": {
      "name": "Weekly patching",
      "description": "Sunday patch window",
      "maintenanceType": "PLANNED",
      "suppression": "DETECT_PROBLEMS_DONT_ALERT",
      "disableSyntheticMonitorExecution": false
    },
    "schedule": {
      "scheduleType": "WEEKLY",
      "weeklyRecurrence": {
        "dayOfWeek": "SUNDAY",
        "timeWindow": { "startTime": "02:00:00", "endTime": "04:00:00", "timeZone": "Europe/Vienna" },
        "recurrenceRange": { "scheduleStartDate": "2026-01-01", "scheduleEndDate": "2026-12-31" }
      }
    },
    "filters": [ { "entityTags": ["Team:platform"] } ]
  }
}
```

Hard, one-off with no problem detection, two OR-ed filters, zone and entity id, and the older Config API shape (from DOCS T1 `maintenance_window.md`):

```json
[
  { "objectId": "INVENTED-maint-0002", "schemaId": "builtin:alerting.maintenance-window", "scope": "environment",
    "value": { "enabled": true,
      "generalProperties": { "name": "Migration freeze", "maintenanceType": "UNPLANNED", "suppression": "DONT_DETECT_PROBLEMS", "disableSyntheticMonitorExecution": true },
      "schedule": { "scheduleType": "ONCE", "onceRecurrence": { "startTime": "2026-12-01T22:00:00", "endTime": "2026-12-02T02:00:00", "timeZone": "UTC" } },
      "filters": [ { "entityType": "HOST", "entityId": "HOST-32D0DB4904B28FB3" }, { "managementZones": ["-189204438944455158"], "entityTags": ["env:prod", "tier:db"] } ] } },
  { "suppression": "DONT_DETECT_PROBLEMS", "type": "PLANNED", "name": "Old config API window",
    "schedule": { "recurrenceType": "ONCE", "start": "2021-05-11 13:41", "end": "2021-05-11 14:41", "zoneId": "Europe/Vienna" },
    "suppressSynthMonExec": true,
    "scope": { "matches": [ { "tagCombination": "AND", "tags": [ { "key": "deldel1", "context": "CONTEXTLESS" } ] } ] } }
]
```

Newer schema (INVENTED from the schema table, DOCS K1d; the `rule` text format is unknown, so the example uses an RRULE string as a guess, UNVERIFIED):

```json
{
  "objectId": "INVENTED-maint-0003",
  "schemaId": "builtin:maintenance-windows",
  "scope": "environment",
  "value": {
    "name": "Nightly batch",
    "enabled": true,
    "filter": "matchesValue(k8s.namespace.name, \"batch\") or in(dt.entity.host, \"HOST-1\")",
    "autoDelete": false,
    "schedule": {
      "trigger": { "type": "time", "recurring": { "time": "01:00:00", "earliestStart": "2026-01-01", "until": "2026-12-31", "rule": "FREQ=DAILY" } },
      "timezone": "UTC",
      "duration": 120
    },
    "objectScopes": { "syntheticMonitors": { "disableSyntheticMonitors": false }, "suppressProblems": true }
  }
}
```

Hostile for maintenance: `dayOfMonth` 31, end before start, zone name that does not exist, empty `filters` (whole environment), a DQL filter with a quote.

### 8.6 Management zones, auto tags

Easy and hard, from the Terraform docs (DOCS T1 `management_zone_v2.md`, `autotag_v2.md`), keys converted to the schema camelCase (my transcription):

```json
[
  { "objectId": "INVENTED-mz-0001", "schemaId": "builtin:management-zones", "scope": "environment",
    "value": { "name": "GCP hosts", "rules": [
      { "enabled": true, "type": "ME", "entitySelector": "",
        "attributeRule": { "entityType": "HOST", "hostToPGPropagation": true,
          "conditions": [ { "key": "HOST_CLOUD_TYPE", "operator": "EQUALS", "enumValue": "GOOGLE_CLOUD_PLATFORM" } ] } },
      { "enabled": true, "type": "ME", "entitySelector": "",
        "attributeRule": { "entityType": "SERVICE", "serviceToHostPropagation": true, "serviceToPGPropagation": true,
          "conditions": [ { "key": "HOST_GROUP_ID", "operator": "EQUALS", "entityId": "HOST_GROUP-8A29CED074001723" } ] } } ] } },
  { "objectId": "INVENTED-tag-0001", "schemaId": "builtin:tags.auto-tagging", "scope": "environment",
    "value": { "name": "GKE-Hosts", "rules": [
      { "enabled": true, "type": "SELECTOR", "entitySelector": "type(host),entityName.startsWith(\"gke\")", "valueFormat": "true", "valueNormalization": "Leave text as-is" } ] } }
]
```

Hostile: a rule `entitySelector` with relationship traversal and escaped quotes, a `valueFormat` with `{ProcessGroup:Environment:x}`, 5,000 rules, a name with newline.

### 8.7 Built-in anomaly detection tuning, log events, log metrics, SLO

Hosts (shape per DOCS K1h, values INVENTED), services and one disk override are exported as objects with `scope` `environment` or an entity id.

```json
{
  "objectId": "INVENTED-adh-0001",
  "schemaId": "builtin:anomaly-detection.infrastructure-hosts",
  "scope": "HOST-32D0DB4904B28FB3",
  "value": {
    "host": {
      "connectionLostDetection": { "enabled": true, "onGracefulShutdowns": "DONT_ALERT_ON_GRACEFUL_SHUTDOWN" },
      "highCpuSaturationDetection": { "enabled": true, "detectionMode": "custom",
        "customThresholds": { "cpuSaturation": 95, "eventThresholds": { "violatingSamples": 18, "violatingEvaluationWindow": 30, "dealertingSamples": 18, "dealertingEvaluationWindow": 30 } } }
    }
  }
}
```

Log event and log metric (DOCS K1l, K1m; INVENTED values; the matcher text uses the Dynatrace log query language and is hostile by nature):

```json
[
  { "objectId": "INVENTED-logevent-0001", "schemaId": "builtin:logmonitoring.log-events", "scope": "environment",
    "value": { "enabled": true, "summary": "Payment gateway timeout", "query": "matchesPhrase(content, \"gateway timeout\") AND status=\"ERROR\"",
      "eventTemplate": { "title": "Gateway timeouts", "description": "", "eventType": "ERROR", "davisMerge": true, "metadata": [] } } },
  { "objectId": "INVENTED-logmetric-0001", "schemaId": "builtin:logmonitoring.schemaless-log-metric", "scope": "environment",
    "value": { "enabled": true, "key": "log.payment_errors", "query": "status=\"ERROR\" AND dt.entity.host=*", "measure": "OCCURRENCE", "dimensions": ["dt.entity.host"] } }
]
```

Classic SLO (shape per DOCS K1n; values from the Terraform example in DOCS T1 `slo_v2.md`):

```json
{
  "objectId": "INVENTED-slo-0001",
  "schemaId": "builtin:monitoring.slo",
  "scope": "environment",
  "value": {
    "enabled": true,
    "name": "Checkout availability",
    "customDescription": "Terraform Test",
    "metricName": "checkout_availability",
    "metricExpression": "100*(builtin:service.requestCount.server:splitBy())/(builtin:service.requestCount.server:splitBy())",
    "evaluationType": "AGGREGATE",
    "filter": "type(SERVICE),serviceType(WEB_SERVICE,WEB_REQUEST_SERVICE)",
    "evaluationWindow": "-1w",
    "targetSuccess": 95,
    "targetWarning": 98,
    "errorBudgetBurnRate": { "burnRateVisualizationEnabled": false, "fastBurnThreshold": 10 }
  }
}
```

### 8.8 Fixture rules

- Every secret-typed field present as `"MASKED"`, `null`, `""` and absent in at least one file each.
- One export with `nextPageKey` set and a second page; one with a 404 on a schema (document each as "tenant has none").
- One object with an unknown extra key at every level (models use `extra="allow"`, as planned in REPO earlier note section 8).
- A golden file produced from these must be reviewed by a human before it is committed (REPO CLAUDE.md quality bar).

## 9. Prioritised build list, and what only a live read can settle

### 9.1 Build order

Ordered by customer coverage first, then by effort. Effort: S small, M medium, L large. Risk is the chance the work is wrong because of unknowns.

| # | Build | Covers | Effort | Risk | Why this order |
|---|---|---|---|---|---|
| 1 | Extractor for the classic read path (`.live.` and Managed host, `Api-Token`, schemas by constant list, pagination, 404 as "none", secret masking handled, no token or URL in output) | every object below | M | medium (host variants, scope names) | Nothing else can be tested without a real export. Path allow-list, not user text. |
| 2 | Problem notifications + alerting profiles pairing to notification plans (email, Slack, PagerDuty, webhook; others to manual destinations) | every classic customer has these; the importer already has plans and workflows (REPO `notifications.py`, `notification_ir.py`) | M | low to medium (workflow filter attributes UNVERIFIED) | Highest coverage per hour; no data prerequisite; secrets re-entered. |
| 3 | Maintenance windows to muting rules (time part only, tag scope when tags are mirrored) | common, simple | S to M | low | Schedule maps cleanly (section 4.3). |
| 4 | Metric events, METRIC_KEY and static, with the metric map file for host and service metrics | the bulk of custom classic alerting | L | high (metric map, units, dimension map, data may not exist) | Needs section 4.2 pieces 1 to 4. Ship with the data check. |
| 5 | Metric events, METRIC_SELECTOR subset parser (splitBy, filter, avg/min/max/sum/count/percentile, default, arithmetic) | the rest of metric events | L | high (grammar and escaping; property tests) | Section 5 table is the test list. Everything else returns MANUAL with a reason. |
| 6 | Log events and log metrics to log-count conditions | log alerts | S to M | low to medium | Reuses the log-count converter (REPO dql.py) if the matcher syntax is mapped; matcher syntax differs from DQL: UNVERIFIED. |
| 7 | Baselines (auto-adaptive and seasonal) to baseline conditions | new-style alerts | M | high (no numeric mapping) | Only after 4 and 5; flagged as chosen defaults. |
| 8 | Davis detectors: timeseries static, then baselines; records mode only for log count | Platform and hybrid tenants | M | medium (storage path Q1, DQL subset) | Reuses 4, 5, 7 once the DQL `timeseries` subset parser exists. |
| 9 | Classic SLOs to service levels | few customers | M | high (metric expression data) | Platform SLO converter exists (REPO `service_levels.py`). |
| 10 | Report-only readers: management zones, auto tags, built-in anomaly tuning, calculated service metrics | explain what does not move | S | low | Counts and one remediation line each. |

Smallest useful slice: 1, 2, 3 and the report from 10.

### 9.2 Questions only a live read of a classic tenant can settle

| # | Question | Why it matters |
|---|---|---|
| Q1 | Do `/platform/classic/environment-api/v2/settings/schemas/builtin:davis.anomaly-detectors` and `/platform/settings/v1/...` give the same answer, and which one is on the owner's tenant? | Decides the detector extract path (section 3). |
| Q2 | What does a masked secret look like in the JSON (null, empty, or a string)? Is webhook `url` returned in clear when `urlContainsSecret` is false? | Scrubber rules and fixture content. |
| Q3 | Do metric event exports contain `modelProperties` fields that do not apply to the chosen type as `null` or omit them? Are defaults filled? | Model design. |
| Q4 | Which `eventType` values are in real use, and how does each map to the alerting profile severity names? | Section 2.2 mapping is inferred. |
| Q5 | How do `entityFilter.conditions` combine, AND or OR? | Section 2.1. |
| Q6 | Is `tagFilter` written as `key:value` or `[Context]key:value`? | Tag mapping. |
| Q7 | Are there still objects only in the Config API (not yet copied to Settings) on old Managed clusters? | Whether a fallback reader is needed. |
| Q8 | Calibrate baselines: for a few real metrics, what `terms.threshold` in New Relic gives about the same alert frequency as a Dynatrace `numberOfSignalFluctuations` 1 or `tolerance` 4? | Section 7.3. Needs a metric that exists on both sides. |
| Q9 | What is in `entityFilter` and `dimensionFilter` for the five most common metric events, and how many use selector mode with functions outside section 5.1? | Coverage estimate. |
| Q10 | What does `builtin:maintenance-windows` `schedule.trigger.recurring.rule` contain? | Section 2.4. |
| Q11 | Do real alerting profiles use `eventFilters` or only severity rules? How many have a zone? | Pairing complexity. |
| Q12 | How many notification targets are non-native in New Relic (OpsGenie, VictorOps, xMatters, Trello, Ansible)? | Manual workload. |
| Q13 | Does New Relic support a recovery duration on static NRQL conditions? | Section 6.3 dealerting. |
| Q14 | Which Dynatrace entity types and names appear in `dimensionKey` and what are the New Relic attribute names for the same entities on this customer's New Relic account? | Dimension map. |

## 10. UNVERIFIED list

1. Config API paths other than `/api/config/v1/notifications` (alerting profiles, maintenance windows, management zones, auto tags, calculated metrics, anomaly detection).
2. Whether a current Config API for metric events exists.
3. How multiple `entityFilter.conditions` combine.
4. Whether `violatingSamples`, `samples`, `dealertingSamples` have a maximum of 60 for metric events (documented for Davis detectors only).
5. Whether the API returns inapplicable `modelProperties` fields as null or omits them, and whether it fills defaults.
6. The JSON representation of a masked secret.
7. Which nested notification objects appear when the type selects one.
8. Tag string format in `tagFilter`.
9. The event type to problem category mapping for classic metric event types.
10. Numeric defaults of built-in service and host anomaly detection.
11. The unit and definition equivalence of every starter metric map row in section 4.2.
12. Export of dimension ids to names (Entities API scope).
13. New Relic: `median`, `rate()`, `derivative()` and `FACET cases()` inside alert NRQL; seasonality field name in NerdGraph; recovery duration for static conditions; workflow field for delay; day-31 monthly muting; muting rule with no condition.
14. Whether the Platform settings service (`/platform/settings/v1`) lists `builtin:davis.anomaly-detectors`.
15. `builtin:maintenance-windows` `rule` format.
16. Whether the OTel metric key is stored unchanged in Dynatrace.
17. Whether a Managed cluster or older classic SaaS tenant still needs the Config API fallback.
18. Aggregation window upper bound in New Relic (120 minutes vs 6 hours; two New Relic pages disagree).
19. Calculated service metric JSON key names (the endpoint page returned 404).
20. Visibility rules for objects owned by other users on Platform.

## 11. Sources

All fetched with `curl` GET on 2026-11-09. Dynatrace schema pages are under
`https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/` (page slug in the Evidence column).

| Id | URL or file |
|---|---|
| K1a | `.../schemas/builtin-anomaly-detection-metric-events` |
| K1b | `.../schemas/builtin-davis-anomaly-detectors` |
| K1c | `.../schemas/builtin-problem-notifications` |
| K1d | `.../schemas/builtin-maintenance-windows` |
| K1e | `.../schemas/builtin-management-zones` |
| K1f | `.../schemas/builtin-tags-auto-tagging` |
| K1g | `.../schemas/builtin-anomaly-detection-services` |
| K1h | `.../schemas/builtin-anomaly-detection-infrastructure-hosts`; K1h2 `...infrastructure-disks-per-disk-override`; K1h3 `...infrastructure-aws`, `...infrastructure-vmware`, `...databases` |
| K1i | `.../schemas/builtin-anomaly-detection-kubernetes-cluster` and siblings |
| K1j | `.../schemas/builtin-anomaly-detection-rum-web` |
| K1k | `.../schemas/builtin-anomaly-detection-holiday-aware-baseline`, `...frequent-issues`, `builtin-alerting-connectivity-alerts`, `builtin-availability-process-group-alerting`, `builtin-infrastructure-disk-edge-anomaly-detectors` |
| K1l | `.../schemas/builtin-logmonitoring-log-events` |
| K1m | `.../schemas/builtin-logmonitoring-schemaless-log-metric` |
| K1n | `.../schemas/builtin-monitoring-slo` |
| K1o | `.../schemas/builtin-alerting-profile` |
| K1p | `.../schemas/builtin-alerting-maintenance-window` |
| K1q | `.../schemas/builtin-custom-metrics` |
| K2 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/key-concepts` (section "Secret-type properties") |
| K3 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/objects/get-objects` |
| K4 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/set-up-anomaly-detectors-via-api` |
| K5 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/anomaly-detection-configuration` |
| K6 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/anomaly-detection-app` |
| K7 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/auto-adaptive-threshold` |
| K8 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/reference/ai-models/seasonal-baseline` |
| K9 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/static-thresholds` |
| K10 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/metric-selector` |
| K11 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/metric-expressions` |
| K12 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/entity-selector` |
| K13 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection-classic/metric-events` |
| K14 | `https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/notifications-api` and `.../notifications-api/get-a-notification` |
| K15 | `https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/alerting-profiles-api` |
| K16 | `https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/maintenance-windows-api` |
| K17 | `https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/calculated-metrics` |
| K18 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/service-level-objectives/get-slo` and `/get-all`; K18b `.../service-level-objectives` (Platform) |
| K19 | `https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-davis-analyzers-v1` and `/analyzers-get` |
| K20 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection-classic/adjust-sensitivity-anomaly-detection/adjust-sensitivity-services-classic` |
| K21 | `https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas` and `https://docs.dynatrace.com/docs/sitemap.xml` |
| K22 | Dynatrace/dynatrace-for-ai `skills/dt-alerting/references/anomaly-detectors.md` (raw.githubusercontent.com; Dynatrace organisation repository, not the docs site) |
| K26 | `https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection-classic/metric-events` ("Dynatrace Classic", metric events) |
| K27 | `https://docs.dynatrace.com/docs/dynatrace-api/configuration-api` (endpoint list) and `.../configuration-api/anomaly-detection-api` |
| T1 | dynatrace-oss/terraform-provider-dynatrace `docs/resources/*.md` (metric_events, alerting, notification, webhook_notification, slack_notification, ops_genie_notification, maintenance_window, management_zone_v2, autotag_v2, slo_v2, log_metrics, calculated_service_metric, davis_anomaly_detectors) |
| REPO-DT | dynatrace-oss/terraform-provider-dynatrace `dynatrace/api/builtin/anomalydetection/metricevents/settings/{settings,model_properties,query_definition,event_template,dimension_filter}.go` (JSON tags) |
| N1 | `https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions` |
| N2 | `https://docs.newrelic.com/docs/alerts/create-alert/set-thresholds/anomaly-detection` |
| N3 | `https://docs.newrelic.com/docs/alerts/create-alert/fine-tune/streaming-alerts-key-terms-concepts` |
| N4 | `https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-nrql-condition-alerts` |
| N5 | `https://docs.newrelic.com/docs/alerts/get-notified/muting-rules-suppress-notifications` |
| N6 | `https://docs.newrelic.com/docs/alerts/get-notified/destinations` |
| N7 | `https://docs.newrelic.com/docs/alerts/admin/rules-limits-alerts` |
| N8 | `https://docs.newrelic.com/docs/alerts/get-notified/incident-workflows` |
| N9 | `https://docs.newrelic.com/docs/infrastructure/infrastructure-data/default-infra-data` |
| N10 | `https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/alert-conditions` |
