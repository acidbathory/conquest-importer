# Grafana test estate: how close is a one-to-one copy into New Relic?

Date: 2026-11-10. Branch `live/grafana-test-estate`. Sources and licences: [grafana-test-estate-sources](grafana-test-estate-sources.md).
Every number below was produced by the project's own commands on the live estate (read-only extract, offline convert);
nothing was run against customer data. Data was not sent to New Relic, so no widget or condition was checked against
real values. Query validity of the changed shapes was checked with read-only NerdGraph NRQL (`round`, `ceil`, `floor`,
`abs` accepted).

## 1. Answers first

- Published: 50 community dashboards (46 new, 4 from earlier sessions) in 6 Grafana folders, 55 paused alert rules,
  5 contact points, 3 mute timings. Manifest: `out/live-estate/manifest.json`.
- Dashboards: 1,699 data widgets (rows and text boxes excluded; 378 of those pass trivially). After the fixes below:
  exact 0, approximate 777 (45.7%), manual 922 (54.3%). Before the fixes: approximate 638 (37.6%). Exact is 0 because the
  importer labels every Prometheus conversion approximate (metric names and label sets are unverified).
- 47 of 50 dashboards could be emitted; 23 reach 50% or more of their data widgets; 5 reach 0%.
- Alerts: 55 rules, 7 approximate (12.7%), 48 manual, 0 exact. All 7 were created disabled in New Relic.
- Notifications: 5 contact points, 3 convert to destinations (webhook, PagerDuty, Opsgenie via webhook), 2 need a person
  (Slack, Teams); 1 of 3 mute timings became a muting rule; 3 secrets must be supplied.
- Not one-to-one: no dashboard is a faithful copy. The honest statement is "a first draft where roughly half of the data
  panels have a plausible query and a visible 'Not migrated' note for the rest".
- Applied to New Relic sandbox: 47 dashboards (private) and 7 disabled conditions in policy `ckg-estate-policy`.
  Manifests: `out/live-estate/nr-manifest.json` (dashboards, guids in `id`), `out/live-estate/nr-manifest-alerts.json`.

## 2. Per dashboard

Columns: widgets are data widgets only. "Converted" = (exact + approximate) / data widgets. Top blocker = most common reason
among manual widgets of that dashboard. Downloads from grafana.com on 2026-11-10.

