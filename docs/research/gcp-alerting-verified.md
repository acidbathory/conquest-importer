# Google Cloud alerting: verified field lists, MQL status, channels, New Relic integration

Research of 2026-11-11. Everything here was fetched with `curl` from public sources on that date;
nothing was sent to Google Cloud, New Relic or any tenant. Raw copies were kept outside the repo.
Evidence labels: **[proto]** read in a `.proto` file at `googleapis/googleapis` `master`,
**[page]** read on a cloud.google.com page (the page's own "Last updated" date is given),
**[mdx]** read in `newrelic/docs-website` `develop`, **[tf]** read in the Terraform provider docs,
**[code]** read in `src/importer/sources/gcp/` at `ad19dfe`, **UNVERIFIED** could not be fetched or
confirmed.

Sources used (all fetched, HTTP 200):
- https://raw.githubusercontent.com/googleapis/googleapis/master/google/monitoring/v3/alert.proto
  (also `alert_service.proto`, `notification.proto`, `notification_service.proto`, `uptime.proto`,
  `service.proto`, `metric_service.proto`, `common.proto` in the same folder). Licence: Apache-2.0
  (header of each file).
- https://raw.githubusercontent.com/googleapis/googleapis/master/google/monitoring/dashboard/v1/
  `dashboard.proto`, `widget.proto`, `layouts.proto`, `xychart.proto`, `scorecard.proto`,
  `metrics.proto`, `dashboard_filter.proto`, `text.proto`. There is no
  `google/monitoring/v3/dashboard.proto` (404); dashboards live in `dashboard/v1`.
- https://cloud.google.com/stackdriver/docs/deprecations/mql, https://cloud.google.com/monitoring/mql,
  https://cloud.google.com/monitoring/promql/promql-in-alerting,
  https://cloud.google.com/monitoring/promql/create-promql-alerts,
  https://cloud.google.com/monitoring/alerts/using-channels-api,
  https://cloud.google.com/monitoring/alerts/sql-based-incidents,
  https://cloud.google.com/monitoring/api/metrics_gcp_c (all "Last updated" 2026-11-06 or -07).
- https://raw.githubusercontent.com/hashicorp/terraform-provider-google/main/website/docs/r/monitoring_alert_policy.html.markdown
  and `.../monitoring_notification_channel.html.markdown` (MPL-2.0 provider; doc text only).
- https://raw.githubusercontent.com/newrelic/docs-website/develop/src/content/docs/infrastructure/google-cloud-platform-integrations/
  `get-started/introduction-google-cloud-platform-integrations.mdx`, `get-started/gcp-integration-metrics.mdx`,
  `get-started/integrations-custom-roles.mdx`, `troubleshooting/troubleshooting-gcp-integrations.mdx`,
  `gcp-integrations-list/google-compute-engine-monitoring-integration.mdx` (plus the 35 service pages listed in section 4).

Not fetched: `terraform-google-modules/terraform-google-monitoring` does not exist (github.com returns 404, as does
the codeload tarball). The closest real repository is `terraform-google-modules/terraform-google-cloud-operations`
(HTTP 200, Apache-2.0); its contents were not read, so it is **UNVERIFIED** as a source of examples. The GitHub
contents/tree API was rate-limited (HTTP 403) during the run, so directory listings come from the repository
tarball (`codeload.github.com/.../tar.gz/refs/heads/master`) and from the HTML tree pages.

## 1. Field lists from the .proto files

### 1.1 AlertPolicy [proto: alert.proto]

| Field | Type / values | Notes |
|---|---|---|
| `name` | string | `projects/P/alertPolicies/ID`, server-assigned |
| `displayName` | string | |
| `documentation` | `Documentation` | `content`, `mimeType` (only `text/markdown`), `subject`, `links[]` (`displayName`, `url`) |
| `userLabels` | map<string,string> | |
| `conditions[]` | `Condition` | see 1.2 |
| `combiner` | `COMBINE_UNSPECIFIED`, `AND`, `OR`, `AND_WITH_MATCHING_RESOURCE` | |
| `enabled` | BoolValue | |
| `validity` | `google.rpc.Status` | output only |
| `notificationChannels[]` | string | channel resource names |
| `creationRecord`, `mutationRecord` | `MutationRecord` | output only |
| `alertStrategy` | `AlertStrategy` | `notificationRateLimit.period`, `notificationPrompts[]` (`OPENED`, `CLOSED`), `autoClose`, `notificationChannelStrategy[]` (`notificationChannelNames[]`, `renotifyInterval`) |
| `severity` | `SEVERITY_UNSPECIFIED`, `CRITICAL`, `ERROR`, `WARNING` | |

### 1.2 Condition [proto: alert.proto]

Common: `name`, `displayName`, and a oneof `condition` of six kinds.

| JSON key | Message | Fields |
|---|---|---|
| `conditionThreshold` | `MetricThreshold` | `filter`, `aggregations[]`, `denominatorFilter`, `denominatorAggregations[]`, `forecastOptions{forecastHorizon}`, `comparison`, `thresholdValue`, `duration`, `trigger{count or percent}`, `evaluationMissingData` |
| `conditionAbsent` | `MetricAbsence` | `filter`, `aggregations[]`, `duration`, `trigger{count or percent}` |
| `conditionMatchedLog` | `LogMatch` | `filter`, `labelExtractors` (map). The proto comment says: if set, no other conditions can be present |
| `conditionMonitoringQueryLanguage` | `MonitoringQueryLanguageCondition` | `query` (must output a boolean stream), `duration`, `trigger`, `evaluationMissingData` |
| `conditionPrometheusQueryLanguage` | `PrometheusQueryLanguageCondition` | `query`, `duration`, `evaluationInterval`, `labels` (map), `ruleGroup`, `alertRule`, `disableMetricValidation` |
| `conditionSql` | `SqlCondition` | `query`; oneof `schedule` = `minutes{periodicity}` / `hourly{periodicity}` / `daily{periodicity, executionTime}`; oneof `evaluate` = `rowCountTest{comparison, threshold}` / `booleanTest{column}` |

Enums [proto: alert.proto, common.proto]:
- `comparison` / `ComparisonType`: `COMPARISON_GT, GE, LT, LE, EQ, NE` (plus `COMPARISON_UNSPECIFIED`).
- `evaluationMissingData`: `EVALUATION_MISSING_DATA_INACTIVE`, `_ACTIVE`, `_NO_OP` (plus `_UNSPECIFIED`).
- `trigger`: oneof `count` (int32) or `percent` (double); default behaviour is "any one series".
- `forecastOptions.forecastHorizon`: required; the proto comment says the horizon "can range from 1 hour to 60 hours".
- `Aggregation`: `alignmentPeriod`, `perSeriesAligner`, `crossSeriesReducer`, `groupByFields[]`.
  Aligners: `ALIGN_NONE, DELTA, RATE, INTERPOLATE, NEXT_OLDER, MIN, MAX, MEAN, COUNT, SUM, STDDEV,
  COUNT_TRUE, COUNT_FALSE, FRACTION_TRUE, PERCENTILE_99, _95, _50, _05, PERCENT_CHANGE`.
  Reducers: `REDUCE_NONE, MEAN, MIN, MAX, SUM, STDDEV, COUNT, COUNT_TRUE, COUNT_FALSE, FRACTION_TRUE,
  PERCENTILE_99, _95, _50, _05`.
- PromQL policies are the only kind with no `trigger`; their duration/evaluation-interval fields replace it.
  The create-PromQL page [page, 2026-11-06] says PromQL alerts do not follow the policy `autoClose`; an alert closes
  after the larger of 270 s and twice `evaluationInterval`; default `evaluationInterval` is 30 s.

### 1.3 NotificationChannel and descriptor [proto: notification.proto]

`NotificationChannel`: `type`, `name`, `displayName` (512 chars), `description` (1024), `labels` (map, shape
defined by the descriptor), `userLabels` (map, up to 64), `verificationStatus` (`UNVERIFIED`/`VERIFIED`, cannot be set
on create or update), `enabled` (BoolValue), `creationRecord`, `mutationRecords[]`.
`NotificationChannelDescriptor`: `name`, `type`, `displayName`, `description`, `labels[]` (LabelDescriptor),
`supportedTiers[]` (deprecated), `launchStage`.
The proto comment on the message: "Fields containing sensitive information like authentication tokens or contact info
are only partially populated on retrieval."

### 1.4 Dashboard [proto: dashboard/v1]

- `Dashboard`: `name`, `displayName`, `etag`, oneof layout (`gridLayout{columns, widgets[]}`,
  `mosaicLayout{columns, tiles[]{xPos,yPos,width,height,widget}}`, `rowLayout{rows[]{weight,widgets}}`,
  `columnLayout{columns[]{weight,widgets}}`), `dashboardFilters[]` (`labelKey`, `templateVariable`, `stringValue`,
  `filterType` = `RESOURCE_LABEL`, `METRIC_LABEL`, `USER_METADATA_LABEL`, `SYSTEM_METADATA_LABEL`, `GROUP`), `labels`.
- `Widget`: `title`, `id`, oneof content: `xyChart`, `scorecard`, `text`, `blank`, `alertChart`, `timeSeriesTable`,
  `collapsibleGroup`, `logsPanel`, `incidentList`, `pieChart`, `errorReportingPanel`, `sectionHeader`, `singleViewGroup`.
- `XyChart`: `dataSets[]{timeSeriesQuery, plotType (LINE, STACKED_AREA, STACKED_BAR, HEATMAP), legendTemplate,
  minAlignmentPeriod, targetAxis}`, `timeshiftDuration`, `thresholds[]`, `xAxis`/`yAxis`/`y2Axis{label, scale}`, `chartOptions{mode}`.
- `Scorecard`: `timeSeriesQuery`, oneof view (`gaugeView{lowerBound, upperBound}`, `sparkChartView`, `blankView`), `thresholds[]`.
- `TimeSeriesQuery` [proto: metrics.proto]: oneof source = `timeSeriesFilter{filter, aggregation, secondaryAggregation,
  pickTimeSeriesFilter | statisticalTimeSeriesFilter}`, `timeSeriesFilterRatio{numerator, denominator, secondaryAggregation, ...}`,
  `timeSeriesQueryLanguage` (MQL string), `prometheusQuery` (PromQL string); plus `unitOverride`, `outputFullDuration`.

Dashboard query mix in the 258 sample dashboards (counted in the tarball of `monitoring-dashboard-samples`, 2026-11-11):
`timeSeriesFilter` 2013, `prometheusQuery` 1553, `timeSeriesFilterRatio` 37, `timeSeriesQueryLanguage` (MQL) 16.
Widgets: `xyChart` 2534, `scorecard` 366, `text` 254, `collapsibleGroup` 105, `timeSeriesTable` 92,
`sectionHeader` 62, `singleViewGroup` 42, `logsPanel` 29, `incidentList` 14, `pieChart` 4.
Layouts: `mosaicLayout` 205, `gridLayout` 53.

### 1.5 What our GCP converter reads and ignores [code]

Alert policy and conditions (`translate.py`, `models.py`):

| Field | Status | What the code does |
|---|---|---|
| `displayName` | read | monitor name (suffixed with the condition name when there are several) |
| `name`, condition `name`/`displayName` | read | source key / id |
| `enabled` | read, weak | only stored in `unmapped["enabled"]` when false; not carried to the emitted condition |
| `combiner` | read, weak | only `AND*` adds a "fires only when all conditions hold" note; `AND_WITH_MATCHING_RESOURCE` is treated like `AND` |
| `severity` | read, lossy | `WARNING` maps to warning; everything else (`CRITICAL`, `ERROR`, unspecified) maps to critical |
| `notificationChannels` | read | handles for the notification plan; adds a "configure manually" reason |
| `documentation.content` | read | message template |
| `documentation.mimeType`, `subject`, `links` | ignored | |
| `alertStrategy` | parsed, never used | `notificationRateLimit`, `notificationPrompts`, `autoClose`, `notificationChannelStrategy` (per-channel renotify) are all dropped silently |
| `userLabels` | ignored | no tags are carried |
| `validity`, `creationRecord`, `mutationRecord` | ignored | |
| `conditionThreshold.filter` | read | `metric.type =`, `resource.type =` (note only), resource and metric label equality; anything else (regex, `!=`, `starts_with`, `monitoring.regex.full_match`, OR) is manual |
| `...aggregations[0]` | read | `alignmentPeriod`, `perSeriesAligner`, `crossSeriesReducer`, `groupByFields`; a second stage is manual |
| aligners | partial | mapped: MEAN, MAX, MIN, SUM, RATE, DELTA, PERCENTILE_50/95/99, NONE. Manual: COUNT, STDDEV, COUNT_TRUE/FALSE, FRACTION_TRUE, INTERPOLATE, NEXT_OLDER, PERCENTILE_05, PERCENT_CHANGE |
| reducers | partial | mapped: MEAN, MAX, MIN, SUM, NONE. Manual: COUNT, STDDEV, COUNT_TRUE/FALSE, FRACTION_TRUE, PERCENTILE_* (REDUCE_PERCENTILE_95 appears 10 times in the sample alerts) |
| `comparison` | read | all six operators |
| `thresholdValue`, `duration` | read | rescaled for counters; duration rounded to whole windows |
| `trigger` (threshold) | read | `count` > 1 or any `percent` is manual; `count` = 1 ignored |
| `trigger` (absent) | IGNORED | `_absence_condition` never reads `trigger`, so a "percent of series" absence condition converts as if it were any-series |
| `evaluationMissingData` | read | `*_ACTIVE` sets no-data notify; `*_INACTIVE`/`*_NO_OP` produce a note. Only the threshold condition is checked; MQL has it too but is manual anyway |
| `denominatorFilter` | read | manual with a reason. `denominatorAggregations` never reached |
| `forecastOptions` | read | manual with a reason |
| `conditionAbsent` filter/aggregations/duration | read | heartbeat signal, raised to the 300 s polling interval |
| `conditionMatchedLog.filter` | read | severity comparisons and `field:value`, `=`, `!=` become a `Log` count; others are manual |
| `conditionMatchedLog.labelExtractors` | ignored | silently dropped |
| log-alert `alertStrategy.notificationRateLimit` | ignored | Google requires it for log alerts; the NR equivalent (alert muting / violation time limit) is not set |
| `conditionMonitoringQueryLanguage` | kind only | manual: "MQL conditions are not converted" |
| `conditionPrometheusQueryLanguage` | kind only | manual: "PromQL conditions are not converted" |
| `conditionSql` | kind only | manual: "SQL conditions are not converted" |

Counts in the Google sample repo (276 alert JSON files, 2026-11-11): `conditionThreshold` in 157 files,
`conditionPrometheusQueryLanguage` 84, `conditionMatchedLog` 25, `conditionAbsent` 5, `conditionMonitoringQueryLanguage` 5,
`denominatorFilter` 1, `trigger` present in 167, `alertStrategy` in 271. These samples are vendor-integration
templates, mostly for Prometheus-style third-party metrics, so they over-represent PromQL compared with a real GCE/GKE estate.
Aligners seen: MEAN 80, RATE 45, DELTA 21, SUM 11, COUNT_TRUE 3, STDDEV 2, COUNT 2, PERCENT_CHANGE 1, MAX 1.

Dashboards (`dashboards.py`, `dashboard_models.py`):

| Field | Status |
|---|---|
| `mosaicLayout`, `gridLayout`, `columnLayout`, `rowLayout` | read, all four |
| `xyChart` (dataSets, plotType, legendTemplate note), `scorecard` (gauge/spark/thresholds noted), `text` | read |
| `timeSeriesFilter` with one `aggregation` | read; `secondaryAggregation` is manual |
| `timeSeriesQueryLanguage`, `prometheusQuery`, `timeSeriesFilterRatio` | named manual reasons |
| `collapsibleGroup`, `sectionHeader`, `singleViewGroup`, `timeSeriesTable`, `logsPanel`, `incidentList`, `pieChart`, `alertChart`, `errorReportingPanel` | fall to "dashboard widget type is not supported" (together about 350 of the widgets in the sample set; `collapsibleGroup`, `sectionHeader` and `singleViewGroup` are layout containers and would need flattening) |
| `dashboardFilters` (template variables), `xyChart.thresholds`, axes, `timeshiftDuration`, `chartOptions` | not read as far as the grep of `dashboards.py` shows; UNVERIFIED that none are noted elsewhere |

## 2. MQL status

Google's deprecation page https://cloud.google.com/stackdriver/docs/deprecations/mql (Last updated 2026-11-06 UTC) says:
- "MQL is no longer a recommended query language". Customer support for MQL ended on **July 22, 2025**, and MQL is no
  longer available for new charts, dashboards and alerting policies **in the Google Cloud console**.
- "MQL is not shut down." Existing MQL charts, dashboards and alerting policies continue to work.
- The page's action table: "Create new MQL charts, dashboards, or alerting policies": console **No**, Monitoring API **Yes**;
  "Edit existing MQL ... policies": console Yes, API Yes.
- Earlier step, **October 22, 2024**: MQL editor autocompletion and the conversion between the query builder and MQL were switched off.
- Recommendation: "We recommend moving to PromQL ... We recommend that you begin using PromQL or the interactive query
  builder for dashboards, alerting policies, and other workflows."
- No shutdown date appears on the page, and https://cloud.google.com/monitoring/mql carries the same announcement (Last updated 2026-11-06).
- `https://cloud.google.com/monitoring/deprecated-features` returned 404, so no further deprecations page was read.

Related status: SQL-based alerting (`conditionSql`) carries a Pre-GA "Preview" banner on
https://cloud.google.com/monitoring/alerts/sql-based-incidents (2026-11-06). The PromQL alerting page
https://cloud.google.com/monitoring/promql/promql-in-alerting shows a Preview callout only on its "query over 2 years" feature,
not on PromQL alerting itself (read from the page text; the callout placement is UNVERIFIED beyond that).

**What it means for us.** New MQL policies can still be created through the API, so MQL will keep appearing in estates and
exports, but it is a frozen, unrecommended language, and new work in Google estates is PromQL. A full MQL translator
is therefore not worth building now: in the sample alert set MQL is 5 of 276 files (84 are PromQL), and in the 258 sample
dashboards 16 queries are MQL against 1553 PromQL. Keep MQL conditions manual with the reason and add the Google dates
to the message ("MQL: Google ended support on 2025-07-22; existing policies still work"). The better investment is the
PromQL condition: Google's own migration path leads there, and we already measured a PromQL-to-NRQL translation route
(`docs/research/nr-promql-baseline.md`, a dashboard translator, so its fit for alert conditions is UNVERIFIED). Revisit if a real customer export shows MQL above roughly 10 percent of conditions.

## 3. Notification channel types and secrets

Types: the page https://cloud.google.com/monitoring/alerts/using-channels-api (Last updated 2026-11-07) lists the output of
`gcloud beta monitoring channel-descriptors list --format='value(type)'`:
`campfire, email, google_chat, hipchat, pagerduty, pubsub, slack, sms, webhook_basicauth, webhook_tokenauth`.
Its Limitations section: "The campfire and hipchat channel types are deprecated. Don't create new channels of these types."
The same page names the "Cloud Mobile App" as a channel that shares infrastructure with PagerDuty, webhooks and Slack,
but the type string `mobile` is **not** in that list, so `mobile` in our `CHANNEL_TYPES` is **UNVERIFIED**.
The descriptor list is per project and the page shows the command, not a guaranteed complete list.

Label keys the page quotes (descriptor output):

| Type | Labels | Secret / obfuscated on read | Source |
|---|---|---|---|
| `email` | `email_address` | no, but contact info is "only partially populated on retrieval" | using-channels-api; notification.proto |
| `pubsub` | `topic` | no | using-channels-api |
| `slack` | `auth_token`, `channel_name` | `auth_token`: "obfuscated by returning only a few characters of the key when fetched" | using-channels-api; [tf] `sensitive_labels.auth_token` (slack) |
| `webhook_basicauth` | `url`, `username`, `password` | `password`: "obfuscated when the channel is fetched" | using-channels-api; [tf] `sensitive_labels.password` |
| `webhook_tokenauth` | not shown on the page; label keys UNVERIFIED | token is part of the labels (UNVERIFIED which key) | |
| `pagerduty` | not shown on the page | `service_key` | [tf] `sensitive_labels.service_key` (pagerduty) |
| `sms` | not shown (a phone number) | UNVERIFIED | |
| `google_chat`, `campfire`, `hipchat` | not shown | UNVERIFIED | |

Terraform provider docs name exactly three sensitive labels: `auth_token` (slack), `password` (webhook_basicauth), `service_key`
(pagerduty), each with a write-only `_wo` variant. The doc also says "Labels with sensitive data are obfuscated by the API".
Effect for us: the API never returns usable secrets, so no channel can be rebuilt in New Relic from an export; the user must
re-enter tokens, passwords and service keys. This matches `notifications.py`, which keeps only `type`, `name`, `displayName`.

Gaps in our code [code]: `CHANNEL_TYPES` has `email, webhook_tokenauth, webhook_basicauth, slack, pagerduty, sms, pubsub,
mobile, campfire`. It lacks `google_chat` and `hipchat`. What an unknown type does was not traced beyond the `KIND`
regex (the plan says "unrecognised type" in that case). `verificationStatus` (`UNVERIFIED` means the channel does not work)
and `enabled` are not used when deciding what to carry over; `enabled` is exported but only for display.

## 4. New Relic Google Cloud integration facts [mdx]

All pages carry `freshnessValidatedDate: 2026-06-30` except the Compute Engine page, which says **`never`**.

Collection (introduction page):
- Three connection methods: Workload Identity Federation (recommended), a New Relic-managed service account, a Google user account.
- Polling is every 5 minutes for all three; 1 minute is "in limited preview" for WIF and only for Managed Kafka, Dataflow,
  Dataproc, Spanner, GKE, BigQuery, AlloyDB, Cloud Load Balancing and Pub/Sub.
- WIF collects "All GCP metrics, including some beta metrics" and "all generally available (GA) and Beta metrics along with
  native labels" for supported services, as dimensional metrics (`Metric` event type); new metrics arrive automatically.
  Service-account and user-account methods give "a predefined set per service" and also `GcpXxxSample` events, and need a
  New Relic update to add metrics.
- Ingest under WIF is "typically higher"; Google bills the Cloud Monitoring API calls to the monitored project.
- A migration tool exists for NRQL written against old names: https://github.com/newrelic-experimental/gcp-queries-migration-tool
  (linked from the page; its contents were not read).

Services list (the 35 pages under `gcp-integrations-list/`): AlloyDB, App Engine, BigQuery, API Gateway, Bigtable, Composer,
Dataflow, Dataproc, Firebase (App Hosting, Auth, Database, Hosting, Storage, Vertex AI), Firestore, Cloud Functions,
Load Balancing, Managed Kafka, Pub/Sub, Cloud Router, Cloud Run, Service Mesh, Spanner, Cloud SQL, Cloud Storage, Compute Engine,
Datastore, Distributed Cloud Edge, Gemini Enterprise, GKE, Memorystore (Memcached, Redis, Valkey), Serverless VPC Access, Vertex AI.
Not in this list: Cloud Logging log-based metrics, Ops Agent (`agent.googleapis.com`), custom and Prometheus metrics. That
agrees with the converter's `UNCOLLECTED` rule. Base path for all of them:
https://raw.githubusercontent.com/newrelic/docs-website/develop/src/content/docs/infrastructure/google-cloud-platform-integrations/gcp-integrations-list/

Compute Engine (page `google-compute-engine-monitoring-integration.mdx`):
- The WIF section says "All Compute Engine metrics available in GCP Cloud Monitoring are collected as dimensional metrics in the
  `Metric` event type. Additional metrics beyond this table are collected automatically."
- Its key-metrics table lists `gcp.compute.instance.cpu.utilization` with unit "Percent" and the text "Fraction of allocated CPU
  currently in use". Google's own page https://cloud.google.com/monitoring/api/metrics_gcp_c (Last updated 2026-11-06) says the
  metric is GA, `GAUGE, DOUBLE, 10^2.%`, "Values are typically numbers between 0.0 and 1.0 (but some machine types allow
  bursting above 1.0)", "reported by the hypervisor", "Sampled every 60 seconds. After sampling, data is not visible for up to 240
  seconds." So the NR "Percent" label is ambiguous: if the metric ever arrives, a threshold written as 0.8 in Google may need
  checking against the unit New Relic stores (UNVERIFIED which).
