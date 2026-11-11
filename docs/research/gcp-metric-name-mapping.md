# Google metric names for the non-polled classes: sourced mapping

Date of the reading: 2026-11-11. Scope: the 63 conditions that need the Ops Agent or OpenTelemetry, the 35 that need
Prometheus remote write and the 4 custom ones in `tests/fixtures/gcp-estate` (docs/research/gcp-estate-baseline.md), and
the widgets on the same metrics. Every claim is marked VERIFIED (with the source read) or UNVERIFIED. The mapping is
data in `src/importer/sources/gcp/metric_names.json`, loaded by `datapath.py`; the converter writes `metric name
VERIFIED` or `metric name UNVERIFIED` on each converted item.

No call to Google Cloud or a tenant was made. The New Relic sandbox was queried read-only for names: it holds no
`system.*`, `agent.*`, `workload.*` or Prometheus metric (only `gcp.*`, `aws.*` and `newrelic.*`), so nothing below is
checked against live data; "VERIFIED" means "read in the vendor's own documentation or source", not "seen arriving".

## 1. Sources read

| Id | Source |
|---|---|
| S1 | Ops Agent source, how each host metric is built: https://raw.githubusercontent.com/GoogleCloudPlatform/ops-agent/master/apps/hostmetrics.go (`RenameMetric`, `ScaleValue`, `AggregateLabels`; plus `apps/<receiver>.go` for the application metrics: `AddPrefix("workload.googleapis.com")`) |
| S2 | Ops Agent collector, how utilization is computed: https://raw.githubusercontent.com/GoogleCloudPlatform/opentelemetry-operations-collector/master/components/otelopscol/processor/agentmetricsprocessor/utils_calculate_utilizations.go (`num / points.sum * 100`) |
| S3 | Google's list of Ops Agent metrics (unit, kind, labels): https://cloud.google.com/monitoring/api/metrics_agent |
| S4 | OpenTelemetry hostmetrics scrapers (name, unit, type, attributes): `https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/receiver/hostmetricsreceiver/internal/scraper/{cpu,memory,filesystem,disk,paging,network,processes,load}scraper/documentation.md` |
| S5 | Receiver metadata for the application metrics: contrib `receiver/<name>receiver/metadata.yaml` (tag v0.116.0 and main), OpenTelemetry Java contrib `jmx-metrics/.../target-systems/*.groovy`, Ops Agent `apps/*.go` |
| S6 | New Relic OTLP metric handling: https://github.com/newrelic/docs-website/blob/develop/src/content/docs/opentelemetry/best-practices/opentelemetry-best-practices-metrics.mdx and `.../opentelemetry-otlp.mdx` |
| S7 | New Relic Prometheus remote write: https://github.com/newrelic/docs-website/blob/develop/src/content/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration.mdx |
| S8 | Managed Service for Prometheus type names: https://cloud.google.com/stackdriver/docs/managed-prometheus/troubleshooting and `/cost-controls` (`prometheus.googleapis.com/<name>/gauge`, `.../unknown`, `.../unknown:counter`) |
| S9 | Prometheus receiver: https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/receiver/prometheusreceiver/README.md (`trim_metric_suffixes` default false) |
| S10 | Google Cloud exporter: https://raw.githubusercontent.com/GoogleCloudPlatform/opentelemetry-operations-go/main/exporter/collector/README.md (`metric.prefix` default `custom.googleapis.com/opencensus/`) |

## 2. How New Relic names and types what arrives (rules used below)

- OTLP: the metric name is kept as sent (dots stay dots). VERIFIED by S6 and the New Relic integration pages that query
  `nginx.requests`, `kafka.message.count`, `rabbitmq.message.current` unchanged. No suffix is added.
- OTLP type, VERIFIED (S6): gauge -> `gauge`; sum monotonic cumulative -> `cumulativeCount` (stored as a delta, query with
  `sum()` or `rate()`); sum monotonic delta -> `count`; sum non-monotonic cumulative -> `gauge`; non-monotonic delta is not
  supported. Cumulative data is converted to deltas by New Relic; delta is recommended (S6, `opentelemetry-otlp.mdx`).
- Prometheus remote write, VERIFIED (S7): the name is kept; `_bucket`, `_count`, `_total` -> count; `_sum` -> summary;
  everything else -> gauge; labels become attributes; a `newrelic_metric_type` relabel overrides the type.
