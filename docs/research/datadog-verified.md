# Datadog monitors and dashboards: verified reference, New Relic mapping, and build plan

Written 2026-11-11 on branch `research/datadog`. Offline with respect to Datadog and New Relic accounts: nothing was run against any tenant, no Datadog site was contacted for data, no credential was used. Public documentation and public repositories were read with `curl`.

This note replaces the Datadog roadmap (section 6) of `docs/research/elastic-omp-translation-analysis.md` (branch `research/elastic-translation`), which was tagged KNOWLEDGE. Nothing was copied from the Elastic repository (Elastic-2.0); only our own analysis note was read.

## 0. How to read this note

Evidence labels. Every factual claim carries one.

| Label | Meaning |
|---|---|
| DOC | Read on a public Datadog docs page fetched 2026-11-11 (the `.md` rendering of docs.datadoghq.com; page key below) |
| SPEC | Read in the Datadog OpenAPI description published with `DataDog/datadog-api-client-python` (file `.generator/schemas/v1/openapi.yaml` and `v2`), fetched 2026-11-11 |
| CORPUS | Counted or read in the 339 monitor templates and 517 dashboard templates of `DataDog/integrations-core` at commit `8c5a7ec0d3a0a1d35c694c643cbfa3fd63b94f12` (BSD-3-Clause; fixtures in `tests/fixtures/datadog/`) |
| NRDOC | Read in `newrelic/docs-website` (branch `develop`, commit `8c9eb7f0b7357f279030804ff9b967375e574141`, `src/content/docs/...`), fetched 2026-11-11 |
| REPO | Read in this repository, with the file |
| OTEL | Read in `open-telemetry/opentelemetry-collector-contrib` (Apache-2.0), `receiver/datadogreceiver`, main branch, fetched 2026-11-11 |
| UNVERIFIED | Not stated in anything read. Needs a sandbox test or a person with a Datadog account |

Page key. All Datadog pages are `https://docs.datadoghq.com/<path>.md` (fetched with `curl -sSL --compressed`).

| Key | Path |
|---|---|
| D-TYPES | `monitors/types` |
| D-METRIC | `monitors/types/metric` |
| D-ANOM | `monitors/types/anomaly` and `monitors/guide/anomaly-monitor` |
| D-OUT | `monitors/types/outlier` |
| D-FCST | `monitors/types/forecasts` |
| D-COMP | `monitors/types/composite` |
| D-CHG | `monitors/types/change-alert` |
| D-LOG, D-EVT, D-PROC, D-PCHK, D-HOST, D-SVC, D-APM, D-RUM, D-SLO, D-WD, D-SYN, D-NET, D-AUD, D-CI, D-ERR, D-DBM, D-COST, D-INTG | `monitors/types/log`, `event`, `process`, `process_check`, `host`, `service_check`, `apm`, `real_user_monitoring`, `slo`, `watchdog`, `synthetic_monitoring`, `network`, `audit_trail`, `ci`, `error_tracking`, `database_monitoring`, `cloud_cost`, `integration` |
| D-CONF | `monitors/configuration` |
| D-OPTS | `monitors/guide/monitor_api_options` |
| D-NOTIFY | `monitors/notify` |
| D-VARS | `monitors/notify/variables` |
| D-DOWN | `monitors/downtimes` |
| D-ASCOUNT | `monitors/guide/as-count-in-monitor-evaluations` |
| D-MODS | `metrics/custom_metrics/type_modifiers` |
| D-MTYPES | `metrics/types` |
| D-FILTER | `metrics/advanced-filtering` |
| D-FUNC | `dashboards/functions` and its children `rollup`, `timeshift`, `rate`, `interpolation`, `algorithms`, `rank`, `exclusion`, `count`, `arithmetic`, `smoothing`, `regression` |
| D-QUERY | `dashboards/querying` |
| D-TV | `dashboards/template_variables` |
| D-LOGSYN | `logs/explorer/search_syntax` |
| D-TAGS | `getting_started/tagging` |
| D-OTELMAP | `opentelemetry/mapping/metrics_mapping` |
| S-V1 | `DataDog/datadog-api-client-python` `.generator/schemas/v1/openapi.yaml` (commit `5d5a30f4f2a9c5715c2f6a0318d9b2edb42786e9`, master at fetch time; licence Apache-2.0, `LICENSE` read) |
| S-V2 | same repository, `.generator/schemas/v2/openapi.yaml` |
| TF-MON | `DataDog/terraform-provider-datadog` `docs/resources/monitor.md` (read only for the `type` list; MPL-2.0 not copied) |

Licence of the fixture assets: BSD-3-Clause (root `LICENSE` of `integrations-core` read, three-clause text; all 269 integration `pyproject.toml` files declare `BSD-3-Clause`; no per-integration LICENSE file exists). Details in `tests/fixtures/datadog/SOURCES.md`.

## 1. Verdict on the earlier roadmap (KNOWLEDGE claims checked)

| Claim in the Elastic note | Result |
|---|---|
| Monitor type values: metric alert, query alert, log alert, composite, service check, event alert and event-v2 alert, process alert, SLO alert, synthetics alert, trace-analytics, RUM, CI pipeline and CI test, audit, cost, network, error-tracking, database monitoring, watchdog | VERIFIED with corrections. SPEC `MonitorType` enum has 23 values (section 3). There is no `watchdog` or `network alert` type string: Watchdog monitors are `event-v2 alert`; network checks are `service check`; the network-performance type is `network-performance alert`. TF-MON lists the same 23. |
| "They key on `type` and on the shape of `query`" | VERIFIED and necessary: SPEC says the create-monitor `type` for anomaly, forecast, outlier, metric, APM metrics and integration metrics is the same string `query alert`; only the query text tells them apart (SPEC create-monitor description). |
| Options `notify_no_data`, `no_data_timeframe`, `evaluation_delay`, `renotify_interval`, `require_full_window`, `notify_by`, thresholds | VERIFIED (SPEC `MonitorOptions`, D-OPTS). Not mentioned there but present: `on_missing_data`, `renotify_statuses`, `renotify_occurrences`, `new_group_delay`, `group_retention_duration`, `timeout_h`, `scheduling_options`, `notification_preset_name`, `threshold_windows`, `variables`, `aggregation`, `include_tags`, `notify_audit`, `escalation_message`, `enable_logs_sample`, `enable_samples`, `silenced`, `locked`. |
| `evaluation_delay` maps to the condition delay | VERIFIED in meaning (SPEC: seconds, meant for backfilled data such as CloudWatch). Range detail: D-CONF says maximum 86400 s. |
| Conditional message blocks `is_alert`, `is_recovery` and tag template variables | VERIFIED and larger: 14 block families plus `is_match`, `is_exact_match`, `is_priority`, `is_renotify` (section 6). |
| Template variables have name, tag, default, optional prefix; used as `$name` | VERIFIED (SPEC `DashboardTemplateVariable`: `name`, `prefix`, `default`/`defaults`, `available_values`, `type`). CORPUS shows `$env` inside a scope expands to `env:<value>`; a variable named `scope` with no prefix (67 of 517 dashboards) expands to a whole scope. The `.value` form (`$env.value`) is DOC (D-TV). The expansion rule itself is not spelled out in prose: UNVERIFIED beyond the corpus. |
| Whole-scope `$scope` variable is a problem | VERIFIED as common: `{$scope}` appears 560 times in the corpus. |
| `@slack-`, `@pagerduty-`, `@webhook-`, `@team-` handles | VERIFIED (D-NOTIFY). Prefix table: `@jira`, `@pagerduty`, `@slack`, `@webhook`, `@teams` (Microsoft Teams), `@servicenow`; plus `@<email>`, `@team-<handle>`, `@incident-...`, workflow and case handles. |
| Percentile aggregators `p50` to `p99` are a different statistic on distribution metrics | VERIFIED (D-METRIC: percentile aggregation is "only available for the distribution metric type"). |
| `timeshift`, legacy `derivative`, `top` in formulas are hard | VERIFIED as present: `top` 170 and `derivative` 15 uses in dashboard queries (CORPUS). |
| Widget types timeseries, query_value, toplist, heatmap, distribution, table, hostmap, change, scatterplot, note, free_text, group, slo, alert_graph, list_stream, geomap, treemap, sunburst | VERIFIED with corrections: the table widget's `type` string is `query_table`, not `table`; the SPEC has 43 widget definition schemas (section 8). |
| "Datadog site is blocked, so all of this is file-based first" | Stands. The monitor and dashboard JSON that the API returns is the input; the shape is in section 2 and 8. |

## 2. The monitor object

Source: SPEC `Monitor`. Required: `type`, `query`. Fields: `id`, `name`, `message`, `tags`, `options`, `priority` (1 high to 5 low, nullable), `restricted_roles`, `multi` (read only), `overall_state`, `state`, `created`, `modified`, `creator`, `deleted`, `matching_downtimes`, `draft_status`, `assets`.

CORPUS note: `integrations-core` ships monitors as templates `{version, created_at, last_updated_at, title, description, tags, definition}` and the API object is under `definition`. A file-first source must accept both the bare API object (what `GET /api/v1/monitor` returns) and this wrapper. The fixtures include both (`SYNTHETIC_monitor_integration_template_shape.json`).

### 2.1 `options` (SPEC `MonitorOptions`, DOC D-OPTS, D-CONF)

