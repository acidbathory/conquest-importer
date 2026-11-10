# Grafana test estate: sources and licences

Date: 2026-11-10. Used by `docs/research/grafana-test-estate-results.md`.

## What the estate is

- 50 dashboards in the owner's Grafana Cloud stack, all with title prefix `ckg-` and uid `ckg-<grafana.com id>`.
  They sit in `ckg-importer-test` (six dashboards from an earlier session) and the nested folders
  `ckg-estate-infra`, `-kubernetes`, `-data`, `-edge-apps`, `-observability`.
- 55 alert rules in `ckg-estate-alerts`, all created paused. 5 contact points (`ckg-*`, addresses at
  `example.invalid`) and 3 mute timings (`ckg-*`). The notification policy tree was not touched.
- The ids created are in `out/live-estate/manifest.json` (git-ignored). `scripts/live-test/grafana-sandbox/cleanup_estate.py`
  deletes exactly those entries (not run).

## Dashboard sources

All dashboards were downloaded from the public grafana.com API
(`https://grafana.com/api/dashboards/<id>/revisions/latest/download`) during the research of 2026-11-09 and kept in
`out/live-estate/src/` (git-ignored; not committed, not redistributed). Download counts and authors below come from
`https://grafana.com/api/dashboards/<id>` on 2026-11-10. Selection: the most downloaded and the largest or most
complex dashboards of the research corpus (108 files), kept to one per product family where possible, plus families
with repeated rows (Alertmanager, JVM), nested variables (Kubernetes / Views, Node Exporter Full), transformations
(K8S Dashboard, Billing/Usage), and Loki or mixed Prometheus and Loki data sources.

Licence: the grafana.com API has no licence field and the listing pages do not state one per dashboard. The terms of
grafana.com content apply and are UNVERIFIED for redistribution. Nothing from the dashboards is committed to this
repository; only counts and panel titles are quoted in the results note. Authors (orgName) are listed so a person can
check the terms of each. Dashboards that come from open-source projects (the dotdc "Kubernetes / Views" set, the
kubernetes-mixin "Kubernetes / ..." set, Node Exporter Full, ArgoCD, Istio, CoreDNS) are Apache-2.0 or MIT in their
upstream repositories, but the grafana.com copy was not compared with the upstream licence file: UNVERIFIED.

