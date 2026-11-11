# Sample alert policies for the field-coverage tests

Each `*.json` file here is one alert policy copied unchanged from
https://github.com/GoogleCloudPlatform/monitoring-dashboard-samples (branch `master`, folder
`alerts/`), licence Apache-2.0 (`LICENSE` in that repository). They were fetched on 2026-11-11 with
`curl` from `https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/<folder>/<file>`.
The file name here is `<folder>__<file>` (one file had a `%20` in its name, written as `-`).

They are the threshold (18), metric-absence (5) and log-match (8) samples listed in
`docs/research/gcp-alerting-verified.md` section 5.1 (rows 6 to 36). The MQL and PromQL samples are
left out on purpose: those conditions are not handled by this converter.

## Conditions converted (placeholders never count; manual is not converted)

| Kind | Samples | Before | After |
|---|---|---|---|
| conditionThreshold | 18 | 3 | 4 |
| conditionAbsent | 5 | 3 | 4 |
| conditionMatchedLog | 8 | 5 | 8 |
| Total | 31 | 11 | 16 |

Before is the converter at integration/all-threads `0d7e4a5`; after is this branch. The numbers are
pinned by `tests/test_gcp_fields_samples.py`. The 15 that stay manual: 14 use metrics that New
Relic's Google Cloud integration does not collect (custom, Ops Agent and Prometheus metrics, which
would return no data) and one needs a percentile of per-series maxima over 3600 s, which NRQL
cannot express.