| id | dashboard | downloads | widgets | exact | approx | manual | converted | top blocker |
|---|---|---|---|---|---|---|---|---|
| 12693 | HAProxy | 1,438,734 | 106 | 0 | 98 | 8 | 92% | comparison or set operator |
| 9578 | Alertmanager | 21,785,174 | 102 | 0 | 94 | 8 | 92% | raw counter |
| 14981 | CoreDNS | 59,208,449 | 23 | 0 | 21 | 2 | 91% | sum/count of a gauge |
| 10826 | Go Metrics | 1,634,871 | 10 | 0 | 9 | 1 | 90% | raw counter |
| 2583 | MongoDB | 31,663,996 | 16 | 0 | 14 | 2 | 88% | sum/count of a gauge |
| 1860 | Node Exporter Full | 149,895,136 | 124 | 0 | 100 | 24 | 81% | comparison or set operator |
| 14191 | Elasticsearch Exporter Quickstart and Da | 9,675,222 | 55 | 0 | 44 | 11 | 80% | raw counter |
| 11378 | Spring Boot 2.1 System Monitor | 5,801,347 | 35 | 0 | 27 | 8 | 77% | raw counter |
| 16361 | Kubernetes / Kubelet | 3,529,433 | 24 | 0 | 18 | 6 | 75% | sum/count of a gauge |
| 6483 | ElasticSearch | 1,731,008 | 71 | 0 | 52 | 19 | 73% | raw counter |
| 15762 | Kubernetes / System / CoreDNS | 8,288,497 | 14 | 0 | 10 | 4 | 71% | heatmap widget |
| 15761 | Kubernetes / System / API Server | 9,376,200 | 12 | 0 | 8 | 4 | 67% | comparison or set operator |
| 19105 | Prometheus | 1,946,643 | 27 | 0 | 17 | 10 | 63% | sum/count of a gauge |
| 4701 | JVM (Micrometer) | 31,370,397 | 27 | 0 | 17 | 10 | 63% | panel options |
| 6671 | Go Processes | 4,332,058 | 8 | 0 | 5 | 3 | 62% | dashboard query uses a range function ot |
| 14055 | Loki stack monitoring (Promtail, Loki) | 48,791,235 | 18 | 0 | 11 | 7 | 61% | vector matching |
| 11835 | Redis Dashboard for Prometheus Redis Exp | 28,235,859 | 12 | 0 | 7 | 5 | 58% | dashboard panel overrides the time range |
| 16368 | Kubernetes / Compute Resources / Pod | 3,519,649 | 16 | 0 | 9 | 7 | 56% | dashboard query has a metric or label na |
| 9628 | PostgreSQL Database | 13,522,645 | 32 | 0 | 18 | 14 | 56% | comparison or set operator |
| 10619 | Docker Container & Host Metrics | 6,317,833 | 13 | 0 | 7 | 6 | 54% | sum/count of a gauge |
| 14584 | ArgoCD | 74,807,432 | 39 | 0 | 21 | 18 | 54% | sum/count of a gauge |
| 14282 | Cadvisor exporter | 168,216 | 6 | 0 | 3 | 3 | 50% | sum/count of a gauge |
| 9614 | NGINX Ingress controller | 73,628,564 | 12 | 0 | 6 | 6 | 50% | function without NRQL form |
| 15760 | Kubernetes / Views / Pods | 14,176,020 | 25 | 0 | 12 | 13 | 48% | vector matching |
| 3662 | Prometheus 2.0 Overview | 6,827,652 | 30 | 0 | 14 | 16 | 47% | _over_time |
| 747 | Kubernetes Pod Metrics | 15,784,614 | 15 | 0 | 7 | 8 | 47% | sum/count of a gauge |
| 7645 | Istio Control Plane Dashboard | 64,687,064 | 13 | 0 | 6 | 7 | 46% | sum/count of a gauge |
| 315 | Kubernetes cluster monitoring (via Prome | 22,716,326 | 21 | 0 | 9 | 12 | 43% | sum/count of a gauge |
| 13659 | Blackbox Exporter (HTTP prober) | 53,106,378 | 3 | 0 | 1 | 2 | 33% | transformations |
| 16364 | Kubernetes / Compute Resources / Cluster | 3,519,779 | 22 | 0 | 7 | 15 | 32% | dashboard query has a metric or label na |
| 19993 | ArgoCD / Operational / Overview | 13,316,534 | 44 | 0 | 14 | 30 | 32% | sum/count of a gauge |
| 11074 | Node Exporter Dashboard EN 20201010-Star | 1,711,861 | 24 | 0 | 7 | 17 | 29% | unreadable shape |
| 12904 | Hashicorp Vault | 3,035,140 | 25 | 0 | 6 | 19 | 24% | without() |
| 7651 | Billing/Usage | 5,225,063 | 139 | 0 | 28 | 111 | 20% | variable stays manual |
| 15757 | Kubernetes / Views / Global | 11,435,306 | 26 | 0 | 5 | 21 | 19% | transformations |
| 7362 | MySQL Overview | 21,146,672 | 36 | 0 | 7 | 29 | 19% | comparison or set operator |
| 11176 | VictoriaMetrics - cluster | 6,333,077 | 97 | 0 | 17 | 80 | 18% | comparison or set operator |
| 12485 | PostgreSQL Exporter | 13,070,658 | 35 | 0 | 5 | 30 | 14% | variable stays manual |
| 17346 | Traefik Official Standalone Dashboard | 2,597,331 | 14 | 0 | 2 | 12 | 14% | dashboard query rewrites labels with lab |
| 15661 | K8S Dashboard | 35,478,431 | 33 | 0 | 4 | 29 | 12% | sum/count of a gauge |
| 14900 | Nginx | 20,816,683 | 20 | 0 | 2 | 18 | 10% | regex |
| 15758 | Kubernetes / Views / Namespaces | 13,177,193 | 25 | 0 | 2 | 23 | 8% | sum/count of a gauge |
| 15759 | Kubernetes / Views / Nodes | 12,002,367 | 35 | 0 | 2 | 33 | 6% | variable not converted |
| 13332 | kube-state-metrics-v2 | 30,002,555 | 42 | 0 | 2 | 40 | 5% | sum/count of a gauge |
| 15987 | Kyverno | 3,585,826 | 39 | 0 | 2 | 37 | 5% | raw counter |
| 10991 | RabbitMQ-Overview | 52,368,781 | 40 | 0 | 0 | 40 | 0% | vector matching |
| 15141 | Loki Kubernetes Logs | 1,428,761 | 2 | 0 | 0 | 2 | 0% | variable stays manual |
| 15983 | OpenTelemetry Collector | 2,856,550 | 41 | 0 | 0 | 41 | 0% | variable stays manual |
| 6417 | Kubernetes Cluster (Prometheus) | 21,852,115 | 29 | 0 | 0 | 29 | 0% | sum/count of a gauge |
| 7636 | Istio Service Dashboard | 61,504,981 | 22 | 0 | 0 | 22 | 0% | variable stays manual |

## 3. Per alert rule

| rule | group | for | noData/execErr | result | reason (first) |
|---|---|---|---|---|---|
| AlertmanagerClusterDown | alertmanager | 5m | NoData/Error | manual | promql uses an _over_time function |
| AlertmanagerFailedToSendAlerts | alertmanager | 5m | NoData/Error | manual | promql uses rate, increase or another range function |
| BlackboxProbeFailed | app-slo | 2m | Alerting/Alerting | approximate | converted (disabled) |
| BlackboxSslExpiry | app-slo | 0s | NoData/Error | manual | promql uses a binary operator or comparison |
| CPUThrottlingHigh | kubernetes-resources | 15m | NoData/Error | manual | promql label matcher is not supported (only = and != with a string, and =~ or !~ with plain wor |
| CrossDatasourceMath | multi-condition | 5m | NoData/Error | manual | grafana pipeline queries more than one data source |
| HighErrorRatio5xx | app-slo | 10m | NoData/Error | manual | promql label matcher is not supported (only = and != with a string, and =~ or !~ with plain wor |
| JVMHeapNearLimit | app-slo | 5m | NoData/Error | manual | promql aggregation sum() across series with reduce function last cannot be one New Relic aggreg |
| KafkaConsumerLag | app-slo | 10m | NoData/Error | manual | promql aggregation sum() across series with reduce function last cannot be one New Relic aggreg |
| KubeAPIErrorBudgetBurn | kubernetes-system | 2m | NoData/Error | manual | promql uses a binary operator or comparison |
| KubeAPILatencyHigh | kubernetes-system | 5m | NoData/Error | manual | promql uses histogram_quantile |
| KubeCPUOvercommit | kubernetes-resources | 10m | NoData/Error | manual | promql uses a binary operator or comparison |
| KubeClientCertificateExpiration | kubernetes-system | 0s | NoData/Error | manual | promql uses vector matching (on, ignoring, group_left or group_right) |
| KubeDeploymentReplicasMismatch | kubernetes-apps | 15m | NoData/Error | manual | promql uses rate, increase or another range function |
| KubeHpaMaxedOut | kubernetes-apps | 15m | NoData/Error | manual | promql uses a binary operator or comparison |
| KubeJobFailed | kubernetes-apps | 15m | NoData/Error | approximate | converted (disabled) |
| KubeNodeNotReady | kubernetes-system | 15m | NoData/Error | manual | promql expression `kube_node_status_condition{job="kube-state-metrics",condition="Ready",status |
| KubePersistentVolumeFillingUp | kubernetes-storage | 1m | NoData/Error | manual | promql uses a binary operator or comparison |
| KubePersistentVolumeFillingUpPredicted | kubernetes-storage | 1h | NoData/Error | manual | promql uses rate, increase or another range function |
| KubePodCrashLooping | kubernetes-apps | 15m | NoData/Error | manual | promql uses an _over_time function |
| KubePodNotReady | kubernetes-apps | 15m | NoData/Error | manual | promql uses vector matching (on, ignoring, group_left or group_right) |
| KubeQuotaAlmostFull | kubernetes-resources | 15m | NoData/Error | manual | promql uses vector matching (on, ignoring, group_left or group_right) |
| KubeStatefulSetReplicasMismatch | kubernetes-apps | 15m | NoData/Error | manual | promql uses a binary operator or comparison |
| KubeletTooManyPods | kubernetes-system | 15m | NoData/Error | manual | promql uses vector matching (on, ignoring, group_left or group_right) |
| LatencyP99Regression | app-slo | 15m | NoData/Error | manual | promql uses offset |
| LokiErrorRate | loki-logs | 5m | NoData/Error | manual | logql uses a function other than count_over_time |
| LokiNoLogs | loki-logs | 5m | Alerting/Error | approximate | converted (disabled) |
| LokiPanicBytes | loki-logs | 0s | NoData/Error | manual | logql uses a function other than count_over_time |
| MathRatioExpression | multi-condition | 5m | NoData/Error | manual | grafana pipeline has a math expression that combines N queries, and a New Relic condition reads |
| MultiCondErrorsAndTraffic | multi-condition | 10m | NoData/Error | manual | grafana pipeline has a math expression that combines N queries, and a New Relic condition reads |
| NodeCPUHighUsage | node-exporter | 15m | NoData/Error | manual | promql uses rate, increase or another range function |
| NodeClockSkewDetected | node-exporter | 10m | NoData/Error | manual | promql uses rate, increase or another range function |
| NodeDiskIOSaturation | node-exporter | 30m | NoData/Error | manual | promql label matcher is not supported (only = and != with a string, and =~ or !~ with plain wor |
| NodeFilesystemAlmostOutOfFiles | node-exporter | 1h | NoData/Error | manual | promql label matcher is not supported (only = and != with a string, and =~ or !~ with plain wor |
| NodeFilesystemSpaceFillingUp | node-exporter | 1h | NoData/Error | manual | promql uses rate, increase or another range function |
| NodeHighNumberConntrackEntriesUsed | node-exporter | 0s | NoData/Error | manual | promql uses a binary operator or comparison |
| NodeMemoryHighUtilization | node-exporter | 15m | NoData/Error | manual | promql uses a binary operator or comparison |
| NodeMemoryMajorPagesFaults | node-exporter | 15m | NoData/Error | manual | promql counter rate without sum(): Prometheus tests each series on its own, and a New Relic tot |
| NodeNetworkReceiveErrs | node-exporter | 1h | NoData/Error | manual | promql ratio: both sides must be the same aggregation of different metrics with the same labels |
| NodeSystemSaturation | node-exporter | 15m | NoData/Error | manual | promql uses without() |
| PostgresConnectionsHigh | app-slo | 5m | NoData/Error | manual | promql ratio: both sides must be the same aggregation of different metrics with the same labels |
| PrometheusAbsent | prometheus | 10m | Alerting/Error | approximate | converted (disabled) |
| PrometheusNotIngestingSamples | prometheus | 10m | NoData/Error | manual | promql counter rate without sum(): Prometheus tests each series on its own, and a New Relic tot |
| PrometheusNotificationQueueRunningFull | prometheus | 15m | NoData/Error | manual | promql uses an _over_time function |
| PrometheusOperatorReconcileErrors | prometheus-operator | 10m | NoData/Error | approximate | converted (disabled) |
| PrometheusRemoteWriteBehind | prometheus | 15m | NoData/Error | manual | promql uses vector matching (on, ignoring, group_left or group_right) |
| PrometheusRuleFailures | prometheus | 15m | NoData/Error | manual | promql counter rate without sum(): Prometheus tests each series on its own, and a New Relic tot |
| PrometheusTSDBReloadsFailing | prometheus | 4h | NoData/Error | manual | promql counter rate without sum(): Prometheus tests each series on its own, and a New Relic tot |
| PrometheusTargetDown | prometheus | 5m | Alerting/Error | approximate | converted (disabled) |
| RecordingRuleCpu | app-slo | 10m | NoData/Error | manual | promql metric or label name cannot be an NRQL attribute name (a character NRQL rejects) |
| RecordingRuleUnavailable | app-slo | 5m | OK/OK | manual | promql metric or label name cannot be an NRQL attribute name (a character NRQL rejects) |
| RedisMemoryHigh | app-slo | 5m | NoData/Error | manual | promql ratio: both sides must be the same aggregation of different metrics with the same labels |
| ThresholdOnReduceMean | multi-condition | 10m | KeepLast/KeepLast | approximate | converted (disabled) |
| TopkNoisyPods | app-slo | 10m | NoData/Error | manual | promql uses topk or bottomk |
| WithinRangeTemperature | multi-condition | 5m | OK/Error | manual | grafana threshold range (within or outside) has no single New Relic threshold |

Alert blockers (a rule can have several reasons; counted over manual rules):

| rules | reason |
|---|---|
| 8 | promql uses a binary operator or comparison |
| 6 | promql uses rate, increase or another range function |
| 5 | promql uses vector matching (on, ignoring, group_left or group_right) |
| 4 | promql label matcher is not supported (only = and != with a string, and =~ or !~ with plain words joined by |, or a plain word with .* befor |
| 4 | promql counter rate without sum(): Prometheus tests each series on its own, and a New Relic total over every series is a different test |
| 3 | promql uses an _over_time function |
| 3 | promql ratio: both sides must be the same aggregation of different metrics with the same labels, grouping and range; anything else needs fil |
| 2 | grafana pipeline has a math expression that combines N queries, and a New Relic condition reads one query |
| 2 | promql metric or label name cannot be an NRQL attribute name (a character NRQL rejects) |
| 2 | logql uses a function other than count_over_time |
| 2 | promql aggregation sum() across series with reduce function last cannot be one New Relic aggregation |
| 1 | grafana pipeline queries more than one data source |
| 1 | grafana threshold range (within or outside) has no single New Relic threshold |
| 1 | promql uses without() |
| 1 | promql expression `kube_node_status_condition{job="kube-state-metrics",condition="Ready",status="true"} == N` keeps only series whose value  |
| 1 | promql uses offset |
| 1 | promql uses histogram_quantile |
| 1 | promql uses topk or bottomk |

## 4. What blocks dashboards, ranked

A widget can carry several reasons, so rows overlap. "Widgets" = manual widgets with the reason; "dashboards" = how many of the 50.

| widgets | dashboards | reason |
|---|---|---|
| 167 | 33 | grafana dashboard convert: sum() or count() across the series of a gauge cannot be written without knowing which labels  |
| 166 | 27 | dashboard query uses a comparison, a set operator or an operator inside an aggregation |
| 153 | 9 | dashboard panel uses a variable that stays manual, so its filter cannot be written |
| 100 | 14 | dashboard query uses vector matching (on, ignoring, group_left or group_right) |
| 67 | 3 | dashboard query uses without() |
| 66 | 16 | dashboard query reads a counter metric (_total, _count, _sum or _bucket) without rate |
| 44 | 10 | dashboard query uses an _over_time function |
| 41 | 16 | dashboard query uses a function with no New Relic counterpart in the converter |
| 40 | 19 | dashboard panel has transformations, which run in the browser and cannot be reproduced in a query |
| 31 | 11 | dashboard query aggregates an aggregation |
| 31 | 8 | dashboard query is not a shape the converter reads |
| 27 | 15 | dashboard panel has options that change what it shows (value mappings, overrides, a unit conversion or a calculation oth |
| 27 | 1 | dashboard query uses a variable format such as ${name:csv} |
| 22 | 2 | grafana dashboard convert: the query uses a variable that did not become a New Relic variable |
| 22 | 5 | dashboard panel type heatmap maps to a New Relic widget the importer cannot create yet |
| 21 | 6 | dashboard query has a regex matcher that RLIKE cannot carry exactly (it matches an empty or missing label value, or uses |
| 15 | 7 | dashboard panel type gauge maps to a New Relic widget the importer cannot create yet |
| 15 | 3 | dashboard query has a metric or label name that is not valid in NRQL |
| 14 | 6 | grafana dashboard convert: an average, minimum or maximum of rates has no correct New Relic form |
| 13 | 1 | dashboard query uses offset |
| 11 | 7 | dashboard query uses a range function other than rate, irate and increase |
| 10 | 2 | dashboard query rewrites labels with label_replace or label_join |

Items completely done if that one group were fixed (remediation plan, "only blocker of"): comparison or vector matching 204,
sum/count of a gauge 167, time or range function 70, raw counter 64, variable that stays manual 21.

## 5. Impossible in NRQL (or without more data), with the workaround

| construct | widgets | why | workaround |
|---|---|---|---|
| `sum()`/`count()` over the series of a gauge, no label list | 167 | NRQL aggregates samples, not series; the correct form needs `latest()` per series (FACET on the identifying labels) | `grafana-prom-hints` / a label list in the name map, then nested query `SELECT sum(v) FROM (SELECT latest(x) AS v ... FACET labels)`; not testable here because the stack's Prometheus holds none of these metrics |
| `on()`/`group_left` joins to info metrics (`node_uname_info`, `kube_pod_info`) | 100 | no join between two metric series in one NRQL | add the label at ingest (relabel/Alloy) and facet on it; roadmap item 3 |
| `without()` | 67 | NRQL cannot say "all labels except" | list the labels (needs the series' label set) |
| `query_result()` of an expression variable | 21 variables | New Relic variables run NRQL, not an expression | rewrite as `uniques(label)` of the selector by hand |
| browser transformations | 40 | run in Grafana's UI only | the NRQL-expressible subset (filter, group by, sort, limit, rename) is roadmap item 2; join/merge/pivot have no equivalent |
| nodeGraph, logs-as-table with data links | 3+ | no chart type | table or markdown |
| `time() - metric`, `scalar()` | ~10 | no clock function usable per bucket | `now()`-based NRQL by hand |
| recording-rule series (names with `:`) | 15 widgets, 2 rules | the series exist only if the Prometheus that evaluates the rules also remote-writes them | remote-write the rule output; backtick quoting is valid NRQL (checked) but how colon names arrive in New Relic is unverified |
| Notification: Slack, Teams OAuth destinations | 2 | cannot be created from script | create in the UI |
| Alert `for`, recovery, `keep_firing_for`, group timing | all | no equivalent knobs | documented as lost |

## 6. Defects fixed in this branch (tests first, all in `tests/`)

1. Variable queries ignored the regex-matcher rules that panel queries use: `label_values(m{name!=""}, name)` and
   `{version=~"vm(insert|select).*"}` stayed manual. Fix: `dashboard_convert/variables.py` masks the selector like panels do.
   Effect on the estate: 13 variables now convert.
2. A whole-value regex with PromQL anchors (`^$Node$`, `^k8s_.*`, `^(a|b)$`, `^($ns)$`) stayed manual although the match is
   implicitly anchored. Fix: `regex_matchers.unanchored`. Effect: +138 widgets (638 to 776 approximate). An older test pinned
   `^(tmpfs|rootfs)$` as manual; it now uses `(?:...)`, which stays manual.
3. `round`, `ceil`, `floor`, `abs` around a whole query: written as the same NRQL function (checked live). Net +1 widget on
   this estate because those panels also hit other blockers (variables, gauge sums).

Gate (`scripts/improve/gate.sh`) passes: ruff, format, mypy strict, vulture, 6,517 tests. No golden or hash file changed, so no
proposal under `docs/improvement/proposals/` was needed.

## 7. Top 15 fixable defects still open (ranked by panels)

Each has a failing-test proposal (inputs from this estate; expected output to be decided by the owner where it changes pinned output).

| # | defect | widgets | failing-test proposal |
|---|---|---|---|
| 1 | Comparison filters `m == 1`, `m > 0`, `and`/`unless` not written (nested query with WHERE on the inner value) | 166 | `count(kyverno_policy_rule_info_total==1) by (policy_name)` expects `SELECT uniqueCount(...) FROM (SELECT latest(...) AS v ... FACET ...) WHERE v = 1` |
| 2 | Gauge `sum()`/`count()` when hints exist but the selector's own labels could name the series | 167 | `sum(kube_node_status_allocatable{resource="pods"})` with a name-map label list expects the nested `latest` form |
| 3 | `max_over_time`/`min_over_time`/`avg_over_time`/`sum_over_time` with a window equal to the bucket map to `max()`/`min()`/`average()`/`sum()` | 44 | `max_over_time(vm_active_merges{job="x"}[5m])` expects `max(vm_active_merges)` with TIMESERIES |
| 4 | Variable `query_result(sum(m) by (label))` shapes | 21 vars | `query_result(sum(istio_requests_total) by (destination_service))` with regex expects `uniques(destination_service)` |
| 5 | Name-suffix counter guess flags gauges (`*_active_count`, `*_segments_count`) | 66 | `elasticsearch_thread_pool_active_count` read without rate should not be refused when a metric-type hint says gauge, and `grafana-prom-hints` should be offered in the reason text |
| 6 | Widget types gauge, bargauge, heatmap not created | 59 | a gauge panel expects a billboard with thresholds |
| 7 | `${var:csv}` / `${var:text}` / `${var:value}` variable formats | 27 | `{job=~"${job:regex}"}` expects `IN ({{job}})` |
| 8 | Variables with Loki `label_values(label)` | 3 | Loki-source variable expects `FROM Log SELECT uniques(label)` |
| 9 | Variable regex with a capture group or lookahead refused outright | 7 vars | `/(.*)-prod/` could be an RLIKE on the values |
| 10 | Dashboard links, annotations, repeated rows dropped without a carried-over equivalent | 34 | repeated row expects one page per value or a note with the value list |
| 11 | `time() - metric` | 8 | `time() - process_start_time_seconds` expects `(now() - latest(timestamp))`-style form, owner to specify |
| 12 | Metric names with `:` (recording rules) refused by `identifier()` | 15 | `node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate` expects a backtick-quoted attribute or metricName filter |
| 13 | Alert side: regex and `!=""` matchers (`code=~"5.."`, `fstype!=""`) refused; the TagFilter model has no RLIKE or present kind | 4 rules (and every mixin rule using them) | `sum(rate(http_requests_total{code=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` should convert with `code RLIKE '5..'` |
| 14 | Alert side: rate/increase/over_time and `predict_linear` always manual; unmerged branch `improve/grafana-rules2` covers part | 9 rules | `increase(prometheus_rule_evaluation_failures_total[5m])` expects a `rate`/`derivative` condition |
| 15 | Alert side: ratio of two different metrics with the same grouping refused when the aggregations differ (`sum`/`max`) | 3 rules | `sum by (instance)(pg_stat_activity_count) / max by (instance)(pg_settings_max_connections)` expects a two-term condition |

## 8. Limits of this measurement

- Panel and rule counts, not importance: a stat tile and a 100-row table count the same.
- No data reached New Relic, so "approximate" means "a query was written", not "the numbers match".
- Library panels, repeated-row output in New Relic, and the Grafana Cloud email contact point could not be tested (see sources note).
- The hints step (`grafana-prom-hints`) was not run: the stack's Prometheus holds none of these metric names.
- Alert numbers are from the code on `origin/main`; the unmerged `improve/grafana-rules2` branch is expected to convert more.
- Alert conditions in New Relic were created disabled; nothing was enabled; notifications were not applied.

## 9. Reproduce

`scripts/live-test/grafana-sandbox/`: `publish_estate.py`, `publish_estate_alerts.py`, `estate_convert.sh OUT`, `estate_measure.py OUT`,
`cleanup_estate.py` (dry run unless `--yes`; not run), `cleanup_estate_nr.sh` (preview unless `--apply`; not run).