| Field | Type | Meaning, with default where documented | Maps to NR |
|---|---|---|---|
| `thresholds` | object | `critical`, `warning`, `critical_recovery`, `warning_recovery`, `ok`, `unknown`, `critical_query`, `critical_recovery_query` (the two `*_query` fields are "in preview", formula monitors only). For metric alerts `critical` is also in the query text. For service checks and logs the keys are status counts, e.g. `{ok:1, warning:1, critical:1}` (D-OPTS). | critical and warning terms: exact. Recovery thresholds: none (section 11.2). |
| `notify_no_data` | bool, default false | Notify when data stops reporting | `expiration_duration` plus `open_violation_on_expiration` |
| `no_data_timeframe` | integer minutes | Minutes before a no-data notification. Documented default: 2x the evaluation window for query alerts, 24 hours (docs say) for service checks. | `expiration_duration` seconds, 30 to 172800 (REPO, NRDOC) |
| `on_missing_data` | enum `default`, `show_no_data`, `show_and_notify_no_data`, `resolve` | For event-style monitors (APM trace analytics, audit, CI, error tracking, event, log, RUM): what an empty evaluation means. Default: a count query treats empty as 0; other query types keep the last status (D-OPTS, TF-MON). | `fill_option`, expiration, `close_violations_on_expiration` (approximate) |
| `renotify_interval`, `renotify_statuses` (`alert`, `warn`, `no data`), `renotify_occurrences`, `escalation_message` | integers, array, integer, string | Reminders while unresolved. `escalation_message` is discouraged in favour of `{{#is_renotify}}` blocks (SPEC). | None documented in NR (section 11.2) |
| `require_full_window` | bool, default false | "Only applies to metric monitors." A window is "full" when the first 1/10-minute-bucket has data and at most three buckets are empty (D-METRIC, with the bucket size by window length). | Nothing equivalent; closest is a sliding window plus gap handling (approximate) |
| `evaluation_delay` | integer seconds | Delay evaluation (SPEC example: 300 s at `last_5m`, 7:00 evaluates 6:50 to 6:55) | `signal.evaluationDelay` (max 7200 s per REPO) or aggregation delay |
| `new_group_delay` (default 60 per D-OPTS), `new_host_delay` (deprecated, default 300) | integer seconds | Do not alert on new groups for N seconds | `evaluation_delay` is the nearest ("new autoscaled entities", REPO) |
| `group_retention_duration` | `60m` to `72h` | Drop groups with missing data after this long | None |
| `timeout_h` | integer 0 to 24 | Auto-resolve after N hours with no data | `close_violations_on_expiration` (approximate) |
| `notify_by` | array of group tags | Alert granularity for multi alerts; `["*"]` makes a multi alert behave as simple | NR conditions facet by FACET; coarser grouping means a coarser FACET (manual) |
| `include_tags` | bool, default true | Add `{host:h1}` to the title | `title_template` (approximate) |
| `notify_audit` | bool | Notify tagged users when the monitor is edited | None |
| `notification_preset_name` | enum | `show_all`, `hide_query`, `hide_handles`, `hide_all`, `hide_query_and_handles`, `show_only_snapshot`, `hide_handles_and_footer` | Message template content (manual) |
| `silenced` | object | Deprecated; reflects v1 downtimes only | Muting rule (section 7) |
| `locked` | bool | Deprecated; use `restricted_roles` | None |
| `scheduling_options` | `{evaluation_window:{day_starts,hour_starts,month_starts,timezone}, custom_schedule}` | Cumulative windows and daily/weekly/monthly evaluation (D-CONF "Customize monitor evaluation frequencies") | None (cumulative windows have no NR counterpart, section 11.2) |
| `threshold_windows` | `{trigger_window, recovery_window}` | Anomaly monitors; `trigger_window` must equal the query's `alert_window` (D-ANOM) | Baseline condition threshold duration (approximate) |
| `aggregation` | `{type: count/cardinality/avg, metric, groupBy}` | Log alert aggregation (D-OPTS) | Same info as in the `logs(...)` query text |
| `variables` | array | Beta: named sub-queries for formula monitors | Several NRQL signals; NR condition holds one query (manual) |
| `enable_logs_sample`, `enable_samples` | bool | Attach samples to notifications | Workflow enrichment NRQL (manual) |
| `min_failure_duration` (max 7200 s), `min_location_failed`, `device_ids`, `synthetics_check_id` | | Synthetics monitors | Synthetics monitor condition (section 3) |
| `groupby_simple_monitor` | bool | Deprecated, use `notify_by` | |

CORPUS options not in the SPEC (undocumented but shipped by Datadog itself): `avalanche_window` (68 of 339 monitors), `restriction_query` (2), `queryConfig` (1), and inside `thresholds` the keys `comparison`, `period`, `timeAggregator` (1; UI state that leaked into a template). A reader must ignore unknown option keys, never reject on them.

### 2.2 Message, tags, priority

`message` is Markdown with `{{...}}` blocks and `@` handles (section 6). `tags` are `key:value` strings; tags are lowercased for metrics and limited to 200 characters (D-TAGS). Priority is an integer 1 to 5 (SPEC); `{{#is_priority 'P1'}}` takes `P1` to `P5` (D-VARS).

## 3. Monitor types

`type` is the API string. UI name to API type comes from the SPEC create-monitor description. "Corpus" is the count among 339 integration monitors.

| UI / family | API `type` | Query grammar (SPEC unless noted) | Corpus |
|---|---|---|---|
| Metric (threshold, change, anomaly, outlier, forecast), integration metric, APM metrics | `query alert` (legacy `metric alert` still in the enum) | `time_aggr(time_window):space_aggr:metric{tags} [by {key}] operator #` | 293 |
| Change alert | `query alert` | `change_aggr(time_aggr(time_window),timeshift):space_aggr:metric{tags} ... op #`, e.g. `pct_change(avg(last_5m),last_30m):avg:system.load.1{*} > -50` (D-CHG). `timeshift` is `#m_ago`, `#h_ago`, `1d_ago` in the SPEC; D-CHG shows `last_30m`. | counted under `query alert` |
| Anomaly | `query alert` | `avg(last_4h):anomalies(<metric_query>, '<algorithm>', <deviations>, direction='<above/below/both>', alert_window='<last_Nm>', interval=<s>, count_default_zero='true'[, seasonality='<hourly/daily/weekly>']) >= <fraction 0..1>` (D-ANOM) | 46 |
| Outlier | `query alert` | `avg(last_30m):outliers(avg:system.cpu.user{role:es-events-data} by {host}, 'dbscan', 7) > 0`; `outliers(<metric>, '<algo>', <tolerance>, <pct>)` where pct applies only to MAD (SPEC, D-FUNC algorithms, D-OUT). Algorithms `DBSCAN`, `MAD`, `scaledDBSCAN`, `scaledMAD` (D-OUT). | 0 |
| Forecast | `query alert` | `<max/min>(next_1w):forecast(<metric_query>, '<linear/seasonal>', <deviations>, interval=<s>[, history='1w'][, model='default/simple/reactive'][, seasonality='...']) <op> <threshold>` (D-FCST) | 1 |
| Log | `log alert` | `logs("<search>").index("<name or *>").rollup("count"\|"avg","<measure>"\|"cardinality","<facet>").by("<facet>,...").last("5m") > N` (SPEC; CORPUS agrees, e.g. `logs("source:adyen @evt.name:X").index("*").rollup("count").by("@pspReference").last("5m") > 0`). Windows `#m` 1 to 2880, `#h` 1 to 48. Log monitors: maximum rolling window 2 days (D-CONF). | 36 |
| Event (legacy) | `event alert` | Replaced by event-v2 (SPEC) | 0 |
| Event v2, Watchdog | `event-v2 alert` | `events("<search>").rollup("count"\|"avg"\|"cardinality"[, "<measure>"]).last("5m") > N`, optionally `.by("a,b")` (SPEC; CORPUS shows `.by(...)`) | 2 |
| Service check, host, custom check, network (TCP/HTTP), process check, some integrations | `service check` | `"<check>".over("<tag>",...).by("<group>",...).last(N).count_by_status()`; `over` cannot be empty; `last` count at most 100; thresholds `{ok, warning, critical}` are status counts in `options.thresholds` (SPEC). Host monitors watch `datadog.agent.up` (D-HOST). | 7 |
| Live process | `process alert` | `processes("<search>").over("<tags>").rollup('count').last("10m") > N` (SPEC) | 0 |
| Composite | `composite` | Boolean expression of monitor IDs, e.g. `12345 && 67890`, `!1234 \|\| 5678`; "IDs of non-composite monitors" (SPEC, D-COMP). Evaluation: `&&` yields the least severe status, `\|\|` the most severe (D-COMP). | 0 |
| SLO alert | `slo alert` | `error_budget("<slo_id>").over("7d") > 75`; time windows `7d`, `30d`, `90d`; operators `>=` or `>` (SPEC). Burn-rate alerts exist (D-SLO) but their query text is not in the SPEC read: UNVERIFIED. | 0 |
| Audit trail | `audit alert` | `audits("<search>").rollup(...).last("5m") > N` (SPEC) | 0 |
| CI pipelines, CI tests | `ci-pipelines alert`, `ci-tests alert` | `ci-pipelines("<search>").rollup(...).last("5m") > N`, `ci-tests(...)` (SPEC) | 0 |
| Error tracking | `error-tracking alert` | `error-tracking("<search>").source("all\|browser\|mobile\|backend").new()` or `.impact()` then `.rollup(...).by("issue.id,...").last("5m") > N` (SPEC) | 0 |
| Database monitoring | `database-monitoring alert` | `database-monitoring("<search>").rollup(...).last("5m") > N` (SPEC). CORPUS also shows a formula form: `formula("default_zero(query1) / default_zero(query2) ...").last("15m") > 1000` with `options.variables`. | 1 |
| Network performance | `network-performance alert` | `network-performance("<search>").rollup(...).last("5m") > N` (SPEC) | 0 |
| Network path | `network-path alert` | `network-path("<search>").index("netpath-path"\|"netpath-hop").rollup(...).last("5m") > N` (SPEC) | 0 |
| Cloud cost | `cost alert` | `formula(query).timeframe_type(time_window).function(parameter) op #`, functions threshold (default), `change`, `anomaly`, `forecast` (SPEC) | 0 |
| RUM | `rum alert` | Same event-search family as logs (D-RUM); exact text UNVERIFIED (no SPEC example). | 0 |
| APM trace analytics | `trace-analytics alert` | Search plus facet/measure over a window (D-APM); exact text UNVERIFIED. APM metrics monitors are `query alert` (SPEC). | 0 |
| Synthetics | `synthetics alert` | Created through the Synthetics API, not the Monitors API (SPEC note); options `min_failure_duration`, `min_location_failed`. | 0 |
| Others in the enum with no SPEC example | `data-quality alert`, `data-jobs alert`, `llm-observability alert` | UNVERIFIED query text; the DOC pages `data_observability` and the LLM page exist (the latter returned 404 at the guessed path). | 0 |

