# GCP data path: what changes in the pinned outputs, and why

Hashes were NOT regenerated. A person must read this, check the diff and pin.

## The change

Google metrics that New Relic's integration does not poll (Ops Agent, Managed Prometheus, custom,
external) used to stop a condition or widget as "not one of the Google Cloud service metrics". Under
the owner's policy they now convert, with the metric name as it will arrive, and the item says which
data path the customer must set up first (`data_path`). Log-match conditions are marked
`needs-log-forwarding`. Items on polled metrics are `native`, which is left out of the saved IR so
that native-only output stays byte for byte as it was.

## Pinned files that change (all in the GCP fixtures)

1. `tests/fixtures/dashboard-output-hashes.json`, group `gcp`: the dashboard `ops-mosaic`
   (`dashboard-ir/58944063589550.json`, `nerdgraph/58944063589550.json`, `dashboards.tf`) and
   `report.md`. Cause: its widget "Custom metric" (`custom.googleapis.com/orders`) was a "Not
   migrated" placeholder and is now a chart with `data_path: needs-custom-ingest`; the report gains a
   "Data path" section. This is the intended gain (+1 chart in the fixture).
2. `tests/fixtures/emitter-output-hashes.json`, group `convert/gcp`: `ir/92147052606053.json` (the
   log-match condition now carries `"data_path": "needs-log-forwarding"`) and `report.md` (a "Data
   path" section and the same note). No `.tf` or NerdGraph file changes for alerts.
3. `tests/fixtures/ir-before-grafana.json` (checked by `test_the_saved_ir_of_the_other_sources_is_byte_identical_to_before_grafana`):
   the same `gcp/ir/92147052606053.json`.

Nothing else changed: AWS, Dynatrace and Grafana outputs are identical, and native-only GCP items
did not change.

## Test edits (not weakened)

- `tests/test_gcp.py`: the test that said custom, Ops Agent and Prometheus metrics stay manual now
  covers the metrics with no route (log-based, unknown domain). The same metrics are tested to
  convert in `tests/test_gcp_datapath.py`.
- `tests/test_gcp_dashboards.py`: "Custom metric" moved from the failed-widget list to its own test
  that checks the chart and its `data_path`.

## What is UNVERIFIED

Names of `agent.googleapis.com/*` metrics in New Relic (no documented mapping to OpenTelemetry host
metrics; the hint table is our reading), receiver names for `workload.googleapis.com/*`, the
names of custom metrics, label attribute names on sent data, and log attribute names. None was
checked against a live account.
