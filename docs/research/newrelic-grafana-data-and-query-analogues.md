# Grafana stack and New Relic: data model and query language analogues

Research for the Grafana source of Conquest Importer. Written 2026-11-09. Public documentation and public source only. No Grafana or New Relic instance was called, no `.env` was read, no cloud command was run. Every NRQL statement below was written from documentation and has not been executed against New Relic; where it matters the row says so.

## 0. Evidence keys

Every claim carries one of: DOCS (a page, key below), CODE (a repo path), UNVERIFIED (from memory or inference, not read in a source). Prose labelled "inference" is reasoning from DOCS facts and is also not tested.

New Relic docs (raw files of `newrelic/docs-website`, branch `develop`, fetched 2026-11-09; base `https://raw.githubusercontent.com/newrelic/docs-website/develop/src/content/docs/`):

- N1: `nrql/nrql-syntax-clauses-functions.mdx` (NRQL reference).
- N2: `infrastructure/prometheus-integrations/view-query-data/supported-promql-features.mdx`.
- N3: `infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql.mdx`.
- N4: `infrastructure/prometheus-integrations/view-query-data/grafana.mdx` (dashboard translation API).
- N5: `infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration.mdx`.
- N6: `infrastructure/prometheus-integrations/install-configure-prometheus-agent/default-metric-labels-and-metric-attributes.mdx`.
- N7: `data-apis/understand-data/metric-data/metric-data-type.mdx`.
- N8: `data-apis/ingest-apis/metric-api/report-metrics-metric-api.mdx` and `metric-api-limits-restricted-attributes.mdx`.
- N9: `data-apis/understand-data/metric-data/query-metric-data-type.mdx`.
- N10: `opentelemetry/best-practices/opentelemetry-best-practices-metrics.mdx`, `-logs.mdx`, `-traces.mdx`, `-resources.mdx`, `opentelemetry-otlp.mdx`.
- N11: `opentelemetry/get-started/apm-monitoring/opentelemetry-apm-ui.mdx`.
- N12: `logs/ui-data/parsing.mdx`, `logs/log-api/introduction-log-api.mdx`, `logs/ui-data/query-time-parsing.mdx`.
- N13: `distributed-tracing/ui-data/query-distributed-trace-data.mdx`, `distributed-tracing/trace-api/report-new-relic-format-traces-trace-api.mdx`.
- N14: `query-your-data/grafana-integrations/get-started/grafana-support-prometheus-promql.mdx`.
- N15: `ebpf/overview.mdx`, `kubernetes-pixie/auto-telemetry-pixie/understand-use-data/overview.mdx`, `infrastructure/prometheus-integrations/install-configure-prometheus-agent/install-prometheus-agent.mdx`.
- N16: `docs/research/newrelic-notifications-and-nrql-reference.md` (REPO note; part 2.3 is the alert-condition rule set, called "the reference note" below; it is itself DOCS-derived from the New Relic condition pages).

Grafana side (raw files, branch `main`/`master`, fetched 2026-11-09):

- P1: `prometheus/prometheus` `docs/querying/{functions,operators,basics}.md`, `docs/configuration/configuration.md`, `docs/feature_flags.md`.
- P2: `prometheus/docs` `docs/concepts/metric_types.md`, `docs/specs/prw/remote_write_spec.md`, `docs/guides/opentelemetry.md`, `docs/practices/naming.md`.
- P3: `open-telemetry/opentelemetry-specification` `specification/compatibility/prometheus_and_openmetrics.md`.
- L1: `grafana/loki` `docs/sources/query/{log_queries/_index,metric_queries}.md`, `get-started/labels/_index.md`, `shared/otel.md`.
- T1: `grafana/tempo` `docs/sources/tempo/traceql/construct-traceql-queries.md`, `metrics-from-traces/*`.
- M1: `grafana/mimir` `docs/sources/mimir/configure/configuration-parameters/index.md`.
- G1: README files of `grafana/{pyroscope,faro-web-sdk,k6,alloy,beyla}` and `grafana/k6-docs` `results-output/real-time`.
- X1: dashboard JSON from `https://grafana.com/api/dashboards/1860/revisions/latest/download` (Node Exporter Full, schemaVersion 41, 28 top-level panels, 301 targets, 291 distinct expressions); `kubernetes-monitoring/kubernetes-mixin` alerts and rules; `prometheus/node_exporter` `docs/node-mixin`.

CODE references: `src/importer/sources/grafana/translate.py` (alert translator, called "translate.py"), `src/importer/sources/grafana/dashboards/query_tier.py` (dashboard classifier, "query_tier.py"), `src/importer/core/emit/nrql.py`, and the earlier notes `docs/research/grafana-notifications-datapath.md` (section 6) and `docs/research/grafana-dashboards.md` (section 5).

## 1. Summary of findings

1. New Relic stores Prometheus counters as deltas, not cumulative values (DOCS N3, N7). So `sum(c)` over a bucket is already an increase, and `rate(sum(c), 1 second)` is that increase divided by the bucket. PromQL `rate()` additionally extrapolates to the window edges (DOCS P1). The two differ at the edges, and are not bit-equal.
2. Over remote write, New Relic guesses the metric type from the name: `_bucket`, `_count`, `_total` become `count`, `_sum` becomes `summary`, everything else `gauge` (DOCS N5). In the Node Exporter Full dashboard, 47 of the 108 distinct names passed to `rate()` or `increase()` do not end in one of those suffixes (CODE: counted from X1, for example `node_netstat_Tcp_InSegs`, `node_vmstat_pgfault`). Without a `newrelic_metric_type` relabel they are gauges, and `rate(sum(...))` on them is wrong.
3. The alert translator accepts only an instant selector with one optional `avg|sum|min|max (by)`; it refuses every counter-looking name, `rate`, binary operators and all joins (CODE translate.py:373-480). The dashboard module classifies queries into tiers and emits nothing (CODE query_tier.py header, `plan.py` header "Nothing is emitted for New Relic").
4. New Relic's own PromQL support is a translator to NRQL ("the closest NRQL approximation"), documented as covering over 99.5 percent of queries in the top Grafana dashboard downloads (DOCS N2). It is available through a Prometheus-compatible query API for Grafana data sources (DOCS N14) and a REST API that translates a Grafana dashboard JSON into a New Relic dashboard (DOCS N4). The documentation never states that a native New Relic dashboard widget or alert condition stores PromQL. Alert conditions are NRQL (DOCS N16 / NQC). Recommendation: the importer emits NRQL.
5. LogQL maps well for stream selectors, line filters and `count_over_time`/`rate`; parsers map to `aparse()`, `capture()` or ingest-time parsing rules; unwrap aggregations map to the plain aggregators on a parsed attribute. Two semantic traps: NRQL `LIKE` is case-insensitive while LogQL `|=` is case-sensitive, and NRQL `RLIKE` is fully anchored while LogQL `|~` is not (DOCS N1, L1).
6. TraceQL structural operators (`>>`, `>`, `~`) have no documented NRQL form. Span-level filters, durations and status map to `Span` attributes, but names differ by agent: OTLP spans carry `duration.ms`, `otel.status_code`, `span.kind`; New Relic agent spans carry `duration` in seconds (DOCS N10, N13).
7. Names matter more than functions: OTLP keeps dots, Prometheus-derived names use underscores, Loki converts dots to underscores (DOCS L1), the New Relic Kubernetes log plugin uses `namespace_name`, `pod_name`, `container_name`, `cluster_name`. A per-estate name map, checked with `keyset()`, is required before any converted NRQL means anything (section 8).
8. Function table counts (section 3): EXACT 18, APPROXIMATE 27, NONE 19 of 64 PromQL rows; LogQL table: 5, 18, 1 of 24.

## 2. Data model: Prometheus family against New Relic NRDB