- Prometheus through the OpenTelemetry Prometheus receiver, VERIFIED (S9): names keep `_total` unless the experimental
  `trim_metric_suffixes` is switched on.

## 3. Ops Agent `agent.googleapis.com/*` metrics in the corpus

Fourteen metric types are used (counts are the occurrences of the type in the fixtures: 6 conditions/widgets on
`disk/percent_used`, 5 on `memory/percent_used`, 2 on `cpu/utilization`, 1 on each other type). `oracledb_audit` and
`oracledb_alert` also contain `agent.googleapis.com/` but are log instrumentation sources, not metrics.

Rule (VERIFIED, S1+S2+S4): the Ops Agent runs the OpenTelemetry hostmetrics receiver, renames each metric, and prefixes
`agent.googleapis.com/`. Utilizations are not the receiver's ratio: the Ops Agent multiplies by 100 (S2) and the unit is `%`
(S3). So **Google value = OpenTelemetry value x factor**; a Google threshold is divided by the factor. `Corpus` is the count
above (0 = not in the corpus; the row is in the table because the old hint table listed it).

| Google type | Corpus | OpenTelemetry metric (S4) | Unit / type there | New Relic name, type | Factor | Attributes | Status |
|---|---:|---|---|---|---:|---|---|
| `cpu/utilization` | 2 | `system.cpu.utilization` (optional: enable) | `1` ratio, gauge, per cpu and state | `system.cpu.utilization`, gauge | 100 (percent) | `cpu_state`->`state`, `cpu_number`->`cpu`; Google averages over cores | VERIFIED |
| `cpu/load_1m` `5m` `15m` | 0 | `system.cpu.load_average.{1m,5m,15m}` | `{thread}`, gauge | same, gauge | 1 | none | VERIFIED |
| `memory/percent_used` | 5 | `system.memory.utilization` (optional: enable) | `1` ratio, gauge | `system.memory.utilization`, gauge | 100 | `state` same; Google `slab` = `slab_reclaimable`+`slab_unreclaimable` | VERIFIED |
| `memory/bytes_used` | 0 | `system.memory.usage` | `By`, sum non-monotonic cumulative | same, gauge | 1 | as above | VERIFIED |
| `disk/percent_used` | 6 | `system.filesystem.utilization` (optional: enable) | `1` ratio, gauge | `system.filesystem.utilization`, gauge | 100 | OpenTelemetry has no `state`; filter `state = used` is dropped; Google takes the max over mount points of a device | VERIFIED |
| `disk/bytes_used` | 1 | `system.filesystem.usage` | `By`, sum non-monotonic | same, gauge | 1 | `state` free/used/reserved; max over mount points | VERIFIED |
| `disk/read_bytes_count` | 1 | `system.disk.io` with `direction = read` | `By`, sum monotonic cumulative | `system.disk.io`, cumulativeCount | 1 | filter `direction = 'read'` added | VERIFIED |
| `disk/write_bytes_count` | 1 | `system.disk.io` with `direction = write` | same | same | 1 | filter `direction = 'write'` added | VERIFIED |
| `swap/percent_used` | 0 | `system.paging.utilization` (optional) | `1` ratio, gauge | same, gauge | 100 | | VERIFIED |
| `pagefile/percent_used` | 1 | `system.paging.utilization` (Windows) | `1` ratio, gauge | same, gauge | 100 | Google sums over page files | VERIFIED |
| `interface/traffic` | 0 | `system.network.io` | `By`, sum monotonic | same, cumulativeCount | 1 | `device`<-`interface`; `rx`/`tx` = `receive`/`transmit` | VERIFIED |
| `interface/errors` | 0 | `system.network.errors` | `{errors}`, sum monotonic | same, cumulativeCount | 1 | same direction values | VERIFIED |
| `processes/count_by_state` | 0 | `system.processes.count` | `{processes}`, sum non-monotonic | same, gauge | 1 | `state`<-`status` | VERIFIED |
| `iis/current_connections` | 1 | `iis.connection.active` (iisreceiver) | `{connections}`, sum non-monotonic | would be `iis.connection.active` | 1 | | UNVERIFIED |
| `iis/new_connection_count` | 1 | `iis.connection.attempt.count` | sum monotonic | | 1 | | UNVERIFIED |
| `iis/request_count` | 1 | `iis.request.count` | sum monotonic, attribute `request` | | 1 | Google sums the HTTP methods | UNVERIFIED |
| `iis/network/transferred_bytes_count` | 1 | `iis.network.io` | `By`, attribute `direction` | | 1 | | UNVERIFIED |
| `mssql/connections/user` | 1 | `sqlserver.user.connection.count` | gauge | | 1 | | UNVERIFIED |
| `mssql/transaction_rate` | 1 | `sqlserver.transaction.rate` (Windows) | `{transactions}/s`, gauge | | 1 | | UNVERIFIED |
| `mssql/write_transaction_rate` | 1 | `sqlserver.transaction.write.rate` | gauge | | 1 | | UNVERIFIED |

