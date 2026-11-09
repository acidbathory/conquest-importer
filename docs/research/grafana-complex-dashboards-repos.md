# Rebuilding complex Grafana dashboards in New Relic: what the big public dashboard sources contain, and what the importer can do with them

Research for the Grafana dashboard converter (`src/importer/sources/grafana/dashboards/**`). Public sources only, fetched with `curl` on 2026-11-09. No Grafana or New Relic instance was called, no `.env` was read, no cloud command was run, no NRQL was executed. Every NRQL statement in this note is therefore written from documentation and from other people's published queries; none was run against New Relic (see section 9).

## 0. How to read this file

Every claim carries one tag.

- DOCS: read on a documentation page; the URL is given (index below).
- CODE: read in a public file (a dashboard JSON, a repository path, a URL). The path or URL is given.
- REPO: read in this repository.
- MEASURED: I computed it from the files listed in section 2. The method is in section 3.1; a different classifier gives different numbers.
- UNVERIFIED: memory, inference, or something I could not check. Collected in section 9.

Documentation index (all fetched with `curl`; the `.mdx` files come from `raw.githubusercontent.com/newrelic/docs-website/develop/src/content/docs/`).

| id | page |
|---|---|
| N1 | NRQL reference, https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/ (source `nrql/nrql-syntax-clauses-functions.mdx`) |
| N2 | Subqueries, https://docs.newrelic.com/docs/nrql/using-nrql/subqueries-in-nrql/ |
| N3 | Subquery joins, https://docs.newrelic.com/docs/nrql/using-nrql/subquery-joins/ |
| N4 | Nested aggregation, https://docs.newrelic.com/docs/nrql/using-nrql/nested-aggregation-make-ordered-computations-single-query/ |
| N5 | Translate PromQL queries to NRQL, https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql/ |
| N6 | Supported PromQL features, https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/supported-promql-features/ |
| N7 | View and query your Prometheus data, https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/view-query-your-prometheus-data/ |
| N8 | Chart types, https://docs.newrelic.com/docs/query-your-data/explore-query-data/use-charts/chart-types/ |
| N9 | Template variables, https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/ |
| N10 | Nested variables, https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/nested-variables/ |
| N11 | Import, export, and add dashboards and charts, https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboards-charts-import-export-data/ |
| Q | github.com/newrelic/newrelic-quickstarts (`dashboards/<name>/<name>.json`, `quickstarts/<name>/config.yml`, `alert-policies/<name>/`), `main` branch, tree read 2026-11-09 |
| P1 | `docs/research/grafana-dashboards.md` (the earlier 69-dashboard note) |
| P2 | `docs/research/grafana-notifications-datapath.md` section 6 (data path) |

## 1. Answers first

1. I fetched and measured 108 real dashboard JSON files: 51 from grafana.com (ids and revisions in 2.1), 33 from the dashboards that kube-prometheus ships (kubernetes-mixin, node-mixin, prometheus-mixin, alertmanager-mixin, grafana), and 24 from the compiled Mimir, Loki and Tempo mixins. They hold 2,179 panels, 3,970 queries (3,822 of them PromQL or LogQL), and 273 non-data-source variables (MEASURED).
2. Using the same tier definitions as P1 (T1 alert-translator reach, T2 plus rate/histogram_quantile/topk, T3 plus arithmetic), 26.5%, 50.7% and 62.5% of these panels convert (MEASURED). The classifier reproduces P1 on the one dashboard both notes share: Node Exporter Full gives 47, 91, 103 in P1 and 47, 91, 105 here. The numbers are higher than P1's 22/39/47 because 94% of this sample is Prometheus (P1 included 31 dashboards on other data sources that convert at 5% to 8%).
3. A fourth tier, "R2", counts panels whose every query uses only patterns for which section 4 gives a recipe, exact or approximate. 87.6% of panels reach R2 (MEASURED against my own recipe list, so read it as "has a recipe on paper", not "verified"). The 25-point gap between T3 and R2 is joins (`on()`/`group_left`), set operators (`or`, `and`, `unless`), comparison filters, `label_replace`, `*_over_time`, `vector(0)` fallbacks; most of it is approximate or needs a different design.
4. New Relic already publishes a machine translation of the same problem. Its query builder has a PromQL-style mode that "translates PromQL syntax queries into the closest NRQL approximation" and claims support for "over 99.5% of queries across the 7.8 million top Grafana dashboard downloads" (DOCS N6). It lists `and`, `or`, `unless`, `label_join`, `*_over_time`, `predict_linear`, `histogram_quantile`, `topk`, `offset` (one value for all vectors), `time()` and the date functions as supported (DOCS N6). The NRQL it produces is visible in the quickstart `kubernetes-prometheus-compute-resources`, a port of the kubernetes-mixin compute-resources dashboards (CODE Q `dashboards/kubernetes-prometheus-compute-resources/`). Whether that translator can be called outside the UI is UNVERIFIED; section 6 treats it as a source of recipes, not as a dependency.
5. Dashboards cannot be converted without variables. 95.8% of queries reference a variable (3,660 of 3,822, MEASURED; P1 had 89%), 67% of dashboards have a variable that depends on another variable (72 of 108), and 21% have a chain two deep or more (23 of 108). New Relic documents nested variables only for some plans (DOCS N10), and the same page family also says "nested variables are not supported" (DOCS N9). The IR has no usable answer yet (REPO `docs/ir-spec.md`, "A variable's own query may not use variables").
6. Of the 51 grafana.com dashboards, 10 (20%) have a New Relic quickstart on the same data path with enough metric-name overlap to install instead of convert; 18 (35%) have a quickstart only if collection changes (infrastructure agent, OpenTelemetry collector, AWS integration, or a different exporter); 19 (37%) are rebuild-with-NRQL; 4 (8%) are manual. Counting the 33 kube-prometheus dashboards (6 map to quickstarts) and the 24 Mimir/Loki/Tempo dashboards (none), 16 of 108 (15%) are better served by a quickstart on the same data, 18 more (17%) if the customer re-points collection.
7. Metric names are the main hazard for the quickstart route. Between the grafana.com dashboard and the quickstart that nominally replaces it, the share of metric names in common was 0% for MongoDB 2583, 0% for Kubernetes Cluster 6417 (kube-state-metrics v1 names), 7% for Node Exporter Full, 24% for K8S Dashboard 15661; it was 96% for the kubernetes-mixin kubelet dashboard, 81% for the node-mixin Nodes dashboard, 76% for ArgoCD, 100% for Traefik (MEASURED, section 5.2). A title match is not enough.
8. 36% of the kube-prometheus queries (212 of 581) and 9% of the Mimir queries (84 of 919) use recording-rule metrics (names with colons, such as `node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate`). Those series exist in New Relic only if the Prometheus that evaluates the rules also remote-writes them. This is a data prerequisite the importer cannot fix (MEASURED; the remote-write behaviour is UNVERIFIED).
9. Chart types in New Relic include Gauge, Heat map, Histogram, Bullet, Treemap, Funnel, JSON, Simplified bar (DOCS N8). No chart type is documented for state timeline, status history, node graph, geomap, canvas or xy chart. Those panels were 6 of 2,179 in this sample (state-timeline 2, nodeGraph 1, and 3 alertlist panels, which also have no equivalent), so the gap is small here; in the grafana/grafana devenv dashboards (P1 section 2.3) it is larger.
10. The recommended order is: variables and `IN ({{v}})` filters; the Prometheus-name-to-NRQL core (rate, ratio, `bucketPercentile`, `FACET ... ORDER BY ... LIMIT`); a quickstart map with a data-presence check; the nested-aggregation recipes; widget types (gauge, bullet, heatmap); and last the join and set-operator recipes behind a "convert-with-note" tag. Section 6 has sizes and risks.

## 2. Sources

### 2.1 grafana.com dashboards fetched (51)

URL pattern: `https://grafana.com/api/dashboards/<id>/revisions/<rev>/download`. I downloaded `revisions/latest/download`; the revision column is what `https://grafana.com/api/dashboards/<id>` reported on 2026-11-09. Downloads are the counter on grafana.com on the same day (CODE: grafana.com API). Panels, T3 and R2 are MEASURED (section 3).

| id | rev | downloads | title | panels | T3 | R2 |
|---|---|---|---|---|---|---|
| 1860 | 46 | 149,816,116 | Node Exporter Full | 124 | 85% | 93% |
| 14584 | 1 | 74,776,335 | ArgoCD | 41 | 83% | 90% |
| 9614 | 1 | 73,614,742 | NGINX Ingress controller | 12 | 58% | 100% |
| 7645 | 345 | 64,680,190 | Istio Control Plane Dashboard | 13 | 85% | 85% |
| 14981 | 2 | 59,182,497 | CoreDNS | 23 | 91% | 91% |
| 13659 | 1 | 53,087,055 | Blackbox Exporter (HTTP prober) | 3 | 67% | 67% |
| 10991 | 15 | 52,360,614 | RabbitMQ-Overview | 40 | 2% | 95% |
| 14055 | 5 | 48,781,413 | Loki stack monitoring (Promtail, Loki) | 18 | 61% | 83% |
| 12239 | 2 | 42,076,170 | NVIDIA DCGM Exporter Dashboard | 8 | 75% | 75% |
| 15661 | 2 | 35,468,982 | K8S Dashboard | 33 | 48% | 64% |
| 14361 | 1 | 33,434,010 | AWS ALB Cloudwatch Metrics | 10 | 0% | 0% |
| 969 | 6 | 33,092,129 | AWS ElastiCache Redis | 12 | 8% | 8% |
| 11265 | 2 | 32,498,205 | Amazon EC2 | 15 | 13% | 13% |
| 10441 | 2 | 31,667,288 | Keycloak Metrics Dashboard | 24 | 58% | 58% |
| 2583 | 2 | 31,647,078 | MongoDB | 16 | 94% | 100% |
| 4701 | 10 | 31,363,051 | JVM (Micrometer) | 27 | 89% | 100% |
| 13332 | 12 | 30,001,976 | kube-state-metrics-v2 | 42 | 74% | 100% |
| 584 | 6 | 29,467,411 | AWS SQS | 6 | 17% | 17% |
| 11264 | 2 | 28,278,976 | Amazon RDS | 14 | 7% | 7% |
| 11835 | 1 | 28,235,364 | Redis Dashboard for Prometheus Redis Exporter (helm stable/redis-ha) | 12 | 92% | 100% |
| 11159 | 1 | 26,954,878 | NodeJS Application Dashboard | 9 | 89% | 100% |
| 11454 | 14 | 24,640,764 | K8s / Storage / Volumes / Cluster | 20 | 25% | 95% |
| 11001 | 1 | 24,814,445 | cert-manager | 13 | 85% | 100% |
| 593 | 14 | 24,161,949 | AWS Lambda | 6 | 17% | 17% |
| 315 | 3 | 22,715,968 | Kubernetes cluster monitoring (via Prometheus) | 21 | 100% | 100% |
| 12009 | 2 | 22,510,814 | AWS Kafka Cluster | 4 | 0% | 0% |
| 6417 | 1 | 21,851,614 | Kubernetes Cluster (Prometheus) | 29 | 97% | 100% |
| 9578 | 4 | 21,777,615 | Alertmanager | 102 | 99% | 100% |
| 7362 | 5 | 21,146,303 | MySQL Overview | 36 | 22% | 100% |
| 4475 | 5 | 21,137,055 | Traefik | 7 | 100% | 100% |
| 8171 | 1 | 20,928,173 | Kubernetes Nodes | 8 | 100% | 100% |
| 14900 | 2 | 20,816,671 | Nginx | 20 | 50% | 75% |
| 7587 | 3 | 19,434,729 | Prometheus Blackbox Exporter | 10 | 90% | 100% |
| 13639 | 2 | 18,119,706 | Logs / App (Loki) | 2 | 0% | 0% |
| 741 | 1 | 16,428,748 | Kubernetes Deployment metrics | 16 | 100% | 100% |
| 747 | 2 | 15,777,897 | Kubernetes Pod Metrics | 18 | 100% | 100% |
| 11022 | 1 | 16,349,384 | Envoy Global | 17 | 100% | 100% |
| 6693 | 1 | 16,212,038 | Envoy Proxy | 13 | 92% | 100% |
| 9628 | 8 | 13,513,489 | PostgreSQL Database | 32 | 78% | 100% |
| 12485 | 1 | 13,063,734 | PostgreSQL Exporter | 35 | 80% | 86% |
| 12693 | 15 | 1,438,386 | HAProxy | 106 | 93% | 97% |
| 3662 | 2 | 6,822,734 | Prometheus 2.0 Overview | 30 | 67% | 100% |
| 16808 | 1 | 4,393,625 | AWS MSK - Kafka Cluster | 25 | 0% | 0% |
| 19734 | 11 | 724,905 | AWS Lambda Insights | 14 | 0% | 0% |
| 763 | 6 | 442,858 | Redis Dashboard for Prometheus Redis Exporter 1.x | 13 | 77% | 92% |
| 14643 | 2 | 349,172 | High level Sloth SLOs | 9 | 44% | 67% |
| 14348 | 5 | 158,474 | SLO / Detail (Sloth) | 12 | 25% | 67% |
| 14057 | 1 | 85,268 | MySQL Exporter Quickstart and Dashboard | 27 | 59% | 100% |
| 14370 | 6 | 48,829 | JVM Quarkus - Micrometer Metrics | 35 | 94% | 100% |
| 14012 | 1 | 383,738 | KMinion Cluster Dashboard (Kafka) | 8 | 88% | 88% |
| 7371 | 1 | 49,938 | MySQL Replication | 11 | 100% | 100% |

Largest files: Node Exporter Full 470 KB, HAProxy 376 KB, Alertmanager 308 KB, RabbitMQ-Overview 236 KB, K8S Dashboard 202 KB (MEASURED, bytes of the download).

### 2.2 Library repositories fetched (57 files)

GitHub stars on 2026-11-09 (CODE: `api.github.com/repos/<owner>/<repo>`).