| Topic | Prometheus / Mimir / Grafana Cloud | New Relic | Consequence for a converter |
|---|---|---|---|
| Unit of data | Sample: float64 (or native histogram) plus millisecond timestamp, in a series identified by metric name plus the full label set (DOCS P1 basics, P2 metric_types) | A `Metric` data point: `metricName`, `timestamp`, a value by type, attributes; a time series is a unique name plus attributes (DOCS N8, N9) | Series identity carries over only if every label becomes an attribute |
| `__name__` | Reserved label holding the metric name; `{__name__=~"x.*"}` selects several metrics (DOCS P1) | Attribute `metricName`, present on every Metric; wildcard `%` allowed in the function argument (DOCS N9) | Query by `WHERE metricName = '...'` or by the bare name in the function |
| Labels | Names `[a-zA-Z_][a-zA-Z0-9_]*`, values any string; UTF-8 names from Prometheus 3 (DOCS P2 opentelemetry.md) | Attributes; names alphanumerics, colon, period, underscore, max 255 characters; values max 4096 characters; max 150 attributes per metric on Metric API; Metric API name under 255 characters (DOCS N8) | Prometheus label names are valid attribute names. Names with a colon or a dot need backticks in NRQL (CODE `identifier()`) |
| Empty label | A label with an empty value is the same as no label; matchers that match "" also select series without the label (DOCS P1 basics) | Missing attribute is NULL; whether `attr != 'x'` includes NULL rows is not stated | UNVERIFIED: test `!=` and `= ''` on a sandbox before relying on them |
| Counter | Cumulative, monotonic, resets to 0 on restart; functions detect resets (DOCS P2 metric_types, P1 functions) | `count`: delta over `interval.ms`; "the Prometheus counter is a cumulative sum while the New Relic count is a delta sum" (DOCS N3). `cumulativeCount` is a variant that keeps the cumulative value, accepted from remote write and OTel, slightly larger (DOCS N7) | Never read a stored counter as cumulative. `sum()` = increase over the window |
| Gauge | Value that goes up and down | `gauge`; functions `latest min max sum count average` (DOCS N7) | `sum(gauge)` adds all data points in the bucket, not one per series |
| Classic histogram | `<b>_bucket{le}` (cumulative counts), `<b>_sum`, `<b>_count` as separate series (DOCS P2) | Each `_bucket` and `_count` is a `count`, `_sum` a `summary` (DOCS N5); `bucketPercentile()` and `histogram()` accept the `_bucket` name (DOCS N1) | `histogram_quantile` converts through `bucketPercentile` |
| Native histogram | Composite sample with dynamic buckets (DOCS P2) | The Metric types page lists Prometheus Remote Write as a source for `distribution` and `cumulativeCount` (DOCS N7), but the remote write page describes only name-based mapping (DOCS N5) | How a native histogram arrives is UNVERIFIED; treat as manual |
| Summary | `<b>{quantile="0.9"}` gauges plus `_sum` and `_count` (DOCS N3) | Quantile series are gauges, `_sum` summary, `_count` counter (DOCS N3). Quantiles cannot be re-aggregated | Quantile series convert as plain gauges; do not average them across series |
| New Relic `summary` | none | Pre-aggregated `count`, `sum`, `min`, `max` map, needs `interval.ms` (DOCS N8) | Differs from a Prometheus summary (DOCS N3) |
| New Relic `distribution` | none (closest: native histogram) | Re-aggregatable sketch; `percentile`, `histogram`, `min max sum count average` (DOCS N7) | Produced from OTLP histograms (DOCS N10) |
| `newrelic_metric_type` | n/a | Label added in `write_relabel_configs` with value `counter`, `gauge` or `summary` (case sensitive); stripped on receipt (DOCS N5) | The fix for counters without a suffix |
| Staleness | A series that stops being scraped gets a stale marker (special NaN `0x7ff0000000000002`); queries return nothing after it; lookback is 5 minutes (DOCS P1 basics, P2 prw spec: senders MUST send stale markers) | How New Relic treats stale NaNs on remote write is not documented | UNVERIFIED. After a target disappears, NRQL shows an empty window (NULL), not a drop to nothing at a precise time |
| Scrape interval and timestamps | Default `scrape_interval` 1m (DOCS P1 configuration.md). Sample timestamp is the scrape time | Metric API `timestamp` is the interval start; points older than 48 h or newer than 24 h are dropped (DOCS N8). PromQL-style instant queries look back only one minute, so a scrape interval above one minute can give "No data found" (DOCS N2) | Choose bucket >= 2 scrape intervals. Warn when the scrape interval exceeds 60 s |
| `job`, `instance` | Added by the scrape; identify the target (DOCS P2 opentelemetry.md) | Kept as attributes `job`, `instance` for the New Relic Prometheus agent (DOCS N6). Remote write adds `prometheus_server` (from the URL parameter), `newrelic.source` (`prometheusAPI`), `instrumentation.provider` (`prometheus`), `instrumentation.name` (`remote-write`) (DOCS N5 via the earlier note, N6) | Filter on `prometheus_server` to avoid duplicates from several servers |
| HA pairs | Two replicas scrape the same targets | Deduplicated using external labels `prometheus` (same across replicas), `prometheus_replica` (unique) and the `prometheus_server` URL parameter (DOCS: `install-configure/prometheus-high-availability-ha.mdx`) | Without these, `sum()` double counts |
| Exemplars | Stored with `--enable-feature=exemplar-storage` and sent in remote write (DOCS P1 feature_flags, P2 prw spec) | OTLP metric exemplars "are not currently supported" (DOCS N10). Remote write handling is not documented | UNVERIFIED for remote write; exemplar links are lost for OTLP |
| Recording rules | Evaluated server side, written back as ordinary series with names like `level:metric:operations` (DOCS P1 recording_rules.md) | They exist in NRDB only if the output series are remote-written | Names contain colons. Either find the rule file and inline its expression, or confirm the series arrives. CODE translate.py:113 `R_RECORD` marks a recording rule manual |
| Relabeling | `relabel_configs` before scrape, `metric_relabel_configs` after, `write_relabel_configs` before remote write (DOCS P1 configuration.md) | The only New Relic-aware relabel is `newrelic_metric_type`; keep/drop rules thin the volume (DOCS N5) | Relabeling can rename or drop the labels the dashboards use; read the Prometheus configuration (see the `prometheus-rules` note in the datapath file) |
| Cardinality limits | Prometheus per-scrape `sample_limit`, `label_limit`, `label_name_length_limit` default 0 (off) (DOCS P1 configuration.md). Mimir: `max_global_series_per_user` 150000, `max_label_names_per_series` 30, label name 1024, value 2048 (DOCS M1) | 1 to 15 million unique series per account per day (3M on the free edition to 10.2M for some paying accounts), 100k per metric name per day, 150 attributes per metric; above the limit raw data still arrives but 1-minute and 5-minute rollups stop for the rest of the day, so windows over 60 minutes can be wrong; remote write also has a count/summary series cap per account per 5 minutes (DOCS N8) | A Grafana Cloud tenant at 150k series is far below New Relic's default; a self-hosted Prometheus with millions of series is not |
| Units | In the metric name (`_seconds`, `_bytes`) by convention (DOCS P2 naming.md) | OTLP `Metric.unit` becomes attribute `unit`; description becomes `description` (DOCS N10). Remote write has no unit field | Units stay in the name; no `unit` attribute from remote write (inference) |
| OTel instrument to NR type | n/a | monotonic Sum cumulative gives `cumulativeCount`; monotonic Sum delta gives `count`; non-monotonic cumulative Sum gives `gauge`; non-monotonic delta Sum unsupported; Gauge gives `gauge`; Histogram and ExponentialHistogram give `distribution`; Summary gives `summary` but "not properly supported" because New Relic assumes it is delta (DOCS N10) | Prometheus summaries over OTLP break; send over remote write instead |
| OTel attributes | n/a | Resource, scope and data point attributes all become attributes; precedence: top-level metric fields over data point over scope over resource. Scope name and version become `otel.library.name` and `otel.library.version`. Limits: value 4095 characters, key 255, 64 resource, 8 scope and 128 record attributes, arrays 64 entries (DOCS N10) | Dots are kept, which differs from Prometheus naming |
| Temporality | Cumulative | New Relic is "generally a delta metrics system"; cumulative input is converted to deltas statefully, using `start_time_unix_nano` to detect resets (DOCS N10) | The first cumulative point of a series yields no delta (inference) |
| Entity synthesis | none; `job` and `instance` are plain labels | From OTel resource attributes: `service.name` makes a service entity; `host.id` plus `host.name` a host; relationships need `host.id`, or `k8s.cluster.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name` (DOCS N10 resources). Whether remote-write data synthesizes an entity is not documented | UNVERIFIED for remote write. Alerts tie health to an entity only when the query is scoped to one (DOCS N16) |
| Prom to OTLP naming | The OTel to Prometheus translation maps `service.name` to `job`, `service.instance.id` to `instance`, adds `_total` to monotonic Sums, unit suffixes, dots to underscores, depending on `translation_strategy` (`UnderscoreEscapingWithSuffixes` default, `NoUTF8EscapingWithSuffixes`, `NoTranslation`) (DOCS P2 opentelemetry.md, P3) | If a pipeline sends OTLP into New Relic without that translation, the OTel names arrive | The metric name a query needs depends on which pipeline wrote the data (also section 8) |
| Remote write arrival | Pass-through of series, with `write_relabel_configs` | Names and labels appear unchanged in the documentation examples (`container_memory_usage_bytes`, `id`) (DOCS N3), type by suffix, data visible after about five minutes (DOCS N5) | Use the exact Prometheus name |
| Alloy arrival | `prometheus.remote_write` with several `endpoint` blocks is the same remote write protocol; `otelcol.exporter.otlphttp` is the OTLP route (DOCS in datapath note section 6.3) | Remote write route: as above. OTLP route: OTel semantics | A converter must know which Alloy component feeds New Relic |
| `newrelic.timeslice.value` | none | The value column of APM timeslice metrics only; "you cannot query timeslice metrics in conjunction with dimensional metrics" (DOCS N9) | Never appears for Prometheus data; ignore |

