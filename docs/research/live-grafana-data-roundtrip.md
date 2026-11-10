# Live data round trip: Grafana Prometheus data to New Relic, read back with converted NRQL

Run 2026-11-10 (owner authorised for this task). Account and keys come from `.env`; none is recorded here.

## What was done

1. Listed every converted NRQL in `out/live-grafana/conv-builtin` and `conv-ckg` (9 Metric queries over 3 families: `grafanacloud_org_info`, the two `grafanacloud_logs_instance_cloud_logs_export_*` metrics, and `kube_node_*`).
2. Asked the Grafana stack (datasource proxy, read-only) for the last hour of each. Only `grafanacloud_org_info` (datasource `grafanacloud-usage`) has series: 1 series, 61 points. The export metrics and `kube_*` do not exist in any datasource of the stack (`grafanacloud-prom` has no series at all; `grafanacloud-usage` has 125 metric names, none of these). No data was invented for them.
3. `scripts/live-test/grafana-sandbox/send_metrics.py` posted the series to the Metric API as a `gauge` (name has no `_total`/`_count`/`_sum` suffix), labels as attributes, plus `ckg_test=true` and `prometheus_server=ckg-sandbox`, timestamps shifted so the newest is now. Response 202. Caps: 20 series, 120 points per series; TLS verification on.
4. Ran the converted NRQL read-only through NerdGraph and compared with Grafana.

## Results

| Widget (dashboard) | Converted NRQL | Source PromQL | New Relic | Grafana | Verdict |
|---|---|---|---|---|---|
| Org variable (Billing/Usage) | `SELECT uniques(org_slug) WHERE metricName='grafanacloud_org_info'` | `label_values(grafanacloud_org_info, org_slug)` | `["blackmerlin1056"]` | same one label value | match |
| Service Status (Cloud Logs Export Insights) | `sum(..._export_status)` | `sum(..._export_status)` | 0.0 (no data) | no series | no data at source, not verifiable |
| Logs data copied / exported 24h, 7d, 30d | `sum(..._exported_bytes)`, `rate(sum(..), N)` | `increase(..._exported_bytes[..])` | 0.0 (no data) | no series | no data at source, not verifiable |
| Number Of Nodes, Nodes Unavailable, node number, cluster (kube-state-metrics) | `sum(kube_node_*)`, `uniques(cluster)` | `kube_node_*` | 0.0 (no data) | no series | no data at source, not verifiable |

Ingested check: 60 of the 61 points were found in the last hour (`count(*)`), `latest(grafanacloud_org_info)` = 1.0 (Grafana: 1), `ckg_test` = true, all 8 labels arrived with the same names, `metricName` is the metric name (no label collision in this series). Chart-shaped probe on the same data, `sum(grafanacloud_org_info)`: with `TIMESERIES AUTO` each 1-minute bucket is 1.0 (Grafana: 1); without it the value is **60.0**.

So: 1 widget with source data, 1 returned data, 1 matched. The 8 chart-type queries ran without error (so the `sum(metric)` and `rate(sum(metric), 1 day)` shapes are valid NRQL on `Metric`) but have nothing to compare with, because this Grafana stack has no such data.

## Mismatch and root cause (measured)

`sum()` of a gauge over a time range adds every sample: 60 samples of value 1 give 60, while Grafana's instant `sum(m)` gives 1 (the latest value per series, summed). This is the units/aggregation problem the converter already warns about (the stat widgets carry the note "sum() of a gauge adds every sample in the bucket" and "value is taken over the whole time range"); the live data confirms it is a real numeric difference, not only a wording caution. Timeseries widgets are not affected when the bucket holds one sample per series. No converter change was made: the correct form depends on how many series exist, which the converter cannot know (a safe rewrite would be `latest()` per series, then a sum over a FACET, which a stat widget cannot show).

## What remains

- A converted chart over a metric whose source has data and many series (needs a Grafana stack with node or kube metrics, or the owner's real Prometheus).
- Counters sent as delta `count` (supported by `send_metrics.py` via `NAME:count`) were not exercised: no counter exists in the usage datasource among the converted widgets.
- Nothing was created in or removed from New Relic apart from the 60 metric points tagged `ckg_test=true`; the ckg- dashboards were not touched and the previous apply is still blocked, so the widgets above were checked by running their NRQL, not by viewing the dashboards.