| source | stars | what I took | files |
|---|---|---|---|
| prometheus-operator/kube-prometheus | 7,758 | `manifests/grafana-dashboardDefinitions.yaml` (1,053,739 bytes; ConfigMaps holding the compiled kubernetes-mixin, node-mixin, prometheus-mixin, alertmanager-mixin and grafana dashboards), split into 33 JSON files | 33 |
| kubernetes-monitoring/kubernetes-mixin | 2,493 | its dashboards are the "k8s-resources-*", "kubelet", "apiserver", "scheduler", "proxy", networking and persistent-volume files inside the kube-prometheus ConfigMap above (the repository itself holds jsonnet; its `metrics.txt` and rules are not part of the measurement) | in the 33 |
| prometheus/node_exporter (node-mixin under `docs/node-mixin`) | 13,915 | `nodes`, `nodes-darwin`, `nodes-aix`, `node-rsrc-use`, `node-cluster-rsrc-use` via kube-prometheus | in the 33 |
| grafana/mimir | 5,277 | `operations/mimir-mixin-compiled/dashboards/`: overview, reads, writes, queries, scaling, compactor, ruler, tenants, slow-queries, rollout-progress, object-store | 11 |
| grafana/loki | 29,138 | `production/loki-mixin-compiled/dashboards/`: operational, reads, writes, logs, chunks, retention, deletion | 7 |
| grafana/tempo | 5,567 | `operations/tempo-mixin-compiled/dashboards/`: operational, reads, writes, resources, tenants, service-graph | 6 |
| slok/sloth | 2,569 | via grafana.com 14643 and 14348 (2.1) | - |
| rfmoz/grafana-dashboards | 1,785 | source repository of Node Exporter Full; I used the grafana.com copy | - |
| newrelic/newrelic-quickstarts | 128 | 558 dashboard JSON files (all of `dashboards/`), `config.yml` of 45 quickstarts | 558 + 45 |
| pyrra-dev/pyrra | 1,571 | not fetched: grafana.com search for "pyrra" returned nothing and I did not look in the repository | 0 |

Not covered: grafana/grafana devenv dashboards (P1 measured those), the Docker/cAdvisor exporter dashboards 893 and 1229 (P1 has them), Explore-style Loki dashboards beyond 13639 and the Loki mixin, and any dashboard that exists only inside a private Grafana. The kube-prometheus file is from its `main` branch on 2026-11-09, so it shows the mixins as built then.

Pre-existing evidence on what is used most: Node Exporter Full has 149.8 million downloads, almost twice the next dashboard (ArgoCD, 74.8 million) (CODE: grafana.com API). The top 60 by downloads are 49 Prometheus, 9 CloudWatch, 1 Loki, 1 Loki+Prometheus (MEASURED from `api/dashboards?orderBy=downloads&pageSize=60`, counted by the `datasource` field the API reports).

## 3. Measurements

### 3.1 Method

Scripts are under `/tmp/gdrs` and are not committed (the brief allows one new file). The measuring script is about 330 lines of Python:

- Load each file, unwrap `dashboard` or `spec` envelopes, flatten panels including row-nested panels and legacy `rows[]`.
- Resolve the data source of each target from the target, the panel, the `__inputs` block and the dashboard's data-source variables.
- Strip string literals and `{...}` matcher bodies from every PromQL expression, then test about 30 regular expressions for the patterns in 3.5 (so a label value containing "or" does not count). A regex classifier misses some nesting and mislabels rare constructs; read counts as accurate to a few percent.
- Tiers T1, T2, T3 are defined as in P1 section 8.2: only convertible panel types (timeseries, graph, stat, singlestat, table, table-old, piechart, text), no transformations, no library panel, every target Prometheus, and the union of patterns inside the tier's allowed set (T1: aggregation and regex matchers; T2: plus `rate`, `irate`, `increase`, `histogram_quantile`, `topk`/`bottomk`, `$__rate_interval`, `$__interval`, `$__range`; T3: plus arithmetic and `count()`).
- R2 is new here: T3 plus `comparison`, `bool`, `or`, `and`, `unless`, `on()/ignoring()`, `group_left/right`, `label_replace/join`, `*_over_time`, `vector()`, `scalar()`, `sort`, `clamp/round/ceil`, `time()`, `without`, `quantile()`, `changes/resets`, `predict_linear/deriv/delta`. It leaves out subqueries, `offset`, `@`, `absent`, `count_values`, native histograms and anything not matched, so a panel in R2 has a recipe in section 4, exact or approximate. R2 was not verified by running any query.
- Sanity check against P1: Node Exporter Full gives T1/T2/T3 = 47/91/105 here and 47/91/103 in P1. The two-panel difference is regex versus P1's parser on `count` shapes.
- CloudWatch targets (143 of 3,970) have no `expr` and never convert here; P1 says the same of CloudWatch.

### 3.2 The corpus

| family | what is in it | dashboards | panels | queries | PromQL/LogQL exprs |
|---|---|---|---|---|---|
| node | Node Exporter Full (1860) | 1 | 124 | 301 | 301 |
| kp-node-mixin | node-mixin from kube-prometheus (nodes, darwin, aix, USE node, USE cluster) | 5 | 42 | 72 | 72 |
| k8s-community | 315, 6417, 13332, 11454, 8171, 741, 747, 15661 | 8 | 187 | 332 | 332 |
| kp-kubernetes-mixin | kubernetes-mixin: compute resources, networking, kubelet, apiserver, scheduler, proxy, controller-manager, persistent volumes, Windows | 24 | 264 | 464 | 464 |
| kp-prom-am-grafana | prometheus-mixin, prometheus remote write, alertmanager-mixin, grafana overview | 4 | 34 | 45 | 45 |
| mimir-mixin | Mimir compiled mixin | 11 | 350 | 919 | 919 |
| loki-mixin | Loki compiled mixin | 7 | 149 | 227 | 227 |
| tempo-mixin | Tempo compiled mixin | 6 | 139 | 266 | 266 |
| jvm-app | 4701, 14370, 11159 | 3 | 71 | 131 | 131 |
| db-queue-exporters | 7362, 9628, 12485, 14057, 7371, 763, 11835, 2583, 10991, 12693, 14900, 14012, 12009 (12009 is CloudWatch) | 13 | 360 | 557 | 552 |
| proxy-mesh-misc | ArgoCD, NGINX Ingress, Istio, CoreDNS, Blackbox x2, Loki stack, DCGM, cert-manager, Keycloak, Prometheus 2.0, Traefik, Alertmanager, Envoy x2 | 15 | 334 | 488 | 488 |
| cloudwatch | 11264, 11265, 14361, 16808, 19734, 584, 593, 969 | 8 | 102 | 143 | 0 |
| slo | Sloth 14643, 14348 | 2 | 21 | 23 | 23 |
| loki-logs | 13639 | 1 | 2 | 2 | 2 |
| all | | 108 | 2,179 | 3,970 | 3,822 |

(All rows MEASURED.) Panel types, all 2,179: timeseries 1,165; graph (legacy) 516; singlestat (legacy) 186; stat 111; table 68; gauge 31; bargauge 25; text 22; heatmap 17; logs 14; piechart 7; table-old 6; grafana-piechart-panel 4; alertlist 3; state-timeline 2; barchart 1; nodeGraph 1. Legacy Angular types are 712 panels (33%). Query data sources: Prometheus 3,637; CloudWatch 148; unresolved `${DS_...}` placeholders with a PromQL expression 147 (counted as Prometheus in the shape numbers); Loki 38. `schemaVersion` of the files: 39 (36 files), 14 (24), 16 (7), 26 (6), 41 (5), then 21, 27, 40 (4 each).

### 3.3 Tier results per family

Share of panels (not of queries) converting at each tier. T3 and R2 as defined in 3.1.

| family | panels | T1 | T2 | T3 | R2 |
|---|---|---|---|---|---|
| node (1860) | 124 | 37.9% | 73.4% | 84.7% | 92.7% |
| kp-node-mixin | 42 | 2.4% | 9.5% | 38.1% | 85.7% |
| k8s-community | 187 | 54.5% | 59.4% | 76.5% | 93.0% |
| kp-kubernetes-mixin | 264 | 21.2% | 43.9% | 61.0% | 84.1% |
| kp-prom-am-grafana | 34 | 38.2% | 58.8% | 76.5% | 97.1% |
| mimir-mixin | 350 | 9.4% | 25.4% | 36.0% | 91.4% |
| loki-mixin | 149 | 10.7% | 38.3% | 62.4% | 89.3% |
| tempo-mixin | 139 | 18.7% | 53.2% | 64.0% | 97.8% |
| jvm-app | 71 | 52.1% | 70.4% | 91.5% | 100% |
| db-queue-exporters | 360 | 32.2% | 61.1% | 66.9% | 94.2% |
| proxy-mesh-misc | 334 | 35.6% | 78.1% | 84.7% | 92.8% |
| cloudwatch | 102 | 5.9% | 5.9% | 5.9% | 5.9% |
| slo | 21 | 23.8% | 23.8% | 33.3% | 66.7% |
| loki-logs | 2 | 0% | 0% | 0% | 0% |
| all | 2,179 | 26.5% | 50.7% | 62.5% | 87.6% |

Counting the extra widget types (gauge, bargauge, barchart, heatmap) as convertible when their queries are: T3 65.0%, R2 91.0% (MEASURED). Gauge, bar gauge and heat map are 73 panels (3.4%).

### 3.4 Comparison with P1

| measure | P1 (69 dashboards, 1,404 panels) | this sample (108 dashboards, 2,179 panels) |
|---|---|---|
| T1 / T2 / T3, all | 21.8 / 38.9 / 47.2% | 26.5 / 50.7 / 62.5% |
| T1 / T2 / T3, Prometheus-heavy part | 31.5 / 57.4 / 69.5% (38 dashboards, 899 panels) | 27.5 / 52.9 / 65.2% (all but the 8 CloudWatch-family dashboards) |
| queries that reference a variable | 89% of targets with `expr` | 95.8% (3,660 of 3,822) |
| variables per dashboard | 3.3 (227 / 69) | 3.3 (361 / 108); 2.5 without data-source variables (273 / 108) |
| panels with overrides | 12.6% (177) | 14.3% (311) |
| panels with value mappings | 3.6% (51) | 9.5% (206, includes legacy `valueMaps`) |
| panels with transformations | 3.6% (50) | 2.6% (56) in 31 dashboards |
| panels that repeat | 30 (2.1%) | 35 (1.6%), plus 10 repeating rows; 11 dashboards use repeat |
| library panels | 0 | 0 |

The Prometheus-heavy T3 is lower here (65.2% against 69.5%) because this sample carries the Mimir mixin (36.0% at T3) and the node-mixin (38.1%), both full of comparisons and joins. P1's selection was community dashboards by download count. The two samples agree on the main conclusion: about half of Prometheus panels convert with rate, quantile, topk and arithmetic, and the rest need joins or set logic.

### 3.5 Query shapes

Panels (not queries) containing at least one query with the pattern, as a share of panels that have a PromQL/LogQL query, by family (MEASURED; the last column is any of: label_replace, or/and/unless, group_left, on/ignoring, subquery, offset, `@`, `*_over_time`, predict_linear/deriv/delta, changes/resets, vector, scalar, time, absent, comparison, bool, quantile, without).

| family | panels with expr | rate / irate / increase | histogram_quantile | topk | label_replace/join | arithmetic | comparison / bool | or / and / unless | on() / group_* | subquery / offset / @ | over_time / predict | vector / scalar / time | any hard |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| node | 124 | 46% | 0 | 0 | 0 | 22% | 6% | 0 | 2% | 0 | 0 | 2% | 10% |
| kp-node-mixin | 42 | 29% | 0 | 0 | 0 | 52% | 36% | 0 | 7% | 0 | 0 | 14% | 48% |
| k8s-community | 183 | 13% | 0 | 0 | 0 | 30% | 13% | 7% | 2% | 0 | 6% | 3% | 18% |
| kp-kubernetes-mixin | 263 | 57% | 7% | 2% | 0 | 54% | 0 | 0 | 25% | 0 | 0 | 2% | 29% |
| kp-prom-am-grafana | 34 | 47% | 6% | 0 | 0 | 26% | 0 | 15% | 3% | 0 | 0 | 3% | 18% |
| mimir-mixin | 321 | 75% | 32% | 2% | 13% | 53% | 52% | 53% | 49% | 1% | 2% | 46% | 63% |
| loki-mixin | 138 | 70% | 30% | 4% | 10% | 36% | 12% | 4% | 3% | 1% | 1% | 1% | 30% |
| tempo-mixin | 138 | 70% | 23% | 3% | 9% | 22% | 19% | 7% | 1% | 1% | 0 | 0 | 36% |
| jvm-app | 71 | 30% | 0 | 0 | 0 | 23% | 0 | 0 | 1% | 0 | 7% | 0 | 8% |
| db-queue-exporters | 356 | 48% | 0 | 2% | 0 | 23% | 4% | 9% | 12% | 0 | 5% | 1% | 29% |
| proxy-mesh-misc | 329 | 54% | 6% | 0 | 0 | 12% | 2% | 0 | 2% | 0 | 2% | 2% | 7% |
| slo | 21 | 0 | 0 | 0 | 0 | 14% | 14% | 14% | 43% | 10% | 10% | 43% | 48% |
| all | 2,020 | 52% | 11% | 1% | 3% | 32% | 14% | 12% | 15% | 0.3% | 3% | 9% | 29% |

Absolute counts over all panels (panels, queries, dashboards of 108 that contain it), MEASURED: aggregation `sum/avg/min/max` 1,289 / 2,567 / 94; `rate` 916 / 1,812 / 71; `$__rate_interval` 716 / 1,531 / 49; arithmetic 647 / 1,016 / 86; `on()` or `ignoring()` 301 / 856 / 36; comparison operator 279 / 832 / 32; `histogram_quantile` 216 / 616 / 28; `vector()` 156 / 640 / 13; `and` 153 / 638 / 10; `group_left`/`group_right` 146 / 221 / 25; `increase` 100 / 173 / 16; `or` 93 / 153 / 19; `$__interval` 78 / 137 / 6; `irate` 73 / 130 / 17; `label_replace`/`label_join` 68 / 96 / 15; `count()` 50 / 72 / 25; `*_over_time` 37 / 48 / 17; LogQL 34 / 40 / 7; `sort` 25 / 33 / 6; `topk`/`bottomk` 25 / 28 / 9; `time()` 20 / 22 / 16; `scalar()` 15 / 27 / 6; `unless` 14 / 18 / 4; `clamp`/`round`/`ceil` 13 / 17 / 10; `without` 13 / 16 / 8; `predict_linear`/`deriv`/`delta` 12 / 12 / 5; subquery 5 / 9 / 4; `bool` 5 / 7 / 4; `changes`/`resets` 3 / 3 / 2; `offset` 2 / 5 / 2.