## 3. PromQL to NRQL function table

Definitions. Dash: usable in a dashboard NRQL widget. Alert: usable in an NRQL alert condition, judged by the reference note part 2.3 (supported: `apdex average count latest max min percentage percentile sum uniqueCount`; not allowed: `SINCE UNTIL`, `TIMESERIES`, `PREDICT`, `histogram()`, several aggregation functions in one query, `COMPARE WITH`, `LIMIT`, subqueries; `rate() derivative() predictLinear()` not mentioned, so UNVERIFIED; `filter()` is documented as the null-safe count tool). "UNV" means UNVERIFIED. Verdict: EXACT means the same function over the same samples (window edges still differ unless `SLIDE BY` is used, DOCS N1); APPROXIMATE means a documented difference in method or an unverified NRQL form; NONE means no NRQL equivalent. All NRQL is untested.

Edge rule for every row: PromQL evaluates at each step over a trailing window and uses the 5-minute lookback for instants (DOCS P1). NRQL `TIMESERIES n` produces tumbling buckets, or overlapping windows with `SLIDE BY` (DOCS N1). Expect shifted peaks, not different values, at bucket boundaries.

| # | PromQL | NRQL proposal | Dash | Alert | Verdict | Notes and gotchas |
|---|---|---|---|---|---|---|
| F01 | `m{a="b",c!="d"}` instant selector | `SELECT latest(m) FROM Metric WHERE a = 'b' AND c != 'd' FACET ... TIMESERIES` (gauge) | yes | yes | EXACT | DOCS N3 example. Empty-label and NULL rules unverified (section 2) |
| F02 | `=~` and `!~` matchers | `RLIKE` / `NOT RLIKE`; `IN (...)`; `LIKE 'x%'` | yes | yes | EXACT | Both fully anchored, RE2 (DOCS N1, P1). `LIKE` is case-insensitive (DOCS N1): only exact for case-free values |
| F03 | `{__name__=~"re"}` | `metricName RLIKE 're'` or `%` in the function name | yes | UNV | APPROXIMATE | One `SELECT` function per metric; the query shape changes |
| F04 | `m[5m]` as a result | `SELECT * FROM Metric WHERE metricName = 'm' RAW` | table | no | NONE | RAW limited to 48 hours (DOCS N9) |
| F05 | `offset` | `COMPARE WITH` or a shifted `SINCE ... UNTIL` | yes | no | APPROXIMATE | New Relic PromQL supports offset only when all vectors share it (DOCS N2). `COMPARE WITH` not usable in conditions (DOCS N16) |
| F06 | `@` modifier | none per expression | no | no | NONE | Not in the supported list (DOCS N2) |
| F07 | subquery `m[1h:1m]` | none documented | no | no | NONE | Subqueries not usable in conditions (DOCS N16) |
| F08 | `rate(c[w])` | `SELECT rate(sum(c), 1 second) ... TIMESERIES w` | yes | UNV | APPROXIMATE | DOCS N3. Delta counts: no extrapolation, resets absorbed at ingest. Counter must be typed `count` |
| F09 | `irate(c[w])` | `rate(sum(c), 1 second)` with `TIMESERIES` equal to the scrape interval, or `latestrate(c, 1 second)` for the last two points | yes | UNV | APPROXIMATE | `latestrate` DOCS N1. Jagged if bucket is below the scrape interval (DOCS N2) |
| F10 | `increase(c[w])` | `SELECT sum(c) ... TIMESERIES w` | yes | yes (`sum`) | APPROXIMATE | `sum` of delta counts is the true increase; Prometheus extrapolates (DOCS P1) |
| F11 | `delta(g[w])` | `latest(g) - earliest(g)` per bucket | yes | UNV | APPROXIMATE | `earliest` is listed for events; not in the Metric gauge function list (DOCS N7): UNV on Metric |
| F12 | `idelta(g[w])` | `latestrate(g, 1 minute)` times the interval in minutes | yes | UNV | APPROXIMATE | Only if scrapes are regular |
| F13 | `deriv(g[w])` | `derivative(g, 1 second)` | yes | UNV | APPROXIMATE | Both use least squares (DOCS N1, P1). `derivative` needs two or more points else null |
| F14 | `predict_linear(g[w], t)` | `predictLinear(g, t)` | yes | UNV | APPROXIMATE | DOCS N1 advises against it with `TIMESERIES`. Conditions have a separate "predict future behavior" threshold option (DOCS N16) |
| F15 | `resets(c[w])` | none | no | no | NONE | Counters are deltas; resets are not stored |
| F16 | `changes(g[w])` | none | no | no | NONE | |
| F17 | `double_exponential_smoothing`, `holt_winters` | none | no | no | NONE | A baseline condition is a different feature (DOCS N16) |
| F18 | `avg_over_time(g[w])` | `average(g)` with `TIMESERIES w` (`SLIDE BY` to overlap) | yes | yes | EXACT | |
| F19 | `min_over_time`, `max_over_time` | `min(g)`, `max(g)` | yes | yes | EXACT | |
| F20 | `sum_over_time(g[w])` | `sum(g)` | yes | yes | EXACT | Adds data points |
| F21 | `count_over_time(g[w])` | `count(g)` | yes | yes | EXACT | Counts data points |
| F22 | `last_over_time(g[w])` | `latest(g)` | yes | yes | EXACT | |
| F23 | `first_over_time(g[w])` | `earliest(g)` | yes | UNV | APPROXIMATE | See F11 |
| F24 | `quantile_over_time(q, g[w])` | `percentile(g, q*100)` | yes | yes | APPROXIMATE | Sketch based (DOCS N1). Percentile over many facets can fail in a condition (DOCS N16) |
| F25 | `stddev_over_time`, `stdvar_over_time` | `stddev(g)`, `stdvar(g)` | yes | UNV | APPROXIMATE | Not in the Metric gauge function list (DOCS N7); the PromQL translator lists them (DOCS N2). Population versus sample not stated |
| F26 | `present_over_time`, `mad_over_time`, `ts_of_*` (experimental) | none | no | no | NONE | |
| F27 | `sum(x)` of rate, increase or count results | `sum(c)` / `rate(sum(c), 1 second)` | yes | yes | EXACT | |
| F28 | `sum(g)` of a gauge | `sum(g)` | yes | yes | APPROXIMATE | Over-counts by the number of points per series in the bucket. Set the bucket near the scrape interval, or use a latest-per-series subquery (form UNV) |
| F29 | `avg(g)` | `average(g)` | yes | yes | EXACT | Equal weights per data point; equal to the mean of series only if series have equal point counts |
| F30 | `min(g)`, `max(g)` | `min(g)`, `max(g)` | yes | yes | EXACT | |
| F31 | `count(x)` (number of series) | `uniqueCount(<identifying attribute>)` | yes | yes | APPROXIMATE | `count(m)` counts points. `uniqueCount` is exact to 256 values and approximate above (DOCS N1) |
| F32 | `stddev(x)`, `stdvar(x)` across series | none | no | no | NONE | NRQL `stddev` runs over points, not series |
| F33 | `quantile(q, x)` across series | `percentile(g, q*100)` | yes | yes | APPROXIMATE | Mixes time and series |
| F34 | `topk(n, x)` | `... FACET l LIMIT n` | yes | no (`LIMIT` ignored) | APPROXIMATE | Ranks over the whole window, not per step (DOCS N1). Default `LIMIT` 10 |
| F35 | `bottomk(n, x)` | none exact | no | no | NONE | `FACET ... ORDER BY` cannot take `ASC` (DOCS N1); not in the supported list (DOCS N2) |
| F36 | `count_values`, `group`, `limitk`, `limit_ratio` | none | no | no | NONE | |
| F37 | `by (a,b)` | `FACET a, b` | yes | yes | EXACT | At most 5 facet attributes; 20,000 facets in a condition (DOCS N1, N16) |
| F38 | `without (a)` | `FACET <all other labels>` from `keyset()` | yes | yes | APPROXIMATE | Needs the label set of the metric (live data) |
| F39 | `histogram_quantile(q, sum by (le,..) (rate(x_bucket[w])))` | `SELECT bucketPercentile(x_bucket, q*100) ... FACET ..` | yes | UNV | APPROXIMATE | DOCS N1 calls it the equivalent; it "reports percentiles along the bucket's boundaries". Not in the condition function list (DOCS N16) |
| F40 | `histogram_quantile` on a native histogram | none known | no | no | NONE | UNV how native histograms arrive |
| F41 | `histogram_count`, `histogram_sum`, `histogram_avg` (classic) | `sum(x_count)`, `sum(x_sum)`, ratio of the two | yes | UNV | EXACT | Classic only |
| F42 | `histogram_fraction(0, t, rate(x_bucket))` | `filter(sum(x_bucket), WHERE le = 't') / filter(sum(x_bucket), WHERE le = '+Inf')` | yes | UNV | APPROXIMATE | `le` is a string attribute; format of `+Inf` and `0.5` must be checked with `uniques(le)` |
| F43 | `label_replace` | none | no | no | NONE | `capture()` in FACET derives a value but is a rewrite (DOCS N1) |
| F44 | `label_join` | `concat()` in `FACET` | yes | UNV | APPROXIMATE | Listed supported in DOCS N2; the NRQL form is UNV |
| F45 | `absent(v)`, `absent_over_time` | dashboard: `count(*)` returns NULL when empty; alert: loss of signal (expiration) | NULL | expiration | APPROXIMATE | NRQL cannot return 1 for "nothing" (DOCS N16: `count()` returns NULL on empty windows). Do not wrap a heartbeat in `filter()` |
| F46 | `clamp`, `clamp_min`, `clamp_max` | `clamp_min(x, n)`, `clamp_max(x, n)`; `clamp` is the pair | yes | UNV | EXACT | DOCS N1 |
| F47 | `abs ceil floor sqrt exp ln` | same names | yes | UNV | EXACT | DOCS N1 |
| F48 | `round(v, n)`, `log2`, `log10` | `round(x, n)`, `log(x, 2)`, `log10(x)` | yes | UNV | EXACT | Tie rounding of `round` not stated |
| F49 | `sgn`, trigonometric, `deg`, `rad`, `pi` | none | no | no | NONE | |
| F50 | `time()` | `aggregationendtime()` | yes | no | APPROXIMATE | It is the end of the bucket, not the evaluation instant (DOCS N1) |
| F51 | `timestamp(v)` | none documented | no | no | NONE | |
| F52 | `hour()`, `minute()`, `month()`, `year()`, `day_of_week()`, `day_of_month()`, `days_in_month()` | `hourOf()`, `minuteOf()`, `weekOf()` and the related table | yes | no | APPROXIMATE | Only some names confirmed (DOCS N1 shows `minuteOf`, `hourOf`, `weekOf`) |
| F53 | `vector(s)` | none | no | no | NONE | A constant has no series. Typical use is `or vector(0)`; see rule 40 |
| F54 | `scalar(v)` | arithmetic inside one `SELECT` | yes | UNV | APPROXIMATE | Only if both sides share the facets |
| F55 | `sort`, `sort_desc` | table ordering; default facet order | table | no | APPROXIMATE | Instant table only |
| F56 | `sort_by_label`, `info()` | none | no | no | NONE | `info()` joins `target_info` |
| F57 | `+ - * /` with a scalar | NRQL arithmetic on aggregates; `^` is `pow()`, `%` is `mod()` | yes | UNV | EXACT | Division by zero gives null, not Inf or NaN (DOCS N1; the earlier note says the same) |
| F58 | arithmetic between two vectors with equal label sets | `latest(a) / latest(b) FACET labels` | yes | UNV | EXACT | The condition reference lists several aggregation functions in one query as unusable |
| F59 | comparison filter `> < == != >= <=` | alert: the threshold operator; dashboard: no series filter | no | yes | APPROXIMATE | NRQL has no post-aggregation filter |
| F60 | comparison with `bool` | `if(cond, 1, 0)` (function exists) | yes | UNV | APPROXIMATE | `if` listed in DOCS N1; use inside a SELECT is UNV |
| F61 | `on(...)`, `ignoring(...)` | none | no | no | NONE | Only implicit matching on facet attributes |
| F62 | `group_left`, `group_right` | none | no | no | NONE | A label that exists on another metric must already be on the series |
| F63 | `and`, `or`, `unless` | none | no | no | NONE | Listed supported in DOCS N2; the NRQL form is not shown |
| F64 | unary minus | `-x` | yes | UNV | EXACT | |