The seven UNVERIFIED rows are Windows performance counters that the Ops Agent reads itself (`apps/iis.go`,
`apps/mssql.go`); the receiver metrics are the closest by description but Google documents no mapping, and the Ops Agent
counters are `_Total` instances. The Google name (`agent.iis.request_count`) stays in the query, the hint is in the
note, and a name map fixes it once data arrives. The old hint table also had `interface/*`, `swap/*`, `processes/*` and
`cpu/load_*` as UNVERIFIED; they are now VERIFIED from S1 and S4.

Per-core versus aggregate: only `cpu/utilization` differs. Google reports the mean over cores (`AggregateLabels mean`,
S1); the OpenTelemetry metric has one series per `cpu`, so `average()` over all series (what the converted query does)
is the same mean when cores report together. Percent versus ratio is the factor column. Bytes versus KB: none of these
metrics is in KB; `system.disk.io_time` and the other times are seconds in OpenTelemetry and milliseconds or microseconds in
Google (`ScaleValue(1000)`, `ScaleValue(1000000)`, S1) but none is in the corpus.

Delta versus cumulative: Google's `disk/*_bytes_count` are cumulative counters; the OpenTelemetry metric is a cumulative
monotonic sum, which New Relic stores as `cumulativeCount` (a delta per reporting interval). The importer's counter rule
(`sum()` over a window, threshold times the window for a rate) is therefore right, but its note still says "New Relic
stores the change in each 5 minute polling interval", which is the Google integration's interval, not a collector's
(often 60 s). The numbers are unaffected; the wording is a follow-up.

## 4. Third-party application metrics `workload.googleapis.com/*`

Rule (VERIFIED, S1: every `apps/<receiver>.go` calls `AddPrefix("workload.googleapis.com")` and renames nothing): the type
is the prefix plus the OpenTelemetry receiver's own metric name; the New Relic name is that name (section 2).

The corpus uses 229 distinct names (293 occurrences). Each name was looked up in the receiver metadata, the JMX target
scripts and the Ops Agent app definitions (S5):

- 197 found: 117 in a contrib receiver's `metadata.yaml`, 53 in a JMX target script, 27 in the Ops Agent's own app
  definition (oracledb 9, vault 8, couchbase 5, dcgm 5). VERIFIED. The list with the source of each is
  `workload_names` in `metric_names.json`.
- 32 not found, UNVERIFIED: `couchbase.bucket.oom.count`; `flink.job.checkpoint.count`, `flink.job.restart.count`, four
  `flink.jvm.memory.*`; `jvm.memory.heap.init`, `jvm.memory.nonheap.init`, `jvm.memory.nonheap.max`; eight
  `vault.storage.operation.{get,put,list,delete}.{count,time}`; fourteen `sap/...` names (SAP's agent, not an OpenTelemetry
  receiver, and the importer refuses a name with `/`).

Type in New Relic follows section 2 from the receiver's instrument (counters are cumulative monotonic sums:
`cumulativeCount`; up-down counters and gauges: `gauge`).

## 5. Managed Service for Prometheus `prometheus.googleapis.com/<name>/<type>`

Rule: the name is `<name>`; the last segment is Google's value type (VERIFIED for the form, S8). 54 occurrences in the
corpus: gauge 22, counter 22, histogram 8, `summary:counter` 1, `unknown` 1. New Relic name = `<name>` (VERIFIED, S7, S9);
type by suffix (section 2).