Structure (MEASURED over the 3,822 expressions): 703 (18%) contain two or more aggregation operators (`sum(... sum by (...) (...))` and similar), 169 contain three or more; 1,349 (35%) are an aggregation of a `rate`/`irate`/`increase`; 443 (12%) call `rate`, `irate` or `increase` with no aggregation at all (per-series rates); no `by (...)` list has more than five labels; 449 of 3,970 targets are instant queries and 300 use `format: table`; 3,267 (82%) have a `legendFormat` or alias.

Recording rules: 212 of 581 kube-prometheus expressions (36%, 129 of 375 panels), 84 of 919 Mimir expressions (9%), 46 of 227 Loki expressions (20%), 16 of 1,829 grafana.com expressions (0.9%), 0 of 266 Tempo expressions use a metric name with a colon (MEASURED; a name pattern, so a few false positives are possible).

### 3.6 Everything else on the panel and dashboard

All MEASURED over the 108 files.

| feature | count | note |
|---|---|---|
| rows | 455 (76 collapsed, 10 repeating) | New Relic has pages, not rows (section 4.15) |
| panels that repeat | 35 (in 11 dashboards with repeating rows) | |
| library panels | 0 | grafana.com inlines them |
| panels with transformations | 56 in 31 dashboards | `organize` 48, `joinByField` 31, `merge` 10, `calculateField` 10, `filterFieldsByName` 9, `renameByRegex` 7, `sortBy` 7, `labelsToFields` 3, `groupBy` 3, `seriesToColumns` 2, one each `extractFields`, `convertFieldType`, `filterByValue`; 30 of the 56 are in kubernetes-mixin tables |
| panels with field overrides | 311 (14.3%) | matchers: `byName` 675, `byRegexp` 437, `byFrameRefID` 23, `byValue` 4; properties: `color` 774, `unit` 117, `custom.width` 96, `custom.fillOpacity` 68, `displayName` 64, `custom.lineStyle` 62, `custom.transform` 55, `decimals` 32, `links` 27, `custom.hidden` 21 |
| panels with legacy `seriesOverrides` | 167 | |
| panels with value mappings | 206 (9.5%) | new style: special 24, value 7, range 4, legacy numeric 6; the rest are legacy `valueMaps` and `rangeMaps` on singlestat |
| panels with threshold steps | 386 (17.7%) | |
| legend with calcs or values (`mean`, `max`, `last`) | 696 (31.9%) | lost in New Relic (P1 4.2) |
| legend shown as a table | 455 (20.9%) | |
| multi-target panels | 790 (36.2%) | |
| hidden targets | 25 | |
| targets with `legendFormat` or alias | 3,267 of 3,970 (82%) | |
| instant targets | 449 | |
| `format: table` / `format: heatmap` targets | 300 / 13 | |
| panel links / dashboard links | 34 / 82 (59 dashboards) | |
| custom annotation queries | 8 (7 dashboards) | |
| panel time override (`timeFrom`, `timeShift`) | 13 | |
| units used, top | `short` 633, `s` 256, `bytes` 222, `none` 189, `percentunit` 144, `ops` 102, `reqps` 72, `percent` 70, `bps` 62, `pps` 58, `ms` 49, `Bps` 33, `decbytes` 31 | |

Variables, 361 in total (MEASURED): query 229, datasource 88 (in 82 dashboards), custom 20, interval 12, textbox 9, constant 3. 66 are multi-value, 105 include "All", 13 use a `regex`. The 229 query variables are `label_values(...)` 172, `query_result(...)` 8, other forms 49. Chain depth, counting a variable that references another non-data-source variable in its query: no chain 36 dashboards, depth 1 49, depth 2 19, depth 3 4. 107 query variables reference another variable. The deepest chains are in kubernetes-mixin: `cluster` -> `namespace` -> `type` -> `workload` (depth 3 in `k8s-resources-workload`; the last two use the recording-rule series `namespace_workload_pod:kube_pod_owner:relabel`) (CODE: kube-prometheus ConfigMap).

### 3.7 What New Relic's own quickstart dashboards use (MEASURED over 558 files)

All 558 dashboard JSON files in `dashboards/` of newrelic-quickstarts, 13,495 widgets, 207 variables (NRQL 192, ENUM 15). Counts are queries and dashboards that contain the feature (regex over the query text). A feature that appears here is accepted by New Relic in a dashboard widget, because quickstart dashboards are installed as dashboards; a feature that does not appear is not thereby unsupported.

| feature | queries | dashboards |
|---|---|---|
| `FROM Metric` | 5,003 | 355 |
| `TIMESERIES` | 6,036 | 539 |
| `TIMESERIES AUTO` | 2,120 | 259 |
| `LIMIT MAX` | 1,607 | 136 |
| `SINCE`/`UNTIL` inside the widget query | 5,651 | 287 |
| `{{variable}}` | 2,192 | 89 |
| `COMPARE WITH` | 931 | 133 |
| `rate(` | 748 | 137 |
| `percentile(` | 587 | 111 |
| `filter(` | 392 | 62 |
| `FACET CASES` | 186 | 37 |
| nested aggregation, `FROM (SELECT ...)` | 189 | 31 |
| `cardinality(` | 185 | 14 |
| subquery in `WHERE` or `SELECT` | 161 | 15 |
| `SLIDE BY` | 90 | 11 |
| `dimensions()` / `FACET dimensions()` | 82 / 81 | 8 / 7 |
| `if(` | 78 | 23 |
| `capture(` | 74 | 11 |
| `histogram(` | 57 | 27 |
| `aparse(` | 38 | 7 |
| `conditional(` | 31 | 2 |
| `irate(` | 22 | 2 |
| `derivative(` | 24 | 6 |
| `bucketPercentile(` (case-insensitive) | 15 | 4 |
| `JOIN (` | 13 | 2 (both on `NrConsumption`, none on `Metric`) |

Visualisation ids used: `viz.line` 4,256; `viz.billboard` 2,927; `viz.markdown` 1,331; `viz.table` 1,230; `viz.area` 1,180; `viz.bar` 1,060; `viz.pie` 716; `viz.stacked-bar` 490; a custom range chart 100; log table 62; stacked-bar custom 49; `viz.heatmap` 33; `viz.histogram` 24; `viz.bullet` 22; `viz.funnel` 14; `viz.json` 1. No gauge, treemap, or state widget id appears. Metric dashboards for Prometheus data put their widgets on pages (kubernetes-prometheus-compute-resources has three pages: By Node, By Pod, By Namespace) (CODE Q).

## 4. NRQL rebuild recipes

### 4.0 What works in dashboard widgets

Read this before the recipes. "Dashboards" means a widget query in a custom dashboard; alert conditions differ and are out of scope here.

| feature | what the documentation says | in dashboards |
|---|---|---|
| `FACET ... LIMIT MAX` | "The maximum allowed value for the `LIMIT` clause is 5,000. Queries can use the `LIMIT MAX` clause" (DOCS N1, `sel-limit`). `FACET` supports up to five attributes (DOCS N1, `sel-facet`). | yes (CODE Q: 1,607 queries) |
| `FACET ... ORDER BY aggregate` | overrides how facets are chosen before `LIMIT`; "can be used in querying but not for alerts or streaming" (DOCS N1, `sel-facet-order`) | yes |
| `FACET CASES` | breaks results by conditions (DOCS N1, `sel-facet-cases`) | yes (CODE Q) |
| `TIMESERIES AUTO` / `MAX` | `AUTO` "will divide your graph into a reasonable number of divisions" (DOCS N1, `sel-timeseries`) | yes (CODE Q). The IR has `auto_period` (REPO `docs/ir-spec.md`) |
| `SLIDE BY` | place after `TIMESERIES`; `MAX` or `AUTO` allowed after `SLIDE BY`, not between (DOCS N1, `slide-by`) | yes (CODE Q: argocd, singlestore) |
| `COMPARE WITH` | needs `SINCE` or `UNTIL`; line chart with `TIMESERIES`, billboard without it (DOCS N1, `sel-compare`) | yes in widgets (CODE Q: 931 queries). How it behaves with the dashboard time picker is UNVERIFIED. The IR's raw-NRQL checker refuses it today (REPO `docs/ir-spec.md`) |
| Subquery in `WHERE`/`SELECT` | up to three per query; "Subqueries aren't supported in NRQL alert conditions"; `TIMESERIES` and `COMPARE WITH` not allowed inside; not in `FACET`; an inner query runs on its own and cannot see the outer query (DOCS N2) | yes (CODE Q: 161 queries) |
| Nested aggregation `SELECT ... FROM (SELECT ...)` | `TIMESERIES` and `FACET` allowed at any level; `SINCE`, `UNTIL`, `COMPARE WITH` only at the outermost level (DOCS N4) | yes (CODE Q: 189 queries; kubernetes-prometheus-compute-resources) |
| `JOIN` | `FROM Event [INNER\|LEFT] JOIN (subquery) ON key SELECT ...`; the joined subquery keeps a default `LIMIT 10` (maximum 5,000), cannot use `TIMESERIES` or `COMPARE WITH` (it yields one result for the whole window), is limited to 1:100 cardinality, equality only, "The JOIN key cannot be a complex attribute, like a metric value", "The subquery cannot be a metric row-wise query", "Like all subqueries, joined subqueries cannot be used in alert conditions" (DOCS N3, N1 `sel-join`) | allowed in widgets; the only two quickstart dashboards that use it join `NrConsumption`, none joins `Metric` (CODE Q). `JOIN` on `Metric` data is UNVERIFIED |
| `WITH ... AS` row-wise variables | "Only row-wise functions (for example, capture()) can be set as a variable" (DOCS N1, `with-as-nrql-var`) | yes (CODE Q: 80 queries) |
| Template variables | usable in `SELECT`, `FROM`, `FACET`, `ORDER BY`, `WHERE`; not in `AS`, `COMPARE WITH`, `LIMIT`, `OFFSET`, `SINCE`, `SLIDE BY`, `TIMESERIES`, `UNTIL`, `WITH` (DOCS N9). Multi-value needs `IN`. "Include variable" off replaces the condition with `true`. Output format: string, number, identifier | yes. The IR already enforces the clause list (REPO `docs/ir-spec.md`) |
| Nested variables | a variable query may reference another variable (`WHERE env IN ({{env}})`); "available only to Pro, Enterprise, and Core Compute customers" (DOCS N10). N9 still says "nested variables are not supported". The two pages disagree | plan-dependent; UNVERIFIED for the owner's account |
| `PREDICT` clause | forecast on line charts (DOCS N1, `sel-prediction`); not allowed with `COMPARE WITH` | yes in charts; not a PromQL `predict_linear` equivalent |

### 4.1 Data shape of remote-written Prometheus data

DOCS (from P2 section 6.1, re-checked against N5 and N7): remote write stores counters as deltas ("the Prometheus counter is a cumulative sum while the New Relic count is a delta sum"), `*_bucket`, `*_count`, `*_total` as `count`, `*_sum` as `summary`, anything else as `gauge`; each label is an attribute with the same name; the metric name is the aggregation argument, and `metricName` is an attribute (`FROM Metric SELECT keyset() WHERE metricName = 'container_memory_usage_bytes'`, N5). The attribute `prometheus_server` is the label you set in the remote-write URL. The Kubernetes OpenMetrics integration uses other attribute names (`clusterName`, `podName`, `namespaceName`, N7); quickstarts for remote-write data use the original label names and filter `instrumentation.name = 'remote-write'` (CODE Q `argocd-dashboard`, `velero-prometheus`). Choose attribute names from the customer's data path, not from the dashboard.

