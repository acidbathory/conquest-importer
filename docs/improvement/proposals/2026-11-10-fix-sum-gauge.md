# Proposal: sum() of a gauge no longer adds every sample

Measured live (live/grafana-dash-roundtrip): `sum(grafanacloud_org_info)` became `sum(metric)` over the range, 60 in New Relic against 1 in Grafana.

Change: for a gauge (not rate/increase), `sum` now converts to `latest()` in a single-value or table panel and `average()` per bucket in a chart. Counters are untouched. Note N_SUM_GAUGE says so. Tiles stay draft.

Before: `SELECT sum(grafanacloud_org_info) FROM Metric`
After: `SELECT latest(grafanacloud_org_info) FROM Metric` (chart: `SELECT average(...) ... TIMESERIES AUTO`)

Approximate when a group holds several series: latest/average then covers one series' value, not the cross-series sum (that needs a subquery with the series identity, which the converter cannot know).

Pinned output that changes (needs owner approval, not regenerated): `tests/test_emitter_byte_identity.py::test_output_bytes_are_unchanged[convert/grafana]`.
