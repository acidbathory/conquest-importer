# Prometheus hints: exact metric types and series keys for the dashboard converter

No pinned hash or golden file changed in this branch, so no approval of a diff is needed. This is a
proposal to merge the branch.

## What it does

- New command `grafana-prom-hints --datasource <uid-or-name> --metrics-from <extract folder or
  converted IR> --out hints.json` reads, with GET only, `/api/v1/metadata`, the metric name list and
  `/api/v1/series` for only the metrics the dashboards name, through Grafana's data source proxy
  (caps on requests, series and bytes; fixed paths and parameters; host check, no redirects, token
  scrubbing, deadline).
- The hints file uses the name map format: new optional `types` (counter, gauge,
  histogram_bucket) next to the existing `series`. Old name maps load unchanged.
- `convert-grafana-dashboards --hints <file>` (repeatable) merges the hints into the name map. With a
  type present, the planner and the converter use it instead of the `_total/_count/_sum/_bucket`
  suffix guess: a gauge called `*_total` converts, a counter without a suffix is refused when read
  without rate(), and a percentile needs a histogram bucket. Without a type, behaviour is exactly as
  before.

## Before and after on the 19 real dashboards

211 widgets converted and 277 placeholders before; 211 and 277 with the hints the owner's tenant
could give (it has no metadata and none of the community metrics). A ceiling run that assumes every
gauge is a single series gives 241 and 247. Details in `docs/research/grafana-prom-hints-live.md`.

## Risks and limits

- A series key reflects the last hour of observed series; a gauge that gains a label later can make
  the key too weak. The key is also left out when a metric has more than 1000 series.
- The planner reads the types through a context variable set by `plan_dashboard(dash, types)`;
  files outside `sources/grafana/dashboards/` and `dashboard_convert/` are untouched.
- The live type path is unproven: both data sources returned empty metadata.
