# Dynatrace: native alerting objects and what New Relic can receive

Research for a Dynatrace source. Written 2026-11-09 from public documentation only. No Dynatrace
tenant, token or API was called, and no account data was sent anywhere.

Evidence marks on every claim:

- DOCS Dn: read in public Dynatrace documentation on 2026-11-09; the URL is in the Sources table at the end.
- REPO file: read in this repository.
- UNVERIFIED: written from memory or inferred; not confirmed in a source. Do not treat as fact.

Terminology note. Current Dynatrace pages call Davis AI "Dynatrace Intelligence" and split the product
into "Dynatrace Classic" (old UI and configuration) and "Latest Dynatrace" (Grail, DQL, Workflows,
Notebooks) (DOCS D10, D14, D3). A tenant on `<id>.apps.dynatrace.com` is a Latest or hybrid tenant
(DOCS D3). Both vocabularies appear below.

## 1. Answers first

- `dt0s16` is a platform token (DOCS D1). It is sent as `Authorization: Bearer <token>` (DOCS D1).
  It is not a classic access token and not an OAuth client.
- Classic access tokens use `Authorization: Api-Token <token>` and appear in the docs with the prefix
  `dt0c01` (DOCS D1, example only; the docs prefix table does not list `dt0c01`).
- Platform tokens act as the user who owns them. A scope grants access only if that user also has the
  permission (DOCS D2).
- Read-only calls against a `.apps` tenant: use the Platform APIs under
  `https://<id>.apps.dynatrace.com/platform/...` (DOCS D6, D24, D19, D21, D37, D38).
  Their scopes are `settings:schemas:read`, `settings:objects:read`, `storage:metrics:read`,
  `storage:entities:read`, `slo:slos:read`, `synthetic:monitors:read`, `automation:workflows:read`,
  `document:documents:read`.
- The classic Settings API can be reached with a platform token on the `.apps` host at
  `/platform/classic/environment-api/v2/settings/objects` with `settings:objects:read`. The only source
  I found is a community tip, not the reference docs (DOCS D4, community). Treat as UNVERIFIED until one
  call confirms it.
- The current alerting path in a `.apps` tenant is Davis anomaly detectors (`builtin:davis.anomaly-detectors`,
  DQL based) feeding Davis problems, with Workflows doing the notification (DOCS D14, D10, D13).
  Metric events, alerting profiles and problem notifications are the Classic path and are documented as
  being replaced (DOCS D9, D13). A migrating tenant usually has both, so the extractor must read both.
- No supported route sends Dynatrace-collected metrics to New Relic. Section 5 lists the partial routes.
  Conversion therefore cannot rely on Dynatrace metric names arriving in New Relic. Rules must be
  re-pointed at metrics New Relic agents or OpenTelemetry produce.

## 2. Token prefixes and scopes

### 2.1 Prefix table

| Prefix | Meaning | Header | Evidence |
|---|---|---|---|
| `dt0s16` | Platform token, programmatic access to Dynatrace platform services | `Authorization: Bearer` | DOCS D1 |
| `dt0c01` | Classic access token (appears as the example in the `Api-Token` header sample; not in the prefix table) | `Authorization: Api-Token` | DOCS D1 (example only) |
| `dt0s01` | Listed in the docs as an "API token" used for account-level changes through SCIM. This is not the classic environment access token despite the name. | not stated | DOCS D1 |
| `dt0s02` | OAuth2 client created in Account Management for Apps and the Account Management API | OAuth2 client credentials exchange | DOCS D1 |
| `dt0s03`, `dt0s08` | OAuth2 clients for internal and external services and integrations | OAuth2 | DOCS D1 |
| `dt0s06` | OAuth2 refresh token, rotates every 5 to 15 minutes | OAuth2 | DOCS D1 |
| `dt0s04`, `dt0s09` | Chat and identity linking | n/a | DOCS D1 |

A token is `prefix.public.secret`: a 24-character public part and a 64-character secret part (DOCS D1).
The docs say the prefix plus public part (the token identifier) is safe to log and the secret is not
(DOCS D1). A community example shows a shorter public part for a `dt0s16` token (DOCS D4), so do not
validate token length strictly.

### 2.2 Platform tokens

- Long-lived, created at `myaccount.dynatrace.com/platformTokens`, optional expiry, up to 50 per user and
  100 per service user per account (DOCS D2).
- A platform token belongs to one account, and can be limited to chosen environments (DOCS D2).
- Platform tokens can be assigned to a service user, which is the documented way to run automation
  (DOCS D3).
- Expired tokens return HTTP 403 (DOCS D2).
- Disabling a token takes up to 5 minutes to propagate (DOCS D2).
- The tenant "environment state" matters. Classic: access tokens only. Hybrid: both, with `v2/apiTokens`
  moving to the `.apps` host. Latest: platform tokens only (DOCS D3).

### 2.3 Scopes that read alerting configuration