Rules applied in every recipe below: a Prometheus name with a colon or other non-identifier character is written in backticks in NRQL (the repository's `identifier()` already does this, REPO `core/emit/nrql.py`); a Prometheus regex matcher is `RLIKE`, which "defaults to full-string matching" like PromQL (DOCS N1); `=~"a|b"` with plain words is `IN ('a','b')`; `!~` is `NOT RLIKE`; a multi-value variable is `IN ({{v}})`, never `=`.

### 4.2 Aggregation

| PromQL | NRQL | exactness |
|---|---|---|
| `max by (instance) (g)` | `SELECT max(g) FROM Metric FACET instance` | exact |
| `avg(g)` over series | `SELECT average(g) FROM Metric` | exact when every series has the same number of samples in the bucket (same scrape interval); otherwise approximate |
| `sum(g)` over series, gauge | `SELECT average(g) * cardinality(g) FROM Metric` | approximate. `sum(g)` over a bucket with several scrapes adds every sample, so a gauge is summed once per scrape. Quickstarts avoid this with `average(x) * cardinality(x)` (CODE Q `kubernetes-prometheus-compute-resources`: "Current Network Usage") or a narrow `SINCE 2 MINUTES AGO UNTIL 1 MINUTE AGO` window. `cardinality()` is documented in N1 (`func-cardinality`). |
| `count(g)`, `count by (a) (g)` | `SELECT cardinality(g) FROM Metric [FACET a]` or `uniqueCount(label)` | exact when the labels identify series |
| `sum(c)` over series, counter, total | `SELECT sum(c)` | exact because counters are stored as deltas (DOCS N5) |
| `quantile(0.9, g)` (aggregation across series) | `SELECT percentile(g, 90)` | approximate: `percentile` is over samples, not over series; N6 lists `quantile()` as supported by the PromQL translator |
| more than five `by` labels | not found in the sample (maximum is 5) | n/a |
| `stddev`, `stdvar` | `stddev(g)`; `stdvar()` is in N1 | exact |

### 4.3 Rate family and windows

| PromQL | NRQL | exactness |
|---|---|---|
| `sum(rate(c[5m]))` | `SELECT rate(sum(c), 1 second) FROM Metric TIMESERIES` | exact in the units; the window is the `TIMESERIES` bucket, not 5m (DOCS N5: `rate(sum(http_request_total), 1 second) ... TIMESERIES 1 minute`) |
| `sum by (h) (rate(c[30s]))` | `SELECT rate(sum(c), 1 second) FROM Metric FACET h TIMESERIES` | exact in the units. DOCS N5 translates this very query to `rate(sum(http_requests_total), 30 seconds)`, which by the definition of `rate()` (DOCS N1 `func-rate`, "rate ... per time interval") reports a count per 30 seconds, not per second. Keep `1 second` so the value matches Grafana's per-second number |
| `increase(c[$__range])` | `SELECT sum(c)` over the dashboard window, no `TIMESERIES` | close: counters are deltas, so `sum` is the exact total, where Prometheus extrapolates |
| `irate(c[5m])` | `irate(c, 1 SECONDS)` inside a nested query (CODE Q: `SELECT irate(container_network_receive_bytes_total, 1 SECONDS) AS __result_0 ... FACET dimensions() LIMIT max TIMESERIES 1 minute`) | approximate. `irate` is not in the N1 function list I read; it is used in quickstarts and listed as supported by N6 |
| `rate(c[5m])` with no aggregation (per-series) | `FROM (SELECT rate(sum(c), 1 second) AS r FROM Metric FACET dimensions() LIMIT MAX TIMESERIES 1 minute) SELECT ... FACET <labels> TIMESERIES 1 minute` | exact; `FACET dimensions()` "produces a unique time series for all facets ... similar to how Prometheus behaves with non-aggregated queries" (DOCS N1, `func-dimensions`); 443 sample queries (12%) are this shape |
| `$__rate_interval`, `$__interval` | `TIMESERIES AUTO` | approximate. A variable cannot go in `TIMESERIES` (DOCS N9). Smoothing that a long range window gave: `TIMESERIES 5 minutes SLIDE BY 1 minute` (DOCS N1, `slide-by`; CODE Q argocd) |
| `$__range` | the dashboard time window itself | exact |
| `[1h]` window in `avg_over_time` and similar | `TIMESERIES 1 hour SLIDE BY 5 minutes` | approximate, see 4.11 |
| `rate()` of a gauge, wrong type | do not convert | N6: "our implementation doesn't handle these functions on the wrong data type and will produce different or incorrect answers" |

`TIMESERIES` bucket count: N6 states the PromQL mode enforces 366 steps; the NRQL limit for widgets is not stated on the pages I read (UNVERIFIED).

### 4.4 Ratios and arithmetic between series

| PromQL | NRQL | exactness |
|---|---|---|
| `sum(rate(a[5m])) / sum(rate(b[5m]))` | `SELECT sum(a) / sum(b) FROM Metric TIMESERIES` (rate cancels) | exact |
| `sum(rate(a{code=~"5.."}[5m])) / sum(rate(a[5m]))` | `SELECT filter(sum(a), WHERE code RLIKE '5..') / sum(a) FROM Metric` | exact; `filter()` is documented (DOCS N1, `func-filter`), `percentage()` only works with `count()`-style conditions |
| `(f - u) / 1024` | `SELECT (latest(f) - latest(u)) / 1024 FROM Metric TIMESERIES` | exact (DOCS N5 example) |
| `x / on (instance) y` with the same label sets | one query: `SELECT latest(x) / latest(y) FROM Metric FACET instance` | exact when both metrics carry the facet labels |
| mixed counter and gauge | `SELECT rate(sum(c), 1 second) / latest(g) ... FACET pod` | exact if labels match (CODE Q: "Cluster-wide CPU Requests Commitment" divides a gauge sum by an `average(...) * cardinality(...)`) |
| scalar math `100 - x * 100` | same in the `SELECT` list | exact |
| series with different label sets, scalar vs vector, `* on() group_left()` with `bool` | see 4.9 | approximate or not possible |

### 4.5 `histogram_quantile` and heat maps

`histogram_quantile(0.95, sum by (le, svc) (rate(x_bucket[5m])))` becomes

```sql
SELECT bucketPercentile(x_bucket, 95) FROM Metric FACET svc TIMESERIES AUTO
```

`bucketPercentile()` "is the NRQL equivalent of the histogram_quantile function in Prometheus ... Instead of the quantile, New Relic returns the percentile, which is the quantile * 100"; pass the `_bucket` metric name; several percentiles in one call, `bucketPercentile(x_bucket, 50, 75, 90)` (DOCS N1, `func-bucket-percentile`). CODE Q shows the shapes in use: `bucketpercentile(argocd_app_reconcile_bucket, (100 * 0.95)) ... FACET concat(dest_server,': 95%') LIMIT 100 TIMESERIES 300000 SLIDE BY 60000` (the interval is in milliseconds there) and `bucketpercentile(raft_process_logcommit_latency_bucket, {{percentile}})/1000000` (the percentile is a variable). Exact in the sense that the bucket boundaries are the same; whether the interpolation inside a bucket is the same as Prometheus's linear interpolation is UNVERIFIED. The percentile argument may not be a template variable in `TIMESERIES`, but may be in the function argument (the cockroach quickstart does it). Native histograms and `histogram_count` are not covered (N6 does not list them).

Prometheus heat maps (`format: heatmap`, `sum by (le) (increase(x_bucket[$__interval]))`, 13 targets in the sample): `SELECT histogram(x_bucket, width: 0.5, buckets: 20) FROM Metric TIMESERIES` with the heat map widget (DOCS N1 `histogram-prometheus`: "`histogram()` accepts Prometheus histogram buckets"; DOCS N8 heat map, "SELECT histogram(duration, width:10, buckets:20) FROM PageView SINCE 1 month ago TIMESERIES"). The `histogram()` buckets are linear (`width`, `buckets` 1 to 500, `start`); Prometheus `le` boundaries are usually exponential, so the conversion is approximate and needs the bucket boundaries from the data (`SELECT uniques(le)`), which the importer does not have at conversion time. The quickstarts use `viz.heatmap` in 33 widgets (CODE Q).

### 4.6 `topk`, `bottomk`, `sort`

`topk(5, sum by (route) (rate(c[5m])))`:

```sql
SELECT rate(sum(c), 1 second) FROM Metric FACET route ORDER BY sum(c) LIMIT 5 TIMESERIES AUTO
```

The ordering clause matters. DOCS N1 (`sel-facet`): when the first function is `min()`, `max()`, `percentile()`, `average()` or `count()` the facets are picked by it; "When selecting any other function, FACET uses the frequency of the attribute you are faceting on". `rate()` and `sum()` are "any other function", so a plain `FACET route LIMIT 5` picks by frequency, not by value. `FACET route ORDER BY sum(c) LIMIT 5` (DOCS N1, `sel-facet-order`) picks by value. With `TIMESERIES` the top five are chosen over the whole window, where `topk` picks them at every step: approximate. `bottomk(n, ...)`: DOCS do not give an ascending form of `FACET ... ORDER BY` ("you can't change the sort order by adding ASC and DESC"), so `bottomk` has no recipe (UNVERIFIED workaround: negate the value). `sort`/`sort_desc` only order a table: `ORDER BY` on a table widget. N6 says the PromQL mode returns 100 series unless a `topk()` is written; for NRQL, a `FACET` without `LIMIT` returns 10.

### 4.7 `legendFormat` and series names

| Grafana | New Relic |
|---|---|
| `legendFormat: "{{status}}"` | `FACET status`; the legend shows the facet value |
| `"{{a}}-{{b}}"` or `"{{ns}}/{{pod}}"` | `FACET concat(ns, '/', pod)` (CODE Q: `FACET if(pod is null, '', concat(namespace, '/', pod))`) or `FACET a, b` (up to five attributes) |
| a fixed text such as `"writes"` | `AS 'writes'`. DOCS N1 (`sel-as`): "`AS` clause labels in time series charts will not be displayed if a `FACET` clause is used" |
| `{{__name__}}` | not available per series; facet on `metricName` |
| overrides that rename or recolour a series by name | partial: `colors.series_overrides` and `units.series_overrides` exist in the provider (P1 4.2); a name that depends on a label value is not known at conversion time |

### 4.8 `label_replace`, `label_join`

Measured shapes (CODE Loki mixin `loki-chunks`, `loki-operational`, `loki-reads`): `label_replace(label_replace(rate(m[$__rate_interval]), "status", "${1}xx", "status_code", "([0-9]).."), "status", "${1}", "status_code", "([a-zA-Z]+)")`, grouped by `status`.

| intent | NRQL | exactness |
|---|---|---|
| keep the first digit and add `xx` | `FACET concat(substring(status_code, 0, 1), 'xx') AS status` | UNVERIFIED on `Metric` attributes. `substring()` and `concat()` are documented row-wise functions (DOCS N1, `func-substring`, `func-concat`); quickstarts facet on `concat(...)` and `if(...)` over `Metric` attributes (CODE Q) |
| a fixed list of classes | `FACET cases(WHERE status_code LIKE '2%' AS '2xx', WHERE status_code LIKE '3%' AS '3xx', ...)` | exact; `FACET CASES` is documented, the lower-case `cases(...)` form appears in CODE Q `amazon-cloudfront-web-logs` |
| a regex extraction | `FACET capture(status_code, r'(?P<c>[0-9]).*')` (RE2 named groups; "each NRQL query can have up to 5 capture statements", DOCS N1 `func-capture`) or `aparse(attr, 'pattern*')` | UNVERIFIED on `Metric` attributes; `capture` is described for string attributes |
| `label_join(m, "dst", "-", "a", "b")` | `FACET concat(a, '-', b)` | exact; N6 lists `label_join` as supported by the PromQL mode |
| the replaced label is used only in a later `on()` or `by` | see 4.9 | |

### 4.9 `on()`, `ignoring()`, `group_left`, `group_right`

15% of panels with a PromQL query use `on()`/`ignoring()`, 7% use `group_left`/`group_right` (3.5). Four distinct intents appear in the files.

A. Both sides carry the label, one value per series: `a / on (instance) b`. Put both metrics in one `SELECT` and facet on the shared label (4.4). Exact. This is how the New Relic port of kubernetes-mixin does it: one query with `sum(conditional(WHERE resource = 'cpu', kube_pod_container_resource_requests))` and `rate(sum(...container_cpu_usage_seconds_total...), 1 second)` facetted by `namespace, pod` (CODE Q `kubernetes-prometheus-compute-resources`, "Cluster-wide CPU Quota"). `conditional()` is not in the N1 function list; `filter(sum(x), WHERE resource = 'cpu')` is the documented form (DOCS N1 `func-filter`).

B. The info metric is used only as a filter. RabbitMQ-Overview 10991 (`sum(rabbitmq_queue_messages_ready * on(instance, job) group_left(rabbitmq_cluster) rabbitmq_identity_info{rabbitmq_cluster="$rabbitmq_cluster", ...})`, in 40 panels) selects series by a label that only the info metric has:

```sql
SELECT latest(rabbitmq_queue_messages_ready) FROM Metric
WHERE instance IN (FROM Metric SELECT uniques(instance, 5000)
                   WHERE metricName = 'rabbitmq_identity_info' AND rabbitmq_cluster = {{rabbitmq_cluster}})
```

A subquery in an `IN` condition returns a set of values (DOCS N2, "IN conditions"); up to three subqueries; a subquery returns at most 5,000 values. Exact when the join key is a single label; two keys (`on(instance, job)`) need a packed key (`concat(instance, '/', job)`), UNVERIFIED on `Metric`.

C. The info metric adds a label to group or display by (`group_left(workload)` in kubernetes-mixin: `... * on (namespace, pod) group_left (workload) namespace_workload_pod:kube_pod_owner:relabel`). New Relic has no PromQL-style label join. Options, in order of preference: (1) get the label onto the series at collection (OpenTelemetry Kubernetes attributes processor, or a relabel rule in the scraper) and facet on it; (2) group by `pod` and regroup in the dashboard by a name pattern (heuristic); (3) `JOIN (subquery) ON key` against the info metric: the documentation lists "The subquery cannot be a metric row-wise query" and "The JOIN key cannot be a complex attribute, like a metric value", and no quickstart joins `Metric` (UNVERIFIED that it works, must be tried in the sandbox). Needs a different design.

D. `on()` with no labels, scalar broadcast: `x * on() group_left() (24 * days_in_month())`. Replace by the scalar. Appears in the Sloth SLO dashboards (10 of 21 panels, 4.12).

### 4.10 Set operators and fallbacks

| PromQL | NRQL | exactness |
|---|---|---|
| `expr or vector(0)` (156 panels contain `vector()`) | `count()`-style results are already 0 when nothing matches: `SELECT filter(uniqueCount(pvc), WHERE ...)`. For `sum`: the widget's null-to-zero option (`null_values.null_value = zero`, P1 4.2) | exact for counts, approximate for sums |
| `a and b` (153 panels) | `a` restricted to label values that exist in `b`: `WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE metricName = 'b')` | exact for one key; the value test inside `b` goes through a nested aggregation (4.11) |
| `a unless b` | `WHERE key NOT IN (FROM Metric SELECT uniques(key, 5000) WHERE metricName = 'b')` | exact for one key |
| `a or b` (series union, 93 panels) | two widgets, or one query `SELECT latest(a) AS 'a', latest(b) AS 'b'` when the label sets agree. CODE Q `docker-otel` uses `max(container.cpu.percent) OR max(container.cpu.utilization)` as a fallback between two metric names (a coalesce, not a union; the `OR` form is not in the N1 pages I read, UNVERIFIED) | approximate |
| `x > bool 0` | `SELECT if(latest(x) > 0, 1, 0)` (DOCS N1 `func-if`) | exact |

### 4.11 Comparison filters, range functions, subqueries, `offset`

`100 * used / capacity > 80` keeps only the series above 80. NRQL has no `HAVING`. Use a nested aggregation, which DOCS N4 shows for exactly this: "the inner query calculates the average CPU usage for all hosts, then the outer query filters the results down to only hosts with CPU usage over a risky threshold (90%)":

```sql
SELECT pvc, pct FROM (SELECT latest(used) / latest(cap) * 100 AS pct FROM Metric FACET pvc TIMESERIES 5 minutes) WHERE pct > 80
```

This returns a table of rows, not a filtered time series; for a chart the approximate form is to draw all series and add a threshold line at 80 (the IR supports line thresholds, REPO).

| PromQL | NRQL | exactness |
|---|---|---|
| `avg_over_time(g[1h])`, `max_over_time`, `sum_over_time`, `count_over_time` (37 panels) | `SELECT average(g) FROM Metric TIMESERIES 1 hour SLIDE BY 5 minutes`, or just the dashboard window when instant | approximate; `SLIDE BY` is documented (DOCS N1) |
| `predict_linear(g[1d], 7*24*3600)` | `latest(g) + derivative(g, 1 second) * 604800` | approximate. `derivative()` "is calculated using a linear least-squares regression" (DOCS N1 `derivative`), the same family as `predict_linear`; the window is the query window, so set `SINCE 1 day ago` and ignore the time picker |
| `deriv(g[5m])`, `delta`, `idelta` | `derivative(g, 1 second)`, `latestrate(g, 1 second)` (DOCS N1) | approximate |
| `changes()`, `resets()` | none | manual |
| `offset 24h` | `SINCE ... COMPARE WITH 1 day ago` | approximate; N6 supports `offset` "only ... if every vector in the query has the same offset value". Only 2 panels use it, both in the Mimir rollout dashboard |
| subquery `x[32d:1h]` | none | manual; 5 panels, all in Mimir and Sloth dashboards |
| `absent(x)` | `SELECT count(*) FROM Metric WHERE metricName = 'x'` with a threshold on 0 | approximate |
| `clamp_min`, `round`, `ceil`, `abs`, `sqrt`, `ln` (13 panels) | `clamp_min(x, 0)` becomes `if(x < 0, 0, x)`; `round()`, `ceil()`, `floor()`, `abs()`, `sqrt()`, `ln()` exist (DOCS N1) | exact |
| `scalar(x)`, `sort`, `without (...)` | `scalar()` collapses to a number: a subquery as a number (DOCS N2 "Delta query"); `without` needs the label list at conversion time | approximate |

### 4.12 Time functions and SLO dashboards

`time()`, `month()`, `days_in_month()`, `day_of_week()` have no NRQL counterpart as functions. N6 lists them as supported in the PromQL mode, but the NRQL it would write is not documented; I did not find an example. The Sloth dashboard 14348 computes the remaining monthly budget with `sum_over_time((... * on() group_left() (month() == bool vector(${__to:date:M})))[32d:1h])` (CODE grafana.com 14348). A faithful conversion has no NRQL form. New Relic has its own service-level objects; I did not read their documentation, so the right target is UNVERIFIED. A minimum viable conversion for these panels uses the recording-rule outputs and a fixed window:

```sql
SELECT 1 - average(`slo:sli_error:ratio_rate5m`) FROM Metric
WHERE sloth_service = {{service}} AND sloth_slo = {{slo}} SINCE 30 days ago
```

and puts the budget panel in the placeholder tier with the original expression in a markdown widget.

### 4.13 Instant versus range, tables, transformations, variables

Instant query (449 targets) and `format: table` (300 targets): a Grafana instant query is the value at the end of the range; an NRQL query without `TIMESERIES` aggregates the whole window. For gauges `latest(x)` is the same; for rates it is an average over the window, not the last step. CODE Q emulates the instant form with `SINCE 2 MINUTES AGO UNTIL 1 MINUTE AGO` plus `FACET` (the "Cluster-wide CPU Quota" table). Recommended: instant stat and gauge: no `TIMESERIES`, keep the picker window, tag `approximate`.

Several instant targets joined by a transformation (`joinByField`, `organize`, `merge`; 56 panels, 30 of them in kubernetes-mixin "quota" tables): one `SELECT` with several columns and a shared `FACET`, column names from `AS`; `organize` renames and hides columns, which `AS` and the select list reproduce (P1 4.3). Exact when the join key is the facet. See fixture 2.

Variables:

| Grafana | New Relic |
|---|---|
| `label_values(metric, label)` (172 of 229 query variables) | `SELECT uniques(label, 5000) FROM Metric WHERE metricName = 'metric'`; the second `uniques` argument is the result cap (DOCS N9: "`uniques(countryCode, 10000)` ... up to 10k"). Quickstarts write `select uniques(cluster_name) from Metric limit max` (CODE Q) |
| `label_values(metric{k=~"$other"}, label)` (chained) | the same query with `AND k IN ({{other}})`, a nested variable (plan-dependent, DOCS N10). Fallback: drop the chain and let the widgets filter on every variable |
| `query_result(...)` (8), other forms (49) | placeholder; `query_result` has no `uniques` form (REPO `reasons.py` `V_QUERY_SHAPE`) |
| multi-value + "All" | New Relic multi-value variable, default "select all"; `IN ({{v}})` |
| "All" with the empty string trick | CODE Q uses `namespace = if({{namespace}} != '', {{namespace}}, namespace)` so an unset variable matches everything; the documented alternative is the "Include variable" toggle, which replaces the condition with `true` (DOCS N9) |
| regex on the variable (13) | not available; filter in the variable's own `WHERE ... RLIKE` |
| `interval` (12), `$__interval`, `$__rate_interval` | `TIMESERIES AUTO` (variables cannot go in `TIMESERIES`) |
| `datasource` (88) | drop; there is one account |
| `custom` (20) | ENUM variable (the IR has `enum`) |
| `textbox` (9) | text field variable (`string`) |
| `constant` (3) | inline the value |
| `adhoc` filters | none; not present in the sample |

### 4.14 Panel types, thresholds, mappings, units

Chart types that exist: area, bar, billboard, bullet, funnel, gauge, heat map, histogram, JSON, line, pie, simplified bar, table, treemap (DOCS N8, headings of the page). The IR emits line, area, bar, pie, billboard, markdown, table only (REPO `dashboard_ir.py`).

| Grafana panel (count in sample) | New Relic | verdict |
|---|---|---|
| timeseries, graph (1,681) | line; area when stacked | exact for the query; legend calcs and tables lost |
| stat, singlestat (297) | billboard; `FACET` gives one value per series; thresholds "Good, Warning, Approaching critical, Critical, Neutral"; "For percentages, use a decimalized format" (DOCS N8 billboard) | exact |
| gauge (31) | Gauge chart: single value, min, max, thresholds; `FACET` renders a grid of gauges (DOCS N8). The widget id is not in the quickstarts or the P1 sources, so the IR cannot emit it (UNVERIFIED id). Fallback: billboard with thresholds | approximate until the id is known |
| bargauge (25) | bullet chart (needs a limit, single value) or bar chart with `FACET`; `viz.bullet` appears 22 times in quickstarts | approximate |
| table, table-old (74) | `viz.table`; one query with all columns | exact for values; cell colouring partly lost |
| piechart (11) | `viz.pie` | exact |
| heatmap (17) | `viz.heatmap` with `histogram()` | approximate (4.5) |
| state-timeline, status-history (2) | no widget. Fallbacks: a faceted billboard of `latest(up)` with thresholds (current state only); a line chart `latest(up)` faceted by instance (history, no bands) | different design |
| logs (14) | log table widget; LogQL to `FROM Log` | needs a LogQL translator; 34 LogQL panels in the sample |
| nodeGraph (1), alertlist (3), geomap, canvas, xychart, news, dashlist | none | placeholder |
| text (22) | `viz.markdown`; HTML mode must be escaped | exact |

Thresholds: Grafana steps `[{color, value}]` map to billboard/table/line thresholds by color family (P1 4.2); 386 panels. A threshold in percent of the max (`mode: percentage`) has no equivalent. Value mappings: `special` (null to text), `value`, `range`: no widget feature; `if()` or `FACET CASES` reproduce a few in the query (P1 4.2); a "null" mapping on a count is moot because counts return 0. Units: the Grafana ids in 3.6 (`short`, `s`, `bytes`, `percentunit`, `ops`, `reqps`, `percent`, `bps`, `pps`, `ms`) need one sandbox check against the strings New Relic accepts (UNVERIFIED, as in the IR spec).

### 4.15 Layout objects

| Grafana | New Relic |
|---|---|
| rows (455), collapsed rows (76) | pages (kubernetes-prometheus-compute-resources uses a page per grouping) or a markdown header widget; collapse is not available |
| repeat panel or row on a multi-value variable (35 panels, 10 rows) | one widget with `WHERE v IN ({{v}}) FACET v`; mark approximate (P1 4.4) |
| dashboard links (82), panel links (34) | markdown links (an http/https URL, one per widget, P1 4.2) |
| annotations (8 custom) | none |
| shared crosshair (`graphTooltip`) | none |
| library panels | inline (0 in the sample) |
| grid 24 columns | 12 columns, `column = x // 2 + 1` (P1 section 4) |

## 5. Ready-made New Relic dashboards instead of conversion

### 5.1 What exists

The quickstarts repository holds 558 dashboard JSON files, 325 alert-policy folders and 327 quickstart definitions (MEASURED from the repository tree, CODE Q). Quickstarts that matter for Prometheus-shaped data (config titles from `quickstarts/<name>/config.yml`, CODE Q):

- Same Prometheus metric names, remote-write or the Prometheus agent: `node-exporter` (dashboard and alert policy), `kubernetes-prometheus` (dashboards `kubernetes-prometheus-compute-resources` with three pages and `kubernetes-prometheus-kubelet`), `redis-prometheus` (dashboard and alerts), `mongodb-prometheus`, `argocd` (dashboard `argocd-dashboard`, alerts), `coredns` (dashboard, alerts), `traefik` (dashboard, alerts), `nginx-ingress`, `nvidia-dcgm` (dashboard, alerts), `velero-prometheus`, `envoy` (dashboard, alerts), `prometheus-agent`, `prometheus-remote-write` (dashboard `prometheus-remote-write-monitoring`), `k6-prometheus`, `sidekiq-prometheus`, `squid-prometheus`, `pinecone-prometheus`.
- Other New Relic data types: infrastructure agent on-host integrations (`redis`, `mysql`, `postgresql`, `rabbitmq`, `nginx`, `haproxy`, `kafka`, `mongodb`, `docker`; event types such as `RedisSample`, `MysqlSample`, `PostgresqlDatabaseSample`, `RabbitmqQueueSample`, `NginxSample`, `HAProxyBackendSample`, `KafkaBrokerSample`), the Kubernetes integration (`kubernetes`; `K8sPodSample`, `K8sContainerSample`, `K8sNodeSample`), OpenTelemetry collector receivers (`*-otel`: `redis-otel`, `mysql-otel`, `postgresql-otel`, `rabbitmq-otel`, `nginx-otel`, `haproxy-otel`, `kafka-otel`, `docker-otel`, `elasticsearch-otel`; dotted metric names such as `rabbitmq.message.current`), the AWS integrations (`amazon-ec2`, `rds`, `aws-lambda`, `aws-alb`, `aws-elb`, `sqs`, `amazon-msk`, `amazon-elasticache-redis`; `FROM Metric` with `aws.*` names), and APM agents (`java`, `nodejs`, `golang`; `Transaction`, `TransactionError`, `Metric` with `apm.*`). MEASURED widget counts: `node-exporter` 27, `kubernetes-prometheus-compute-resources` 48, `kubernetes-prometheus-kubelet` 23, `redis-prometheus` 16, `mongodb-prometheus` 47, `argocd-dashboard` 47, `coredns` 21, `traefik` 10, `nginx-ingress` 17, `nvidia-dcgm` 9, `envoy` 41, `prometheus-agent` 21, `rabbitmq-otel` 31, `kafka-otel` 117, `amazon-msk` 67, `mysql` 93 (infrastructure agent), `java` 46.
- Install route and licence: a quickstart is a bundle of dashboards, alerts and a data-source description. How the importer would install one (UI, NerdGraph, or by copying the JSON into an import) and the repository licence were not checked (UNVERIFIED). The documented manual route is "Import dashboard" with JSON (DOCS N11).

The `kubernetes-prometheus-compute-resources` dashboard is itself a PromQL-to-NRQL port of kubernetes-mixin "Compute Resources": its widget titles follow the mixin's panels ("CPU Quota", "Current Network Usage", "Receive Bandwidth", "IOPs (Reads + Writes)" against the mixin's "IOPS(Reads+Writes)"), and its queries use the translator's idioms (`__result_0`, `FACET dimensions()`, `irate(..., 1 SECONDS)`, `cardinality`) (CODE Q). That is useful evidence for sections 4.3 and 4.9.