Totals (computed from the table rows F01 to F64): EXACT 18, APPROXIMATE 27, NONE 19 (64 rows).

## 4. New Relic's own PromQL support, and what it implies

What exists (all DOCS):

- A PromQL-style query mode in the query builder that translates PromQL "into the closest NRQL approximation"; "a handful of edge cases aren't fully supported", "over 99.5% of queries across the 7.8 million top Grafana dashboard downloads" (DOCS N2). The query builder page in the current docs tree only keeps a redirect for the old PromQL mode page (DOCS: `query-your-data/explore-query-data/query-builder/introduction-query-builder.mdx` redirects list), so whether the mode is still offered in the UI is UNVERIFIED.
- A Prometheus-compatible query API (`prometheus-api.newrelic.com`, EU and JP hosts, header `X-Query-Key`, optional `X-Prometheus-Server`) so Grafana can use New Relic as a Prometheus data source; 366 steps maximum, 100 series by default, instant queries inspect one minute (DOCS N2, N14; hosts and headers from the datapath note D10).
- A REST API: `POST https://promql-gateway.service.newrelic.com/api/v1/translate/dashboard/grafana` (EU and JP hosts exist), user API key in `api-key`, body `{"accountId": int (optional), "dashboard": <Grafana dashboard JSON>}`, returns `{"dashboard": {name, permissions, pages: [...]}}` (DOCS N4). It uses the same translation logic.
- Supported list (DOCS N2): aggregations `avg count min max quantile stddev stdvar sum topk`, `histogram_quantile`, `avg count last min max quantile stdev stvar sum` `_over_time` (the page spells two of them wrongly), arithmetic operators `+ - * / % ^`, `and or unless`, date and time functions, math functions `abs ceil clamp_max clamp_min exp floor ln log10 log2 round sqrt`, rate-like `delta deriv idelta increase irate rate`, `predict_linear`, instant and range selectors, uniform `offset`, `sort sort_desc`, `label_join`, and Grafana `label_values()`.
- Not listed (so UNVERIFIED whether they work): `bottomk`, `group`, `count_values`, `clamp`, `absent`, `changes`, `resets`, `label_replace`, `scalar`, `vector`, comparison operators and `bool`, `on`/`ignoring`/`group_left`, the `@` modifier, subqueries.
- Known behaviors: because "NRDB converts PromQL-style accumulating counters to delta counters", applying functions to the wrong metric type "will produce different or incorrect answers" (DOCS N2); `topk` differs from Prometheus; the query range must exceed the step else "`TIMESERIES` bucket size is larger than the current time window" (DOCS N2).

Implications:

1. No page read says that a native dashboard widget or an alert condition can hold PromQL. Alert conditions are NRQL (DOCS N16). So "keep PromQL as is" is only possible by leaving the dashboards in Grafana and pointing a Grafana Prometheus data source at New Relic (DOCS N14). That keeps Grafana, so it is not a migration to New Relic dashboards.
2. For native dashboards the importer should emit NRQL. The translator in DOCS N4 is a useful comparison oracle (feed it fixtures, diff its NRQL against ours) but it calls a New Relic service with a user API key, so it needs the owner's approval for a sandbox run. Whether its output widgets contain NRQL or PromQL is not documented: UNVERIFIED.
3. The 99.5 percent figure measures translation into some NRQL, not equality of values (DOCS N2 says "approximation"). The importer should still label results APPROXIMATE.
4. Translator limits apply to the importer too: 366 steps, 100 series, one-minute instant lookback.