- `gcp-integration-metrics.mdx` lists `gcp.compute.instance.cpu.utilization` (old sample name `instance.cpu.Utilization`),
  `cpu.reserved_cores`, disk, network and firewall metrics; this is the table our `gcp_verified.json` was checked against.
- Entities: VM instance is entity type `HOST` for resource type `gce_instance`; VM disk is `GCPVIRTUALMACHINEDISK`.
- Non-WIF methods: data is in `GcpVirtualMachineSample`, not `Metric`. A link made with the service-account or user-account
  method would show `instance.cpu.Utilization` there and no `gcp.compute.instance.cpu.utilization`.

Documented conditions that could keep a metric from arriving (documented facts only, not a diagnosis):
1. Compute Engine page `freshnessValidatedDate: never`: the page has never been validated by New Relic staff.
2. Troubleshooting page: "Some services only report metrics when in use"; confirm the service is enabled under Infrastructure > GCP > Manage services.
3. WIF needs five APIs enabled (IAM, Security Token Service, Service Account Credentials, Stackdriver Monitoring, Cloud Asset) plus
   Cloud Resource Manager for folders; roles `Viewer`, `Service Usage Consumer`, `Cloud Asset Viewer` (and `Folder Viewer` for folder links).
   "If a permission error occurs, New Relic stops retrying the failed calls for one hour".