| Google type | New Relic | Status / effect |
|---|---|---|
| `<n>/gauge` | `<n>`, gauge, unless `<n>` ends `_total`, `_count` or `_bucket` (then stored as count) | VERIFIED; the converter now says so and gives the `newrelic_metric_type = gauge` relabel |
| `<n>/counter`, `<n>/summary:counter`, `<n>/unknown:counter` | `<n>`, count when `<n>` ends `_total`/`_count`/`_bucket` | VERIFIED; otherwise stored as a gauge: `Requests2XX`, `Requests4XX`, `Requests5XX` in the corpus would give a wrong rate; the converter now says to add the `newrelic_metric_type = counter` relabel |
| `<n>_sum/summary:counter` | `<n>_sum`, summary type | name VERIFIED; which NRQL function fits a summary is UNVERIFIED (noted) |
| `<n>/unknown` | `<n>`, gauge | VERIFIED name |
| `<n>/histogram` | `<n>_bucket` (count, label `le`), `<n>_sum`, `<n>_count` | VERIFIED form (S7); stays manual: one NRQL condition cannot take a quantile from them |

The labels keep their Prometheus names (VERIFIED, S7).

## 6. Custom and external metrics

`custom.googleapis.com/<path>` and `external.googleapis.com/<path>` have the name the sender gave them. The OpenTelemetry
Google exporter writes `metric.prefix` + the OpenTelemetry name, default `custom.googleapis.com/opencensus/` (VERIFIED, S10),
so `custom.googleapis.com/opencensus/config_sync/resource_count` is the sender's `config_sync/resource_count`. What the same
sender calls it in New Relic depends on its pipeline, so the dotted-path name is a proposal: UNVERIFIED for all of them
(corpus: bluemedora 47, vmware 18, gemini_cli 18, opencensus 14, app 4, saphana 3 occurrences). The Blue Medora and VMware
`external.googleapis.com` metrics come from third-party senders; nothing documents a New Relic name for them.

## 7. Before and after on the estate corpus

Run: `uv run python scripts/analysis/gcp_estate_measure.py --no-experiments` (new output lines "names on non-polled
metrics"; the "before" run used the same script against `origin/main`'s `src`).

| Items converted on non-polled metrics | Name unverified before | Verified name after | Unverified after |
|---|---:|---:|---:|
| Conditions: Ops Agent / OpenTelemetry (63) | 63 | 60 | 3 (couchbase, 2 flink) |
| Conditions: Prometheus remote write (35) | 35 (rule read in the docs, not marked) | 35 | 0 |
| Conditions: custom ingest (4) | 4 | 0 | 4 |
| Widgets: Ops Agent / OpenTelemetry (165) | 165 | 154 | 11 |
| Widgets: Prometheus remote write (8) | 8 | 8 | 0 |
| Widgets: custom ingest (87) | 87 | 0 | 87 |

Converted totals did not move (191 of 350 conditions, 786 data widgets). Of the 95 conditions whose text changed, 91 changed
only their notes.

Threshold scaling fixes (4 conditions, all percent against a ratio): before, the converted query asked
`average(agent.memory.percent_used) > 90` for a metric that arrives as `system.memory.utilization` in 0..1, which could never
fire once the name was mapped. Now the threshold is divided by 100.

| Condition file | Before | After |
|---|---|---|
| `public/google-gce__disk-utilization-too-high.v1.json` | `agent.disk.percent_used` > 95 | `system.filesystem.utilization` > 0.95 (the `state = used` filter is dropped) |
| `public/google-gce__memory-utilization-too-high.v1.json` | `agent.memory.percent_used` > 90 | `system.memory.utilization` > 0.9 |
| `synthetic/agent-memory.json` | same | same |
| `synthetic/forecast-disk.json` | `agent.disk.percent_used` > 90 | `system.filesystem.utilization` > 0.9 |

Not handled: conditions written as PromQL over `agent.googleapis.com` names (the MongoDB policies) stay manual (PromQL path,
unchanged); a chart on a percent metric shows 0..1 where Google showed percent (the note says so; no query is multiplied).

## 8. Open points

- Nothing here was seen arriving. When a collector runs, `importer validate` and `check-gcp-data` should confirm each
  VERIFIED name; a mismatch means the collector version or an enabled-metric setting differs (the three utilization
  metrics are optional in the receiver and must be enabled).
- `system.filesystem.utilization` has no `state`, and its definition of "used" (against used+free, or including reserved
  blocks) was not read in the scraper source; Google's is used/(free+used+reserved). The value can differ by the reserved
  share: the row's name and factor are VERIFIED, the equality of the values is not.
- The counter note wording for non-polled counters (section 3).