## 5. LogQL to NRQL

Source semantics from L1. New Relic semantics from N1, N10, N12.

| # | LogQL | NRQL | Dash | Alert | Verdict | Notes |
|---|---|---|---|---|---|---|
| Q01 | `{k="v"}` stream selector | `FROM Log WHERE k = 'v'` | yes | yes | EXACT | Attribute names must be mapped (section 6). A selector needs at least one non-empty matcher in Loki; NRQL has no such rule |
| Q02 | `{k=~"re"}`, `!~` | `RLIKE`, `NOT RLIKE`, `IN` | yes | yes | EXACT | Both anchored (DOCS L1 says label matchers are fully anchored; N1 says RLIKE is full match) |
| Q03 | `\|= "text"` | `message LIKE '%text%'` | yes | yes | APPROXIMATE | LogQL is case-sensitive (DOCS L1); `LIKE` is case-insensitive (DOCS N1). A literal `%` cannot be escaped (CODE nrql.py:104) |
| Q04 | `!= "text"` | `message NOT LIKE '%text%'` | yes | yes | APPROXIMATE | Same |
| Q05 | `\|~ "re"`, `!~` | `message RLIKE '(?s).*re.*'` | yes | yes | APPROXIMATE | `\|~` is unanchored and `.` matches newlines (DOCS L1); RLIKE is anchored so wrap with `.*` and use `(?s)`; RE2 on both sides but the `(?s)` effect in NRQL is UNV |
| Q06 | `\| json` | JSON in `message` is parsed into attributes at ingest (DOCS N12 Log API) | yes | yes | APPROXIMATE | Only when the shipper sends the line as `message` and the account parses it; nested names may be flattened with dots (UNV); `jparse()` exists for query time (DOCS N1) |
| Q07 | `\| logfmt` | ingest-time Grok/regex rule, or `aparse()`/`capture()` | partly | no | APPROXIMATE | No native logfmt parser documented |
| Q08 | `\| pattern "<ip> - <_> [<ts>]"` | `aparse(message, '%...*...%')` | yes | UNV | APPROXIMATE | `aparse` anchors with `*` captures and `%` skips; case-insensitive; results are strings, use `numeric()` (DOCS N1) |
| Q09 | `\| regexp "(?P<x>re)"` | `capture(message, r'...(?P<x>re)...')` | yes | UNV | APPROXIMATE | RE2 named groups; must match the whole input; 16 groups per capture and 5 captures per query (DOCS N1) |
| Q10 | `\| line_format`, `\| label_format`, `\| unpack`, `\| drop`, `\| keep` | `WITH <expr> AS name`, `concat()`; others none | partly | no | NONE | `line_format` changes display only; `unpack` is for Promtail-packed JSON (UNV) |
| Q11 | label filter after a parser: `\| status >= 500`, `\| duration > 10s`, `\| level="error"` | `WHERE status >= 500` on a parsed or JSON attribute | yes | yes | APPROXIMATE | Unit literals (`10s`, `1MB`) must be converted by hand; `__error__` has no equal (DOCS L1) |
| Q12 | `count_over_time(sel [5m])` | `SELECT count(*) FROM Log WHERE ...` over a 5 minute window | yes | yes | EXACT | CODE translate.py:495 converts this |
| Q13 | `rate(sel [5m])` | `SELECT rate(count(*), 1 second) FROM Log WHERE ...` | yes | UNV | APPROXIMATE | |
| Q14 | `bytes_over_time`, `bytes_rate` | `sum(length(message))` | yes | no | APPROXIMATE | Characters, not bytes. `bytecountestimate()` is named in the condition page as unsupported in conditions (DOCS N16) |
| Q15 | `sum by (l) (...)`, `avg`, `min`, `max`, `count` | `FACET l` with the matching aggregator | yes | yes | EXACT | |
| Q16 | `topk(n, ...)` | `FACET l LIMIT n` | yes | no | APPROXIMATE | |
| Q17 | `\| unwrap f` with `sum_over_time`, `avg_over_time`, `min_over_time`, `max_over_time` | `sum(f)`, `average(f)`, `min(f)`, `max(f)` | yes | yes | EXACT | `f` must be numeric; Loki adds `__error__` on conversion failure, NRQL ignores non-numeric rows |
| Q18 | `quantile_over_time(q, ... \| unwrap f [w])` | `percentile(f, q*100)` | yes | yes | APPROXIMATE | |
| Q19 | `rate(... \| unwrap f)`, `rate_counter` | `rate(sum(f), 1 second)` | yes | UNV | APPROXIMATE | |
| Q20 | `first_over_time`, `last_over_time` | `earliest(f)`, `latest(f)` | yes | yes (`latest`) | APPROXIMATE | |
| Q21 | `stddev_over_time`, `stdvar_over_time` | `stddev(f)`, `stdvar(f)` | yes | UNV | APPROXIMATE | |
| Q22 | `absent_over_time` | loss of signal | no | expiration | APPROXIMATE | |
| Q23 | `offset` | `COMPARE WITH` | yes | no | APPROXIMATE | |
| Q24 | `sum(count_over_time(...)) or vector(0)` | `SELECT filter(count(*), WHERE <filters>) FROM Log WHERE <entity filter>` | yes | yes | APPROXIMATE | Returns 0 only when the entity reports; with no event at all the result is NULL (DOCS N16). CODE nrql.py:150-175 `null_safe` does this |

Totals (rows Q01 to Q24): EXACT 5, APPROXIMATE 18, NONE 1 (24 rows).

Attribute names. Loki labels are index labels; everything else is structured metadata. An OTLP resource attribute such as `service.name` becomes the label `service_name` (dots to underscores) (DOCS L1 shared/otel.md). Loki fills `service_name` from the first non-empty of `service`, `app`, `application`, `app_name`, `name`, `app_kubernetes_io_name`, `container`, `container_name`, `k8s_container_name`, `component`, `workload`, `job`, `k8s_job_name`, else `unknown_service` (DOCS L1). Loki's default list of resource attributes kept as index labels has 18 entries (`cloud.region`, `k8s.namespace.name`, `k8s.pod.name`, `service.name`, `service.namespace`, ...) and Loki's default index-label limit is 15 (DOCS L1).

New Relic side:

| Concept | Loki / Alloy | New Relic `Log` |
|---|---|---|
| Line | the entry body | `message`; JSON strings are parsed into attributes (DOCS N12, N10) |
| Time | entry timestamp | `timestamp`; defaults to ingest time if missing; older than 48 h may be dropped (DOCS N12) |
| Severity | `severity_text` structured metadata for OTLP, or an extracted label (DOCS L1) | OTLP: `severity.text`, `severity.number` (DOCS N10); plain logs: whatever the shipper sets, often `level` (UNV) |
| Trace link | `trace_id`, `span_id` structured metadata (DOCS L1) | `trace.id`, `span.id` (DOCS N10) |
| Service | `service_name` | `service.name` as a resource attribute, entity synthesis, `entity.guid`/`entity.name` appear when the entity is linked (UNV exact names); logs link to a service when the required resource attributes are present (DOCS N10 logs) |
| Kubernetes | `namespace`, `pod`, `container`, `app`, `job`, `cluster` from the Alloy/Promtail relabeling | New Relic Fluent Bit plugin config keeps `cluster_name`, `container_name`, `namespace_name`, `pod_name` (DOCS: `newrelic/newrelic-fluent-bit-output` README config); OTLP route: `k8s.cluster.name`, `k8s.namespace.name`, `k8s.pod.name`, `k8s.container.name` (DOCS N10 resources) |
| Labels from Alloy via OTLP | `otelcol.receiver.loki` turns labels into log attributes | whether a label named `service_name` stays that way is UNVERIFIED (also noted in the datapath file 6.6) |
| Limits | 15 index labels, 64 KB and 128 structured metadata entries per line, 256 KB line (DOCS L1) | 255 attributes per event, attribute name 255, value 4094 characters kept (longer is a blob), payload 1 MB, parsing rule 100 ms per message and a per-account budget, rules unordered (DOCS N12) |
| Parsing | query time (`| json` etc.) | query time `aparse()`, `capture()`, `WITH ... AS`; ingest time Grok and regex rules, plus built-in rules chosen by a `logtype` attribute (DOCS N12, N1) |

## 6. TraceQL and Tempo against Span, Transaction and distributed tracing