4. Quota: "Time series queries per minute" for the Cloud Monitoring API; "If you're hitting rate limits, data collection can slow down."
5. A custom IAM role must include `compute.instances.list`, `compute.disks.get`, `compute.disks.list` for Compute Engine; New Relic
   "has no way of identifying problems related to custom permissions".
6. Google side: metrics are visible only up to 240 s after sampling; New Relic's own pages add its poll interval on top.
The pages say nothing specific to `cpu/utilization` being excluded or renamed; that absence is the only thing this research can state.
Pages: .../get-started/introduction-google-cloud-platform-integrations.mdx, .../get-started/integrations-custom-roles.mdx,
.../troubleshooting/troubleshooting-gcp-integrations.mdx (base path as above).

## 5. Verified example files

Source: https://github.com/GoogleCloudPlatform/monitoring-dashboard-samples, branch `master`, licence **Apache-2.0**
(`LICENSE`, "Apache License Version 2.0, January 2004", read from the tarball). Real layout, from the tarball:
`alerts/<product>/*.json|yaml|md` (276 JSON, 70 YAML), `dashboards/<product>/*.json|yaml|png|md` (258 JSON, 112 YAML),
plus `dm/`, `integrations/`, `scripts/`, `terraform/` (`main.tf` and an `agents` folder). The alert files are
single-policy JSON in the `AlertPolicy` REST shape; they use `conditionThreshold`, `conditionAbsent`, `conditionMatchedLog`,
`conditionMonitoringQueryLanguage` and `conditionPrometheusQueryLanguage`. No `conditionSql` example exists in the repo.
Each URL below was fetched with `curl -sSL` on 2026-11-11 and returned HTTP 200; the last column is the downloaded size in bytes.
Local checks of the contents were limited to JSON parsing (no file failed) and counting keys; the files were not run anywhere.