Corpus mix: 293 `query alert`, 36 `log alert`, 7 `service check`, 2 `event-v2 alert`, 1 `database-monitoring alert`. Integration templates contain no `composite`, `outlier`, `process`, `slo`, `rum`, `synthetics` or `trace-analytics` monitors, so those are covered only by the synthetic fixtures. A real export from a customer account will contain them. Sizing the source from the integration corpus alone would be wrong.

## 4. Metric query grammar

### 4.1 Pieces (SPEC create-monitor text, D-QUERY, D-FILTER, D-FUNC)

```
<time_aggr>(<time_window>):<space_aggr>:<metric>{<scope>} [by {<tag>,...}] [.function()...] <op> <number>
```

- `time_aggr`: `avg`, `sum`, `max`, `min`, `change`, `pct_change` (SPEC). Percentile aggregation is a distribution-only option (D-METRIC).
- `time_window`: `last_#m` (1 to 10080 depending on monitor type), `last_#h` (1 to 168), `last_1d`, `last_1w` (SPEC). CORPUS also shows `current_1h` (cumulative window) and `next_12h` (forecast). Windows seen: `last_5m` 161, `last_15m` 28, `last_4h` 24, `last_10m` 21, `last_1h` 18, `last_30m` 8, `last_1d` 6, `last_12h` 4, `last_13h` 4, `last_1m` 3.
- `space_aggr`: `avg`, `sum`, `min`, `max` (SPEC). Dashboards also use `p50`..`p99` on distributions and the `queries[].aggregator` set `avg, min, max, sum, last, area, l2norm, percentile` (SPEC `FormulaAndFunctionMetricAggregation`).
- `scope`: comma-separated tags (AND), `*` for everything, `!tag` to exclude, wildcards as prefix, suffix or substring (`pod_name:web-*`, `node:*-prod-*`). Boolean syntax: `AND`, `OR`, `NOT`, `IN (a,b)`, `NOT IN (...)`, parentheses; "include uses AND, exclude uses OR"; symbolic (`!`, `,`) and word forms cannot be mixed (D-FILTER).
- `by {a,b}`: multi alert grouping; `N/A` groups are skipped, tag keys must have a value (D-METRIC).
- Template variables inside dashboard queries: `$env`, `$host`, `$scope`, `$name.value` (section 8.4).

### 4.2 Functions (D-FUNC and children; counts are dashboard-query uses in the corpus)

| Family | Functions | Notes |
|---|---|---|
| Type modifiers | `.as_count()`, `.as_rate()` | 324 and 14 uses. Described in 4.3. |
| Rollup | `.rollup(<avg/sum/min/max/count>[,<seconds>])`, `moving_rollup(m, seconds, agg)`, calendar-aligned `.rollup(sum, weekly, monday, 'America/New_York')` | 7 uses. Query display limit 1500 points per series (150 for query_value). Rollups in monitors are discouraged: the buckets align to Unix time, not to the evaluation window (D-FUNC rollup). Default rollup interval table by timeframe is in D-FUNC rollup. |
| Interpolation | `.fill(linear\|last\|zero\|null[, limit])` limit default 300 s, max 600; `default_zero(q)` | `default_zero` locks the missing-data behaviour to "evaluate as zero" (D-CONF). 15 monitor uses. |
| Rate | `per_second`, `per_minute`, `per_hour`, `dt`, `diff`, `monotonic_diff`, `derivative` | `diff` is "the delta per interval, after time aggregation and before space aggregation". 27/22/15 uses of per_minute/per_second/derivative. |
| Timeshift | `timeshift(q, -seconds)`, `calendar_shift(q, "-7d", "UTC")` (units `d`, `w`, `mo`), `hour_before`, `day_before` (deprecated), `week_before`, `month_before` | Only 2 uses of `timeshift` in the corpus. |
| Rank | `top(q, 5\|10\|25\|50\|100, 'max\|mean\|min\|sum\|last\|l2norm\|area', 'asc\|desc')`, `top5_mean()`, `bottom10_min()` forms | 170 uses, the most common non-trivial function. |
| Algorithms | `anomalies(q, 'basic\|agile\|robust', bounds)`, `outliers(q, 'dbscan\|mad\|scaleddbscan\|scaledmad', tolerance[, pct])`, `forecast(q, 'linear\|seasonal', deviations)` | In monitors the full forms are in section 3. Functions that cannot be nested inside `anomalies()`: `cumsum`, `integral`, `outliers`, `piecewise_constant`, `robust_trend`, `trend_line`; inside `forecast()`: also `anomalies` (D-ANOM, D-FCST). |
| Arithmetic | `abs`, `log2`, `log10`, `cumsum`, `integral`, `+ - * /` between queries | |
| Count | `count_nonzero`, `count_not_null` | |
| Smoothing | `autosmooth`, `ewma_3/5/7/10/20`, `median_3/5/7/9`, | `autosmooth` 2 uses |
| Exclusion | `exclude_null`, `cutoff_min/max`, `clamp_min/max` | CORPUS: `clamp_max` 2, `cutoff_min` 1 |
| Regression | `robust_trend`, `trend_line`, `piecewise_constant` | |

### 4.3 `as_count`, `as_rate`, rollup, fill: exact semantics (D-MODS, D-ASCOUNT, D-MTYPES)

- Submission types: COUNT, RATE, GAUGE, SET, HISTOGRAM, DISTRIBUTION; in-app types COUNT, RATE, GAUGE, DISTRIBUTION, HISTOGRAM (D-MTYPES).
- A COUNT is "total occurrences in one interval"; a RATE is the same normalised per second (D-MODS).
- On a COUNT: `as_count()` disables interpolation and sets the time aggregator to SUM. `as_rate()` does the same and then divides by the sampling interval (`[1,1,1,1].as_rate()` with a 20 s rollup gives `[0.05, ...]`). On a RATE: `as_count()` multiplies by the interval (`[0.05 x4].as_count()` at 20 s gives `[1,1,1,1]`). The UI appends `.as_count()` to COUNT and RATE queries automatically (D-FUNC rollup).
- In a monitor, a query with at least one `as_count()` uses a different evaluation path from one without: the order of time aggregation and arithmetic differs, so `error.as_count() / total.as_count()` and `error.as_rate() / total.as_rate()` can evaluate to different numbers on the same data. `pct_change()` on a gauge without `as_count()` is computed per bucket then summed and can produce values like -1,500% (D-ASCOUNT).
- A converter therefore cannot treat `.as_count()` as a cosmetic suffix; section 12.

## 5. Log, event and span search syntax (D-LOGSYN)

Used inside `logs("...")`, `events("...")`, `audits("...")`, `rum`, `ci-*`, dashboard `log_query.search.query` and `event_query.search.query`.

- Free text matches only `message`, `@title`, `@error.message`, `@error.stack`; `*:term` is full-text across all attributes (monitor, dashboard and notebook queries only).
- Attributes: `@attr:value`; tags: `key:value`; reserved attributes: `service`, `status`, `host`, `source`, `env`.
- Boolean operators are case-sensitive: `AND` (default), `OR`, `-` for exclusion; parentheses; wildcards `*` and `?`; ranges `@duration:[100 TO 200]`, `@x:>=40` (CORPUS has `@Severity:>=40`); quoted phrases use straight double quotes only.
- Special characters and spaces are escaped with a backslash.
- UNVERIFIED: the complete range and wildcard grammar (read in part).

## 6. Notifications and message templating

### 6.1 Handles (D-NOTIFY)