Platform token or OAuth scopes (each from the endpoint's reference page):

| Need | Endpoint on `https://<id>.apps.dynatrace.com` | Scope | Evidence |
|---|---|---|---|
| Settings schemas list and detail | `GET /platform/settings/v1/schemas`, `/schemas/{schema-id}` | `settings:schemas:read` | DOCS D6b |
| Settings objects (metric events, anomaly detectors, alerting profiles, problem notifications, maintenance windows, management zones, log events, log metrics, classic SLOs) | `GET /platform/settings/v1/objects` | `settings:objects:read` | DOCS D6 |
| Workflows | `GET /platform/automation/v1/workflows` | `automation:workflows:read` | DOCS D37 |
| SLOs (Platform) | `GET /platform/slo/v1/slos` | `slo:slos:read` | DOCS D19 |
| Synthetic monitors (Platform) | `GET /platform/synthetic/v1/monitors` | `synthetic:monitors:read` | DOCS D21 |
| Dashboards and notebooks | `GET /platform/document/v1/documents` | `document:documents:read` | DOCS D38 |
| DQL (metrics, entities, events) | `POST /platform/storage/query/v1/query:execute` | one of `storage:metrics:read`, `storage:entities:read`, `storage:events:read`, `storage:logs:read`, `storage:buckets:read`, ... | DOCS D24 |
| Classic Settings API via platform token | `/platform/classic/environment-api/v2/settings/objects` | `settings:objects:read` | DOCS D4 (community tip) |

Classic access token scopes for the same data (DOCS D1):
`settings.read`, `metrics.read`, `entities.read`, `slo.read`, `problems.read`, `events.read`,
`syntheticExecutions.read`, v1 `ReadConfig`, `ReadSyntheticData`, `DataExport`.

Platform scope for reading OpenPipeline configuration (needed for log and metric extraction rules):
UNVERIFIED, not looked up.
Platform token scopes for the classic Metrics v2, Entities v2 and Problems v2 endpoints: UNVERIFIED.
The classic-service page maps classic endpoints to OAuth scopes of the form
`environment-api:<resource>:read` (DOCS D40, one example); I did not find the full list, so do not guess
names. The importer should read metrics and entities through DQL instead (DOCS D24, D25).

### 2.4 Which token to ask the owner for

A platform token on a service user, read-only, with exactly the eight scopes in the table above
(`settings:schemas:read`, `settings:objects:read`, `automation:workflows:read`, `slo:slos:read`,
`synthetic:monitors:read`, `document:documents:read`, `storage:metrics:read`, `storage:entities:read`),
expiry set to days not never, restricted to the one environment (DOCS D2). The owner's existing
`dt0s16` token may already carry some of these; its scopes are visible only in the Account Management
page, so the owner should check there rather than test by calling the API.

## 3. Hosts, auth, pagination, limits

| Topic | Fact | Evidence |
|---|---|---|
| Platform API base | `https://<id>.apps.dynatrace.com/platform/<service>/v<n>/...` | DOCS D6, D19, D21, D24, D37, D38 |
| Classic API base for SaaS | `https://<id>.live.dynatrace.com/api/v2/...` with `Api-Token` | DOCS D5, D28 |
| Classic API in a hybrid tenant | `v2/apiTokens` moves to `.apps`; other classic endpoints not stated | DOCS D3 |
| Platform token on the `.live` host | Docs show `Bearer` with a platform token on `.live` for ingest (`/api/v2/logs/ingest`). Not stated for Settings reads. | DOCS D1; settings UNVERIFIED |
| Platform token on classic settings via `.apps` | `/platform/classic/environment-api/v2/settings/objects`, Bearer, `settings:objects:read` | DOCS D4 (community) |
| Classic settings list | `GET /api/v2/settings/objects?schemaIds=...&fields=objectId,value,...&pageSize=...&nextPageKey=...`. Either `schemaIds` or `scopes` is required on the first page. Page size max 500, default 100. When `nextPageKey` is set, send no other query parameters. Response: `items`, `nextPageKey`, `pageSize`, `totalCount`. | DOCS D5 |
| Platform settings list | `GET /platform/settings/v1/objects?schema-id=...&scope=...&add-fields=value,summary,...&page-size=...&page-key=...`. Page size max 500. Default fields are only `objectId` and `version`, so `add-fields=value` is required. Both `schema-id` and `scope` are marked required on the first page. | DOCS D6 |
| SLO list | `GET /platform/slo/v1/slos`, `page-size` max 500, `page-key` | DOCS D19 |
| Documents list | `page-size` max 1000, `page-key` | DOCS D38 |
| Metrics v2 query | up to 10 metrics per selector, default 120 points, finest resolution 1 minute | DOCS D26, D28 |
| Rate limits (classic) | One request pool per environment with a queue. Full pool or 10 second queue timeout returns 429. No per-endpoint numbers published. Read access is free on fair use. | DOCS D30 |
| Rate limits (platform) | 429 error body carries `retryAfterSeconds`. No numeric limit published. | DOCS D6 |
| Per-endpoint numeric limits | UNVERIFIED |
| DQL cost | The query API has `enforceQueryConsumptionLimit` and `defaultScanLimitGbytes`, so queries consume billable capacity. Set a low scan limit and a short window on every query. Price is not read here. | DOCS D24; price UNVERIFIED |
| Request payload | 1 MB cap on requests | DOCS D30 |

Retry rule for the extractor: on 429 wait `retryAfterSeconds` if present, else back off; cap pages and
total calls the way `MAX_PAGES` does in `src/importer/sources/gcp/extract.py` (REPO).

## 4. Alerting object model

### 4.1 Objects, generation, current path

| Object | Where it lives | Generation | Recommended in a `.apps` tenant | Evidence |
|---|---|---|---|---|
| Built-in Davis anomaly detection (services, hosts, k8s) | automatic; only sensitivity is tuned | both | yes, nothing to export except tuning settings | DOCS D14; tuning schemas UNVERIFIED |
| Metric events | Settings schema `builtin:anomaly-detection.metric-events`, scope `environment`, up to 10,000 | Classic | no: docs push users to Anomaly Detection | DOCS D7, D9 |
| Davis anomaly detectors ("custom alerts", Anomaly Detection app) | Settings schema `builtin:davis.anomaly-detectors` | Latest | yes | DOCS D8, D10, D14 |
| Alerting profiles | `builtin:alerting.profile` | Classic | replaced by Workflow trigger filters | DOCS D11, D13 |
| Problem notifications | `builtin:problem.notifications`, one alerting profile each | Classic | replaced by Workflows | DOCS D12, D13 |
| Workflows | Platform Automation API | Latest | yes | DOCS D14, D37 |
| Maintenance windows | Classic: `builtin:alerting.maintenance-window`. Latest: a new Maintenance windows feature (Preview, Jun 2026): max 100 upcoming windows, max 7 days each | both | the new feature, but it is Preview | DOCS D15, D16 |
| Management zones | `builtin:management-zones`; docs recommend manual tags instead; Latest uses security context | Classic | tags and security context | DOCS D17, D11, D13 |
| SLOs | Classic: `builtin:monitoring.slo` (up to 10,000). Latest: Platform SLO API with objective templates | both | Platform SLO API | DOCS D18, D19 |
| Synthetic monitors | Classic `/api/v1/synthetic/monitors` (HTTP, BROWSER); Platform `/platform/synthetic/v1/monitors` | both | Platform | DOCS D20, D21 |
| Log events (log pattern triggers an event) | `builtin:logmonitoring.log-events`; Latest adds OpenPipeline alert-style extraction and detectors on records | both | OpenPipeline and detectors on `fetch logs` | DOCS D22, D14, D10 |
| Log metrics | `builtin:logmonitoring.schemaless-log-metric`; OpenPipeline metric extraction in Latest | both | OpenPipeline | DOCS D23, D14 |
| Dashboards, notebooks | Documents API (Latest); classic dashboards under the Configuration API | both | Documents | DOCS D38; classic path UNVERIFIED |
| Problems | Davis problems in Grail (`davis.problems` is a Grail data type); Problems v2 API in classic | both | Grail | DOCS D33 (data type listed), D1 (`problems.read`) |

Event grouping. Anomaly detectors raise Davis events; Davis groups related events into a problem and
adds impacted entities and root cause; Workflows trigger on the problem, a Davis event or a generic
event (DOCS D14).

### 4.2 Metric event object (Classic)

Fields from the schema table (DOCS D7). The JSON key for "Sliding window samples" was not captured in
the page text I could extract; the key is probably `slidingWindow` (UNVERIFIED).

```
enabled, summary, eventEntityDimensionKey, legacyId
queryDefinition:
  type: METRIC_KEY | METRIC_SELECTOR
  metricKey, metricSelector, aggregation (MIN MAX SUM COUNT AVG VALUE MEDIAN PERCENTILE90)
  managementZone, queryOffset
  entityFilter: { dimensionKey, conditions: [ { type, operator, value } ] }
  dimensionFilter: [ { dimensionKey, operator, dimensionValue } ]
modelProperties:
  type: STATIC_THRESHOLD | AUTO_ADAPTIVE_THRESHOLD | SEASONAL_BASELINE
  threshold, signalFluctuation, tolerance
  alertCondition: ABOVE | BELOW | OUTSIDE
  alertOnNoData
  violatingSamples, slidingWindow(?), dealertingSamples      (one-minute samples)
eventTemplate: { title, description, eventType, metadata[] }
  eventType: INFO ERROR AVAILABILITY SLOWDOWN RESOURCE CUSTOM_ALERT CUSTOM_ANNOTATION ...
entity filter condition types: NAME ENTITY_ID MANAGEMENT_ZONE TAG HOST_NAME HOST_GROUP_NAME
  PROCESS_GROUP_NAME PROCESS_GROUP_ID CUSTOM_DEVICE_GROUP_NAME
```

Rules stated in the docs (DOCS D9, D7):

- Metric key events support only static thresholds. Auto-adaptive and seasonal baseline need a metric
  selector query.
- Defaults seen in the Latest doc: 3 violating samples of a 5 minute window raise an event; 5 recovered
  samples close it; sliding window up to 60 minutes (DOCS D10).
- `alertOnNoData` is ORed with the threshold condition (DOCS D10).

A real example exported by the API for this schema: no DOCS example found, so no sample JSON of a full
exported object with real values is reproduced here. The vendor GitHub repo `Dynatrace/dynatrace-for-ai`
(a Dynatrace-organisation repo, not the docs site) gives this older-format sample (DOCS D39) and itself
warns the shape is an older representation:

```json
{
  "enabled": true,
  "queryDefinition": {
    "type": "METRIC_KEY",
    "metricKey": "builtin:service.errors.total",
    "aggregation": "AVG",
    "entityFilter": { "dimensionKey": "dt.entity.service",
                      "conditions": [ { "type": "TAG", "value": "env:production" } ] }
  },
  "modelProperties": { "type": "STATIC_THRESHOLD", "threshold": 5.0, "alertCondition": "ABOVE",
                       "violatingSamples": 3, "dealertingSamples": 5 },
  "eventTemplate": { "title": "Error rate above 5% in production", "eventType": "ERROR" }
}
```

The sample uses key names (`violationType`, `violationWindowInMinutes`, `severity`) that differ from
the schema table, which confirms the importer must read a real export before fixing field names. This
is unknown 1 in section 9.

### 4.3 Davis anomaly detector object (Latest)

Schema fields (DOCS D8): `enabled`, `title`, `description`, `source`, `executionSettings`
(`actor` = service-user UUID, `queryOffset`, `delay`), `analyzer` (`name`, `input[]` of key/value strings),
`eventTemplate.properties[]` (key/value, supports `{dims:...}` placeholders).

Analyzer classes and input keys, from the Dynatrace-organisation GitHub repo (DOCS D39; not the docs
site, and it uses scope `tenant` where the schema table says `environment`):

| Analyzer `name` | Model | Key inputs |
|---|---|---|
| `dt.statistics.ui.anomaly_detection.StaticThresholdAnomalyDetectionAnalyzer` | static threshold | `query`, `threshold`, `alertCondition` |
| `dt.statistics.ui.anomaly_detection.AutoAdaptiveAnomalyDetectionAnalyzer` | auto-adaptive threshold | `query.expression`, `numberOfSignalFluctuations`, `alertCondition` |
| `dt.statistics.ui.anomaly_detection.SeasonalBaselineAnomalyDetectionAnalyzer` | seasonal baseline | `query.expression`, `tolerance`, `alertCondition` |

Shared inputs: `alertOnMissingData`, `violatingSamples`, `slidingWindow`, `dealertingSamples`
(DOCS D39). The `query` is a DQL `timeseries` command, for example
`timeseries avg(dt.host.cpu.usage), by: {dt.entity.host}` (DOCS D39, D25).

There is also a records mode: each row a DQL query returns is an alert, with `alertIdentityFields`
selecting stable fields; used for logs, events, missing-event checks and Smartscape conditions
(DOCS D10). Records mode can hold arbitrary DQL, so it is manual.

Other detector sources, also stated in D39 (UNVERIFIED beyond that single source): OneAgent edge alerts
(disk, process availability), OpenPipeline alerts on logs, synthetic alerts, and externally ingested events.
Edge alerts run on the agent, so no query exists to convert.

### 4.4 Alerting profile and problem notification

Alerting profile fields (DOCS D11): `name`, `managementZone` (one), `severityRules[]`
(severity AVAILABILITY, CUSTOM_ALERT, ERRORS, MONITORING_UNAVAILABLE, PERFORMANCE, RESOURCE_CONTENTION;
`delayInMinutes`; tag filter NONE / INCLUDE_ANY / INCLUDE_ALL with `tagFilter`), `eventFilters[]`
(predefined event type or custom filter). Max 100 rules and 100 filters each.

Problem notification (DOCS D12): `enabled`, `type`, `displayName`, `alertingProfile`, and one type object.
Types: EMAIL, SLACK, JIRA, ANSIBLETOWER, OPS_GENIE, PAGER_DUTY, VICTOROPS, WEBHOOK, XMATTERS, TRELLO,
SERVICE_NOW. Webhook fields: `url` or `secretUrl`, `headers[]`, `payload` with placeholders such as
`{ProblemTitle}`, `{State}`, `{ProblemSeverity}`, `{ProblemImpact}`, `{Tags}`, `{ProblemDetailsJSONv2}`,
`notifyClosedProblems`, `notifyEventMergesEnabled`, `useOAuth2`. Secrets are typed `secret` in the
schema (Slack URL, PagerDuty `serviceApiKey`, OpsGenie `apiKey`, Jira `apiToken`, ServiceNow `password`).
Whether the API returns secret values or masks them: UNVERIFIED (assume masked, like the GCP channels in
REPO `src/importer/sources/gcp/extract.py`, and have the owner re-enter secrets).

Dynatrace's own upgrade guide maps one alerting profile plus one notification to one Workflow with a
Problem trigger whose DQL matchers do the filtering (DOCS D13). A single workflow can replace several
profile and notification pairs that share a destination (DOCS D13).

### 4.5 SLO, synthetic, others

- Classic SLO fields (DOCS D18): `name`, `customDescription`, `metricName`, `metricExpression`
  (a metric selector), `evaluationType` AGGREGATE, `filter` (entity selector), `evaluationWindow`
  (for example `-1w`), `targetSuccess`, `targetWarning`, `errorBudgetBurnRate`
  (`fastBurnThreshold`, `burnRateVisualizationEnabled`).
- Platform SLO API: list, get, create, evaluate, with objective templates (DOCS D19). Response shape not
  read: UNVERIFIED.
- Synthetic monitors: HTTP and BROWSER types with locations, tags, `managementZone` filters (DOCS D20).
  Script and credential details: UNVERIFIED. Credentials live in a vault and are not exported
  (UNVERIFIED).
- Log event: `enabled`, `summary`, `query` (matcher), `eventTemplate` (DOCS D22).

## 5. Query languages and NRQL

### 5.1 Metric selector (classic) to NRQL

Syntax: `metric.key:transform1:transform2`, up to 10 metrics per query, arithmetic between selectors
allowed with brackets, `metric1 / metric2 * 100` (DOCS D26, D27). Quotes and tildes inside strings are
escaped with `~` (DOCS D26); the parser must handle that escape and every value that reaches NRQL must
go through `quote_string`/`identifier` (REPO CLAUDE.md).

| Dynatrace | NRQL | Converts? | Evidence |
|---|---|---|---|
| `:avg`, `:min`, `:max`, `:sum` | `average()`, `min()`, `max()`, `sum()` | yes | DOCS D26 |
| `:count` (values in a time slot) | `count()` | yes for event data; metric semantics differ | DOCS D26 |
| `:median` | `percentile(x, 50)` | yes | DOCS D26 |
| `:percentile(99.9)` | `percentile(x, 99.9)`; needs histogram or distribution data in New Relic | approximate | DOCS D26; NR side UNVERIFIED |
| `:value` | no aggregation, use `latest()` | approximate | DOCS D26; NR side UNVERIFIED |
| `:auto` (metric's default aggregation) | needs the metric descriptor's `defaultAggregation` | needs metadata | DOCS D26 |
| `:histogram` | none for alert conditions | no | DOCS D26 |
| `:splitBy("a","b")` | `FACET a, b` | yes | DOCS D26 |
| `:splitBy()` (empty) | no FACET | yes | DOCS D26 |
| `:filter(eq/ne/prefix/suffix/contains(...))` | `WHERE a = / != / LIKE 'x%' / LIKE '%x' / LIKE '%x%'` | yes | DOCS D26 |
| `:filter(and/or/not(...))` | `AND / OR / NOT` | yes | DOCS D26 |
| `:filter(in("dim",entitySelector(...)))` | `WHERE dim IN (...)` only if the entity list is resolved first | manual | DOCS D26 |
| `:filter(series(avg,gt(10)))` (keep series whose period average exceeds a value) | none in a condition | no | DOCS D26 |
| `:filter(remainder("d"))`, `existsKey` | `existsKey` maps to `IS NOT NULL`; remainder no | partly | DOCS D26 |
| `:fold(agg)` (collapse series to one point) | the condition's own window aggregation | yes | DOCS D26 |
| `:rate(1m)` | `rate(sum(x), 1 minute)` for counters | approximate | DOCS D26; NR side UNVERIFIED |
| `:delta` (difference from previous point, negative becomes 0) | `derivative()` differs on negatives | manual | DOCS D26; NR side UNVERIFIED |
| `:rollup(avg,15m)` (trailing window, max 60 minutes) | NRQL `SLIDE BY` on the window | approximate | DOCS D26; NR side UNVERIFIED |
| `:timeshift(-1d)` | `COMPARE WITH` exists in NRQL for charts but not in alert conditions; use a baseline condition | no for alerts | DOCS D26; NR side UNVERIFIED |
| `:default(0)` | gap-filling strategy of the condition | approximate | DOCS D26; NR side UNVERIFIED |
| `:merge("dim")` | drop that dimension from FACET | yes | DOCS D26 |
| `:partition`, `:smooth`, `:sort`, `:limit`, `:last`, `:lastReal`, `:names`, `:parents`, `:setUnit` | display or topology transforms | ignore (sort/limit/unit) or manual (partition, parents, names) | DOCS D26 |
| arithmetic between metrics | `filter(sum(a))/filter(sum(b))*100`; the IR already holds this as `BinOp` | yes | DOCS D27; REPO `src/importer/core/ir.py` |
| entity selector `type(HOST),tag("x"),entityName.startsWith("y")`, `mzName`, relationships | `WHERE` over entity attributes or tags; relationships need topology | tag and name yes, relationships no | DOCS D29 |
| `queryOffset` (minutes) | aggregation delay (`aggregation_delay_seconds`) | yes | DOCS D7; REPO ir.py |

### 5.2 DQL to NRQL

- `timeseries agg(metric[, filter:, default:, rollup:, rate:]), by:{...}, filter:, interval:|bins:, from:, shift:`
  is the Latest equivalent of a metric selector (DOCS D25). Aggregations: sum, avg, min, max, count (counts
  distinct series, not samples), percentile and percentRank (histogram metrics; gauges and counts need a
  `rollup` argument), countDistinct (DOCS D25). Mapping follows the table above, with this exception:
  DQL `count(metric)` is series cardinality and has no NRQL counterpart (DOCS D25).
- `fetch logs | filter ... | summarize count()` maps to `SELECT count(*) FROM Log WHERE ...` for simple
  filters (DOCS D10 shows records mode; NRQL side REPO sources/gcp log-match translation).
- DQL with `parse`, `lookup`, `join`, `fieldsAdd` pipelines, Smartscape traversal, or arbitrary
  record-mode queries: manual. The NRQL has no pipeline equivalent for most of these (UNVERIFIED in detail).
- Classic metric keys are `builtin:...` and Grail keys are `dt.*` (for example `dt.host.cpu.usage` in the
  DQL docs, DOCS D25, vs classic `builtin:host.cpu.usage`, DOCS D26). A general rule between the two
  forms is UNVERIFIED; read it from a live descriptor.
- Units: classic service response time is in microseconds, New Relic duration is in seconds
  (UNVERIFIED for both). A threshold in the wrong unit is silent wrong output, so unit handling needs a
  per-metric table and a test.

### 5.3 What cannot map (summary)

Records-mode detectors on arbitrary DQL, `series()` filters, `:timeshift` in alerts, `:delta` with clamp,
topology relationship selectors, edge (OneAgent-side) alerts, Davis built-in adaptive detection with no
user configuration, Smartscape-based conditions, and "event type" semantics (Dynatrace severity belongs to
the problem category, not to the detector).

## 6. Getting Dynatrace data into New Relic

| Route | Supported? | What would arrive | Evidence |
|---|---|---|---|
| OpenTelemetry Collector exporting to several backends, including Dynatrace and New Relic | Yes, for telemetry that passes through your own collector or SDK. It cannot export data Dynatrace has already stored. | OpenTelemetry names and attributes as produced by the app. | DOCS D32; NR OTLP docs page exists, `docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/` (not read in depth) |
| Dynatrace OTLP API | Inbound only (into Dynatrace) | not applicable | DOCS D35 |
| Metrics API v2 poll plus push to the New Relic Metric API | No product; custom bridge. Names would be whatever we choose (for example `dynatrace.<metric key>`). Dimensions are entity IDs (`dt.entity.host`) not names, so a lookup join is needed. | custom | DOCS D26, D28 (read side); bridge UNVERIFIED |
| OpenPipeline forwarding | Yes, to cloud object storage (AWS, Azure, GCS). Scopes include logs, events, metrics, spans, `davis.problems`. NDJSON, ZSTD or GZIP, batches about once a minute. | raw Dynatrace records in a bucket. A second hop from the bucket into New Relic is needed. | DOCS D33; second hop UNVERIFIED |
| OneAgent dual shipping to another vendor | No documentation found | n/a | UNVERIFIED |
| Prometheus scrape of Dynatrace metrics | Dynatrace documents ingesting Prometheus (inbound). I found no vendor endpoint that exposes Dynatrace metrics in Prometheus format; open-source community exporters exist. | n/a | DOCS D34 (inbound only); outbound UNVERIFIED |
| ActiveGate extensions | They collect data into Dynatrace (inbound). No outbound path found. | n/a | UNVERIFIED |
| A Dynatrace integration in New Relic | None found | n/a | UNVERIFIED |

Consequences for the importer:

1. The metric map for a Dynatrace source cannot be "Dynatrace metric to the same metric in New Relic".
   It must be "Dynatrace metric key to the New Relic agent or OpenTelemetry signal that measures the
   same thing", like `gcp_verified.json` is for GCP (REPO `src/importer/sources/gcp/gcp_verified.json`),
   but here built by us and checked on live New Relic data.
2. Metrics only OneAgent produces need New Relic agents or OpenTelemetry on the hosts and apps. Per
   the docs OneAgent is the collector for host, process, service and PurePath data, and its local OTLP
   endpoint accepts traces only (DOCS D31 for the OTLP limit; the OneAgent-only metric list is UNVERIFIED).
   In practice this means built-in `builtin:host.*`, `builtin:process.*`, `builtin:service.*`,
   `builtin:tech.*`, Real User Monitoring and session metrics (UNVERIFIED family list). These are
   re-instrumentation items; the report should say so rather than emit a condition with no data.
3. Metrics Dynatrace pulled from cloud APIs (for example AWS CloudWatch or Azure Monitor) exist in New
   Relic through its own cloud integrations. Those can reuse the existing `cloudwatch` and `gcp` metric
   maps. Dynatrace key names for them (`cloud.aws.*`) are UNVERIFIED.
4. Custom metrics sent with the Dynatrace Metrics API or Micrometer/OTel would need the same code pointed
   at New Relic (OTLP or Metric API). Custom metrics are a re-instrumentation item.
5. A "data check" like `check-gcp-data` (REPO `src/importer/sources/gcp/data_check.py`) is more
   important here than for GCP, because the default state will be "no data".

## 7. Analogue table for a manager

"Converts" means the importer can produce a New Relic object and the owner reviews it. "Manual" means a
person rebuilds it using the report. "None" means no New Relic equivalent.

New Relic names below are from this repo's IR and emitters (REPO `src/importer/core/ir.py`,
`notification_ir.py`) or are UNVERIFIED where marked.

| Dynatrace | New Relic | Estimate | Notes |
|---|---|---|---|
| Metric event, static threshold | NRQL alert condition (static) | converts, if the data exists in New Relic | REPO ir.py `Threshold`; DOCS D7 |
| Metric event or detector, auto-adaptive threshold | Baseline NRQL condition | converts, approximate | IR has `BaselineSpec` with deviations and direction (REPO ir.py). Dynatrace "signal fluctuations" and New Relic "deviations" are not the same scale: UNVERIFIED. |
| Seasonal baseline | Baseline NRQL condition | converts, approximate | same caveat |
| Sliding window n of m samples | Condition threshold duration, "all" or "at least once" | approximate | IR has only `ALL` and `AT_LEAST_ONCE` (REPO ir.py); n of m has no exact match: UNVERIFIED on the New Relic side |
| Dealerting samples (recovery) | Condition recovery/close behaviour | approximate | UNVERIFIED |
| Alert on missing data | Loss of signal or gap filling | converts | REPO `NoData`; NR side UNVERIFIED |
| Records-mode detector (arbitrary DQL) | none automatic | manual | DOCS D10 |
| OneAgent edge alert (disk, process availability) | Infrastructure conditions | manual | DOCS D39 only |
| Log event (pattern) | NRQL condition on `Log` | converts for simple matchers | REPO count-signal LOG kind |
| Problem | Issue | concept | New Relic groups incidents into issues; correlation differs: UNVERIFIED |
| Davis root cause and impact analysis | Applied Intelligence correlation | none for root cause | different method; DOCS D14 for the Dynatrace side |
| Alerting profile (severity, tags, delay) | Workflow filter | converts as a pair with the notification | DOCS D13 describes the same collapse; NR workflow filter fields UNVERIFIED |
| Problem notification, email / Slack / PagerDuty / webhook | Destination + channel in a Workflow | converts, secrets re-entered | REPO notification_ir.py (kinds, planned destinations and workflows); DOCS D12 |
| Jira, ServiceNow, OpsGenie, VictorOps, xMatters, Trello, Ansible Tower notification | Matching destination if New Relic has one, else webhook | manual for most | NR destination list UNVERIFIED |
| Dynatrace Workflow (Latest) | Workflow | partial: trigger filter and notification actions only; custom JavaScript, remediation actions and agentic steps are manual | DOCS D14, D37 |
| Placeholders `{ProblemTitle}`, `{State}` ... | New Relic template variables | converts with a table | DOCS D12; NR variable list UNVERIFIED |
| Maintenance window | Muting rule | converts for time-based windows; entity scope depends on tags | DOCS D15, D16; NR side UNVERIFIED |
| Management zone | Tags on the condition and account scope | manual: zones are rule-built entity sets | DOCS D17 |
| SLO | Service level (SLI/SLO) | converts if the SLI is expressible as good/total NRQL; calendar windows may not match | DOCS D18; NR side UNVERIFIED |
| Synthetic monitor, HTTP | Synthetic ping / API monitor | converts | DOCS D20; NR side UNVERIFIED |
| Synthetic monitor, browser clickpath | Scripted browser | manual (script rewrite) | UNVERIFIED |
| Dashboard / notebook | Dashboard | partial: tiles with simple metric queries; DQL-heavy tiles manual | DOCS D38; repo has a dashboard pipeline (REPO `core/dashboard_ir.py`) |
| Log metric / OpenPipeline extraction | Log parsing rule, or NRQL on `Log` | manual | DOCS D23 |
| Calculated service metrics, request attributes | none | none | UNVERIFIED |
| Business events, user sessions, RUM | Browser / Mobile / custom events | manual and re-instrumentation | UNVERIFIED |
| Smartscape topology | Entity relationships | none | UNVERIFIED |

Rough proportions for a first estimate. These are guesses, not measured: of exportable alert
configuration, simple threshold metric events and notifications convert; baselines convert with
review; anything depending on entity topology, records-mode DQL or OneAgent-only data is manual or
re-instrumentation. Do not quote a percentage to the manager until the live read in section 9 gives
real counts.

## 8. Build plan

Follows the shape of the GCP source: `models.py`, `extract.py`, `translate.py`, `remediation_rules.py`,
a `Source` registered in `__init__.py`, fixtures, CLI command, `doctor` support (REPO
`src/importer/sources/base.py`, `src/importer/sources/gcp/`, `src/importer/cli.py`).

| Phase | Work | Effort | Risk | Done when |
|---|---|---|---|---|
| 0 | Owner creates the read-only token (section 2.4) and approves the calls in section 9. Capture one sanitized export per schema as the fixture basis. | small | low | fixture files reviewed by the owner |
| 1 | `models.py`: pydantic models with `extra="allow"` for metric events, detectors, alerting profiles, notifications, maintenance windows, workflows. Loader accepts both the classic list shape (`items`, `nextPageKey`) and the platform shape. Handles the Settings `value` wrapper. Input size cap like `MAX_INPUT_BYTES`. | small to medium | medium: field names in 4.2 and 4.3 are unconfirmed | models load the real export without dropping fields |
| 2 | `extract.py`: allow-listed GET paths only, schema ids from a constant list (not user text), page cap, 429 handling with `retryAfterSeconds`, token from env only, bearer header only, host pattern check (`<id>.apps.dynatrace.com`). Notification secrets and webhook URLs written as type and name only. CLI `extract-dynatrace` and `doctor --source dynatrace`. | medium | medium: host and path variants (section 3), secret masking | extract against the owner tenant writes a clean folder |
| 3 | Metric selector parser (selector grammar in 5.1) and DQL `timeseries` subset parser. Unsupported syntax returns a labelled failure, never a guess. Escapes `~` handled. | large | high: two grammars and nested filters; needs property tests | table in 5.1 covered by tests |
| 4 | `translate.py`: metric events and detectors to `IRMonitor` (threshold, baseline, no-data, window, offset); notifications to the notification plan; alerting profile plus notification pairing; severity mapping (Dynatrace has no detector severity). Metric map file for Dynatrace metric to New Relic signal with units. | large | high: metric map is built by hand and needs live verification; n-of-m windows approximate | golden file reviewed by a human (REPO CLAUDE.md rule) |
| 5 | `remediation_rules.py`: texts for records-mode DQL, edge alerts, OneAgent-only metrics (re-instrument), management zones, `series()`, `:timeshift`, `:delta`, Jira/ServiceNow etc., maintenance windows, secrets to re-enter. | small | low | each unsupported branch has a rule |
| 6 | Fixtures and tests: sanitized real export plus hand-built edge cases (every model type, each transform, 429, pagination, hostile names for the escaping rules). | medium | low | `ruff`, `mypy --strict`, `pytest`, `pip-audit` green |
| 7 | Verification: `check-dynatrace-data` equivalent of `check-gcp-data`, and a sandbox apply in the New Relic test account only (create-only). | medium | medium: expected result is mostly "no data" until agents are installed | report says per condition whether data exists |
| 8 | Later: workflows, maintenance windows to muting rules, SLOs, synthetics, dashboards. Each is its own source or sub-converter. | large each | medium to high | per object |

Order recommendation: 0, 1, 2, then 4 for static metric events only, then 3 and 5, then baselines.
Smallest useful slice: metric events with static thresholds, problem notifications for email, Slack,
PagerDuty and webhook, and a report that lists everything else.

## 9. Top unknowns that need a live read-only call

Each is a read-only GET (or one DQL `timeseries`/`fetch` with a short window and a low scan limit). Owner
approval needed per call type; none changes anything. I did not make any of them.

1. Real JSON of one metric event and one anomaly detector as the API returns it (key names in 4.2 and 4.3,
   `scope` value `environment` vs `tenant`, the key for sliding window).
   Call: `GET /platform/settings/v1/objects?schema-id=builtin:anomaly-detection.metric-events&scope=environment&add-fields=value,summary&page-size=5`
   and the same for `builtin:davis.anomaly-detectors`.
2. Whether the platform token can call `GET /platform/classic/environment-api/v2/settings/objects` on the
   `.apps` host, and whether it also works on `.live` (section 3).
3. Which scopes the existing `dt0s16` token already holds, and whether the user behind it can read settings
   (no API call needed: check the token page in Account Management).
4. Counts per schema (`totalCount` only, `page-size=1`) for metric events, detectors, alerting profiles,
   notifications, workflows, SLOs, monitors, documents. This sizes the work and tells which generation the
   tenant really uses.
5. Whether secrets in `builtin:problem.notifications` come back masked or in clear.
6. Metric inventory used by the real alerts: distinct `metricKey`/`metricSelector` values, then one
   `GET` of the metric descriptor (or a DQL metric listing) for each, to get unit, default aggregation and
   whether the metric is OneAgent-only or cloud-pulled. Needs `storage:metrics:read` or `metrics.read`.
7. Mapping rule from `builtin:` keys to `dt.` keys (5.2) from live descriptors.
8. Entity lookups for `dt.entity.*` IDs: one DQL `fetch dt.entity.host` style query with a limit, to learn
   which names and tags exist for translating entity filters.
9. Rate limit behaviour (does a 429 appear at 5 concurrent pages?). Cheap test: sequential calls with the
   observed response headers recorded.
10. Whether DQL queries from the platform token count against a billed consumption and how much for the
    sample queries in item 6 and 8 (Account Management usage view, no call).
11. The platform scope names for reading OpenPipeline configuration, Notification v2 and classic Metrics
    v2 / Entities v2 / Problems v2 (documentation lookup, no tenant call needed; not done here).
12. Whether the tenant is Classic, Hybrid or Latest (3rd row of section 2.2), which decides whether Classic
    objects still hold the real alerts.

## 10. What the owner must do

1. In Account Management, My platform tokens: create a token for a service user (or yourself), scopes
   `settings:schemas:read`, `settings:objects:read`, `automation:workflows:read`, `slo:slos:read`,
   `synthetic:monitors:read`, `document:documents:read`, `storage:metrics:read`, `storage:entities:read`;
   limit it to one environment; set an expiry. Put it only in `.env` (CLAUDE.md secrets rule).
2. Confirm the user behind the token can read settings in that environment. A scope does not grant more
   than the user's own permissions (DOCS D2).
3. Check whether the corporate network allows `*.apps.dynatrace.com`. CLAUDE.md says the Datadog site is
   blocked; Dynatrace has not been tested and must not be worked around if it is blocked.
4. Approve the call list in section 9 per session (REPO memory rule: approvals are per session).
5. Review the 4.2 and 4.3 sample shapes after the first export before any golden file is created.

## Sources

| Key | URL |
|---|---|
| D1 | https://docs.dynatrace.com/docs/dynatrace-api/basics/dynatrace-api-authentication |
| D2 | https://docs.dynatrace.com/docs/manage/identity-access-management/access-tokens-and-oauth-clients/platform-tokens |
| D3 | https://docs.dynatrace.com/docs/platform/upgrade/set-up-your-environment/upgrade-from-access-tokens-classic |
| D4 | https://community.dynatrace.com/t/classic-environment-api-with-platform-token/55231 (community post, March 2026, not reference docs) |
| D5 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/objects/get-objects |
| D6 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-settings-v1/objects-get |
| D6b | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-settings-v1/schemas-get |
| D7 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-anomaly-detection-metric-events |
| D8 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-davis-anomaly-detectors |
| D9 | https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection-classic/metric-events |
| D10 | https://docs.dynatrace.com/docs/dynatrace-intelligence/anomaly-detection/anomaly-detection-configuration |
| D11 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-alerting-profile |
| D12 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-problem-notifications |
| D13 | https://docs.dynatrace.com/docs/platform/upgrade/keep-problems-and-alerting-working/upgrade-guide-alert-notification |
| D14 | https://docs.dynatrace.com/docs/analyze-explore-automate/alerting-and-notifications |
| D15 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-alerting-maintenance-window |
| D16 | https://docs.dynatrace.com/docs/analyze-explore-automate/alerting-and-notifications/maintenance-windows |
| D17 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-management-zones |
| D18 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-monitoring-slo |
| D19 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-slo-v1 and .../platform-slo-v1/slos-get |
| D20 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-monitors/get-all-monitors |
| D21 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-synthetic-v1/monitors-get |
| D22 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-logmonitoring-log-events |
| D23 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-logmonitoring-schemaless-log-metric |
| D24 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post |
| D25 | https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/metric-commands |
| D26 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/metric-selector |
| D27 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/metric-expressions |
| D28 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-data-points |
| D29 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/entity-selector |
| D30 | https://docs.dynatrace.com/docs/dynatrace-api/basics/access-limit |
| D31 | https://docs.dynatrace.com/docs/ingest-from/dynatrace-oneagent/oneagent-and-opentelemetry/oneagent-otel |
| D32 | https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector-use-cases/multi-export |
| D33 | https://docs.dynatrace.com/docs/platform/openpipeline/concepts/forwarding |
| D34 | https://docs.dynatrace.com/docs/ingest-from/extend-dynatrace/extend-metrics/ingestion-methods/prometheus |
| D35 | https://docs.dynatrace.com/docs/ingest-from/opentelemetry/otlp-api |
| D37 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-automation-v1/workflows-get |
| D38 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-document-v1/documents-get |
| D39 | https://github.com/Dynatrace/dynatrace-for-ai/blob/main/skills/dt-alerting/references/anomaly-detectors.md (Dynatrace GitHub organisation; not the docs site) |
| D40 | https://developer.dynatrace.com/plan/platform-services/classic-environment-service/ |

Not read, or read only as a title: the New Relic OTLP page, the Dynatrace Platform SLO response body,
OpenPipeline configuration scopes, classic dashboards API, Notification v2, the Dynatrace Hub AWS/Azure
integration metric names. All New Relic-side behaviour marked UNVERIFIED above was not re-checked against
New Relic docs in this pass.

## Live read of a Platform tenant (2026-11-09)

Read-only GET requests with a platform token (`Authorization: Bearer`) to `/platform/settings/v1`,
`/platform/slo/v1`, `/platform/automation/v1` and `/platform/document/v1` on a `*.apps` host.
Nothing was changed. Values are not recorded here, only counts and structure.

| Finding | Evidence |
|---|---|
| Authentication works; the platform token reads Platform settings, SLOs, Workflows and documents | LIVE |
| `GET /platform/settings/v1/schemas` returns 313 schemas | LIVE |
| The classic alerting schemas do not exist on this tenant: `builtin:anomaly-detection.metric-events`, `builtin:alerting.profile`, `builtin:problem.notifications`, `builtin:management-zones`, `builtin:davis.anomaly-detectors` all return 404 | LIVE |
| The schemas that do exist for alerting are narrow: anomaly detection for services, hosts and Kubernetes, `builtin:event-correlation-rules`, `builtin:maintenance-windows`, `builtin:health-experience.*-alert`, `builtin:problem.fields` | LIVE |
| Object counts: services 0, hosts 0, Kubernetes cluster/node/pvc/workload 1 each, namespace 0, event-correlation rules 34, problem fields 27, maintenance windows 0 | LIVE |
| Integration connections for Slack, PagerDuty, Teams, ServiceNow, Jira: 0 each | LIVE |
| Workflows: 0. SLOs: 0. Site Reliability Guardians: 0 | LIVE |
| Documents: 251 (117 dashboards, 51 workflow templates, 23 AI agent resources, 6 smartscape, 2 notebooks, 1 launchpad). All 200 sampled share one owner and none is private | LIVE |
| From a Python client, the corporate network's TLS inspection breaks certificate checks (the corporate root is missing from Python's bundle). `curl` works through the system trust store | LIVE |