| Topic | Tempo / TraceQL (DOCS T1) | New Relic | Notes |
|---|---|---|---|
| Span record | span with intrinsics and attributes | `Span` event; OTLP mapping: `trace_id` to `trace.id`, `span_id` to `id`, `parent_span_id` to `parent.id`, `name` to `name`, `kind` to `span.kind`, start to `timestamp`, duration to `duration.ms`, status code to `otel.status_code`, status message to `otel.status_description`, scope to `otel.library.name`, tracestate to `w3c.tracestate`; span events become `SpanEvent` (DOCS N10 traces) | New Relic agent spans use `duration` in seconds and categories such as `datastore`, `http` (DOCS N13): two shapes in one event type |
| Attribute scopes | `span.http.method`, `resource.service.name`, `event.`, `link.`, `instrumentation:` | All of them flattened into one attribute space; resource, scope and span attributes merge, top-level fields win (DOCS N10) | Name clashes between resource and span attributes are resolved silently |
| Status | `span:status = error` | `otel.status_code = 'ERROR'`; for OTel services errors are spans with status ERROR, and a span exception event alone does not count (DOCS N11) | |
| Duration | `span:duration > 100ms`; `trace:duration` | `duration.ms > 100`; trace duration needs aggregation by `trace.id` (inference) | The unscoped TraceQL `duration` is the span duration (DOCS T1) |
| Name, kind | `span:name`, `span:kind = server` | `name`, `span.kind` | |
| Trace-level | `trace:rootName`, `trace:rootService` | none documented as columns | Filter on spans where `parent.id IS NULL` (inference, UNV) |
| Structural `>>`, `>`, `<`, `~`, `<<` | descendant, child, parent, sibling, ancestor, right side returned (DOCS T1) | No NRQL operator. A join over `trace.id` is possible in principle with `JOIN` (DOCS N1 describes JOIN with a subquery) but parent/child depth cannot be expressed | NONE |
| Aggregators | `count()`, `avg(duration)`, `by()` | `count(*)`, `average(duration.ms)`, `FACET` | |
| TraceQL metrics | `{...} \| rate()`, `count_over_time`, `sum/min/max/avg/quantile/histogram_over_time`, `topk`, `bottomk`, `compare`, arithmetic between queries (DOCS T1) | `SELECT rate(count(*), 1 second) FROM Span WHERE ...`, `percentile(duration.ms, ...)`, `histogram(duration.ms)` | `compare` has no equal. Sampling: New Relic traces are head or tail sampled (DOCS: `distributed-tracing/concepts/how-new-relic-distributed-tracing-works.mdx`) so span rates are not request rates unless `EXTRAPOLATE` applies (DOCS N1; scope of that clause is APM agent event sampling) |
| RED metrics | Tempo metrics-generator writes `traces_spanmetrics_latency` (histogram), `traces_spanmetrics_calls_total`, `traces_spanmetrics_size_total`, and `traces_service_graph_request_total`, `_failed_total`, `_server_seconds`, `_client_seconds` with labels `client`, `server`, `connection_type` to a Prometheus backend (DOCS T1) | For OTel services New Relic derives `apm.service.transaction.duration`, `apm.service.error.count` etc. from OTel HTTP/RPC metrics, with spans as fallback; messaging transactions come from spans (DOCS N11) | Dashboards on `traces_spanmetrics_*` need those series sent over remote write, or must be rebuilt on `Span` or on `apm.service.*`. A `Transaction` event does not exist for OTel services (DOCS N11 says OTel has no direct analog; the APM UI is metric driven) |
| Exemplars and links | exemplars link metrics to traces | `trace.id`, `span.id` on `Log`; OTLP metric exemplars unsupported (DOCS N10) | Logs to traces works; metric exemplars do not |
| Span links | `link:spanID` | supported (DOCS N10 traces) | |

## 7. Other products: analogues and migration notes

| Grafana product | New Relic analogue | Evidence and migration note |
|---|---|---|
| Pyroscope (continuous profiling; 2.0 writes straight to object storage; SDK push, Alloy, OTLP) (DOCS G1) | None found in the docs tree. The tree has a thread profiler for APM (`apm/apm-ui-pages/events/thread-profiler-tool.mdx`) which is a different feature | Continuous profiles do not migrate. Whether New Relic ingests OTLP profiles is UNVERIFIED. Report as "no analogue found" |
| Faro (browser SDK that sends to Alloy, then Loki and Tempo) (DOCS G1) | Browser agent (`PageView`, `BrowserInteraction`, `JavaScriptError` events, session replay, browser logs) (DOCS: `browser/browser-monitoring/...`; event names UNV) | Different SDK and data model; re-instrument the front end. Faro dashboards use Loki and Tempo queries over Faro-specific labels (UNV) so they do not convert |
| k6 (load testing; outputs list includes New Relic, Prometheus remote write, OpenTelemetry; trend metrics become `k6_*_p99` style gauges by default or native histograms) (DOCS G1) | Synthetic monitors (scripted API/browser) for scheduled checks; k6 can keep running and send results to New Relic through its own outputs (DOCS G1 lists New Relic) | Load tests are not monitors. No k6 page exists in the New Relic docs tree read (UNVERIFIED that none exists elsewhere). Percentile gauges (`..._p99`) cannot be re-aggregated |
| Alloy (OpenTelemetry Collector distribution with Prometheus pipelines, supports metrics, logs, traces, profiles) (DOCS G1) | Infrastructure agent, OpenTelemetry Collector, New Relic Prometheus agent (a Prometheus in agent mode with New Relic defaults) (DOCS N15) | Alloy can dual write; the datapath note section 6.3 has a config. Alloy components that translate (OTLP to Prometheus) change names (section 8) |
| Grafana Agent (flow/static; end of life, replaced by Alloy) | same as Alloy | The end of life claim is from memory: UNVERIFIED. Configs are River or YAML; not parsed by the repo |
| Beyla (eBPF auto-instrumentation; produces OTel spans and RED metrics; donated to OpenTelemetry as OBI) (DOCS G1) | New Relic eBPF agent (docs present, large parts commented out in the source file read) and Pixie auto-telemetry (DOCS N15) | Both give zero-code HTTP/gRPC telemetry; names and attributes differ (OTel `http.server.request.duration` against Pixie data). GA status of the eBPF agent is UNVERIFIED |
| Grafana Cloud | Managed Mimir, Loki, Tempo, Pyroscope behind one stack; Loki `discover_service_name` configurable only by support (DOCS L1) | Nothing in New Relic reads from Grafana Cloud; data must be sent (datapath note section 6) |

## 8. Metric and label naming consequences, checks, and a name map

How names look in NRDB, by pipeline.

| Pipeline | Metric name | Labels / attributes | Type | Evidence |
|---|---|---|---|---|
| Prometheus remote write (or Alloy `prometheus.remote_write`) | Unchanged Prometheus name, including `_total`, `_bucket` and colons | Prometheus labels unchanged, plus `prometheus_server`, `newrelic.source`, `instrumentation.provider`, `instrumentation.name` | by suffix; override with `newrelic_metric_type` | DOCS N3 examples, N5, N6 |
| New Relic Prometheus agent | Unchanged | `prometheus_server`, `instance`, `job`, `cluster_name`, `namespace`, `pod`, `node`, `service`, plus `up` and `scrape_*` series | as remote write | DOCS N6 |
| Older OpenMetrics integration (`nri-prometheus`) | Unchanged | `clusterName`, `integrationName`, `nrMetricType`, `promMetricType`, `scrapedEndpoint`, `podName`, `namespaceName`, `nodeName`, `deploymentName`, `label.*` (camelCase) | `nrMetricType` attribute | DOCS (`view-query-your-prometheus-data.mdx`) |
| OTLP, OTel native instruments | OTel name with dots (`http.server.request.duration`) | Resource, scope, point attributes with dots; `unit`, `description` | by instrument | DOCS N10 |
| OTLP from a Prometheus scraper in a Collector or Alloy `otelcol.receiver.prometheus` | Depends on the receiver and the exporter; not read | `job` to `service.name` and `instance` to `service.instance.id` is the OTel specification mapping (DOCS P3) | cumulative Sum becomes `cumulativeCount`; Summary not supported (DOCS N10) | name and label handling in Alloy UNVERIFIED |
| Prometheus receiving OTLP and then writing to New Relic | `UnderscoreEscapingWithSuffixes` default adds `_total` and unit suffixes (DOCS P2) | `service.name` to `job`, `instance` from `service.instance.id`; other resource attributes only in `target_info` | | DOCS P2 |
| Loki to New Relic Logs through OTLP | n/a | Loki replaces dots by underscores when storing (DOCS L1) | | |