### 5.1 Alert policies (48)

| # | Condition kind | Raw URL | Bytes |
|---|---|---|---|
| 1 | MonitoringQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/quota-usage-mql.v1.json | 704 |
| 2 | MonitoringQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/single-allocation-quota.v1.json | 981 |
| 3 | MonitoringQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/single-daily-rate-quota.v1.json | 1172 |
| 4 | MonitoringQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/single-per-minute-rate-quota.v1.json | 999 |
| 5 | MonitoringQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/single-per-second-rate-quota.v1.json | 1009 |
| 6 | Absent | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/all-silent-forwarder-logtype-combinations-except-few-logtypes.v1.json | 957 |
| 7 | Absent | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/all-silent-forwarder-logtype-combinations.v1.json | 844 |
| 8 | Absent | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/silent-forwarder.v1.json | 870 |
| 9 | Absent | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-dataproc/create-cluster-latency-too-high.v1.json | 797 |
| 10 | Absent | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/saphana/saphana-server-down.v1.json | 818 |
| 11 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-agent-for-sap/host-memory-alert.v1.json | 3447 |
| 12 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-alerting/log-match-condition.v1.json | 462 |
| 13 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-deploy/release-render-failure-within-pipeline.v1.json | 792 |
| 14 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-firebase-crashlytics/new-issue-alert.v1.json | 4912 |
| 15 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gce/host-error-log-detected-within-vm.v1.json | 863 |
| 16 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/failedscheduling-log-event-within-cluster.v1.json | 1169 |
| 17 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/network-incident.v1.json | 621 |
| 18 | MatchedLog | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/all-adjustments-by-quota-adjuster.v1.json | 1392 |
| 19 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/active-directory/ldap-connections.v1.json | 1331 |
| 20 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/activemq/activemq-dropped-connection.v1.json | 954 |
| 21 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/aerospike/aerospike-high-memory-utilization.v1.json | 1033 |
| 22 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/airflow-gke/dag-import-errors.v1.json | 1012 |
| 23 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/apache/high-request-rate.v1.json | 1214 |
| 24 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/argo-server-gke/workflow-errors.v1.json | 1058 |
| 25 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/cassandra/high-read-latency.v1.json | 1638 |
| 26 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/couchbase/high-memory-usage.v1.json | 1306 |
| 27 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/couchdb/couchdb-high-request-rate.v1.json | 1446 |
| 28 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch-gke/red-cluster-status.v1.json | 1270 |
| 29 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch/elasticsearch-red-cluster-status.v1.json | 990 |
| 30 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/etcd-gke/long-fsync-duration.v1.json | 1206 |
| 31 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/flink/flink-high%20job-restarts.v1.json | 965 |
| 32 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-application-integration/high-data-usage.v1.json | 1081 |
| 33 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-bigquery/background-metadata-cache-slot-usage.v1.json | 1222 |
| 34 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-alerting/utilization-too-high.v1.json | 707 |
| 35 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/forwarder-buffer-usage-more-than-threshold-with-filters.v1.json | 1013 |
| 36 | Threshold | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-redis/integrations-redis-engine-cpu-utilization-high.v1.json | 1089 |
| 37 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/cassandra/high-error-rate.v1.json | 1314 |
| 38 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch-gke/high-jvm-memory-usage.v1.json | 840 |
| 39 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch/elasticsearch-high-jvm-memory-usage.v1.json | 944 |
| 40 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/flink/flink-high-jvm-memory-non-heap-usage.v1.json | 1271 |
| 41 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-bigquery/background-metadata-cache-slot-usage.v2.json | 1058 |
| 42 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-agent-for-sap/location-constraint-detected.v1.json | 1635 |
| 43 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-alerting/entries-exceeds.v1.json | 635 |
| 44 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/ingestion-quota-limit-approaching.v1.json | 856 |
| 45 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-dataproc/failed-job.v1.json | 679 |
| 46 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gce/reservation-utilization-too-high.v1.json | 783 |
| 47 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/cpu-limit-utilization-entire-workload.v1.json | 2638 |
| 48 | PrometheusQueryLanguage | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/interconnect-egress.v1.json | 1093 |