Not included although asked for, with the reason: Tempo and Kafka (no popular Prometheus dashboard in the research
corpus that is not CloudWatch-based), SNMP and GitLab (not in the corpus), Windows exporter (id 12006 in the corpus is
the Kubernetes API server dashboard, not the Windows one), library panels (the corpus has none, and creating library
panels was not in the owner's authorisation). Dashboards that only use CloudWatch, Postgres SQL or the Grafana Cloud
cardinality data source were left out because the stack has no such data source.

| grafana.com id | title | author (orgName) | downloads | updated |
|---|---|---|---|---|
| 1860 | Node Exporter Full | rfmoz | 149,895,136 | 2026-10-31 |
| 14584 | ArgoCD | Stian Øvrevåge | 74,807,432 | 2021-06-14 |
| 9614 | NGINX Ingress controller | gonzalesraul | 73,628,564 | 2021-02-06 |
| 7645 | Istio Control Plane Dashboard | istio | 64,687,064 | 2026-11-09 |
| 7636 | Istio Service Dashboard | istio | 61,504,981 | 2026-11-09 |
| 14981 | CoreDNS | egg82 | 59,208,449 | 2021-09-26 |
| 13659 | Blackbox Exporter (HTTP prober) | harloprillar | 53,106,378 | 2020-12-31 |
| 10991 | RabbitMQ-Overview | RabbitMQ | 52,368,781 | 2025-04-17 |
| 14055 | Loki stack monitoring (Promtail, Loki) | QuortexLegacy | 48,791,235 | 2021-04-09 |
| 15661 | K8S Dashboard | leospbru | 35,478,431 | 2025-06-27 |
| 2583 | MongoDB | saada | 31,663,996 | 2020-11-04 |
| 4701 | JVM (Micrometer) | mweirauch | 31,370,397 | 2024-11-22 |
| 13332 | kube-state-metrics-v2 | garysdevil | 30,002,555 | 2022-09-07 |
| 11835 | Redis Dashboard for Prometheus Redis Exporter (helm stable/r | 2C2R | 28,235,859 | 2020-03-02 |
| 315 | Kubernetes cluster monitoring (via Prometheus) | Instrumentisto Team | 22,716,326 | 2023-03-20 |
| 6417 | Kubernetes Cluster (Prometheus) | sekka1 | 21,852,115 | 2018-06-06 |
| 9578 | Alertmanager | Martin Chodur | 21,785,174 | 2019-04-15 |
| 7362 | MySQL Overview | nasskach | 21,146,672 | 2018-08-07 |
| 14900 | Nginx | opstreedevops | 20,816,683 | 2022-06-03 |
| 747 | Kubernetes Pod Metrics | Ore Olarewaju | 15,784,614 | 2016-10-26 |
| 15760 | Kubernetes / Views / Pods | dotdc | 14,176,020 | 2026-09-20 |
| 9628 | PostgreSQL Database | Lucas Estienne | 13,522,645 | 2024-12-06 |
| 19993 | ArgoCD / Operational / Overview | adinhodovic | 13,316,534 | 2026-08-16 |
| 15758 | Kubernetes / Views / Namespaces | dotdc | 13,177,193 | 2026-03-24 |
| 12485 | PostgreSQL Exporter | egmont1227 | 13,070,658 | 2020-06-17 |
| 15759 | Kubernetes / Views / Nodes | dotdc | 12,002,367 | 2025-11-01 |
| 15757 | Kubernetes / Views / Global | dotdc | 11,435,306 | 2025-02-11 |
| 14191 | Elasticsearch Exporter Quickstart and Dashboard | Grafana Labs | 9,675,222 | 2021-04-06 |
| 15761 | Kubernetes / System / API Server | dotdc | 9,376,200 | 2026-03-24 |
| 15762 | Kubernetes / System / CoreDNS | dotdc | 8,288,497 | 2025-11-01 |
| 3662 | Prometheus 2.0 Overview | jeremy b | 6,827,652 | 2020-12-23 |
| 11176 | VictoriaMetrics - cluster | VictoriaMetrics | 6,333,077 | 2026-11-04 |
| 10619 | Docker Container & Host Metrics | dtuygulamayonetimi | 6,317,833 | 2019-07-26 |
| 11378 | Spring Boot 2.1 System Monitor | malltshik | 5,801,347 | 2019-12-12 |
| 7651 | Billing/Usage | Grafana Labs | 5,225,063 | 2026-11-06 |
| 6671 | Go Processes | galexrt | 4,332,058 | 2018-06-26 |
| 15987 | Kyverno | adonaicosta | 3,585,826 | 2022-03-28 |
| 16361 | Kubernetes / Kubelet | leohoster | 3,529,433 | 2022-06-02 |
| 16364 | Kubernetes / Compute Resources / Cluster | leohoster | 3,519,779 | 2022-06-02 |
| 16368 | Kubernetes / Compute Resources / Pod | leohoster | 3,519,649 | 2022-06-02 |
| 12904 | Hashicorp Vault | nilune | 3,035,140 | 2020-10-09 |
| 15983 | OpenTelemetry Collector | Monitoring Artist | 2,856,550 | 2026-09-25 |
| 17346 | Traefik Official Standalone Dashboard | Traefik Labs | 2,597,331 | 2024-08-02 |
| 19105 | Prometheus | dotdc | 1,946,643 | 2026-03-24 |
| 6483 | ElasticSearch | dcwangmit01 | 1,731,008 | 2019-11-25 |
| 11074 | Node Exporter Dashboard EN 20201010-StarsL.cn | StarsL.cn | 1,711,861 | 2023-07-07 |
| 10826 | Go Metrics | Arthur Aukhatov | 1,634,871 | 2025-06-10 |
| 12693 | HAProxy | rfmoz | 1,438,734 | 2026-10-31 |
| 15141 | Loki Kubernetes Logs | Jon Skarpeteig | 1,428,761 | 2021-10-22 |
| 14282 | Cadvisor exporter | kokorinav | 168,216 | 2021-04-21 |

## Alert rule sources

The 55 rules are written in `scripts/live-test/grafana-sandbox/estate_rules.py`. They are paraphrases (re-typed in the
Grafana query, reduce, threshold form, with shortened label selectors) of rules in these Apache-2.0 projects, read from
memory of their published `PrometheusRule` files, not copied from a fetched file: UNVERIFIED that each expression matches
the current upstream text.

| Project | Licence | Rules used |
|---|---|---|
| kubernetes-monitoring/kubernetes-mixin (apps, resources, storage, system) | Apache-2.0 | 16 |
| prometheus/node_exporter node-mixin | Apache-2.0 | 10 |
| prometheus/prometheus mixin, prometheus/alertmanager mixin, prometheus-operator | Apache-2.0 | 9 |
| Written for this estate (HTTP ratio, latency regression with offset, JVM, Kafka lag, Postgres, Redis, blackbox, recording-rule references, top-k) | project | 10 |
| Loki log rules (count_over_time, bytes_over_time) | project | 3 |
| Multi-expression shapes (AND of two thresholds, math ratio, within-range, reduce mean with KeepLast, cross data source) | project | 5 |
| Total | | 55 |

Shapes covered: rate and increase, histogram_quantile, absent, predict_linear, deriv, changes, ratios, comparison filters,
`on()`/`group_left`, `offset`, `topk`, `without`, regex and negative matchers, `for` from 0s to 4h, `noDataState` NoData,
Alerting and OK, `execErrState` Error, Alerting, OK and KeepLast, recording-rule names with colons, and three of the
rules routed to a contact point or mute timing through per-rule notification settings.

An email contact point was refused by Grafana Cloud (the address is not a member of the organisation), so 5 of 6
planned contact points exist.