`@` plus a prefix. Integration form `@<integration>-<value>` for `@jira`, `@pagerduty`, `@slack`, `@webhook`, `@teams`, `@servicenow`. Others: `@<email>` (Datadog user or any address), `@team-<handle>` (routes to the team's channel), workflow, case and `@incident-` handles. A handle must follow whitespace (`text @ops@co.com` valid, `text@ops@co.com` not). Handles with parentheses are not parsed. Email addresses with `/` are unsupported. Handles can be built from tag variables: `@slack-{{service.name}}`, and if the variable is empty the handle is invalid, so Datadog recommends a fallback `{{#is_exact_match "kube_namespace.owner" ""}}@slack-example{{/is_exact_match}}` (D-VARS).

Webhook, Slack and PagerDuty handle names refer to objects configured in the Datadog account (webhook name, Slack channel, PagerDuty service). The monitor JSON carries the name only, never the URL, token or routing key. Therefore migrating a handle requires a human to supply the destination (REPO `newrelic-notifications-and-nrql-reference.md` section 1.5/1.6 effort codes apply).

CORPUS: in 339 monitors only 25 messages contain an `@` token and 22 of those are `@example...` placeholders; 2 are `@email`, 1 is `@slack`. Real customer monitors have many more; the synthetic fixtures carry fake secrets in handles and bodies.

### 6.2 Conditional blocks (D-VARS)

`{{#is_alert}}`, `{{#is_warning}}`, `{{#is_recovery}}`, `{{#is_warning_recovery}}`, `{{#is_alert_recovery}}`, `{{#is_alert_to_warning}}`, `{{#is_no_data}}`, `{{#is_no_data_recovery}}`, `{{#is_unknown}}`, `{{#is_renotify}}`, `{{#is_priority 'P1'}}`, `{{#is_match "var.name" "a" "b"}}`, `{{#is_exact_match ...}}`; each has a negated `{{^...}}` form; `{{else}}` is allowed. 217 of 339 corpus messages use at least one.

### 6.3 Variables (D-VARS)

Monitor: `{{value}}`, `{{threshold}}`, `{{warn_threshold}}`, `{{alert_recovery_threshold}}`, `{{warn_recovery_threshold}}`, `{{ok_threshold}}`, `{{comparator}}`, `{{first_triggered_at}}`, `{{first_triggered_at_epoch}}`, `{{last_triggered_at}}`, `{{last_triggered_at_epoch}}`, `{{triggered_duration_sec}}`, `{{check_message}}` (checks), `{{local_time 'last_triggered_at' 'Asia/Tokyo'}}`, composite `{{a.value}}`, `{{a.status}}`.

Tags of the alerting group: `{{host.name}}`, `{{env.name}}`, `{{[dot.key].name}}`, `{{@facet.name}}`, host metadata `{{host.metadata_platform}}` etc.

Event-source variables: `{{log.attributes.key}}`, `{{log.tags.key}}`, `{{span.attributes.key}}`, `{{rum.attributes.key}}`, `{{event.attributes.key}}`, `{{cipipeline...}}`, `{{citest...}}`, `{{issue...}}`, `{{audit...}}`, `{{databasemonitoring...}}`, `{{synthetics...}}`, with `.link` forms.

Escaping: double braces HTML-escape, triple braces `{{{x}}}` do not (D-VARS). Comments and `{{url_encode ...}}`-style helpers exist (D-VARS "Advanced").

## 7. Downtimes and mutes

- v1 `options.silenced`: map of scope to timestamp (or null for forever); deprecated (D-OPTS).
- v2 downtimes (SPEC v2 `DowntimeCreateRequest`): `data.type = downtime`, attributes `scope` (required, search-syntax group scope, `*` for all), `monitor_identifier` (required, `{monitor_id}` or `{monitor_tags}`), `schedule` (one-time `{start,end}` with UTC offset zero, or recurring `{recurrences:[{rrule,duration,start}], timezone}`), `message`, `display_timezone`, `mute_first_recovery_notification`, `notify_end_states` (`alert`, `no data`, `warn`), `notify_end_types` (`canceled`, `expired`).
- D-DOWN: downtimes silence notifications but do not prevent monitor state transitions; recurrence uses RFC 5545 RRULE without `DTSTART`, `DTEND`, `DURATION`; monitor tags max 32 tags of 256 characters; a monitor created after a matching downtime is included automatically.
- Mapping: section 11.5.

## 8. Dashboards

### 8.1 Top level (SPEC `Dashboard`; CORPUS keys)

`title`, `description`, `layout_type` (`ordered` or `free`), `reflow_type` (`auto` or `fixed`; ordered layouts only), `widgets[]`, `template_variables[]`, `template_variable_presets[]` (saved views), `tags` (team names), `notify_list`, `tabs`, `default_timeframe`, `restricted_roles`, `experience_type`, `is_read_only` (deprecated), `author_handle`, `author_name`, `created_at`, `modified_at`, `id`, `url`. Corpus frequencies: `widgets`/`title`/`layout_type`/`template_variables` 517, `reflow_type` 372, `template_variable_presets` 4, `tabs` 1, `tags` 2.

Widget wrapper: `{id, definition, layout:{x,y,width,height,is_column_break}}`. Grid width is 12 columns for ordered layouts (UNVERIFIED in the SPEC text; seen in corpus layouts). Free layout uses absolute x and y.

### 8.2 Widget definition types (SPEC `WidgetDefinition` oneOf, 43 schemas) and what each has

`type` string: key fields beyond `title`, `title_size`, `title_align`, `time`, `custom_links`.

| `type` | Key fields | Corpus uses |
|---|---|---|
| `timeseries` | `requests[]`, `yaxis`, `right_yaxis`, `markers`, `events`, `show_legend`, `legend_layout`, `legend_columns` | 4627 |
| `query_value` | `requests[]`, `autoscale`, `custom_unit`, `precision`, `text_align`, `timeseries_background` | 2533 |
| `toplist` | `requests[]`, `style` | 2187 |
| `group` | `widgets[]`, `layout_type`, `background_color`, `show_title`, `banner_img` | 1817 |
| `note` | `content`, `background_color`, `font_size`, `show_tick`, `tick_edge`, `tick_pos`, `has_padding` | 1744 |
| `sunburst` | `requests[]`, `legend`, `hide_total` | 794 |
| `list_stream` | `requests[]` (data source and query) | 755 |
| `query_table` | `requests[]`, `has_search_bar` | 731 |
| `image` | `url`, `url_dark_theme`, `sizing`, `margin` | 353 |
| `check_status` | `check`, `grouping`, `group`, `group_by`, `tags` | 188 |
| `geomap` | `requests[]`, `style`, `view` | 122 |
| `manage_status` | monitor summary: `query`, `summary_type`, `display_format`, `color_preference`, `sort` | 79 |
| `log_stream` | `query`, `columns`, `indexes`, `sort` | 39 |
| `heatmap` | `requests[]`, `yaxis`, `events`, `markers` | 38 |
| `change` | `requests[]` with `change_type`, `compare_to`, `increase_good`, `order_by` | 30 |
| `bar_chart` | `requests[]`, `style` | 23 |
| `treemap` | `requests[]` | 22 |
| `hostmap` | `requests[]`, `group`, `scope`, `node_type`, `no_group_hosts`, `no_metric_hosts` | 16 |
| `event_stream` | `query`, `event_size` | 12 |
| `distribution` | `requests[]`, `xaxis`, `yaxis`, `markers` | 10 |
| `wildcard` | custom Vega-Lite style widget | 8 |
| `free_text` | `text`, `font_size`, `color`, `text_align` | 7 |
| `flame_graph`, `cloud_cost_summary`, `topology_map`, `event_timeline`, `sankey`, `iframe` | profile, cost, topology, events | 6, 5, 5, 5, 4, 1 |
| in SPEC, not in corpus | `scatterplot` (`xaxis`, `yaxis`, `requests` with `x`,`y`), `slo` (`slo_id`, `time_windows`, `view_type`), `slo_list`, `alert_graph` (`alert_id`, `viz_type`), `alert_value`, `servicemap`, `trace_service`, `split_group`, `powerpack`, `run_workflow`, `embedded_app`, `cohort`, `funnel`, `retention_curve`, `heatgrid`, `point_plot`, `journey_map` | |

### 8.3 Request shapes

Two generations coexist (CORPUS request keys: `queries` 9959, `formulas` 9745, `response_format` 10702, `q` 1767, `query` 775):

1. Legacy: `requests:[{q:"avg:system.cpu.user{$host} by {host}", display_type, style, conditional_formats, aggregator, change_type...}]`.
2. Formula and function API (current): `requests:[{queries:[{data_source, name, query, aggregator,...}], formulas:[{formula:"query1 / query2", alias, limit:{count,order}, conditional_formats, cell_display_mode}], response_format:"timeseries"|"scalar", display_type, style, on_right_yaxis, sort}]`.

`queries[].data_source` seen in the corpus: `metrics` 6225, `logs` 4974, `logs_stream` 712, `security_signals` 350, `cloud_cost` 139, `network_path` 84, `network_device_flows` 71, `events` 55, `infrastructure` 13, `process` 6, `database_queries`, `profiling_aggregated_profile`, `llm_observability`, `data_streams`, `service_map`, `network_devices`. SPEC event sources: `logs, spans, network, network_device_flows, rum, security_signals, profiles, audit, events, ci_tests, ci_pipelines, incident_analytics, product_analytics, on_call_events, errors, llm_observability`. SPEC query definition kinds: metrics, event (with `compute`, `group_by`, `search`, `indexes`), process, APM dependency stats, APM resource stats, APM metrics, SLO, cloud cost, product analytics, user journey, retention.

Formulas support arithmetic of named queries and functions inside the formula string (`timeshift(query1, -604800)`, `default_zero(query1)`, `abs(...)`). `display_type`: `area`, `bars`, `line`, `overlay`; `style.line_type`: `dashed`, `dotted`, `solid`. `conditional_formats[]`: `comparator` (`=`, `>`, `>=`, `<`, `<=`; corpus counts `>` 2035, `>=` 370, `<` 212, `<=` 198, `=` 58), `value`, `palette`, `custom_bg_color`, `custom_fg_color`, `hide_value`, `image_url`, `metric`, `timeframe`. `markers[]`: `{display_type, label, time, value}`. `yaxis`: `{include_zero, label, max, min, scale}`. Widget `time`: `live_span` of `1m, 5m, 10m, 15m, 30m, 1h, 4h, 1d, 2d, 1w, 1mo, 3mo, 6mo, week_to_date, month_to_date, 1y, alert`, or a fixed span.

### 8.4 Template variables, saved views (SPEC `DashboardTemplateVariable`, D-TV)

- Fields: `name`, `prefix` (tag key; only tags with that prefix appear in the dropdown), `default` (deprecated single value) or `defaults[]` (several, OR-ed), `available_values[]`, `type` (filter versus group-by variable).
- Use: in a query scope `{$env}` (filter) or `by {$group}` (group-by type). `$name.value` gives the raw value (`env:staging-$service.value`). The dashboard URL carries `tpl_var_<name>=<value>`. Defaults: `*`. The list always offers `*` (D-TV).
- Facet variables for logs, APM and RUM start with `@`, e.g. `@http.status_code`; wildcards may sit mid-value on those sources (D-TV).
- Metric values come from the last 48 hours; other sources from the dashboard time frame (D-TV).
- A variable whose tag key is `team` renders as a team filter with hierarchy (D-TV): selecting a parent adds its sub-teams.
- CORPUS: 1655 variables; `default` on all, `prefix` on 1627 (the 28 without are `scope` style), `available_values` on 1493.
- Saved views: `template_variable_presets:[{name, template_variables:[{name, value|values[]}]}]`. Four dashboards in the corpus have them.

## 9. SLOs (SPEC `ServiceLevelObjective`)

`type`: `metric`, `monitor`, `time_slice`. Fields: `name`, `description`, `tags`, `thresholds[]` (`timeframe` of `7d`, `30d`, `90d`, `custom`; `target`; `warning`; `target_display`), `query` (`numerator`, `denominator` for metric SLOs), `monitor_ids`, `groups`, `sli_specification` (time slice). SLO alert monitors: section 3. NR service levels are created by NerdGraph or Terraform from any NRDB event or dimensional metric and have fast and slow burn alerts (NRDOC `service-level-management/create-slm.mdx`, `alerts-slm.mdx`). Not read in depth; section 11.1 marks SLO as approximate.

## 10. What the corpus tells us that the docs do not

- Datadog ships monitors as templates (wrapper plus `definition`), dashboards as bare bodies. Accept both.
- Most monitors are grouped (216 of 339), warn at a second level (154), and carry a recovery threshold in 55; 8 are formula monitors with `options.variables`; 7 have `thresholds.ok` (service checks).
- Anomaly algorithms in the corpus: `agile` 23, `basic` 23, `robust` 0 (46 anomaly monitors). So `basic` and `agile` are the priority for any special handling and `robust` needs only the same refusal.
- `avalanche_window`, `restriction_query`, `queryConfig` and odd threshold keys exist and are undocumented (section 2.1).
- One shipped dashboard (`Network Metrics`) has `widgets: []`; the source must accept an empty dashboard.
- Metrics use dots (`active_directory.ldap.bind_time`), tags use `key:value`, and `by {host}` is the dominant grouping.

## 11. Mapping verdicts to New Relic

New Relic facts used here are the verified ones already in the repository (`docs/research/newrelic-notifications-and-nrql-reference.md` for alerts, destinations, workflows, muting; `docs/research/newrelic-dashboard-variables.md` for variables; `docs/research/promql-to-nrql-census.md` for NRQL function behaviour), plus three NRDOC reads made for this note: compound alerts, outlier detection, anomaly detection, predictive alerts and `COMPARE WITH` text.

Verdict scale: exact (same meaning, mechanical), approximate (works with a named caveat), manual (a person decides or supplies something), none (no New Relic feature).

Hard rule from the repository: no query-bearing object is ever marked exact (REPO `may_be_exact`); a verdict "exact" below means "the setting maps one to one", not "the query is proved".

### 11.1 Monitor families

| Datadog | NR target | Verdict | Caveats |
|---|---|---|---|
| `query alert`, plain threshold: `avg\|sum\|max\|min(last_Nm):<space>:m{scope} [by {k}] op #` | NRQL static condition on `Metric` (or the event type the data lands in) with `FACET k`; `critical` and `warning` terms | approximate | Time aggregator is applied to raw points in NR, to rollup points in Datadog; `last_Nm` is a rolling window evaluated every minute, NR needs `aggregation_window` 30 s to 3600 s plus `slide_by` to evaluate more often (section 12). The metric name and attribute names must be re-mapped (section 13). |
| `query alert` with `.as_count()`, `.as_rate()`, `per_second`, `rollup`, `fill`, `default_zero` | `sum()`, `rate(sum(x), 1 second)`, `fill_option` | approximate or manual | See 12. `as_count` on a gauge-stored count and `rate()` inside a condition: `rate()` in conditions is UNVERIFIED (REPO). |
| `query alert` change alert: `pct_change(avg(last_5m),last_30m)` | baseline (anomaly) condition, or two-window NRQL comparison | manual | `COMPARE WITH` is "incompatible with NRQL alerting"; NR recommends anomaly conditions (NRDOC `create-nrql-alert-conditions.mdx` line 267). A change threshold has no direct NR condition. |
| `query alert` anomaly `anomalies(q,'basic'\|'agile'\|'robust',n,...)` | `type=baseline` NRQL condition, `baseline_direction` UPPER_ONLY/LOWER_ONLY/UPPER_AND_LOWER, threshold in standard deviations (1 to 1000), seasonality `NEW_RELIC_CALCULATION`/`HOURLY`/`DAILY`/`WEEKLY`/`NONE` | approximate | Different algorithms and a different trigger rule (section 12). Datadog fires when a fraction of the `alert_window` is outside bounds; NR fires when the signal is `n` deviations from the baseline for the threshold duration. NR needs 1 to 4 weeks of history and does not train `FACET`ed queries on stored data (NRDOC `anomaly-detection.mdx`). `deviations` maps to NR standard deviations only as an informed guess. `direction` maps. `interval`, `count_default_zero` do not. |
| `query alert` outlier `outliers(q,'dbscan'\|'mad',tol,pct)` | `type=outlier` condition with `dbscan {epsilon, minimum_points, evaluation_group_facet}` | approximate for DBSCAN, manual for MAD | NR outlier detection is DBSCAN (NRDOC `outlier-detection.mdx`); described as beta/limited per account in REPO (SCHEMA). `tolerance` is a multiplier on a data-derived threshold in Datadog (D-OUT) and `epsilon` an absolute distance in NR: not convertible. MAD and scaled forms: manual. |
| `query alert` forecast `forecast(q,'linear'\|'seasonal',n,...)` | static condition with predictive threshold (`predict_by`, Holt-Winters, up to 360 windows ahead) | approximate | NR predictive alerts exist (NRDOC `predictive-alerts.mdx`), beta in the Terraform schema (REPO); linear vs seasonal and "any part of the bounds crosses" differ. |
| `log alert` `logs(q).index().rollup().by().last()` | NRQL on `Log` with `FACET`, `count(*)`, `average()` or `uniqueCount()` | approximate | The Datadog search syntax must be translated to NRQL `WHERE` (section 5); the log attribute names must exist in New Relic logs. `count(*)` returns NULL on empty windows (REPO `count-null-empty-window.md`), so "alert above 0" is fine and "below N" needs loss of signal or `filter()`. `cardinality` is not supported in conditions (NRDOC via REPO: `cardinality()` not yet supported); use `uniqueCount`. |
| `event-v2 alert`, Watchdog | NRQL on a custom event type or none | manual | Datadog events have no source-side equivalent unless the events are ingested; Watchdog is a Datadog-internal detector: none. |
| `service check` | NRQL on a service-check-like event / synthetics check, or an infrastructure host-not-reporting condition | manual | Service checks carry statuses 0 to 3 (SPEC); the data only exists in New Relic if the check is re-ingested. `datadog.agent.up` host monitors map to "host not reporting" conditions (NRDOC `create-nrql-host-not-reporting-conditions.mdx` exists; not read in detail). `last(N).count_by_status()` means "N consecutive runs": `threshold_occurrences ALL` with a duration. |
| `process alert` | NRQL on `ProcessSample` | manual | Needs the infrastructure agent's process samples enabled; `search` text maps to `WHERE commandName LIKE`. UNVERIFIED data availability. |
| `composite` | New Relic compound alert condition (AND, OR, NOT, up to 5 component conditions, variables a to e) | approximate | NRDOC `compound-alerts/`: component conditions must be standard conditions in one owning account; max five; facet matching is optional. Datadog `&&` returns the least severe status and `\|\|` the most severe (D-COMP), NR has "any threshold of a component counts as triggered". API exposure for compound conditions was not found in the docs read: UNVERIFIED. A composite needs its component monitors converted first (IDs resolve against the export). |
| `slo alert` (`error_budget(...)`, burn rate) | New Relic service level with fast-burn and slow-burn alert conditions | approximate / manual | The SLO object has to be converted too (metric SLO: good and valid event NRQL). `error_budget("id").over("30d") > 75` maps to a budget-consumed alert only if NR offers one: UNVERIFIED (docs read describe burn-rate alerts). |
| `synthetics alert` | New Relic synthetic monitor plus alert condition | manual | Created through the Synthetics API in Datadog; the test definition is a separate export. |
| `rum alert`, `trace-analytics alert`, `ci-*`, `audit alert`, `error-tracking alert`, `database-monitoring alert`, `network-performance alert`, `network-path alert`, `cost alert`, `data-quality alert`, `data-jobs alert`, `llm-observability alert` | NRQL on the matching New Relic event type (`PageView`/`BrowserInteraction`, `Span`, ...) | manual | The data is Datadog-product-specific. Each needs an owner decision on the New Relic product that replaces it. Query text for several is UNVERIFIED. |

Summary of what New Relic mapping supports: static-threshold metric alerts (`query alert` without special functions) and log count alerts are the only families with a mechanical path; anomaly, forecast, composite, SLO and outlier have a New Relic feature but a different algorithm, so approximate; everything else is manual.

### 11.2 Options

| Datadog option | NR setting | Verdict |
|---|---|---|
| `thresholds.critical`, `.warning` | terms CRITICAL and WARNING (max two, REPO) | exact |
| `thresholds.critical_recovery`, `warning_recovery` | none found: no recovery-threshold setting in the NR condition pages or schema read | none (record as a loss; NR closes when the signal returns under the open threshold) |
| operator `<`, `<=`, `>`, `>=`, `==`, `!=` | `below`, `below_or_equals`, `above`, `above_or_equals`, `equals`, `not_equals` | exact |
| `last_Nm` / `last_Nh` evaluation window | `aggregation_window` (use 30 to 3600 s per REPO) and `threshold_duration` multiple of it | approximate (section 12) |
| `notify_no_data`, `no_data_timeframe` | `expiration_duration` (30 to 172800 s), `open_violation_on_expiration` true | approximate: needs a signal to have existed (both products say so: D-CONF; NRDOC `create-nrql-alert-conditions.mdx`) |
| `on_missing_data = resolve` | `close_violations_on_expiration` | approximate |
| `on_missing_data = default` for count queries | an empty window is 0 in Datadog; NULL in NR (REPO) | manual: use `filter(count(*), WHERE ...)` or loss of signal |
| `require_full_window` | none | none |
| `evaluation_delay` (seconds) | `evaluation_delay` (max 7200 s) or `aggregation_delay` | approximate |
| `new_group_delay` | `evaluation_delay` for new signals (REPO) | approximate |
| `renotify_interval`, `renotify_statuses`, `renotify_occurrences`, `escalation_message` | none documented in the alert pages read (no reminder setting found) | none (record) |
| `timeout_h` | `close_violations_on_expiration` with `expiration_duration` | approximate |
| `notify_by` | choice of FACET | manual |
| `include_tags` | `title_template` | approximate |
| `priority` 1 to 5 | issue priority CRITICAL/HIGH/MEDIUM/LOW; condition priority is only critical/warning | approximate (P1,P2 critical; P3 high...; the mapping rule is a policy decision) |
| `scheduling_options` (cumulative / custom schedule windows) | none | none |
| `group_retention_duration` | none | none |
| `notification_preset_name`, `notify_audit`, `locked`, `restricted_roles` | none | none |
| `tags` | entity tags set after creation (`newrelic_entity_tags`) | approximate |

### 11.3 Notifications

| Datadog | NR | Verdict |
|---|---|---|
| `@<email>` | EMAIL destination and channel | exact (address is in the message) |
| `@slack-<channel>` | SLACK destination (OAuth in a browser) and channel with `channelId` | manual (OAuth; the Slack channel id is not in the monitor) |
| `@pagerduty-<service>` | PAGERDUTY_SERVICE_INTEGRATION (integration key) | manual (secret) |
| `@webhook-<name>` | WEBHOOK destination with URL, auth and a payload template | manual (URL, secret and payload live in Datadog's account) |
| `@teams-<name>` | MICROSOFT_TEAMS destination (app install, security code) | manual |
| `@jira-...`, `@servicenow-...`, `@opsgenie-...` | JIRA, SERVICE_NOW_APP, WEBHOOK with vendor template | manual |
| `@team-<handle>` | a workflow filtered on a tag, one destination per team channel | manual |
| `@<name>` that is a Datadog user | EMAIL if the address is known | manual |
| dynamic handle `@slack-{{service.name}}` | one workflow per value, or a webhook that routes | manual (NR workflows route by filters, not by interpolated handles) |
| `{{#is_alert}}`, `{{#is_recovery}}`, ... | Handlebars `{{#if}}`, `{{#eq state "ACTIVATED"}}`, notification triggers per destination (ACTIVATED, CLOSED, ...) | approximate: a rewrite table (`is_alert` -> state ACTIVATED with priority CRITICAL, `is_recovery` -> CLOSED, `is_warning` -> priority HIGH, `is_no_data` -> loss-of-signal title). Rendering differs, no shared variable names. |
| `{{value}}`, `{{threshold}}`, `{{comparator}}` | `accumulations.evaluation...` and `{{accumulations.nrqlQuery}}`, condition fields (names in REPO reference, 72 keys) | approximate |
| `{{host.name}}` and other tag variables | `{{accumulations.tag.<name>}}` / `{{tags.<name>}}` (title template) | approximate; the tag spelling is UNVERIFIED (REPO flags three conflicting spellings) |
| `{{first_triggered_at}}`, `{{triggered_duration_sec}}` | `createdAt`, `activatedAt` style issue fields | approximate |

Never mark an enabled condition complete when it has no notification target (rule shared with the existing sources).

### 11.4 Dashboards: widgets

NR visualisations used by the repo: `viz.line`, `viz.area`, `viz.bar`, `viz.stacked-bar`, `viz.billboard`, `viz.table`, `viz.pie`, `viz.heatmap`, `viz.histogram`, `viz.bullet`, `viz.markdown`, `viz.funnel`, `viz.treemap` (REPO, grep of `src` and research). Verdicts are for the widget container; the query inside follows 11.1/12.

| Datadog `type` | NR | Verdict |
|---|---|---|
| `timeseries` (`display_type` line/area/bars) | `viz.line`, `viz.area`, `viz.bar` with `TIMESERIES AUTO` | approximate (query semantics) |
| `query_value` | `viz.billboard` | approximate; conditional formats become billboard thresholds (warning, critical) only for the first comparators |
| `toplist` | `viz.bar` with `FACET ... LIMIT n` | approximate |
| `query_table` | `viz.table` | approximate; column formulas become multiple selects |
| `heatmap`, `distribution` | `viz.heatmap`, `viz.histogram` (`histogram()` or `percentile` buckets) | approximate; distribution metrics need the distribution type in NR |
| `change` | billboard with `COMPARE WITH` | approximate: `COMPARE WITH` shows the current value and percent change (NRDOC nrql-syntax) |
| `note`, `free_text` | `viz.markdown` | exact for text (HTML in notes is not exact) |
| `group` | dashboard page (one page per group) or a markdown header | approximate; NR has no collapsible group, pages are the container |
| `hostmap` | none (no host map widget in the repo viz list; whether New Relic has one is UNVERIFIED) | none |
| `treemap`, `sunburst` | `viz.treemap`; sunburst: pie or none | approximate / none |
| `scatterplot` | none documented in repo viz list | none |
| `list_stream`, `log_stream`, `event_stream`, `event_timeline` | `viz.event-feed` / logs table | approximate |
| `slo`, `slo_list` | service level widget (not in repo viz list) | manual |
| `alert_graph`, `alert_value`, `manage_status`, `check_status` | none (monitor status widgets) | none |
| `geomap` | map widget (not in the repo viz list) | manual |
| `image`, `iframe` | markdown image / none | approximate / none |
| `topology_map`, `servicemap`, `sankey`, `flame_graph`, `cloud_cost_summary`, `powerpack`, `run_workflow`, `wildcard` | none | none |

### 11.5 Dashboards: variables, layout, saved views; mutes

| Datadog | NR | Verdict |
|---|---|---|
| Template variable (tag key + `available_values`), `$name` in scope | query variable `FROM Metric SELECT uniques(<attr>)` and `WHERE <attr> IN ({{var}})` | approximate (REPO `newrelic-dashboard-variables.md`: multi-select needs `IN`; a variable is substituted whole; no variable in `TIMESERIES`, `LIMIT`, `COMPARE WITH`, `AS`) |
| Default `*` | `default_values = ["*"]` | exact |
| `prefix` | filtering the `uniques()` query by attribute | approximate |
| `defaults[]` (several) | `default_values` list | exact |
| `$scope` (empty prefix, whole scope) | none: NR substitutes one variable for one value, and a scope can be several tags | manual |
| group-by variable (`type`) in `by {$x}` | `FACET {{x}}` with an `identifier` variable | approximate |
| `$name.value` | `{{name}}` | approximate |
| `@facet` variables on logs/RUM | attribute variables on `Log` | approximate |
| `team` filter | none | none |
| `template_variable_presets` (saved views) | none documented for dashboards read (only `default_values`) | none (record as a loss) |
| `layout_type: ordered` + grid `x,y,width,height` | NR 12-column grid, row/column/width/height | approximate |
| `layout_type: free` | same grid, positions normalised | approximate |
| `reflow_type`, tabs | pages | approximate |
| `description`, `tags`, `notify_list` | dashboard description; tags as entity tags; none | approximate / none |
| Widget `time.live_span` | `SINCE ... UNTIL` inside the query overrides the time picker (REPO finding) | approximate: prefer dashboard time picker |
| Muting: `silenced`, downtimes (one-time) | `alertsMutingRuleCreate` with `schedule` (`startTime`, `endTime`, `timeZone`) and a condition on `conditionName`, `policyName`, `tags.<name>` | approximate |
| Muting: recurring downtimes with RRULE | NR repeat `DAILY`, `WEEKLY` (with `weeklyRepeatDays`), `MONTHLY`, ending by date or count | approximate: only simple RRULEs map (FREQ DAILY/WEEKLY/MONTHLY, BYDAY, COUNT/UNTIL); anything else manual |
| Downtime `scope` (group scope on tags) | rule condition on facet tags | approximate (matches alert-event attributes only) |
| `mute_first_recovery_notification`, `notify_end_*` | `actionOnMutingRuleWindowEnded` | approximate |
| Workflow muting handling | A migrated workflow must set `mutingRulesHandling` to something other than `NOTIFY_ALL_ISSUES` or muting does nothing (REPO). | note |

## 12. Semantic differences that change numbers

1. `last_5m` is a rolling window evaluated every minute (windows under 24 h: every minute; 24 to 48 h: every 10 min; 48 h or more: every 30 min, D-CONF). A New Relic aggregation window is a non-overlapping bucket unless `slide_by` is set; `slide_by` must divide the window, is at least 30 s for windows of 3600 s or less, and costs more data points (REPO). Two choices: window = Datadog window with a long evaluation interval (fewer alerts) or window = Datadog window with `slide_by` 60 s (closest, more cost).
2. Time aggregator versus NRQL function: `avg(last_5m):avg:m` is the average of per-interval averages; `average(m)` over a 5 minute window averages raw points. Equal when intervals have equal weights, different for sparse series. `min(last_5m) > x` ("all points above x") and `max(...) > x` ("any point above x") map to `min()` and `max()` of raw points; the documented reversal for "below" alerts (D-METRIC) must be mirrored.
3. `sum(last_5m)` of a gauge in Datadog sums per-interval values; in NR `sum()` of a gauge sums raw points; interval length changes the answer (the same hazard exists in REPO `sum-gauge`).
4. `as_count()` versus `rate()`: Datadog `COUNT` metric `.as_count()` is a sum per rollup interval; `.as_rate()` divides by the interval length. NR count metrics are delta counts with `interval.ms` (NRDOC `metric-data-type.mdx`), so `sum(m)` is the count and `rate(sum(m), 1 second)` is the per-second rate. For Datadog RATE metrics stored as per-second values, `.as_count()` multiplies by the interval; NR has no automatic conversion. If the DD RATE reaches NR as a delta sum with `datadog.metric.as_type=rate` (OTEL), the value is per-interval already or per-second depending on the route: UNVERIFIED, and a data check is the only test.
5. `rollup(sum, 60)` fixes the bucket; NRQL uses `TIMESERIES 60 seconds` in widgets and the aggregation window in conditions. In monitors Datadog advises against rollup (bucket alignment to Unix time, D-FUNC rollup); a converted condition should drop it and say so.
6. `fill(zero|last|linear, limit)`: NR has `fill_option` NONE/STATIC/LAST_VALUE for conditions (no linear). `default_zero(q)` is "evaluate as zero": `STATIC` with `fill_value 0`, but empty-window behaviour is UNVERIFIED (REPO).
7. `timeshift(q, -604800)` and `week_before`: NRQL `COMPARE WITH 1 week ago` shows two series for the outermost query only, cannot be a condition, and cannot be nested; a formula `a / timeshift(a, -604800)` has no NRQL form. Verdict manual in alerts, approximate in widgets.
8. `top(q, 10, 'mean', 'desc')`: `FACET ... ORDER BY average(...) LIMIT 10` (LIMIT not allowed in conditions).
9. `diff`, `derivative`, `monotonic_diff`, `per_minute`: NR has `derivative(attr, 1 minute)` (least-squares, NRDOC nrql-syntax) and `rate()`; `diff` of gauges has no function. `derivative` in conditions is UNVERIFIED (REPO).
10. Percentile aggregators and distribution metrics: Datadog percentiles on a distribution are computed server-side from sketches; `percentile(m, 95)` in NR on a gauge is over raw points; on a NR distribution metric it is a different statistic. Flag.
11. Anomaly algorithms: Datadog `basic` (a simple lagging rolling quantile, no seasonality, scale-insensitive), `agile` (a robust version of SARIMA, adjusts quickly to level shifts, not scale-insensitive), `robust` (seasonal-trend decomposition, stays constant through long anomalies, scale-insensitive); `bounds` = standard deviations; alert when more than `threshold` (fraction 0 to 1) of points in `alert_window` are outside the band; `agile`/`robust` need up to three weeks (weekly seasonality) (D-ANOM, D-FUNC). New Relic baseline: learns 1 to 4 weeks, cyclical patterns, threshold in standard deviations, seasonality setting, band tightness; no equivalent of the fraction-of-window rule and no named algorithms. Threshold values are not transferable. Treat as: create a baseline condition disabled, with the Datadog parameters in the description, and require a person to enable it after the baseline is learned.
12. Evaluation of missing data: Datadog `count` queries read empty as 0, other types keep the last status; NR reads NULL and does not evaluate (REPO).
13. Multi-alert grouping: Datadog groups by tag with unlimited groups (retention per `group_retention_duration`); NR facets are capped at 20000 per condition and a faceted query cannot use `LIMIT` (REPO). NRQL `FACET` in widgets caps at five attributes and 5000 facets (REPO census).
14. Unit handling: Datadog metric units come from metric metadata and widget `custom_unit`; NR billboard units are limited. Record unit loss.
15. Naming: tags are lowercased and limited to 200 characters in Datadog (D-TAGS); NR attribute names are case-sensitive and the repo's `identifier` quoting handles dots.

## 13. How a Datadog metric is named in New Relic

Honest summary: no page read gives a mapping table from a Datadog metric name to a New Relic name. What exists are four ingest routes, and the name story differs per route. The table says what is documented and what is a guess.

| Route | What is documented | Name and attributes in New Relic |
|---|---|---|
| Datadog Agent dual-ship through the Gateway API (preview) | NRDOC `data-apis/ingest-apis/gateway-api-import-data-other-observability-platforms.mdx`: Datadog agent 6.x or higher; set `dd_url`, `logs_dd_url`, `apm_dd_url` to `https://gateway.service.newrelic.com`; New Relic account staff map Datadog API keys to New Relic keys; a transformation service converts data into New Relic formats and sends it to the ingest APIs; "doesn't map perfectly"; no default UI; query with NRQL. Only Datadog is supported. | UNVERIFIED. The page gives no naming rule, no attribute rule, no list of limitations. Needs a sandbox test with a real agent before any name map claims it. Feature is "preview". Not a "Datadog-compatible endpoint" in the sense of an open `/api/v1/series` URL: it is an account-managed gateway. |
| Datadog Agent or DogStatsD through the OpenTelemetry Collector `datadogreceiver` (alpha) then OTLP to New Relic | OTEL: receiver listens on 8126 (traces) and the metrics intake; Datadog metric types: `gauge` becomes an OTel Gauge; `count` becomes a delta Sum, non-monotonic; `rate` becomes a delta Sum with the data point attribute `datadog.metric.as_type = rate`. Tags in `key:value` form become attributes; a tag without a colon becomes `unnamed_<tag>`; repeated keys keep the last value unless the feature gate `receiver.datadogreceiver.EnableMultiTagParsing` is on. The host is carried as a resource attribute. A table `datadogKnownResourceAttributes` maps Datadog tag keys to OTel semantic-convention names (`env` -> `deployment.environment.name`, `service` -> `service.name`, `version` -> `service.version`, `kube_namespace` -> `k8s.namespace.name`, `pod_name` -> `k8s.pod.name`, `kube_cluster_name` -> `k8s.cluster.name`, `container_name` -> `container.name`, `region` -> `cloud.region`, ...). NRDOC `opentelemetry/best-practices/opentelemetry-otlp.mdx`: New Relic prefers delta temporality for Counter, Histogram; cumulative for UpDownCounter and Gauge. | The metric name: the receiver passes `serie.Metric` into the dimensions it builds; the function body was not read, so name pass-through is likely but UNVERIFIED. In New Relic that string would be `metricName` on `Metric`, dots kept. Attributes: tag key as attribute name; the semantic-convention rename table is named for resource attributes; whether it also renames metric tags is UNVERIFIED. So `env:prod` may arrive as `env` or as `deployment.environment.name`. A `rate` arrives as a delta sum with an extra marker attribute and without the interval division: the user must apply `rate()` or multiply. |
| DogStatsD to New Relic's StatsD integration (`nri-statsd`, gostatsd based) | NRDOC `infrastructure/other-infrastructure-integrations/statsd-monitoring-integration.mdx`: format `<name>:<value>\|<type>\|@<rate>\|#<tags>`; types `c`, `g`, `ms` only; "tags attached to your metrics are converted into attributes"; uses the Metric API; counters send 0 after reset unless `expiry-interval` is 1ms; timers emit sub-metrics `.std_dev`, `.median`, `.summary`, `.sum_squares`, `.mean`, `.per_second` and percentile series with the threshold as a tag; default tags via `TAGS="a:b c:d"`. Query: `SELECT count(*) FROM Metric WHERE metricName = 'myMetric' AND environment = 'production'`. | Name: the StatsD name unchanged in `metricName` (documented example). Attributes: DogStatsD `#key:value` tags become attributes of the same name (documented). DogStatsD-only types (`h` histogram, `d` distribution, `s` set) and events and service checks are not in the documented type list: UNVERIFIED whether they are accepted. |
| Datadog-style OpenTelemetry metrics (semantic mapping in the other direction) | D-OTELMAP: Datadog maps OTel names to Datadog names when ingesting OTLP, e.g. `system.cpu.load_average.1m` -> `system.load.1`, `system.cpu.utilization` -> `system.cpu.user` (rows in the page). | This is the reverse of what a Datadog-to-New Relic migration needs, but it shows that a monitor on a Datadog name (`system.cpu.user`) cannot be translated by name alone when the estate is OTel-instrumented: the New Relic data carries the OTel name (`system.cpu.utilization` with a `state` attribute) and the Datadog name appears only in the Datadog UI. |
| New Relic infrastructure agent | Not Datadog-related, but the usual landing place for host data after a switch. | Host data lands in the `SystemSample` event (REPO `gcp-vm-cpu-diagnosis.md` alerts on `SystemSample.cpuPercent`); there is no `system.cpu.user` metric. A Datadog host monitor needs a rewrite onto `SystemSample` fields (other field names: UNVERIFIED here), not a rename. |

Practical rule for the future source (the part that is solid): a name map keyed by Datadog metric name with, per entry, the target event type and attribute, `unit_scale`, and `kind` (count or gauge), as already proposed in the Elastic note section 7 item 13 (REPO analogue: `src/importer/sources/grafana/dashboard_convert/name_map.py`), plus a data-check step that reports which Datadog names have no data in the target. Nothing may be marked verified until the name has arrived in a sandbox (existing project rule).

## 14. Build plan: a file-first Datadog source (no live API)

Principle: input is exported JSON (monitors from `GET /api/v1/monitor`, dashboards from `GET /api/v1/dashboard/<id>` or the integration template shape), loaded with size caps. Untrusted text reaches outputs only through the existing escaping functions. Anything not understood is `Confidence.MANUAL` with a reason that has a remediation (REPO `docs/dev/adding-a-source.md`).

### 14.1 Milestones

| # | Milestone | Content | Fixtures that gate it | Effort (one developer, days) |
|---|---|---|---|---|
| D0 | Models and loader | `sources/datadog/models.py`: pydantic models with `extra=allow` for Monitor (API and template wrapper) and Dashboard; `load_*` with size cap and the JSON depth guard (`core/json_depth.py`); accept both shapes; reject nothing on unknown option keys | all real and synthetic fixtures load; `SYNTHETIC_monitor_nulls`, `_wrong_types`, `_deep_nesting`, `_huge_query` do not crash | 2 |
| D1 | Query parser | Hand-written scanner for the metric query (aggregators, scope with `AND`/`OR`/`IN`/`!`/wildcards, `by`, function chain including nested calls, formulas of named queries), the log-search tokenizer, and the other monitor query forms in section 3 (`logs()`, `events()`, service check, composite expression, SLO). Length and depth caps. A parse that is not lossless is a first-class result | 70 real monitor queries parse; deep-nesting case is capped | 6 |
| D2 | Translator: monitors, simple threshold | `query alert` plain threshold and `log alert` count to static NRQL conditions: windows, terms, operators, `notify_no_data` to expiration, `evaluation_delay`, FACET. Everything else MANUAL with reasons | counts: how many of the 339 become converted vs manual (report in the improvement loop) | 6 |
| D3 | Name map and data check | Metric name map with `unit_scale`, `kind`; scaffold listing unmapped Datadog metrics; data check against New Relic (reuse the GCP/Grafana data-check shape) | section 13 routes tested in a sandbox | 5 (plus sandbox time and cost approval) |
| D4 | Notifications | Parse handles from messages; create notification IR; destinations with the "secret/OAuth" effort codes; message template rewrite for `is_alert`, `is_recovery`, `is_warning`, `is_no_data`, `is_renotify`, tag variables | the 25 corpus messages with handles; synthetic secret fixtures: no secret reaches output or logs | 5 |
| D5 | Alert fields and approximations | `renotify`, recovery thresholds, priority, tags, `scheduling_options` recorded as precise losses; `fill`, `default_zero`, `as_count` rules (section 12); anomaly to disabled baseline condition with parameters in the description; forecast to predictive; composite to compound | per-reason remediation test | 6 |
| D6 | Muting | Downtime v2 and `silenced` to muting rules, simple RRULEs | RRULE table cases | 3 |
| D7 | Dashboards | Widget IR for the types marked approximate in 11.4, formulas and queries, `conditional_formats`, template variables and `$name` rewriting, `$scope` refused with a reason | 30 real dashboards; `SYNTHETIC_dashboard_*` | 8 |
| D8 | Report, CLI, docs, baseline | Report profile, `--source datadog`, runbook, `docs/what-migrates.md`, add the folder to `FIXTURES` in `core/improve.py` and record a baseline with the owner's approval | gate green | 3 |
| D9 | Optional extractor | Read-only `extract.py` with an allow-list of GETs (`/api/v1/monitor`, `/api/v1/dashboard`, `/api/v1/downtime`, `/api/v1/slo`); impossible on the owner's network, so it ships untested against a live site until someone with access runs it | none | 3 |

Total: about 47 developer-days for D0 to D9; D0 to D2 plus D8 (a first useful release: threshold and log monitors) is about 17 days. Add a reviewer pass per milestone as in the improvement loop.

### 14.2 What is shared with other branches

| Shared piece | Where | Reuse for Datadog |
|---|---|---|
| PromQL engine and AST (`improve/promql-engine`, `sources/grafana/promql_ast.py`, the 857-expression census) | Grafana branches | The Datadog metric query is a different grammar but the same job (aggregation, scope, function chain, offset to NRQL). Reuse the NRQL emission helpers (`identifier`, `quote_string`), the `FACET`/`WHERE` builder, the lossless-flag parse result and the length/depth caps. Do not reuse the grammar. A common "metric query IR" (aggregation, selectors, group-by, window, transforms) lets one NRQL emitter serve both; that refactor is the real shared work. |
| Alert-field handling (`improve/gcp-alert-fields`, `sources/gcp/alert_fields.py`) | GCP branch | Same structure: severity, strategy, missing-data handling and forecasts to settings or precise notes, with printable bounded untrusted fields and taxonomy registration. The Datadog options table in 11.2 is the same exercise with different inputs. |
| Notifications (`improve/grafana-notify-convert`, `core/notification_ir.py`, `core/apply_notifications.py`) | Grafana, AWS | Destination and workflow emission and the effort codes; the Datadog-specific part is the handle parser and message-template rewrite. |
| Muting (`core/muting_ir.py`) | Dynatrace, Grafana | Downtimes to muting rules. |
| Dashboard IR and emit (`core/dashboard_ir.py`, `core/emit/dashboard.py`, `improve/dashboard-ir`, `improve/chained-vars`) | Grafana, GCP, Azure | Widget IR, variables (`IN ({{v}})`), nested variable ordering. Datadog adds formulas and conditional formats. |
| Data path and data check (`improve/gcp-datapath`, `grafana-datapath-plan`, `sources/*/data_check.py`) | GCP, Grafana | The metric-name story of section 13 is the same problem class. |
| Parity verifier (`improve/parity-verifier`) | Grafana | A parity rig that seeds identical data into both systems is the only way to settle section 12 items 2 to 6. |
| Remediation (`core/remediation.py`, taxonomy) | all | Every manual reason needs a rule; a test fails otherwise. |

### 14.3 Risks and what could invalidate the plan

- The integration corpus under-represents customer estates (composite, outlier, SLO, synthetics, RUM, trace analytics are absent). The first real export must be run through the loader before D2 is sized.
- Section 13 is mostly UNVERIFIED. The name map cannot be trusted until a sandbox run with a real Datadog agent through one of the routes; the Gateway API is a preview feature that needs New Relic account staff to enable it.
- The Datadog site is blocked on the owner's network, so D9 cannot be tested here.
- API field names in the SPEC are for API v1/v2 as of 2026-11-11; Datadog adds fields (the SPEC already has fields the integration templates lack).

## 15. Still unverified (needs a Datadog account or a sandbox)

1. Query text for `rum alert`, `trace-analytics alert`, `synthetics alert`, `data-quality alert`, `data-jobs alert`, `llm-observability alert`, and for SLO burn-rate alerts.
2. Exact `$var` expansion rule (prefix + value) for dashboard queries beyond the corpus examples.
3. Whether anomaly `deviations` and New Relic standard deviations can be related at all.
4. New Relic: compound-alert API, recovery thresholds (none found), reminders (none found), `rate()` and `derivative()` inside conditions, `fill_option STATIC 0` on empty windows.
5. Route behaviour for names and attributes (section 13), especially Gateway API and whether the receiver renames metric tags.
6. Which of the 23 monitor types appear in a given customer estate.