What this means: the counts of 1 and 34 and 27 match Dynatrace defaults, and there are no
integrations, workflows or SLOs. This tenant holds no user-built alerting to migrate. Where
Platform Davis anomaly detectors are stored was not found in the settings schemas; the Davis
Anomaly Detection app is the next place to look (UNVERIFIED).

To build and verify the converter someone has to create alerting objects in this tenant (a write
action that needs the owner's approval per session), or supply an export from a tenant that has
them. Until then the JSON shapes in section 4 remain documentation, not observation.

## Davis anomaly detector read-back: what one real object would settle

Written 2026-11-09 offline. The live read above found no Davis anomaly detector and no settings schema
for one on the owner's Platform tenant, so the detector shape in section 4.3 is still documentation, not
observation. When the owner creates one detector in the Davis Anomaly Detection app (a static threshold on
a host or service metric is enough; a second with a log query is better), these are the questions a
read-back answers. Everything in the "Why it matters" column that is not marked is a plan, not a fact. No
call below has been made; each is a read-only GET and needs the owner's approval for the session.

### Where to look first (all UNVERIFIED, from memory)

1. `GET /platform/settings/v1/schemas` and filter the ids for `davis`, `anomaly` and `detector`. The
   section 4.3 id `builtin:davis.anomaly-detectors` returned 404 on this tenant, so the real id may differ.
2. The Documents API (`GET /platform/document/v1/documents`): the live read showed 251 documents. A
   detector may be stored as a document type or as an AI agent resource rather than as a settings object.
   List the distinct `type` values and look for a new one after the detector is created.
3. The app's own network call, if the owner is willing to copy one request from the browser developer tools
   (URL and response shape only, never the token). This is the quickest route when 1 and 2 find nothing.

### Questions the read-back settles

| # | Question | Why it matters for the converter |
|---|---|---|
| 1 | Which API and id hold the detector (settings schema id, document type, or another service)? | Decides whether `extract-dynatrace` can read it with the existing token scopes or needs a new one. |
| 2 | Is `source` a DQL string, and is it the whole query or only the metric? | A static threshold on `timeseries avg(dt.host.cpu.usage), by: {dt.entity.host}` has no New Relic metric of that name (section 6), so the query decides whether anything converts. |
| 3 | Are the analyzer names exactly `dt.statistics.ui.anomaly_detection.StaticThresholdAnomalyDetectionAnalyzer` and its two siblings? | Section 4.3 took them from a vendor repository, not from the docs site. |
| 4 | Which input keys carry the threshold, the alert condition (ABOVE, BELOW, OUTSIDE), the violating samples, the sliding window and the dealerting samples, and are their values strings or numbers? | The converter needs the exact keys and types to map them to a New Relic threshold and duration. The defaults (3 of 5 minutes, 5 to close) are from DOCS D10 only. |
| 5 | How is "alert on missing data" stored, and what is its default? | A New Relic condition on a missing metric is a loss-of-signal setting, which behaves differently from a threshold. |
| 6 | How does a records-mode detector (a log or event query) store the query and `alertIdentityFields`? | It is the only mode where a log count could convert, which is the one shape the log-count converter already handles. |
| 7 | What does `eventTemplate.properties` hold: title, description, event type, and how are `{dims:...}` placeholders written? | Decides the New Relic condition name and description, and whether placeholders need removing. |
| 8 | What is `executionSettings.actor`, and does the export contain a user or service-user id that must not be copied? | Possible personal data in an export. The scrubber would need a rule before a fixture is committed. |
| 9 | Is the detector linked to a workflow, or does it only raise a Davis event that any Davis problem workflow can pick up? | Decides whether the converted condition can route to the notification plans from `convert-notifications --source dynatrace`, or whether that link must be made by hand. |
| 10 | Does the object carry an `enabled` flag and a modification time? | Needed for the draft and inactive notes already used for workflows. |
| 11 | What do the entity dimensions look like in the result (`dt.entity.host` ids), and is there a name field? | A New Relic condition needs a human-readable filter, not an entity id (see unknown 8 in section 9). |
| 12 | Is a maintenance window or a management zone stored on the detector? | These have no direct New Relic condition field; they would be notes. |

### After the read

1. Save the response with names, ids and user ids replaced, as a new file in `tests/fixtures/dynatrace/`.
2. Add a model and a translate step for the confirmed shape; keep every other detector shape manual.
3. Re-run `importer improve-measure` and review the golden diff before approving it.
4. Update this section and section 4.3 with what was observed, and change each UNVERIFIED mark to LIVE or
   remove the claim.