### 5.2 Dashboards (24, one per product folder)

| # | Raw URL | Bytes |
|---|---|---|
| 1 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/active-directory-ds/active-directory-domain-services-gce-overview.json | 18962 |
| 2 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/activemq/activemq-gce-overview.json | 17516 |
| 3 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/aerospike/aerospike-gce-overview.json | 10158 |
| 4 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/anthos-config-management/ACM-ConfigSync.json | 16963 |
| 5 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/apache/apache-gce-overview.json | 10003 |
| 6 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/apache-airflow/apache-airflow-prometheus.json | 11923 |
| 7 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/apigee/health-availability.json | 8724 |
| 8 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/argo-workflows/argo-workflows-prometheus.json | 8300 |
| 9 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/bigdata/bigquery-monitoring.json | 14417 |
| 10 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/bindplane/host-monitoring.json | 7733 |
| 11 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/cassandra/cassandra-gce-overview.json | 25322 |
| 12 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/claude-code/claude-code-monitoring.json | 13912 |
| 13 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/consul/consul-prometheus.json | 14097 |
| 14 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/couchbase/gce-overview.json | 11268 |
| 15 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/couchdb/couchdb-gce-overview.json | 17227 |
| 16 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/dataflow/dataflow-job.json | 16512 |
| 17 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/elasticsearch/elasticsearch-cluster-gce-overview.json | 14063 |
| 18 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/etcd/etcd-prometheus.json | 15810 |
| 19 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/flink/flink-gce-jobs-overview.json | 10811 |
| 20 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/gateway-api-inference-extension/inference-extension-prometheus-2.json | 70534 |
| 21 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/gemini-cli/gemini-cli-monitoring.json | 21229 |
| 22 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/golang/go-runtime-view-prometheus.json | 20825 |
| 23 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/google-anthos/anthos-cluster-control-plane-uptime.json | 10894 |
| 24 | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/dashboards/google-anthos/deprecated/Anthos-cluster-control-plane-uptime.json | 5945 |

Terraform-based examples: the provider docs above (`monitoring_alert_policy.html.markdown`) hold HCL examples for
threshold, missing data, forecast, PromQL and SQL conditions (the SQL example query is on `_Default._AllLogs`);
not counted among the 48.

## 6. What changes what we build

1. Add severity `ERROR` handling: the proto has three levels and we collapse two of them silently.
2. Read `trigger` on absence conditions, and add the missing aligners and reducers that real estates use (COUNT, STDDEV,
   PERCENTILE reducers) or give each its own manual reason.
3. Carry `alertStrategy` (rate limit, autoClose, prompts, per-channel renotify) as notes at least; log alerts require it.
4. Add `google_chat` and `hipchat` to the channel table; check `mobile` against a real descriptor list.
5. MQL stays manual with dates; PromQL conditions and `prometheusQuery` dashboard widgets are the large unconverted share.
6. Dashboard container widgets (`collapsibleGroup`, `sectionHeader`, `singleViewGroup`) and `timeSeriesTable` need handling.
7. Compute `cpu/utilization` stays flagged; add a unit check (0 to 1 fraction in Google, "Percent" in New Relic's page).
