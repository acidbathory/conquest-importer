# grafana-prom-hints: first live read of the owner's Grafana Cloud Prometheus data sources

Run 2026-11-10 on branch `improve/prom-hints`. GET requests only, through
`/api/datasources/proxy/uid/<uid>/api/v1/...`, with the command's own caps (600 requests, 1000
metrics, 1000 series per metric, 64 MB). No token or tenant name is recorded here. The system CA
bundle broke httpx on this network (`ConnectError`), so the runs used `SSL_CERT_FILE=/tmp/q/ca.pem`;
verification stayed on.

Input: the 19 dashboards in `live-grafana-dash/out/live-run/extract-all`. The command found 540
candidate metric names in their Prometheus queries (a few are words from odd expressions, such as
`customer` or `fractional`; they simply are not found).

## What the tenant holds

| Data source | `/api/v1/metadata` | `/api/v1/label/__name__/values` | series keys written |
|---|---|---|---|
| `grafanacloud-usage` (Prometheus) | empty (`data: {}`) | 125 names, all `grafanacloud_*` usage and billing metrics | 87 |
| `grafanacloud-prom` (Prometheus) | empty | 0 names | 0 |

Findings:

1. **Metadata is empty on both.** The usage data source is filled by Grafana itself with recorded
   series and no TYPE lines, and the owner's own data source holds no metrics yet. So the run produced
   0 exact types. The `types` path is covered only by offline fixtures shaped like the Prometheus
   API; it has not been seen on a live stack. Prometheus or Mimir stacks that receive remote write
   with metadata should fill it.
2. Because metadata can be empty, the command now also lists metric names
   (`label/__name__/values`, one request) and reads the series of any metric that exists but has no
   type. It records the key labels for those, so `sum()` of a gauge still gets its hint.
3. The 87 series keys are real observations: for example `grafanacloud_instance_alertmanager_alerts`
   needs `state`, `grafanacloud_instance_info` needs `name`, and the `grafanacloud_org_*` billing
   gauges are single series (`[]`). 90 requests in all.
4. The community dashboards (node exporter, JVM, kube-state-metrics, cAdvisor, NGINX, PostgreSQL)
   use metrics that this tenant does not hold, so their gauges got no hint.

## Effect on the 19 dashboards

| Run | Widgets converted | Placeholders |
|---|---:|---:|
| Before (no hints) | 211 | 277 |
| With the live hints (87 series keys, 0 types) | 211 | 277 |
| Ceiling: every gauge in the dashboards declared a single series (`[]`), a hypothetical file, not data | 241 | 247 |

The live hints changed nothing. I did not trace each of the 30 panels refused for "sum() or count()
across the series of a gauge"; the ceiling row shows they are all blocked by the series gate, and the
87 observed keys did not satisfy any of their queries (the query must group by or pin every key
label). The ceiling row is the most the series gate could free (30
widgets); how many it frees for real depends on running the command against the Prometheus that
holds the node exporter, JVM and kube-state metrics. The 3 panels refused for a bare counter-looking
name (Softnet RPS, Load, Idle sessions) need real metadata to decide.

## What remains

- Run the command against a Prometheus that has the community metrics and ingested metadata.
- Counter or gauge for a metric with no metadata and no series is still the name-suffix guess.