Checks (all read-only NRQL; the datapath note section 6.8 has the wording for the importer):

- `SELECT uniques(metricName) FROM Metric WHERE metricName LIKE 'node_%' SINCE 1 hour ago LIMIT MAX` (the form `SELECT uniques(metricName)` is DOCS N3, N9).
- `SELECT keyset() FROM Metric WHERE metricName = '<name>' SINCE 1 hour ago` (DOCS N3).
- `SELECT uniques(prometheus_server) FROM Metric WHERE metricName = '<name>'`.
- `SELECT uniques(le) FROM Metric WHERE metricName = '<name>_bucket'` before F42.
- Type check: `SELECT uniques(metricName) FROM Metric WHERE getField(%, type) = 'gauge'` or the shorthand `%[type]` (DOCS N9) listing gauges, to find counters stored as gauges.
- `SHOW EVENT TYPES SINCE 1 day ago` to see which of `Metric`, `Log`, `Span` exist (statement name from memory: UNVERIFIED in the pages read).
- Logs: `SELECT keyset() FROM Log SINCE 1 hour ago`; Spans: `SELECT keyset() FROM Span SINCE 1 hour ago`.

Recommended `name map` schema (a file the importer reads and a check command fills; nothing guessed):

```yaml
version: 1
estate:
  prometheus_server: "prod-eu"          # value of the remote write URL parameter, or null
  metrics_pipeline: remote_write        # remote_write | prometheus_agent | otlp_direct | otlp_via_collector | unknown
  logs_pipeline: fluent_bit             # fluent_bit | otlp | log_api | unknown
metrics:
  - source: node_cpu_seconds_total
    nr_name: node_cpu_seconds_total
    nr_type: count                      # count | gauge | summary | distribution | cumulativeCount | unknown
    type_origin: suffix                 # suffix | override | measured | assumed
    labels:
      mode: { nr_attribute: mode, seen: true }
      cpu:  { nr_attribute: cpu,  seen: true }
      job:  { nr_attribute: job,  seen: true }
    evidence: { checked_at: "2026-11-09T00:00:00Z", keyset: true, uniques: true }
logs:
  message: message
  labels:
    namespace: namespace_name
    pod: pod_name
    container: container_name
    service_name: service.name
  evidence: { checked_at: null, keyset: false }
```

Rules for the file: every entry has `evidence`; a converter treats `unknown` or `seen: false` as manual; `type_origin: assumed` blocks any `rate()` rule; one `prometheus_server` per file, else the query filter is mandatory.

## 9. Derived rules for the importer