### 5.2 Metric-name overlap between a Grafana dashboard and the quickstart that replaces it

MEASURED: the share of the Grafana dashboard's metric names (tokens with an underscore found in its PromQL after dropping labels and variables) that also occur in the quickstart's dashboard JSON. A low share means the quickstart shows other metrics, or the dashboard uses another exporter's names.

| Grafana dashboard | quickstart dashboard | Grafana names | shared | share |
|---|---|---|---|---|
| kube-prometheus kubelet | kubernetes-prometheus-kubelet | 28 | 27 | 96% |
| kube-prometheus nodes (node-mixin) | node-exporter | 16 | 13 | 81% |
| kube-prometheus k8s-resources-workload | kubernetes-prometheus-compute-resources | 12 | 9 | 75% |
| kube-prometheus k8s-resources-namespace | same | 22 | 13 | 59% |
| kube-prometheus k8s-resources-pod | same | 23 | 13 | 57% |
| kube-prometheus k8s-resources-cluster | same | 22 | 11 | 50% |
| kube-prometheus k8s-resources-node | same | 11 | 1 | 9% |
| kube-prometheus node-rsrc-use | node-exporter | 12 | 2 | 17% |
| Traefik 4475 | traefik | 3 | 3 | 100% |
| Envoy Proxy 6693 | envoy | 14 | 12 | 86% |
| NVIDIA DCGM 12239 | nvidia-dcgm | 6 | 5 | 83% |
| ArgoCD 14584 | argocd-dashboard | 25 | 19 | 76% |
| Envoy Global 11022 | envoy | 11 | 8 | 73% |
| Redis 11835 | redis-prometheus | 14 | 9 | 64% |
| Redis 763 | redis-prometheus | 15 | 9 | 60% |
| CoreDNS 14981 | coredns | 22 | 12 | 55% |
| NGINX Ingress 9614 | nginx-ingress | 10 | 5 | 50% |
| Kubernetes Pod Metrics 747 | kubernetes-prometheus-compute-resources | 8 | 4 | 50% |
| Kubernetes Deployment metrics 741 | same | 12 | 4 | 33% |
| Kubernetes cluster monitoring 315 | same | 13 | 4 | 31% |
| K8S Dashboard 15661 | same | 29 | 7 | 24% |
| kube-state-metrics-v2 13332 | same | 30 | 3 | 10% |
| Node Exporter Full 1860 | node-exporter | 242 | 18 | 7% |
| Prometheus 2.0 Overview 3662 | prometheus-agent | 58 | 2 | 3% |
| Kubernetes Cluster 6417 | same | 25 | 0 | 0% (uses kube-state-metrics v1 names, `kube_node_status_capacity_cpu_cores`) |
| Kubernetes Nodes 8171 | same | 15 | 0 | 0% |
| MongoDB 2583 | mongodb-prometheus | 16 | 0 | 0% (`mongodb_op_counters_total`; the quickstart uses `mongodb_collstats_latencyStats_*`) |
| RabbitMQ-Overview 10991 | rabbitmq-otel | 41 | 0 | 0% (Prometheus plugin names against OTel dotted names) |
| MySQL Overview 7362 | mysql / mysql-otel | 82 | 0 | 0% |
| PostgreSQL 9628 | postgresql (infrastructure agent) | 37 | 0 | 0% |
| HAProxy 12693 | haproxy (infrastructure agent) | 157 | 0 | 0% |
| JVM Micrometer 4701 | java (APM) | 38 | 0 | 0% |

