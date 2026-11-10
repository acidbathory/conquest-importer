# Telemetry rig results: Prometheus remote write into New Relic, and converted queries against real Prometheus-shaped data

Run 2026-11-10 (owner authorised for this task). Scripts: `scripts/live-test/telemetry-rig/` (`fetch.sh`, `start.sh`, `stop.sh`, `exporter.py`, `watchdog.py`, `compare.py`, `report.py`, `prometheus.yml.tmpl`, `rules.yml`). Account and keys come from `.env` and are never printed or saved; the licence key sat only in a mode 0600 file inside the temp directory, which `stop.sh` deletes. Every sample carries `ckg_test="true"` (external label) and `prometheus_server=ckg-local` (or `ckg-local-typed` for the type experiment).

## 1. What was built

| Part | Detail |
|---|---|
| Reference | Prometheus 3.16.0 (official darwin-arm64 release, sha256 checked against `sha256sums.txt` of the same release), bound to 127.0.0.1:9090, scrape and rule interval 15 s |
| Real exporter | node_exporter 1.12.1 (same checks), 127.0.0.1:9100, job `node`; only the families the Node Exporter Full panels read are kept (`metric_relabel_configs`) |
| Synthetic exporter | `exporter.py`, stdlib only, 11 ports on 127.0.0.1 (9101 kube-state-metrics v2, 9111/9112 JVM Micrometer apps `orders` and `payments`, 9121/9122 node_exporter extras with Linux names macOS lacks, 9131/9132 cAdvisor, 9141/9142 postgres_exporter `pg-main` and `pg-replica`, 9151 NGINX ingress controller with two pods, 9161 flaky) plus a port nothing listens on (9199) |
| Remote write | `https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=ckg-local`, `authorization.credentials_file` (the Prometheus 3 form of the bearer token file; the documented form per the repo's data path research, DOCS D6), `external_labels: {ckg_test: "true"}`, drop rules for `go_*`, `promhttp_*`, `scrape_duration_seconds` and two more `scrape_*` series |
| Rules | 3 recording rules (`instance:node_cpu_utilisation:rate5m`, `ingress:nginx_requests:rate2m`, `application:http_server_requests_seconds:p95_5m`) and 3 alert rules evaluated locally, no Alertmanager (`CkgIngress5xxRatioHigh`, `CkgTargetDown`, `CkgPodRestartingOften`) |
| Lifetime | detached processes with pid files and logs in `out/live-run/rig/` (git-ignored); the watchdog stops everything after 90 minutes or above 1.4 M samples, `stop.sh` stops it on demand and deletes the temp dir |

Names, types and labels of the exporter come from the six converted community dashboards in the owner's estate (Node Exporter Full, kube-state-metrics v2, cAdvisor, JVM Micrometer, PostgreSQL, NGINX ingress controller). Dynamics: waves with noise, counters that reset (cAdvisor `worker` container, NGINX pod 2, `pg-replica` stats reset), histograms with cumulative `_bucket`/`_sum`/`_count` (log-normal latencies), info metrics (value 1) for joins, series that appear later (`kube_job_status_failed`) or stay absent (`nginx_ingress_controller_ssl_expire_time_seconds`), `up` for every job including one target that returns 503 five minutes in every fifteen and one that does not exist, several clusters, namespaces, pods, nodes, instances and databases so dashboard variables have choices.

### Cardinality and volume (bounded and documented)

| Job | Series | | Job | Series |
|---|---|---|---|---|
| kube-state-metrics | 823 | | cadvisor | 88 |
| node-extras (2 instances) | 464 | | ingress-nginx | 143 |
| jvm (2 apps) | 242 | | node (real) | 142 |
| postgres (2 instances) | 216 | | flaky, missing | 6, 7 |

2,131 series after drops (head series 2,145, 262 metric names; New Relic saw 273 names, the extra ones are `up`, `ALERTS` and the rule outputs). At 15 s that is about 142 samples per second, so a full 90 minutes would send about 770 k samples, under the 1.5 M cap. The real count is lower because the Mac slept (section 2.9): Prometheus counted 395,723 samples handed to remote storage after 80 minutes (the main queue plus the small type-experiment queue).

## 2. What arrives in New Relic (read-only NRQL)

### 2.1 Arrival and completeness

- Remote write works end to end: no failed sample, `up`, rule outputs and `ALERTS` included.
- Latency: the newest sample was 2.5 to 4.2 seconds old when queried (three probes: `max(timestamp)` against the wall clock). The repo's research note (DOCS D6) says "about five minutes"; that was not observed.
- Completeness: over a 10 minute window with no gap, the New Relic row count equals the Prometheus count per job exactly (kube-state-metrics 30,309, node-extras 18,320, jvm 9,440, postgres 8,400, ingress-nginx 5,600, cadvisor 3,280; ratio 1.0000 for each).

### 2.2 Metric type per name (what New Relic infers on remote write)

| Prometheus name pattern (example) | Prometheus TYPE | Stored as | Reads with |
|---|---|---|---|
| `*_total` (`container_cpu_usage_seconds_total`) | counter | `cumulativeCount`: `count` is the delta of the interval, `cumulative` the running value | `sum(m)`, `rate(sum(m), 1 second)` |
| `*_count` of a histogram (`http_server_requests_seconds_count`) | histogram part | `cumulativeCount` | same |
| `*_bucket` (`http_server_requests_seconds_bucket`) | histogram part | `cumulativeCount` with an `le` attribute | `bucketPercentile(m, 95)`, `sum(m) FACET le` |
| `*_sum` (`nginx_ingress_controller_request_duration_seconds_sum`) | histogram part | `summary` (`sum`, `count`, `min`/`max` null) | `sum(m)` |
| counter without `_total` (`nginx_ingress_controller_requests`, `pg_stat_database_xact_commit`, `node_netstat_IpExt_InOctets`) | counter | **gauge** holding the cumulative value | `rate(sum())` is wrong (section 4) |
| gauge ending `_count` (`pg_stat_activity_count`, `pg_locks_count`, `system_cpu_count`) | gauge | **`cumulativeCount`**: `count` 0.0, `cumulative` 3.0 for a steady value 3 | `average`/`latest` return 0.28/0.0 against 2 |
| everything else (`up`, `container_memory_rss`, recording rule outputs, `ALERTS`) | gauge | `gauge` (`count`, `sum`, `min`, `max`, `latest`) | `latest`, `average`, `max` |

Counters are therefore stored as delta counts, as the research note said (DOCS D8/D9). The `newrelic_metric_type` write relabel label works in both directions and is removed on receipt: with `counter` the request counter arrived as `cumulativeCount` (count 11.26, cumulative 873.68); with `gauge` `pg_stat_activity_count` arrived as a gauge (latest 2.0, average 3.1, as Prometheus). The converter's name suffix guess (`_total`, `_count`, `_sum`, `_bucket`) is the same rule as New Relic's, so it is right for the data as stored, and wrong against the real Prometheus type for the two last rows of the table above.

### 2.3 Label to attribute mapping

Every label is an attribute with the same name (`controller_pod`, `ingress`, `status`, `mode`, `datname`, `le` ...). Added by the path: `prometheus_server` (from the URL), `job` and `instance` (from Prometheus), `ckg_test` (external label), `metricName` (the metric name), `newrelic.source=prometheusAPI`, `instrumentation.name=remote-write`, `instrumentation.provider=prometheus`, `instrumentation.source=<prometheus_server>`, `instrumentation.version=0.0.2`, and `timestamp` (numeric). Counters and summaries also carry `endTimestamp`. For node_exporter style metrics (`job` `node` and `node-extras`) New Relic additionally set `entity.guid`, `entity.name` (the instance) and `entity.type=HOST`: three HOST entities were created by the data itself, a side effect of sending node metrics that the owner may want to know about (they are not objects the importer created).

### 2.4 The `metricName` collision

A label named `metricName` (probe metric `ckg_metricname_probe{metricName="orders_total", env="rig"}`) is silently lost: the stored `metricName` is the metric name (`ckg_metricname_probe`) and no attribute holds `orders_total`. After a `write_relabel_configs` rule copying it to `metric_name` and dropping the original, the value arrived as `metric_name="orders_total"`. The data path guide and runbook said "UNVERIFIED"; both now state the measured behaviour.

### 2.5 Histogram form

Classic histograms arrive as three series: `_bucket` (one row per `le`, type `cumulativeCount`, `le` a string such as `0.025` and `+Inf`), `_sum` (`summary`) and `_count` (`cumulativeCount`). `SELECT bucketPercentile(http_server_requests_seconds_bucket, 95)` returns a map `{"95": 0.2425}` per row/bucket and agreed with Prometheus's `histogram_quantile` within 0.01 percent in a chart (all 3 histogram cases MATCH, section 3). `percentile()` on the bucket attribute gives nonsense (292 for a latency in seconds).

### 2.6 Timestamps

Counters and summaries carry `timestamp` = start of the interval (the previous scrape) and `endTimestamp` = this scrape: 14.998 s apart for a 15 s scrape. Gauges carry only `timestamp`. The data path keeps Prometheus sample times, not arrival times.

### 2.7 Names with colons

Recording rule names (`instance:node_cpu_utilisation:rate5m`) arrive unchanged, and NRQL reads them in backticks (`SELECT latest(\`instance:node_cpu_utilisation:rate5m\`) FROM Metric FACET instance`). The converter refused every such name before this run (bug 1 below).

### 2.8 `ALERTS` and `up`

`ALERTS{alertname, alertstate="firing", ...}` is written like any gauge. `up` arrives per target; the `missing` target wrote `up = 0` continuously. Locally: `CkgTargetDown` fired for `missing` and `flaky`, `CkgIngress5xxRatioHigh` fired for `api` during the injected 5xx burst (21 evaluations), `CkgPodRestartingOften` never fired (the restart bump in the exporter is probabilistic; no second restart within 30 minutes happened).

### 2.9 Reliability finding: the Mac sleeps

macOS here sleeps one minute after the display turns off (`pmset`: sleep 1). That stopped Prometheus, so both Prometheus and New Relic have holes (about 11:11 to 11:13 with one `connection reset by peer`, 11:28 to 11:42 and 11:45 to 11:56 UTC), and the exporter, which advances counters by rate times elapsed time, makes one large catch-up step after each wake. From 11:59 the rig held off idle sleep with `caffeinate -i -w <watchdog pid>` (no sudo, ends with the watchdog). Comparison windows avoid the holes. Remote write itself reported no failed sample in the whole run; the reset error was retried.

## 3. Parity: local Prometheus against the converter's NRQL

Method: `compare.py` takes 36 PromQL expressions that occur in the six dashboards (plus 6 for the type experiment), converts each with the project's `convert_query` (chart: `TIMESERIES 60 SECONDS`; stat: no timeseries), runs the NRQL read-only for the same window and compares with Prometheus `query_range` (step 60 s) for the chart, and a Prometheus instant query with the range rewritten to the window for the stat. Selectors carry `ckg_test="true"` for the converter (which becomes the `WHERE`); Prometheus is queried without it. Each chart is compared two ways: against the expression as written in the dashboard (`[5m]`, `[2m]`) and against the same expression with every range set to 60 s, the New Relic bucket. The second separates data path errors from window semantics. Verdicts: MATCH (mean of NR/Prom within 5 percent and median per-bucket error within 15 percent), MATCH-MEAN (mean within 5 percent, buckets differ more: smoothing), MISMATCH, SERIES-DIFF, MERGED (several Prometheus series, one New Relic series), UNCONVERTIBLE (the converter refuses, which is its safe answer), EMPTY.

Window A: 11:14 to 11:27 UTC (13 minutes, includes the cAdvisor container reset), after the first outage and before the sleeps. Window B: see section 3.2.

### 3.1 Window A (11:14 to 11:27 UTC): 36 expressions, plain name map

Summary of the 36 base expressions as charts: 22 converted and 14 refused. Of the 22: 10 MATCH, 3 MATCH-MEAN, 2 MERGED (series not kept apart, existing note), 7 MISMATCH. Of the 7 mismatches 5 are the running-total gauge problem (finding 1), 1 is window smoothing (`rate_cadvisor_rx`, MATCH against the 60 s range) and 1 is `topk` ranking. As stats: 12 MATCH, 2 MERGED, 8 MISMATCH (irate, `increase` extrapolation, running totals, and `avg(...)` over a window that is not the latest value). In every case where the data was stored with the right type the counters, ratios of counters, summary quotients and the three `histogram_quantile` cases agree with Prometheus.

| case | PromQL | converter NRQL | chart vs dashboard range (mean ratio NR/Prom) | chart vs 1 m range | stat | root cause |
|---|---|---|---|---|---|---|
| rate_cpu_mode | `sum by (mode) (rate(node_cpu_seconds_total{job="node-extras"}[5m]))` | `SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND job = 'node-extras…` | MATCH (0.98-1.00) | MATCH | MATCH | none; counters named _total arrive as delta counts |
| rate_cpu_busy | `sum by (instance) (rate(node_cpu_seconds_total{job="node-extras",mode!="idle"}[…` | `SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND job = 'node-extras…` | MATCH (0.99-1.02) | MATCH | MATCH | none |
| rate_cadvisor_cpu | `sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+"}[5m])) * 100` | `SELECT rate(sum(container_cpu_usage_seconds_total), 1 second) * 100 FROM Metric WHERE ckg_test = 'true' AND n…` | MATCH-MEAN (0.96-1.03) | MATCH | MATCH | 5m Prometheus window smooths; bucket is 60 s |
| rate_cadvisor_rx | `sum by (name) (rate(container_network_receive_bytes_total{name=~".+"}[5m]))` | `SELECT rate(sum(container_network_receive_bytes_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND nam…` | MISMATCH (0.95-1.04) | MATCH | MATCH | 5m Prometheus window smooths; bucket is 60 s |
| rate_reset_container | `sum by (name) (rate(container_cpu_usage_seconds_total{instance="127.0.0.1:9131"…` | `SELECT rate(sum(container_cpu_usage_seconds_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND instanc…` | MATCH-MEAN (1.01-1.01) | MATCH | MATCH | reset handled on both sides (New Relic stores the new value as the delta) |
| rate_nginx_total_cpu | `sum(rate(nginx_ingress_controller_nginx_process_cpu_seconds_total[2m]))` | `SELECT rate(sum(nginx_ingress_controller_nginx_process_cpu_seconds_total), 1 second) FROM Metric WHERE ckg_te…` | MATCH (0.97-0.97) | MATCH | MATCH | none |
| irate_nginx_requests | `sum(irate(nginx_ingress_controller_requests[2m]))` | `SELECT rate(sum(nginx_ingress_controller_requests), 1 second) FROM Metric WHERE ckg_test = 'true' TIMESERIES …` | MISMATCH (109.89-109.89) | MISMATCH | MISMATCH | counter without _total is stored as a cumulative gauge; rate(sum()) of it is wrong |
| rate_nginx_by_ingress_ok | `sum by (ingress) (rate(nginx_ingress_controller_requests{status!~"[4-5].*"}[2m]…` | `SELECT rate(sum(nginx_ingress_controller_requests), 1 second) FROM Metric WHERE ckg_test = 'true' AND status …` | MISMATCH (112.95-114.30) | MISMATCH | MISMATCH | same: cumulative gauge |
| ratio_nginx_success | `sum(rate(nginx_ingress_controller_requests{status!~"[4-5].*"}[2m])) / sum(rate(…` | `SELECT rate(filter(sum(nginx_ingress_controller_requests), WHERE status NOT RLIKE '[4-5].*'), 1 second) / rat…` | MATCH (1.03-1.03) | MATCH | MATCH | matches only because both operands are cumulative gauges and the ratio cancels |
| irate_pg_commit | `sum by (datname) (irate(pg_stat_database_xact_commit[5m]))` | `SELECT rate(sum(pg_stat_database_xact_commit), 1 second) FROM Metric WHERE ckg_test = 'true' FACET datname LI…` | MISMATCH (1.00-110.78) | MISMATCH | MISMATCH | same: cumulative gauge (name has no _total) |
| irate_pg_bgwriter | `irate(pg_stat_bgwriter_buffers_alloc_total{instance="127.0.0.1:9141"}[5m])` | `SELECT rate(sum(pg_stat_bgwriter_buffers_alloc_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND inst…` | MATCH (1.00-1.00) | MATCH | MISMATCH | none (name ends _total); stat differs: irate is the last two samples, NRQL is the window mean |
| rate_pg_reset | `sum(rate(pg_stat_database_xact_commit{instance="127.0.0.1:9142",datname="app"}[…` | `SELECT rate(sum(pg_stat_database_xact_commit), 1 second) FROM Metric WHERE ckg_test = 'true' AND instance = '…` | MISMATCH (113.56-113.56) | MISMATCH | MISMATCH | same: cumulative gauge; the reset also drops the value |
| increase_http_count | `sum by (application) (increase(http_server_requests_seconds_count[10m]))` | `SELECT rate(sum(http_server_requests_seconds_count), 10 minutes) FROM Metric WHERE ckg_test = 'true' FACET ap…` | MATCH-MEAN (0.96-1.04) |  | MISMATCH | stat extrapolates the window rate to 10 minutes (existing note); chart matches |
| rate_http_sum_latency | `sum(rate(http_server_requests_seconds_sum[5m])) / sum(rate(http_server_requests…` | `SELECT rate(sum(http_server_requests_seconds_sum), 1 second) / rate(sum(http_server_requests_seconds_count), …` | MATCH (1.00-1.00) | MATCH | MATCH | none; _sum is a summary, _count a count |
| avg_over_time_conn | `sum(avg_over_time(nginx_ingress_controller_nginx_process_connections[2m]))` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused: sum over avg_over_time of a multi-series gauge |
| avg_rss | `avg(nginx_ingress_controller_nginx_process_resident_memory_bytes)` | `SELECT average(nginx_ingress_controller_nginx_process_resident_memory_bytes) FROM Metric WHERE ckg_test = 'tr…` | MATCH (1.00-1.00) |  | MISMATCH | none |
| sum_alloc_cpu | `sum(kube_node_status_allocatable{resource="cpu"})` | `grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing wh…` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused by design: sum() of a multi-series gauge |
| sum_pods_running | `sum(kube_pod_status_phase{phase="Running"})` | `grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing wh…` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused by design: sum() of a multi-series gauge |
| ratio_requests_alloc | `sum(kube_pod_container_resource_requests{resource="cpu"}) / sum(kube_node_statu…` | `grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing wh…` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused by design: sum() of a multi-series gauge |
| jvm_heap_by_app | `sum by (application) (jvm_memory_used_bytes{area="heap"})` | `grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing wh…` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused by design: sum() of a multi-series gauge |
| mem_used_pct | `100 * (1 - node_memory_MemAvailable_bytes{job="node-extras"} / node_memory_MemT…` | `SELECT 100 * (1 - (average(node_memory_MemAvailable_bytes) / average(node_memory_MemTotal_bytes))) FROM Metri…` | MERGED |  | MERGED | series merged (two instances); add a FACET (existing note) |
| hq_nginx_p95 | `histogram_quantile(0.95, sum by (le) (rate(nginx_ingress_controller_request_dur…` | `SELECT bucketPercentile(nginx_ingress_controller_request_duration_seconds_bucket, 95) FROM Metric WHERE ckg_t…` | MATCH (1.00-1.00) | MATCH | MATCH | none; _bucket arrives as cumulativeCount and bucketPercentile reads it |
| hq_nginx_p50_ingress | `histogram_quantile(0.5, sum by (le, ingress) (rate(nginx_ingress_controller_req…` | `SELECT bucketPercentile(nginx_ingress_controller_request_duration_seconds_bucket, 50) FROM Metric WHERE ckg_t…` | MATCH (1.00-1.00) | MATCH | MATCH | none |
| hq_http_p99_app | `histogram_quantile(0.99, sum by (le, application) (rate(http_server_requests_se…` | `SELECT bucketPercentile(http_server_requests_seconds_bucket, 99) FROM Metric WHERE ckg_test = 'true' FACET ap…` | MATCH (1.00-1.01) | MATCH | MATCH | none |
| topk_tx | `topk(3, sum by (name) (rate(container_network_transmit_bytes_total{name=~".+"}[…` | `SELECT rate(sum(container_network_transmit_bytes_total), 1 second) FROM Metric WHERE ckg_test = 'true' AND na…` | MISMATCH (0.93-1.03) | SERIES-DIFF | MATCH | NRQL ranks over the whole range, Prometheus at each step (existing note); stat matches |
| changes_restarts | `changes(kube_pod_container_status_restarts_total[30m]) > 1` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: changes() |
| join_group_left | `sum by (node) (kube_pod_container_resource_requests{resource="cpu"} * on (pod) …` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: on()/group_left |
| up_by_job | `sum by (job) (up)` | `grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing wh…` | UNCONVERTIBLE |  | UNCONVERTIBLE | refused by design: sum() of a multi-series gauge |
| up_flaky_avg | `avg_over_time(up{job="flaky"}[10m])` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: avg_over_time of a gauge |
| alert_5xx_ratio | `sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"5.."}[2m])) /…` | `SELECT rate(filter(sum(nginx_ingress_controller_requests), WHERE status RLIKE '5..'), 1 second) / rate(sum(ng…` | MISMATCH (0.40-1.01) | MISMATCH | MISMATCH | cumulative gauge (name has no _total) in both operands |
| alert_5xx_ratio_gt | `sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"5.."}[2m])) /…` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | comparison to a number is not written |
| alert_target_down | `up{job=~"flaky\|missing"} == 0` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | comparison filter on a gauge is not written |
| recording_name | `instance:node_cpu_utilisation:rate5m` | `SELECT average(`instance:node_cpu_utilisation:rate5m`) FROM Metric WHERE ckg_test = 'true' TIMESERIES 60 SECO…` | MERGED |  | MERGED | fixed in this run: colon names are now quoted; two instances merge (add a FACET) |
| uptime_days | `(time() - container_start_time_seconds{name=~".+"}) / 86400` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: time() |
| absent_failed_jobs | `absent(kube_job_status_failed)` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: absent() |
| count_cmp_evicted | `count(kube_pod_status_reason{reason="Evicted"} == 1)` | `grafana dashboard convert: the query is a shape the planner reads but the converter does not write` | UNCONVERTIBLE |  | UNCONVERTIBLE | not supported: count of a comparison |


Type experiment (same PromQL, `prometheus_server` untyped vs forced by `newrelic_metric_type`):

| case | untyped chart / 1 m / stat | typed chart / 1 m / stat |
|---|---|---|
| t_nginx_rate_ingress | MISMATCH / MISMATCH / MISMATCH | MATCH / MATCH / MATCH |
| t_nginx_irate_total | MISMATCH / MISMATCH / MISMATCH | MATCH / MATCH / MISMATCH |
| t_pg_commit_irate | MISMATCH / MISMATCH / MISMATCH | MATCH / MATCH / MISMATCH |
| t_pg_activity_state | UNCONVERTIBLE / - / UNCONVERTIBLE | UNCONVERTIBLE / - / UNCONVERTIBLE |
| t_pg_locks_mode | UNCONVERTIBLE / - / UNCONVERTIBLE | UNCONVERTIBLE / - / UNCONVERTIBLE |
| t_system_cpu_count | UNCONVERTIBLE / - / UNCONVERTIBLE | UNCONVERTIBLE / - / UNCONVERTIBLE |

With hints (types and series keys read from the local Prometheus by the project's own `read_hints`), the verdict changed for: no case.


### 3.2 Window B (11:58 to 12:08 UTC), straight after the second sleep

Second window verdicts (plain, chart / stat):

| case | window A | window B |
|---|---|---|
| rate_cpu_mode | MATCH / MATCH | MISMATCH / MATCH |
| rate_cpu_busy | MATCH / MATCH | MISMATCH / MATCH |
| rate_cadvisor_cpu | MATCH-MEAN / MATCH | MISMATCH / MATCH |
| rate_cadvisor_rx | MISMATCH / MATCH | MISMATCH / MATCH |
| rate_reset_container | MATCH-MEAN / MATCH | MATCH-MEAN / MATCH |
| rate_nginx_total_cpu | MATCH / MATCH | MATCH / MATCH |
| irate_nginx_requests | MISMATCH / MISMATCH | MISMATCH / MISMATCH |
| rate_nginx_by_ingress_ok | MISMATCH / MISMATCH | MISMATCH / MISMATCH |
| ratio_nginx_success | MATCH / MATCH | MATCH / MATCH |
| irate_pg_commit | MISMATCH / MISMATCH | MISMATCH / MISMATCH |
| irate_pg_bgwriter | MATCH / MISMATCH | MATCH / MISMATCH |
| rate_pg_reset | MISMATCH / MISMATCH | MISMATCH / MISMATCH |
| increase_http_count | MATCH-MEAN / MISMATCH | MISMATCH / MATCH |
| rate_http_sum_latency | MATCH / MATCH | MATCH / MATCH |
| avg_over_time_conn | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| avg_rss | MATCH / MISMATCH | MATCH / MISMATCH |
| sum_alloc_cpu | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| sum_pods_running | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| ratio_requests_alloc | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| jvm_heap_by_app | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| mem_used_pct | MERGED / MERGED | MERGED / MERGED |
| hq_nginx_p95 | MATCH / MATCH | MATCH / MATCH |
| hq_nginx_p50_ingress | MATCH / MATCH | MATCH / MATCH |
| hq_http_p99_app | MATCH / MATCH | MATCH / MATCH |
| topk_tx | MISMATCH / MATCH | MISMATCH / MATCH |
| changes_restarts | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| join_group_left | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| up_by_job | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| up_flaky_avg | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| alert_5xx_ratio | MISMATCH / MISMATCH | MISMATCH / MISMATCH |
| alert_5xx_ratio_gt | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| alert_target_down | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| recording_name | MERGED / MERGED | MERGED / MERGED |
| uptime_days | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| absent_failed_jobs | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |
| count_cmp_evicted | UNCONVERTIBLE / UNCONVERTIBLE | UNCONVERTIBLE / UNCONVERTIBLE |


Window B explains the one thing window A hid: in B the rate cases that were MATCH in A (`rate_cpu_mode`, `rate_cpu_busy`, `rate_cadvisor_cpu`, `increase_http_count`) turn MISMATCH against the dashboard's `[5m]`/`[10m]` range, but stay MATCH against the 60 s range (for example `rate_cpu_mode` mean 1.06 against `[5m]`, 1.002 against `[1m]`). Prometheus looks back over the catch-up step that the exporter made at 11:57 after the Mac woke; New Relic's per-bucket data starts at 11:58 and has no such step. That is a property of long ranges around a gap or a burst, not of the converter. The type-forced series (`ckg-local-typed`) stay MATCH in both windows (`t_nginx_rate_ingress` means 0.994, `t_pg_commit_irate` 1.007).


## 4. Findings and root causes (ranked by how many dashboard queries they touch)

1. **Counters without a `_total`/`_count`/`_sum`/`_bucket` suffix arrive as cumulative gauges.** Every converted `rate()`, `irate()` or `increase()` of such a metric is wrong: NGINX `nginx_ingress_controller_requests` (the main traffic panels and the 5xx alert), `pg_stat_database_*` (about 20 PostgreSQL panels), several `node_netstat_*`, `kube_*` counters without `_total`. Measured: New Relic returned 105 to 114 times the Prometheus value in the chart (and 0.40 to 1.0 times for the 5xx ratio), because `rate(sum(m), 1 second)` adds up running totals. With the type forced to `counter` by `write_relabel_configs` (`newrelic_metric_type`), the same NRQL matches (`t_nginx_rate_ingress`: MATCH / MATCH / MATCH, `t_pg_commit_irate` chart MATCH). The fix belongs in the data path (relabel from the Prometheus metadata), not in the NRQL. `ratio_nginx_success` matches on untyped data only because both operands are running totals and the ratio cancels.
2. **Gauges ending `_count` arrive as counts.** `pg_stat_activity_count`, `pg_locks_count`, `system_cpu_count`: `average` returned 0.28 and `latest` 0.0 where Prometheus has 2. The converter refused the sum shapes of these metrics (series key rules), so no wrong number is drawn, but a plain `pg_stat_activity_count{state="active"}` panel would be.
3. **Window semantics (not an error).** Prometheus smooths over the dashboard's `[5m]` or `[2m]`, the chart bucket is 60 s. Means agree within 8 percent, but per-bucket values differ by 20 percent on series that move. Against the same expression with a 60 s range every counter case is within 3 percent (`rate_cadvisor_rx` MISMATCH against `[5m]`, MATCH against `[1m]`).
4. **Stats.** Stat widgets divide by the whole window; the mean over a window is 2 percent below Prometheus (51 of 52 scrape intervals: the first interval starts before the window). `increase(x[10m])` as a stat is the window rate scaled to 10 minutes (existing note, 0.75 here because the window is 13 minutes). `irate` is the window mean, not the last two samples (existing note; 1.07 to 1.30 in stats).
5. **`topk`** ranks over the whole window in NRQL and at each step in Prometheus (existing note); the stat matches, the chart's series set can differ.
6. **Refused (UNCONVERTIBLE), 14 of 36 base cases:** `sum()` of a multi-series gauge unless the labels naming one series are pinned, `changes()`, `on()/group_left`, `time()`, `absent()`, `count(... == 1)`, `avg_over_time` of a gauge, comparison filters (`> 0.05`, `== 0`). Type and series hints from `read_hints` did not change a single verdict for these queries: the hints key labels include `instance`, `node`, `state` which the queries do not pin, which is the correct reason to refuse.
7. **Reliability.** One TCP reset and two multi-minute sleeps cost data on both sides; nothing in Prometheus's remote write counters flagged the hole, only the New Relic row counts did.

## 5. Converter bugs found and fixed (failing test first, small commits)

| # | Bug | Evidence | Fix |
|---|---|---|---|
| 1 | Recording rule names (`instance:node_cpu_utilisation:rate5m`, any `a:b:c`) were refused: "a metric or label name has a character NRQL does not accept". Such names are everywhere in the kube-prometheus dashboards | New Relic stores them unchanged and `SELECT latest(\`a:b:c\`)` works; window A `recording_name` now converts | `metric_identifier()` quotes a colon name in backticks in the dashboard converter only (shared `identifier()` untouched, so nothing else changes); tests in `test_grafana_dashboard_convert_queries.py`; census marks `name.recording_rule` supported |
| 2 | A hinted metric type that New Relic would store differently (counter without `_total`, gauge ending `_count`) converted with no hint of the problem and drew wrong numbers | section 2.2 and 4.1 | A note on the widget: "<metric> is a counter in Prometheus but New Relic infers the other type from its name on remote write; add newrelic_metric_type=counter to write_relabel_configs". Only when type hints are given; the NRQL text is unchanged, so no hash file moved |
| 3 | Data path guide and runbook said the `metricName` behaviour was unverified and that remote write was untested | section 2.4 | Both now state the measured behaviour and the type rule |

No golden, baseline or hash file was regenerated (`tests/fixtures/*-hashes.json` and `tests/golden` untouched; the gate was green after the changes).

## 6. Proposals (not done)

- `plan-grafana-datapath` should write the `newrelic_metric_type` relabel rules itself from the type hints (`counter` for hinted counters whose names lack a counter suffix, `gauge` for hinted gauges whose names end `_count`, `_total`, `_sum`, `_bucket`). The generated `prometheus-remote-write.yml` has only a commented example today. `compare.py` shows the effect: untyped MISMATCH, typed MATCH. This adds output to a generated file, so it needs the owner's review of the plan hashes.
- A correct NRQL for a counter that is already stored as a gauge: `derivative()` takes an attribute, not an aggregate (`derivative(max(x), 1 second)` is rejected with "Unknown function max()"), and `rate(max(x), 1 second)` is the maximum divided by the window, not a derivative. No chart form was found that handles several series; keep the relabel route.
- Backfill the Grafana-style `sum(gauge)` refusal with a nested query over `latest()` per series if New Relic allows it in a chart (not tested).
- The research note's "about five minutes" to see data should be replaced by the measured 2.5 to 4.2 seconds, with the caveat that it is one Prometheus, one account, one region.

## 7. Operating the rig

This run: started 10:49:26 UTC, stopped itself at 12:19:44 UTC (90 minutes), temp directory (binaries, TSDB, token file) deleted, caffeinate ended, no process left. Last watchdog reading: 437,982 samples handed to remote storage (cap 1.4 M). The New Relic data stays until retention removes it (all tagged `ckg_test=true`).

Run: `bash scripts/live-test/telemetry-rig/fetch.sh` (download and verify), `bash scripts/live-test/telemetry-rig/start.sh` (needs `NEW_RELIC_LICENSE_KEY` in the repository `.env`). Stop: `bash scripts/live-test/telemetry-rig/stop.sh` (stops caffeinate, Prometheus, node_exporter, the exporter and the watchdog, deletes the temp dir with the binaries, TSDB and token file). Compare: `uv run python scripts/live-test/telemetry-rig/compare.py ENVFILE OUT.json [T0 T1]` then `report.py OUT.json`. Set `SSL_CERT_FILE` if corporate inspection breaks TLS; verification stays on.