Source of the idioms: Node Exporter Full (id 1860) 291 distinct expressions, of which 131 use `$__rate_interval`; the kubernetes-mixin and node-mixin alert and rule expressions; the LogQL forms from L1 (CODE: the repo's own corpus count is in `docs/research/grafana-dashboards.md` section 8). "Repo" says whether translate.py (alerts) or query_tier.py (dashboards, classification only) handles it: yes, partly, no. Every NRQL below is proposed and untested; exactness uses section 3 definitions.

| # | Idiom (example) | Precondition | Proposed NRQL | Exactness | Repo |
|---|---|---|---|---|---|
| 1 | Gauge selector, `node_load1{instance="$node",job="$job"}` | name map says gauge; labels seen | `SELECT latest(node_load1) FROM Metric WHERE instance = '..' AND job = '..' TIMESERIES AUTO` | EXACT | partly: alerts convert it with an average reducer (translate.py:426-480); dashboards classify tier 1 only |
| 2 | Matchers `=`, `!=`, `mode!="idle"`, `image!=""` | labels seen; NULL handling tested | `WHERE mode != 'idle'`, `image != ''` | EXACT after the NULL test | yes (negation noted, translate.py `N_NEGATION`) |
| 3 | Regex matchers `fstype!~'rootfs\|tmpfs'`, `mode=~".*irq"`, `code=~"5.."` | none | `NOT IN`, `RLIKE '.*irq'`, `RLIKE '5..'`; `LIKE` only for case-free values | EXACT with RLIKE | partly: plain words, prefix, suffix only; other regex manual (query_tier.py `plain_regex`) |
| 4 | `avg\|min\|max by (l) (gauge)` | gauge | `SELECT average\|min\|max(g) FACET l` | EXACT | yes (alerts, `EXACT_AGGREGATES`) |
| 5 | `sum by (l) (gauge)`, `kube_pod_container_resource_requests` | gauge; one point per series per bucket, i.e. bucket near the scrape interval | `SELECT sum(g) FACET l TIMESERIES <scrape interval>` | APPROXIMATE | no: alerts refuse `sum` with a reducer (`R_AGG_MIX`) |
| 6 | `rate(c[$__rate_interval])` | `nr_type: count` verified | `SELECT rate(sum(c), 1 second) FACET .. TIMESERIES AUTO` | APPROXIMATE | no: translate.py refuses `rate` and counter suffixes; dashboards classify tier 2 and emit nothing |
| 7 | `sum by (l) (rate(c[w]))` | as 6 | `SELECT rate(sum(c), 1 second) FACET l` | APPROXIMATE | no (same) |
| 8 | `increase(c[w])` | as 6 | `SELECT sum(c) TIMESERIES w` | APPROXIMATE | no |
| 9 | `irate(c[w])` | as 6; bucket equal to the scrape interval | `rate(sum(c), 1 second)` per bucket | APPROXIMATE | no |
| 10 | CPU use `100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[w])))` | cpu counter verified count; `cpu` attribute seen | `SELECT 100 * (1 - rate(sum(node_cpu_seconds_total), 1 second) / uniqueCount(cpu)) WHERE mode = 'idle' ...` (per host `FACET instance`) | APPROXIMATE (the form with `uniqueCount` and `rate` in one SELECT is UNV) | no |
| 11 | Ratio of gauges `node_filesystem_avail_bytes / node_filesystem_size_bytes * 100` | same facet attributes on both | `SELECT latest(a) / latest(b) * 100 FACET mountpoint, device` | EXACT | partly: tier 3 classified, not emitted |
| 12 | Scalar arithmetic `* 8`, `* 1024`, `- 1`, `/ 1024`, `scalar(a) * 100 / count(count(x) by (cpu))` | operands share facets | NRQL arithmetic inside one SELECT | EXACT for constants; APPROXIMATE with `scalar()` | partly (constants are tier 3) |
| 13 | Division guards `a / (b > 0)`, `(b > bool 0) * (...)` | none | plain `a / b` (division by zero is null) | APPROXIMATE | no: comparison refused |
| 14 | `count(count(node_cpu_seconds_total) by (cpu))` (CPU count) | `cpu` seen | `SELECT uniqueCount(cpu) FROM Metric WHERE metricName = 'node_cpu_seconds_total'` | EXACT up to 256 values | partly: `count` classified tier 3 |
| 15 | `sum without (mode) (rate(...))`, `sum without (device)` | label set known from `keyset()` | `FACET <remaining labels>` | APPROXIMATE | no: `without` refused |
| 16 | `clamp_min(expr, 0)` | none | `clamp_min(expr, 0)` | EXACT | no |
| 17 | `histogram_quantile(0.99, sum by (le,..) (rate(x_bucket[w])))` | `x_bucket` typed count | `SELECT bucketPercentile(x_bucket, 99) FACET ..` | APPROXIMATE | partly: tier 2 classified for the plain shape |
| 18 | `histogram_quantile` with `* on(instance) group_left(node) max by (..) (kubelet_node_name)` | | drop the join; facet by `instance` | NONE as written | no |
| 19 | `topk(10, expr)` | numeric `n` | add `LIMIT 10` | APPROXIMATE | partly: tier 2 classified |
| 20 | Error ratio `sum(rate(rest_client_requests_total{code=~"5.."}[5m])) / sum(rate(rest_client_requests_total[5m])) > 0.01`; throttling and packet-error ratios | counter typed count | `SELECT filter(sum(c), WHERE code RLIKE '5..') / sum(c)` (the `rate` cancels) | APPROXIMATE; alert use needs a sandbox test of two aggregations in one condition (UNV) | no |
| 21 | `max_over_time(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff"}[5m]) >= 1`; `min_over_time(x[5m]) == 0`; `avg_over_time` | gauge | `SELECT max(x) WHERE reason = 'CrashLoopBackOff' FACET namespace, pod, container`, threshold at or above 1 over 5 minutes | EXACT | no: `_over_time` refused |
| 22 | Two-metric comparison `kube_deployment_spec_replicas > kube_deployment_status_replicas_available`; `observed_generation != metadata_generation` | same facet labels | `SELECT latest(a) - latest(b) FACET namespace, deployment`, threshold above 0 (or not equal 0) | APPROXIMATE | no |
| 23 | `predict_linear(node_filesystem_avail_bytes[6h], 24*3600) < 0 and avail/size*100 < 15` | | two conditions, or the "predict future behavior" option; the `and` has no form | NONE as one expression | no |
| 24 | `deriv(node_timex_offset_seconds[5m]) >= 0` | | `derivative(x, 1 second)` | APPROXIMATE | no |
| 25 | `absent(up{job="x"})`, `absent_over_time` | | loss-of-signal setting on a count/gauge condition; no `filter()` wrapper | APPROXIMATE | no: refused |
| 26 | Joins and sets `a * on(ns,pod) group_left(node) topk(1, max by(..) (kube_pod_info{node!=""}))`, `... and on(..) ...`, `unless on(..)` | | none; require the label on the series (Prometheus agent adds `node`, `namespace`, `pod`, DOCS N6) | NONE | no: refused |
| 27 | Fallback `node_memory_MemAvailable_bytes or (Buffers + Cached + MemFree + Slab)` | | use MemAvailable only, state it | NONE | no |
| 28 | `label_replace(kubernetes_build_info, "git_version", "$1", "git_version", "(v[0-9]*.[0-9]*).*")` | | none; `capture()` in FACET as a hand rewrite | NONE | no |
| 29 | `time() - max by (..) (kube_job_status_start_time ...)` | | none | NONE | no |
| 30 | Recording rule names `node:node_cpu_utilization:ratio_rate5m`, `instance:node_num_cpu:sum`, `apiserver_request:burnrate5m` | rule output verified in NRDB, else inline the rule | backticked name in `latest()`; else expand the rule | EXACT if series arrives | partly: colon names pass the name check; recording rules are manual (`R_RECORD`) |
| 31 | `offset 1d` | uniform | `COMPARE WITH 1 day ago` | APPROXIMATE | no: refused |
| 32 | Variables `$job`, `$node`, `$__rate_interval`, `$__range`; `label_values(node_uname_info, job)` | | NRQL variable `SELECT uniques(job) FROM Metric WHERE metricName = 'node_uname_info'`; built-in intervals map to the `TIMESERIES` bucket | EXACT | partly: planned in dashboards (`remediation_rules.py:298`), not emitted |
| 33 | `count_over_time({k="v"} \|= "text" [5m])` | log attribute `k` seen | `SELECT count(*) FROM Log WHERE k = 'v' AND message LIKE '%text%'` | EXACT except case | yes (translate.py:495-540) |
| 34 | `sum by (l) (count_over_time(...))` | | add `FACET l` | EXACT | yes |
| 35 | `rate({..} \|= "x" [1m])` | | `SELECT rate(count(*), 1 second) FROM Log ..` | APPROXIMATE | partly: tier 2 classified for dashboards; alerts refuse `rate` |
| 36 | Regex line filters `\|~ "err\|fail"`, `!~` | | `message RLIKE '(?s).*(err\|fail).*'` | APPROXIMATE | partly: plain-word regex only (`LOKI_REGEX`) |
| 37 | Parsers with label filters `\| json \| status >= 500`, `\| logfmt`, `\| pattern`, `\| regexp` | the log line is JSON in `message`, or an ingest rule exists | `WHERE status >= 500`; `aparse`/`capture` otherwise | APPROXIMATE | no: `LOKI_PARSER` refuses |
| 38 | `unwrap` aggregates, `quantile_over_time(0.95, {..} \| json \| unwrap dur [5m])` | numeric attribute | `percentile(dur, 95)`, `average(dur)` | APPROXIMATE for quantiles | no |
| 39 | `bytes_rate`, `bytes_over_time` | | `sum(length(message))` | APPROXIMATE | no |
| 40 | `sum(count_over_time(..)) or vector(0)`, alert on zero logs | entity filter identified | `SELECT filter(count(*), WHERE message LIKE '%x%') FROM Log WHERE <entity attr> = '..'` | APPROXIMATE | partly: `null_safe` exists in the emitter (nrql.py:150-175) and the empty-window note exists (`N_COUNT_EMPTY`); the `vector(0)` text itself is refused |

Counts over the 40 rules: repo status yes 4 (rules 2, 4, 33, 34), partly 12, no 24; exactness EXACT 13, APPROXIMATE 21, NONE 6.

## 10. Top gotchas

1. Counters without a recognised suffix are stored as gauges (47 of 108 rated names in Node Exporter Full); `rate(sum())` on them silently gives wrong numbers. The repo guard checks only `_total _count _sum _bucket` (CODE translate.py:75).
2. NRQL stored counters are deltas; `sum()` is the increase and `rate()` divides by the bucket. PromQL `rate()` extrapolates and corrects resets itself, so values differ at window edges and across restarts (DOCS N2, N3, P1).
3. `sum(gauge)` and `count(metric)` operate on data points in the bucket, not on one value per series; they inflate when the bucket is longer than the scrape interval.
4. Names: dots against underscores (OTLP against Prometheus against Loki), mixed case (107 distinct mixed-case `node_*` names in Node Exporter Full, for example `node_memory_Active_bytes`), colons in recording-rule names. Check with `keyset()` and `uniques(metricName)` before emitting.
5. `LIKE` is case-insensitive and cannot take a literal `%`; `RLIKE` is full-match. LogQL `|=` is case-sensitive and `|~` is unanchored.
6. Empty windows are NULL, not 0. `count(*)` in an empty window is NULL, `absent()` and `or vector(0)` have no direct form; only a `filter()` bound to an entity returns 0 (DOCS N16).
7. `FACET` defaults to 10 values, `LIMIT` up to 5,000, at most 5 attributes; conditions allow 20,000 facets (DOCS N1, N16). Prometheus charts show every series.
8. Alert conditions forbid `TIMESERIES`, subqueries, `COMPARE WITH`, several aggregations in one query; `rate`, `derivative`, `predictLinear`, `bucketPercentile` are not on the supported list (DOCS N16). Many mixin alerts (ratios, `and`, joins) therefore have no one-condition form.
9. No vector matching: `on`, `ignoring`, `group_left`, `unless` joins such as `kube_pod_info` enrichment have no NRQL; the label must already be on the series.
10. Cardinality and rollups: past the per-account (1 to 15 million) or per-metric-name (100,000) daily series cap, rollups stop and windows over 60 minutes are wrong; scrape interval above 60 s breaks one-minute instant lookups; duplicate HA servers double count unless the dedup labels are set (DOCS N8, N2, N6).

## 11. UNVERIFIED list

- Whether any NRQL in this note runs as written (no instance was called). Especially: `rate(sum()) / uniqueCount()` in one SELECT (rule 10), `filter()` ratios in a condition (rule 20), `earliest()` and `stddev()` on `Metric`, `concat()` and `if()` in `FACET`/`SELECT` on `Metric`, `(?s)` inside `RLIKE`.
- NULL handling of `attr != 'x'` and `attr = ''` against absent attributes.
- Whether `rate()`, `derivative()`, `predictLinear()`, `bucketPercentile()` are accepted in an alert condition.
- How remote write treats stale NaN markers, the first sample of a series, native histograms, exemplars, and the `interval.ms` it assigns; whether remote write data synthesizes entities.
- Whether the PromQL query mode still exists in the New Relic query builder; whether the translated dashboards (DOCS N4) contain NRQL or PromQL widgets.
- Names produced by Alloy `otelcol.receiver.prometheus` and `otelcol.receiver.loki`; whether a `service_name` label survives as that name.
- Names of the date functions beyond `minuteOf`, `hourOf`, `weekOf`; `SHOW EVENT TYPES` as a statement.
- Whether New Relic has a k6 integration page, a continuous profiling product, or OTLP profile ingest; GA status of the eBPF agent; end of life of Grafana Agent.
- New Relic browser event type names for the Faro comparison; the `entity.guid` and `entity.name` attributes on logs; the default severity attribute name (`level`) for non-OTLP logs.
- Trace-level queries (root span, trace duration) as NRQL.