Reading the table: the kubernetes-mixin and node-mixin dashboards from kube-prometheus match their quickstarts best because the quickstarts are ports of them. Community dashboards are keyed to older exporter versions (kube-state-metrics v1, older mongodb exporters) or to a partial subset. A "map to quickstart" step must therefore check names, not titles.

### 5.3 Mapping table, grafana.com dashboards

Verdicts: Q = install the quickstart (same Prometheus data path, enough overlap, or a documented port); Q* = a quickstart exists but on a different data path or exporter, so the customer must change collection first; R = rebuild with NRQL (no quickstart, or too little overlap); M = manual or placeholder. Rule applied: Q needs a quickstart on the same data path and at least 50% name overlap where I measured it; Q* needs a quickstart whose data comes from the infrastructure agent, OpenTelemetry, the AWS integration or a different exporter. The judgement of "enough scope" is mine, not a measurement.

| Grafana id and name | quickstart (path in newrelic-quickstarts) | data-source difference | verdict |
|---|---|---|---|
| 1860 Node Exporter Full (124 panels) | `quickstarts/node-exporter`, `dashboards/node-exporter` (27 widgets), `alert-policies/node-exporter` | same `node_*` names; the quickstart covers 7% of the dashboard's names | R (install the quickstart too, for its alerts) |
| 14584 ArgoCD | `dashboards/argocd-dashboard` (47 widgets, 4 pages), alerts `argocd` | same `argocd_*`; remote-write attribute `instrumentation.name = 'remote-write'` | Q |
| 9614 NGINX Ingress controller | `dashboards/nginx-ingress` | same `nginx_ingress_controller_*`; 50% overlap | Q |
| 14981 CoreDNS | `dashboards/coredns` (21), alerts `coredns` | same `coredns_*` | Q |
| 12239 NVIDIA DCGM | `dashboards/nvidia-dcgm` (9), alerts | same `DCGM_FI_*` | Q |
| 4475 Traefik | `dashboards/traefik` (10), alerts | same `traefik_*` | Q |
| 11022 Envoy Global, 6693 Envoy Proxy | `dashboards/envoy` (41), alerts | same `envoy_*` | Q |
| 11835, 763 Redis exporter | `dashboards/redis-prometheus` (16), alerts | same `redis_*`, 60-64% | Q |
| 747 Kubernetes Pod Metrics | `dashboards/kubernetes-prometheus-compute-resources` | cAdvisor and kube-state-metrics v2 names; 50% | Q |
| 15661 K8S Dashboard, 315, 741, 8171, 6417, 13332 (Kubernetes) | `kubernetes-prometheus-compute-resources`, `kubernetes-prometheus-kubelet` | kube-state-metrics v1 or old cAdvisor names, 0-33% overlap | R (the quickstart may serve as the target design) |
| 7645 Istio Control Plane | none (`linkerd-otel`, `envoy` are related) | `istio_*`, `pilot_*` | R |
| 13659, 7587 Blackbox exporter | none | `probe_*`; Synthetics is a different product (not read) | R |
| 14055 Loki stack, 9578 Alertmanager, 3662 Prometheus 2.0, 11001 cert-manager, 10441 Keycloak | none (`prometheus-agent` monitors the agent, 3% overlap with 3662) | `loki_*`, `alertmanager_*`, `prometheus_*`, `certmanager_*`, `keycloak_*` | R |
| 4701 JVM Micrometer, 14370 Quarkus, 11159 NodeJS app | `dashboards/java`, `dashboards/nodejs` are APM-agent dashboards (`Transaction`, `Metric` `apm.*`) | `jvm_*` Micrometer or `nodejs_*` prom-client against APM data | R (Q* only if the customer installs the APM agent) |
| 14012 KMinion | `kafka` (infrastructure agent), `kafka-otel` | `kminion_*` against `KafkaBrokerSample` or `kafka.*` | R |
| 10991 RabbitMQ-Overview | `rabbitmq` (infrastructure agent, `RabbitmqQueueSample`), `rabbitmq-otel` (31 widgets) | RabbitMQ Prometheus plugin names, `group_left` on `rabbitmq_identity_info`; OTel names `rabbitmq.*`; 0% overlap | Q* |
| 7362, 14057, 7371 MySQL | `mysql` (infrastructure agent, `MysqlSample`, 93 widgets), `mysql-otel` | `mysql_global_status_*` against `MysqlSample` fields or `mysql.*` | Q* |
| 9628, 12485 PostgreSQL | `postgresql` (`PostgresqlDatabaseSample`), `postgresql-otel` | `pg_*` against sample attributes | Q* |
| 12693 HAProxy | `haproxy` (`HAProxyBackendSample`), `haproxy-otel` | `haproxy_*` against samples | Q* |
| 14900 Nginx | `nginx` (`NginxSample`), `nginx-otel` | `nginx_*` against samples | Q* |
| 2583 MongoDB | `mongodb-prometheus` (47 widgets), `mongodb` | Prometheus names of an older exporter against `mongodb_collstats_*` | Q* |
| 14361 AWS ALB, 969 ElastiCache Redis, 11265 EC2, 11264 RDS, 584 SQS, 593 Lambda, 19734 Lambda Insights, 12009 and 16808 Kafka (MSK) | `aws-alb`, `amazon-elasticache-redis`, `amazon-ec2`, `rds`, `sqs`, `aws-lambda`, `amazon-msk` (67 widgets) | CloudWatch data source (no PromQL, 6% convert) against `FROM Metric` with `aws.*` names from the AWS integration | Q* |
| 11454 K8s / Storage / Volumes | none | `kubelet_volume_stats_*` with `predict_linear`, `unless`, `or vector(0)` | M (see fixture 3) |
| 14643 Sloth SLOs, 14348 SLO / Detail | none | recording-rule series `slo:*`, time functions, subqueries | M (see fixture 4) |
| 13639 Logs / App | none | LogQL data source | M |

Count over the 51 grafana.com dashboards (MEASURED from this table): Q 10, Q* 18, R 19, M 4. In percentages: 20%, 35%, 37%, 8%.

### 5.4 Mapping table, kube-prometheus and LGTM mixins

| source | dashboards | quickstart | verdict |
|---|---|---|---|
| kubernetes-mixin compute resources: cluster, namespace (pods), pod, workload | 4 | `kubernetes-prometheus-compute-resources` (50-75% overlap) | Q |
| kubernetes-mixin kubelet | 1 | `kubernetes-prometheus-kubelet` (96%) | Q |
| node-mixin Nodes | 1 | `node-exporter` (81%) | Q |
| node-mixin darwin, aix, USE node, USE cluster | 4 | none (17% overlap with `node-exporter` for USE node) | R |
| kubernetes-mixin node, nodes overview, workloads-namespace, multi-cluster, networking (4), apiserver, controller-manager, scheduler, proxy, persistent volumes, Windows (5) | 19 | none | R |
| prometheus-mixin overview and remote write, alertmanager-mixin, grafana overview | 4 | none | R |
| Mimir mixin | 11 | none | R (63% of its panels use a hard pattern; T3 is 36%) |
| Loki mixin | 7 | none | R |
| Tempo mixin | 6 | none | R |

Counts over all 108 (MEASURED from the two tables): better served by a quickstart on the same data path, 16 (15%); a quickstart exists if collection changes, 18 (17%); rebuild or manual, 74 (68%). Of the ten most downloaded grafana.com dashboards in the sample (1860, 14584, 9614, 7645, 14981, 13659, 10991, 14055, 12239, 15661), 4 are Q (14584, 9614, 14981, 12239), 1 is Q* (10991) and 5 are R; for 1860 the alert policy of `node-exporter` is the part worth installing.

An accurate reading of "how many better served by a quickstart": for the 25 or more dashboards sampled, 28 of 51 grafana.com dashboards (55%) have a quickstart of some kind, 10 (20%) are a clean substitute, and the other 18 depend on changing the collector, which is a data-path decision outside the importer.

### 5.5 What the importer should do with a quickstart match

1. Never replace silently. Report it: "Dashboard X (grafana.com id N) corresponds to quickstart Y; metric names in common Z%; install Y or convert".
2. Keep the conversion available as an option, because the quickstart may cover only part of the dashboard (for 1860, 27 widgets against 124 panels).
3. For Q* rows the report should name the data-path change (for example "send RabbitMQ metrics with the OpenTelemetry collector `rabbitmq` receiver") and must not claim the dashboard is migrated.
4. The alert half is where quickstarts are strongest: `alert-policies/` exist for node-exporter, kubernetes-opentelemetry, redis-prometheus, coredns, argocd, traefik, nvidia-dcgm, envoy, velero-prometheus, prometheus-agent, mysql, mongodb (CODE Q). That links to the existing alert translator: a dashboard matched to a quickstart can suppress the duplicate alert plans the translator would write from the same Prometheus rules (UNVERIFIED that the rules overlap).

## 6. Recommended conversion strategy for the importer

### 6.0 The no-conversion option

New Relic exposes a Prometheus-compatible query API so that Grafana can read New Relic as a Prometheus data source (P2 section 6.5; DOCS N6 and the "Configure New Relic as a Prometheus data source for Grafana" page named in P2). N6 claims 99.5% of queries across the 7.8 million most downloaded Grafana dashboards run through the translator. A customer who wants to keep Grafana for the interface can keep the dashboards and change the data source; that needs no conversion and is the only route for panels that have no New Relic widget. It is not a migration to New Relic dashboards, and I could not test it, so it is a stop-gap to offer, not a plan to depend on.

### 6.1 Four outcomes per dashboard and per panel

| tier | meaning | share in the sample (MEASURED where a number is given) |
|---|---|---|
| convert | every query is T1, T2 or T3 and uses only exact recipes (4.2 to 4.7, 4.13 for variables); widget types the IR can emit | 62.5% of panels at T3 |
| convert-with-note | the recipes marked approximate: instant versus range, top-N over the window, heat maps, IN-subquery set logic, nested-aggregation gauge sums, repeat as FACET, `SLIDE BY` for range windows | R2 minus T3: 25.1 points of panels (87.6% - 62.5%) |
| map-to-quickstart | dashboard-level: a quickstart matches by id, title and name overlap, same data path | 15% of dashboards (Q); 17% more with a collector change (Q*) |
| placeholder | markdown widget with the panel title, the original query (escaped), the reason; for panel types with no widget, LogQL, subqueries, time functions, `changes`/`resets`, `bottomk`, native histograms | 12.4% of panels are outside R2 (100% - 87.6%); 4.7 points of that are CloudWatch panels, which need a different converter |

### 6.2 Build order

Sizes: S is about a day, M about a week, L more than a week. These are my estimates (UNVERIFIED), not measurements.

| step | what | why this order | effort | panels newly covered (MEASURED upper bound) |
|---|---|---|---|---|
| 1 | Variable model for `label_values()` queries and the `IN ({{v}})` filter, chain depth up to three, "All" handling, `$__interval`/`$__rate_interval` removal, data-source variables dropped | 95.8% of queries reference a variable; without it nothing converts; the IR already has `IRVariable` and the clause checks (REPO) | M | prerequisite for all |
| 2 | T1 to T3 core emitter: aggregation by `FACET`, `rate(sum(c), 1 second)`, `filter()` ratios, arithmetic in one `SELECT`, `bucketPercentile`, `FACET ... ORDER BY ... LIMIT n` for `topk`, `legendFormat` to `FACET`/`AS`, `TIMESERIES AUTO` | covers T3 | M | T3 = 62.5% of panels |
| 3 | Quickstart map: a static table (Grafana id or title, quickstart path, data path, metric names), a name-overlap check against the quickstart JSON, a report row | cheap, and 15% of dashboards are served by a clean substitute; it also removes the largest per-dashboard conversions (kubernetes-mixin sets) | S | 16 of 108 dashboards |
| 4 | Widget types the IR lacks: gauge, bullet, heat map, histogram; read one dashboard back in the sandbox to learn the ids and option names (REPO `docs/ir-spec.md` already lists them as UNVERIFIED) | gauge and bar gauge are 56 panels; heat maps 17 | S to M | +65.0% - 62.5% = 2.5 points |
| 5 | Nested-aggregation recipes: per-series `rate` with no aggregation, gauge sums (`average * cardinality`), comparison filters as a table, `predict_linear` via `derivative` | 12% of queries are bare `rate`; part of T3 depends on it for exactness | M | inside T3 and R2 |
| 6 | `or vector(0)`, `unless`, `and` as `IN`/`NOT IN` subqueries, `*_over_time` with `SLIDE BY`, `label_replace` with `substring`/`cases` | R2 region; each is small, each needs a sandbox check on `Metric` attributes | M | up to +25 points |
| 7 | Info-metric joins (`group_left`) | 15% of panels with a query use `on()`/`ignoring()` (301 panels); Mimir 49%, kubernetes-mixin 25%; RabbitMQ-Overview is 2% convertible at T3 and 95% at R2 because of it; sub-cases B and C of 4.9 need a data-path decision, not just code | L | at most 15% of panels, mostly with a data-design change |
| 8 | LogQL | 34 panels in the sample, a separate translator | L | 1.6% |

### 6.3 Risks

