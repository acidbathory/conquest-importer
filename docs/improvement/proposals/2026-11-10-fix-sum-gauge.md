# Proposal: sum() of a gauge is converted only when one series is proven

## The problem (measured live, live/grafana-dash-roundtrip)

New Relic `sum()` of a gauge adds every sample in the window, while Prometheus `sum()` adds the latest value of each series.
- `sum(grafanacloud_org_info)`: 60 in New Relic against 1 in Grafana (60 samples of value 1).
- Real node_exporter data, `sum by (mountpoint) (node_filesystem_avail_bytes)`: 4.01x too high per 60 s bucket (4 scrapes) and 41x as a stat over 10 minutes (41 scrapes).

An earlier fix on this line turned `sum()` into `latest()` (stat) or `average()` (chart). That is right only when a group holds exactly one series. For several series it returns one series' value (or their mean) where Grafana shows the total, with no sign that it is wrong.

## What changes

Plain English: the converter no longer guesses. A `sum()` or `count()` across the series of a gauge becomes a "Not migrated" placeholder with manual steps, unless the owner has said which labels name exactly one series.

- Hint: the optional name map (`IMPORTER_GRAFANA_NAME_MAP`) takes `"series": {"<metric>": ["<label>", ...]}`. The labels together name one series of that metric; an empty list means the metric has exactly one series (for example `grafanacloud_org_info`).
- Provably safe: the query groups by (`by (...)`) or pins with an equality matcher (`label="value"`) every hinted label. A regex, a `!=` or a dashboard variable does not pin (a variable may hold several values). Then the old single-series conversion applies: `latest()` in a stat or table, `average()` per bucket in a chart, with the note N_SUM_GAUGE.
- Everything else: placeholder with reason C_GAUGE_SERIES. The steps name the measured factors (4x per bucket, 41x as a stat, 60 against 1), the correct per-series pattern `SELECT sum(v) FROM (SELECT latest(metric) AS v FROM Metric FACET <labels> LIMIT MAX) SINCE 5 minutes ago`, the exact one-sample-window form, and the name map hint.
- `count()` of a gauge: `count()` in New Relic counts samples, not series, so it is always a placeholder (use `uniqueCount()` of a label that names one series).
- `avg`, `min`, `max` of a gauge: unchanged, still converted. Measured against the same data: `average()` equals the mean of the sent samples to floating-point precision (chart and stat, e.g. `avg(node_load1)`), and equals Prometheus within the bucket-versus-latest difference (up to 0.4 percent for memory in a chart; the stat of `avg()` is the mean of the window, not the latest, which the existing N_INSTANT note says). `min`/`max` over samples are the extremes in the bucket or window, equal to Prometheus' extremes across series when each series reports once per bucket, and the same instant-versus-window note applies. None of them multiplies by the sample count, so there is no silent factor like the one for `sum()`.

## Before and after

Before: `sum by (instance) (node_load1)` became `SELECT average(node_load1) FROM Metric FACET instance ...` for every dashboard, right only if `instance` named one series.
After: same query, no hint: placeholder "Not migrated". With `{"series": {"node_load1": ["instance"]}}`: the same `average()` query as before.

Also now manual unless hinted: the ratio `100 * (1 - sum(free) / sum(total))`. It was measured as matching (both sums add the same number of samples, so the factor cancels), but the converter cannot prove the sample counts are equal, so it follows the rule. Owners can hint both metrics.

## Fixture counts (widgets converted / placeholders, three dashboard fixture folders)

- grafana-dashboard-convert: 47 / 8 before, 37 / 18 after.
- grafana-dashboards: 25 / 25 before, 22 / 28 after.
- grafana-dashboard-exact: 9 / 1 before, 7 / 3 after.
- Total: 81 / 34 before, 66 / 49 after (15 more placeholders, all sum or count of a gauge).

Tests that need a converted gauge sum use a name map hint or `avg()`.

## Pinned output that changes (needs owner approval, not regenerated)

`tests/test_emitter_byte_identity.py::test_output_bytes_are_unchanged[convert/grafana]` fails by design until a person reviews the diff and regenerates `tests/fixtures/emitter-output-hashes.json`. No golden file was touched.

## Not verified

Offline only. The hint rule and placeholder were tested on converter output, not against a live New Relic account; the subquery pattern in the steps follows NRQL subquery syntax but was not run.