- Metric names. The quickstart map and the conversion both assume the metric exists in New Relic under the name the dashboard uses. Remote write keeps names; the OpenMetrics integration uses other attribute names (DOCS N7); OTLP uses dotted names. Measured overlap shows older exporters differ (5.2). Run `SELECT uniques(metricName) FROM Metric WHERE metricName IN (...)` as a pre-flight (the data-check tool P2 section 6.8 describes).
- Recording rules (36% of kube-prometheus queries, 9% of Mimir, 20% of Loki) exist only if the rule-evaluating Prometheus writes them out. Detect `name:with:colons` and put them in a prerequisite section of the report.
- Counters as deltas: `rate()` and `increase()` on a gauge give wrong results (DOCS N6). The importer must know whether a metric name is a counter (suffix `_total`, `_bucket`, `_count`; P2 6.1) and refuse `rate()` on a name that does not look like one.
- Per-account cardinality caps and the rollup behaviour described in P2 6.1 can make long-range queries wrong; `FACET dimensions()` over a large cluster may exceed the 5,000 facet limit (inference, not tested).
- Approximate conversions that look exact: instant queries (4.13), top-N over a window (4.6), `increase` (4.3). Every approximate recipe needs a visible reason on the widget (a description string) as the IR already does for height clamps.
- NRQL written from documentation, not run: the biggest unknowns are `substring`/`capture`/`concat` on `Metric` attributes, `JOIN` on `Metric`, `derivative` on gauges with aliases in nested queries, and `COMPARE WITH` with the dashboard time picker. One sandbox pass over the five fixtures in section 7 would settle most of them (create-only, per the sandbox rules; reading data back needs the owner's approval for the session).
- Nested variables are plan-dependent (DOCS N10); where unavailable, chains degrade to independent filters.
- Escaping: every recipe puts user text into a query. Names go through `identifier()`, strings through `quote_string()`, regexes through the RLIKE `r'...'` form with the same escaping, variable references only as `{{name}}` (REPO rules in `CLAUDE.md` and `docs/ir-spec.md`).

### 6.4 Data prerequisites to state in every report

1. Prometheus remote write (or the agent) with `prometheus_server` set, same label names, and rule-evaluated series written too; or an OTLP/OpenTelemetry route with the conversion of names stated.
2. Scrape interval of 60 seconds or less (N6: the translator inspects data up to one minute old for instant queries; a `TIMESERIES` bucket below the scrape interval gives gaps).
3. The metric exists: pre-flight query.
4. Exporter versions match the dashboard's names (5.2).

## 7. Fixture recommendations

Five synthetic dashboards, each between 2.0 and 2.8 KB, built from the hardest patterns above. They contain no customer data: names and queries follow public dashboards (kubernetes-mixin, Sloth, node exporter, standard HTTP metrics). Put them under `tests/fixtures/grafana-dashboards/` when the tests are written. The JSON is classic schema 39, one panel per line. `${DS}` is the data-source variable placeholder. The "expected" sketches are NRQL written from documentation and never run (section 9); mark the golden files for human review as the project rules require.

Common rules in the expected output: attribute names are those of Prometheus remote write; `{{v}}` is a New Relic variable; the dashboard grid halves (`column = x // 2 + 1`); a data-source variable is dropped; `TIMESERIES AUTO` replaces `$__rate_interval`; every approximate widget carries a description with the reason. The IR's raw-NRQL checker refuses `SINCE`, `UNTIL` and `COMPARE WITH` today (REPO `docs/ir-spec.md`), so fixtures 3 and 4, which need a fixed window, also test that gap.

### 7.1 Fixture 1: RED panels (label_replace, histogram_quantile, ratio, topk, chained variables)

Tests: `label_replace` in a `sum by`, two `histogram_quantile` targets, a 5xx ratio with thresholds, `topk` as a bar gauge, a multi-value variable and a chained variable, a data-source variable.

```json
{"uid":"fx-red","title":"RED service overview","schemaVersion":39,"time":{"from":"now-6h","to":"now"}
 ,"templating":{"list":[
  {"name":"DS","type":"datasource","query":"prometheus"},
  {"name":"job","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(http_requests_total, job)","refId":"v"},"refresh":2,"multi":true,"includeAll":true},
  {"name":"instance","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(http_requests_total{job=~\"$job\"}, instance)","refId":"v"},"refresh":2,"multi":true,"includeAll":true}
 ]},
 "panels":[
  {"id":1,"type":"timeseries","title":"Requests by status class","gridPos":{"x":0,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"sum by (status) (label_replace(rate(http_requests_total{job=~\"$job\"}[$__rate_interval]), \"status\", \"${1}xx\", \"code\", \"([0-9])..\"))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{status}}"}],"fieldConfig":{"defaults":{"unit":"reqps","custom":{"stacking":{"mode":"normal"},"fillOpacity":30}},"overrides":[]}},
  {"id":2,"type":"timeseries","title":"Latency percentiles","gridPos":{"x":12,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job=~\"$job\"}[$__rate_interval])))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"p95"},{"refId":"B","expr":"histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{job=~\"$job\"}[$__rate_interval])))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"p99"}],"fieldConfig":{"defaults":{"unit":"s"},"overrides":[]}},
  {"id":3,"type":"stat","title":"5xx ratio (5m)","gridPos":{"x":0,"y":8,"w":6,"h":4},"targets":[{"refId":"A","expr":"sum(rate(http_requests_total{job=~\"$job\",code=~\"5..\"}[5m])) / sum(rate(http_requests_total{job=~\"$job\"}[5m]))","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false}],"fieldConfig":{"defaults":{"unit":"percentunit","thresholds":{"mode":"absolute","steps":[{"color":"green","value":null},{"color":"orange","value":0.01},{"color":"red","value":0.05}]}},"overrides":[]}},
  {"id":4,"type":"bargauge","title":"Top 5 routes","gridPos":{"x":6,"y":8,"w":18,"h":4},"targets":[{"refId":"A","expr":"topk(5, sum by (route) (rate(http_requests_total{job=~\"$job\"}[$__rate_interval])))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{route}}","instant":true,"range":false}],"fieldConfig":{"defaults":{"unit":"reqps","min":0},"overrides":[]}}
 ]}
```

Expected New Relic output:

- Variables: `job` (nrql, multi, default all): `SELECT uniques(job, 5000) FROM Metric WHERE metricName = 'http_requests_total'`. `instance` is chained on `job` and no widget uses it: drop it, or keep it hidden with `AND job IN ({{job}})` (nested variables are plan-dependent, DOCS N10). `DS` is dropped.
- Panel 1, `viz.area` (stacked requested; whether faceted series stack is UNVERIFIED), approximate:
  `FROM Metric SELECT rate(sum(http_requests_total), 1 second) WHERE job IN ({{job}}) FACET concat(substring(code, 0, 1), 'xx') AS status TIMESERIES AUTO`
  Reason: label_replace emulated with `substring`/`concat` (UNVERIFIED on `Metric` attributes); the original keeps 3-character codes only; an alternative is `FACET cases(WHERE code LIKE '2%' AS '2xx', ...)`.
- Panel 2, `viz.line`, exact apart from interpolation:
  `FROM Metric SELECT bucketPercentile(http_request_duration_seconds_bucket, 95) AS 'p95', bucketPercentile(http_request_duration_seconds_bucket, 99) AS 'p99' WHERE job IN ({{job}}) TIMESERIES AUTO`
- Panel 3, `viz.billboard`, warning 0.01 and critical 0.05 (decimal form, DOCS N8), approximate (the 5m window becomes the picker window; a fixed window needs `SINCE 5 minutes ago`):
  `FROM Metric SELECT filter(sum(http_requests_total), WHERE code RLIKE '5..') / sum(http_requests_total) AS '5xx ratio' WHERE job IN ({{job}})`
- Panel 4, `viz.bar`, approximate (bar gauge look lost; instant becomes window average):
  `FROM Metric SELECT rate(sum(http_requests_total), 1 second) WHERE job IN ({{job}}) FACET route ORDER BY sum(http_requests_total) LIMIT 5`
- Layout: widths 12, 12, 6, 18 become 6, 6, 3, 9 columns.

### 7.2 Fixture 2: kubernetes-mixin joins and a multi-query table

Tests: `group_left` with a recording-rule info series, a gauge ratio across metrics, three instant table queries merged by `joinByField` and renamed by `organize`, an override by regex.

```json
{"uid":"fx-k8s-join","title":"Workload CPU (kubernetes-mixin style)","schemaVersion":39,"time":{"from":"now-6h","to":"now"}
 ,"templating":{"list":[
  {"name":"DS","type":"datasource","query":"prometheus"},
  {"name":"cluster","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(kube_pod_info, cluster)","refId":"v"},"refresh":2,"multi":false,"includeAll":false},
  {"name":"namespace","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(kube_pod_info{cluster=\"$cluster\"}, namespace)","refId":"v"},"refresh":2,"multi":false,"includeAll":false}
 ]},
 "panels":[
  {"id":1,"type":"timeseries","title":"CPU by workload","gridPos":{"x":0,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"sum by (workload) (rate(container_cpu_usage_seconds_total{namespace=\"$namespace\",container!=\"\"}[$__rate_interval]) * on (namespace, pod) group_left (workload) namespace_workload_pod:kube_pod_owner:relabel{namespace=\"$namespace\"})","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{workload}}"}],"fieldConfig":{"defaults":{"unit":"cores"},"overrides":[]}},
  {"id":2,"type":"timeseries","title":"CPU usage / requests by pod","gridPos":{"x":12,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=\"$namespace\",container!=\"\"}[5m])) / sum by (pod) (kube_pod_container_resource_requests{namespace=\"$namespace\",resource=\"cpu\"})","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{pod}}"}],"fieldConfig":{"defaults":{"unit":"percentunit"},"overrides":[]}},
  {"id":3,"type":"table","title":"CPU quota by pod","gridPos":{"x":0,"y":8,"w":24,"h":8},"targets":[{"refId":"A","expr":"sum by (pod) (rate(container_cpu_usage_seconds_total{namespace=\"$namespace\",container!=\"\"}[5m]))","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false,"format":"table"},{"refId":"B","expr":"sum by (pod) (kube_pod_container_resource_requests{namespace=\"$namespace\",resource=\"cpu\"})","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false,"format":"table"},{"refId":"C","expr":"sum by (pod) (kube_pod_container_resource_limits{namespace=\"$namespace\",resource=\"cpu\"})","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false,"format":"table"}],"fieldConfig":{"defaults":{},"overrides":[{"matcher":{"id":"byRegexp","options":"/Value #.*/"},"properties":[{"id":"unit","value":"cores"}]}]},"transformations":[{"id":"joinByField","options":{"byField":"pod","mode":"outer"}},{"id":"organize","options":{"renameByName":{"Value #A":"Usage","Value #B":"Requests","Value #C":"Limits"},"excludeByName":{"Time":true}}}]}
 ]}
```

Expected:

- Variables: `cluster` and `namespace` as nrql variables on `kube_pod_info`, `namespace` chained (`AND cluster = {{cluster}}`); the label may be `cluster_name` in the customer's data (CODE Q uses `cluster_name`).
- Panel 1 (CPU by workload): different design. The `workload` label comes from `namespace_workload_pod:kube_pod_owner:relabel`, a recording rule. Output a degraded widget grouped by pod with a note, and a placeholder entry listing the options in 4.9 C:
  `FROM Metric SELECT rate(filter(sum(container_cpu_usage_seconds_total), WHERE container != ''), 1 second) WHERE namespace = {{namespace}} FACET pod TIMESERIES AUTO`
- Panel 2 (usage over requests): approximate, nested to avoid summing a gauge over several scrapes:
  `FROM (FROM Metric SELECT rate(filter(sum(container_cpu_usage_seconds_total), WHERE container != ''), 1 second) AS usage, filter(latest(kube_pod_container_resource_requests), WHERE resource = 'cpu') AS req WHERE namespace = {{namespace}} FACET pod, container LIMIT MAX TIMESERIES 1 minute) SELECT sum(usage) / sum(req) FACET pod LIMIT MAX TIMESERIES 1 minute`
- Panel 3 (table): the three targets and both transformations collapse into one query, exact apart from instant semantics. The `Value #A` override and the unit are applied to the columns by name:
  `FROM (FROM Metric SELECT rate(filter(sum(container_cpu_usage_seconds_total), WHERE container != ''), 1 second) AS usage, filter(latest(kube_pod_container_resource_requests), WHERE resource = 'cpu') AS req, filter(latest(kube_pod_container_resource_limits), WHERE resource = 'cpu') AS lim WHERE namespace = {{namespace}} FACET pod, container LIMIT MAX) SELECT sum(usage) AS 'Usage', sum(req) AS 'Requests', sum(lim) AS 'Limits' FACET pod LIMIT MAX`

### 7.3 Fixture 3: set operators, fallbacks, predict_linear, comparison filters

Tests: `and` with `predict_linear`, `or vector(0)`, `unless`, a comparison filter on a ratio, a `null` value mapping, threshold lines.

```json
{"uid":"fx-pvc","title":"PVC capacity (set operators)","schemaVersion":39,"time":{"from":"now-6h","to":"now"}
 ,"templating":{"list":[
  {"name":"DS","type":"datasource","query":"prometheus"},
  {"name":"namespace","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(kubelet_volume_stats_used_bytes, namespace)","refId":"v"},"refresh":2,"multi":true,"includeAll":true}
 ]},
 "panels":[
  {"id":1,"type":"stat","title":"PVCs predicted full within 7d","gridPos":{"x":0,"y":0,"w":6,"h":4},"targets":[{"refId":"A","expr":"count(kubelet_volume_stats_available_bytes and predict_linear(kubelet_volume_stats_available_bytes[1d], 7 * 24 * 60 * 60) < 0) or vector(0)","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false}],"fieldConfig":{"defaults":{"thresholds":{"mode":"absolute","steps":[{"color":"green","value":null},{"color":"red","value":1}]},"mappings":[{"type":"special","options":{"match":"null","result":{"text":"0"}}}]},"overrides":[]}},
  {"id":2,"type":"table","title":"PVCs with no usage stats","gridPos":{"x":6,"y":0,"w":18,"h":6},"targets":[{"refId":"A","expr":"(1 - max by (namespace, persistentvolumeclaim) (kube_persistentvolumeclaim_info)) unless max by (namespace, persistentvolumeclaim) (kubelet_volume_stats_used_bytes)","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false,"format":"table"}],"fieldConfig":{"defaults":{},"overrides":[]}},
  {"id":3,"type":"timeseries","title":"Used % where above 80","gridPos":{"x":0,"y":6,"w":24,"h":8},"targets":[{"refId":"A","expr":"100 * kubelet_volume_stats_used_bytes{namespace=~\"$namespace\"} / kubelet_volume_stats_capacity_bytes{namespace=~\"$namespace\"} > 80","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{namespace}}/{{persistentvolumeclaim}}"}],"fieldConfig":{"defaults":{"unit":"percent","custom":{"thresholdsStyle":{"mode":"line"}},"thresholds":{"mode":"absolute","steps":[{"color":"green","value":null},{"color":"red","value":90}]}},"overrides":[]}}
 ]}
```

Expected:

- Panel 1 (billboard, critical from 1), approximate; the `or vector(0)` and the null mapping are dropped because `count(*)` returns 0:
  `FROM (FROM Metric SELECT latest(kubelet_volume_stats_available_bytes) AS avail, derivative(kubelet_volume_stats_available_bytes, 1 second) AS slope FACET namespace, persistentvolumeclaim LIMIT MAX) SELECT count(*) WHERE avail + slope * 604800 < 0 SINCE 1 day ago`
  `predict_linear(x[1d], t)` is read as `latest(x) + slope * t` (4.11). The fixed `SINCE` is outside what the IR can write today.
- Panel 2 (table), approximate (`unless` as `NOT IN`; the `1 - max` value is meaningless and dropped):
  `FROM Metric SELECT uniques(concat(namespace, '/', persistentvolumeclaim), 5000) AS 'PVC' WHERE metricName = 'kube_persistentvolumeclaim_info' AND concat(namespace, '/', persistentvolumeclaim) NOT IN (FROM Metric SELECT uniques(concat(namespace, '/', persistentvolumeclaim), 5000) WHERE metricName = 'kubelet_volume_stats_used_bytes')`
  Using `concat` in `WHERE` and inside `uniques` is UNVERIFIED; fall back to `persistentvolumeclaim` alone and a note.
- Panel 3 (line), approximate: the `> 80` filter cannot stay in a time series; draw all series with a line threshold at 90 and add the note:
  `FROM Metric SELECT latest(kubelet_volume_stats_used_bytes) / latest(kubelet_volume_stats_capacity_bytes) * 100 WHERE namespace IN ({{namespace}}) FACET namespace, persistentvolumeclaim LIMIT MAX TIMESERIES AUTO`

### 7.4 Fixture 4: SLO error budget (subquery, time functions, panel time override)

Tests: a subquery `[32d:1h]`, `month() == bool vector(${__to:date:M})`, `days_in_month()`, `on() group_left()` as scalar broadcast, `timeFrom: 30d`, recording-rule names with colons, a threshold line.

```json
{"uid":"fx-slo","title":"SLO error budget (Sloth style)","schemaVersion":39,"time":{"from":"now-6h","to":"now"}
 ,"templating":{"list":[
  {"name":"DS","type":"datasource","query":"prometheus"},
  {"name":"service","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(slo:sli_error:ratio_rate5m, sloth_service)","refId":"v"},"refresh":2,"multi":false,"includeAll":false},
  {"name":"slo","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(slo:sli_error:ratio_rate5m{sloth_service=\"$service\"}, sloth_slo)","refId":"v"},"refresh":2,"multi":false,"includeAll":false}
 ]},
 "panels":[
  {"id":1,"type":"stat","title":"Remaining error budget (month)","gridPos":{"x":0,"y":0,"w":8,"h":5},"targets":[{"refId":"A","expr":"1 - (sum_over_time((slo:sli_error:ratio_rate1h{sloth_service=\"$service\",sloth_slo=\"$slo\"} * on() group_left() (month() == bool vector(${__to:date:M})))[32d:1h]) / on(sloth_id) (slo:error_budget:ratio{sloth_service=\"$service\",sloth_slo=\"$slo\"} * on() group_left() (24 * days_in_month())))","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false}],"fieldConfig":{"defaults":{"unit":"percentunit","thresholds":{"mode":"absolute","steps":[{"color":"red","value":null},{"color":"orange","value":0.25},{"color":"green","value":0.5}]}},"overrides":[]}},
  {"id":2,"type":"stat","title":"SLI over the last 30 days","gridPos":{"x":8,"y":0,"w":8,"h":5},"targets":[{"refId":"A","expr":"1 - avg_over_time(slo:sli_error:ratio_rate5m{sloth_service=\"$service\",sloth_slo=\"$slo\"}[30d])","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false}],"fieldConfig":{"defaults":{"unit":"percentunit","decimals":3},"overrides":[]},"timeFrom":"30d"},
  {"id":3,"type":"timeseries","title":"Burn rate vs page threshold","gridPos":{"x":16,"y":0,"w":8,"h":5},"targets":[{"refId":"A","expr":"slo:current_burn_rate:ratio{sloth_service=\"$service\",sloth_slo=\"$slo\"}","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"burn rate"}],"fieldConfig":{"defaults":{"custom":{"thresholdsStyle":{"mode":"line"}},"thresholds":{"mode":"absolute","steps":[{"color":"green","value":null},{"color":"red","value":14.4}]}},"overrides":[]}}
 ]}
```

Expected:

- Panel 1: placeholder. No NRQL form; the markdown widget shows the title, the reason ("subquery, month() and days_in_month() have no NRQL equivalent") and the escaped original expression. Option for a human: rebuild as a New Relic service level (not read, UNVERIFIED).
- Panel 2, `viz.billboard`, exact for the recorded series (backticks required for the colon name):
  ``FROM Metric SELECT 1 - average(`slo:sli_error:ratio_rate5m`) AS 'SLI' WHERE sloth_service = {{service}} AND sloth_slo = {{slo}} SINCE 30 days ago``
  The 30-day override is a fixed `SINCE`; the picker must be ignored for this widget (IR gap, same as fixture 3).
- Panel 3, `viz.line` with a threshold line at 14.4, exact:
  ``FROM Metric SELECT latest(`slo:current_burn_rate:ratio`) AS 'burn rate' WHERE sloth_service = {{service}} AND sloth_slo = {{slo}} TIMESERIES AUTO``
- Prerequisite line in the report: the `slo:*` series are Sloth recording rules and must be remote-written.

### 7.5 Fixture 5: widgets with no direct equivalent (heat map, state timeline, gauge, repeat, overrides)

Tests: `format: heatmap`, a state timeline with value mappings, a gauge with min, max and thresholds, a repeated panel with two targets and a by-name override.

```json
{"uid":"fx-viz","title":"Heatmap, state timeline, gauge, repeat","schemaVersion":39,"time":{"from":"now-6h","to":"now"}
 ,"templating":{"list":[
  {"name":"DS","type":"datasource","query":"prometheus"},
  {"name":"job","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(up, job)","refId":"v"},"refresh":2,"multi":true,"includeAll":true},
  {"name":"instance","type":"query","datasource":{"type":"prometheus","uid":"${DS}"},"query":{"query":"label_values(node_uname_info{job=~\"$job\"}, instance)","refId":"v"},"refresh":2,"multi":true,"includeAll":true}
 ]},
 "panels":[
  {"id":1,"type":"heatmap","title":"Request duration","gridPos":{"x":0,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"sum by (le) (increase(http_request_duration_seconds_bucket{job=~\"$job\"}[$__interval]))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{le}}","format":"heatmap"}],"fieldConfig":{"defaults":{},"overrides":[]},"options":{"calculate":false,"yAxis":{"unit":"s"}}},
  {"id":2,"type":"state-timeline","title":"Targets up","gridPos":{"x":12,"y":0,"w":12,"h":8},"targets":[{"refId":"A","expr":"up{job=~\"$job\"}","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"{{instance}}"}],"fieldConfig":{"defaults":{"mappings":[{"type":"value","options":{"0":{"text":"DOWN","color":"red"},"1":{"text":"UP","color":"green"}}}]},"overrides":[]}},
  {"id":3,"type":"gauge","title":"CPU busy","gridPos":{"x":0,"y":8,"w":6,"h":6},"targets":[{"refId":"A","expr":"100 - avg(rate(node_cpu_seconds_total{mode=\"idle\",instance=~\"$instance\"}[5m])) * 100","datasource":{"type":"prometheus","uid":"${DS}"},"instant":true,"range":false}],"fieldConfig":{"defaults":{"unit":"percent","min":0,"max":100,"thresholds":{"mode":"absolute","steps":[{"color":"green","value":null},{"color":"orange","value":70},{"color":"red","value":90}]}},"overrides":[]}},
  {"id":4,"type":"timeseries","title":"Load $instance","gridPos":{"x":6,"y":8,"w":18,"h":6},"targets":[{"refId":"A","expr":"node_load1{instance=\"$instance\"}","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"load1"},{"refId":"B","expr":"count(count by (cpu) (node_cpu_seconds_total{instance=\"$instance\"}))","datasource":{"type":"prometheus","uid":"${DS}"},"legendFormat":"cores"}],"fieldConfig":{"defaults":{},"overrides":[{"matcher":{"id":"byName","options":"cores"},"properties":[{"id":"custom.lineStyle","value":{"fill":"dash"}},{"id":"color","value":{"mode":"fixed","fixedColor":"red"}}]}]},"repeat":"instance","repeatDirection":"v","maxPerRow":1}
 ]}
```

Expected:

- Panel 1, `viz.heatmap`, approximate and needs a data probe for the bucket boundaries:
  `FROM Metric SELECT histogram(http_request_duration_seconds_bucket, width: 2, buckets: 20) WHERE job IN ({{job}}) TIMESERIES AUTO`
  `width` and `buckets` come from `SELECT uniques(le)`; Prometheus buckets are not linear.
- Panel 2 (state timeline), different design; the `0 -> DOWN`, `1 -> UP` mapping is lost. Emit a line chart that keeps the history, and say so:
  `FROM Metric SELECT latest(up) WHERE job IN ({{job}}) FACET instance LIMIT MAX TIMESERIES AUTO`
  or a faceted billboard of the current value with a critical threshold on 0 (set the Warning threshold higher than Critical to highlight a low value, DOCS N8).
- Panel 3 (gauge): a Gauge chart exists (DOCS N8) but its widget id is not known to the IR; emit `viz.billboard` with warning 70 and critical 90:
  `FROM Metric SELECT 100 - 100 * filter(rate(sum(node_cpu_seconds_total), 1 second), WHERE mode = 'idle') / filter(uniqueCount(cpu), WHERE mode = 'idle') WHERE instance IN ({{instance}}) FACET instance`
  (UNVERIFIED: the `uniqueCount(cpu)` core count per facet.)
- Panel 4 (repeat by `instance`): one widget instead of N, approximate; the second target and the dashed red override (by name `cores`) are lost, because `AS` labels do not show when a `FACET` is present (DOCS N1):
  `FROM Metric SELECT latest(node_load1) WHERE instance IN ({{instance}}) FACET instance TIMESERIES AUTO`
  and a separate widget `FROM Metric SELECT uniqueCount(cpu) WHERE instance IN ({{instance}}) FACET instance` for the core count.

## 8. Files and reproduction

Downloads are cached under `/tmp/gdr` (not committed): `gc/<id>.json` (51), `kp/*.json` (33), `mimir/`, `loki/`, `tempo/` (24), `nrqall/` (558 quickstart dashboards), `qscfg/` (quickstart config files), `docs/` (documentation sources), `gh/nrq-tree.json` (repository tree). Scripts are under `/tmp/gdrs`: `measure.py` (per-dashboard counts), `tiers.py` (T1 to R2), `report.py`, `shapetable.py`, `mapping.py`, `overlap.py` (metric-name overlap), `nrqfeat.py` (quickstart NRQL features), `fixtures.py`. A reader can regenerate every number in section 3 from the URLs in section 2. GitHub API calls were limited to a handful of listings and one tree read; the documentation tree read hit the unauthenticated rate limit, so the `.mdx` files were fetched by path from `raw.githubusercontent.com` instead.

No site blocked access: grafana.com, docs.newrelic.com, api.github.com (until its rate limit) and raw.githubusercontent.com all answered `curl -sSL`.

## 9. UNVERIFIED

1. No NRQL in this note was executed. Every recipe is built from documentation and from queries that New Relic published in its quickstarts. Syntax and results are unchecked.
2. Whether New Relic's PromQL-to-NRQL translator can be called outside the query builder UI (N6 describes only the UI mode).
3. `JOIN` on `Metric` data. The documentation restrictions ("The subquery cannot be a metric row-wise query", "The JOIN key cannot be a complex attribute, like a metric value") do not say whether a faceted `Metric` aggregation is allowed; no quickstart does it.
4. `substring()`, `concat()`, `capture()` and `aparse()` on `Metric` dimension attributes, and `concat()` inside `WHERE` and inside `uniques()`; `FACET concat(...)` and `FACET if(...)` over `Metric` are used in quickstarts, the others are not.
5. `conditional()`, `irate()`, and the `OR` fallback between two aggregates are used in quickstarts but not in the NRQL reference pages I read.
6. Whether `bucketPercentile()` interpolates inside a bucket the way `histogram_quantile` does, the names of the result series with several percentiles, and the behaviour of `AS` on them.
7. `COMPARE WITH`, and any fixed `SINCE`/`UNTIL`, together with the dashboard time picker.
8. The widget ids and option names for gauge, bullet, heat map and histogram in NerdGraph and Terraform; the unit strings; whether faceted area series stack.
9. Whether nested variables are available in the owner's New Relic plan (the two documentation pages disagree).
10. How a quickstart is installed programmatically, and the licence of the quickstarts repository.
11. Whether Prometheus remote write forwards recording-rule results and `up` unchanged (it should, since they are stored series, but I did not read it).
12. New Relic service levels as a target for SLO dashboards: not read.
13. The `TIMESERIES` bucket-count limit for widgets (N6 gives 366 steps only for the PromQL mode).
14. Effort sizes (S, M, L) in section 6.2.
15. The verdict column in 5.3: "enough scope" for Q is my judgement; only the name overlap is measured.
16. The regex classifier in section 3 has an error of a few percent; R2 is a statement about recipes on paper.
17. Pyrra dashboards were not fetched; grafana/grafana devenv dashboards were not re-measured (P1 has them); `grafana.com` ids were chosen by download count and keyword search, so the sample leans to popular dashboards.
18. That New Relic has no Grafana-specific dashboard import: I read the dashboards documentation pages and tried several likely URLs, which returned 404. This is not an exhaustive search.
19. That `FACET dimensions()` on a large cluster exceeds the 5,000-facet limit (inference).
20. The meaning of N5's second example, which writes a `[30s]` rate window as the `rate()` interval (`rate(sum(...), 30 seconds)`); I read it as a per-30-second rate by the definition of `rate()`.
