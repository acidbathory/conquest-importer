# Proposal: an offline differential harness for PromQL rewrites, and one finding for the owner

## What changed, in plain English

There is now a way to check, without Prometheus or New Relic, whether the NRQL the converter writes computes what the PromQL computes. It generates seeded synthetic series (counters with resets, gauges, histogram buckets, missing samples), evaluates the PromQL with a reference evaluator we wrote, evaluates the converted NRQL with a small model of New Relic's behaviour, and gives each case one verdict: IDENTICAL, EQUAL_WITHIN_TOLERANCE, DIFFERS_AT_EDGES or WRONG. Results are in `docs/research/promql-differential-results.md`.

It is evidence, not proof: both sides are our own readings. It never changes a converter confidence; the suggested level it prints is advice.

Files: `src/importer/parity/diff_series.py`, `diff_promql.py`, `diff_nrql.py`; `scripts/analysis/promql_diff_harness.py` and `promql_diff_cases.json` (20 added cases); `tests/test_promql_diff_harness.py`.

## Finding for the owner (decision needed)

`DIFF-RATE-D03`: `sum(rate(nginx_requests[2m]))` on a dashboard becomes `SELECT rate(sum(nginx_requests), 1 second) ...`. When the counter has no `_total`/`_count`/`_bucket` suffix New Relic stores it as a gauge holding the running total, so the chart shows the total divided by the bucket, not a rate. This is finding 1 of `docs/research/telemetry-rig-results.md`, reproduced offline. The alert reader already refuses it (`DIFF-RATE-A05`). The real fix is on the data path (relabel the metric to a counter type), not in the rule. Options: make the dashboard reader refuse or add a note unless the name map says the metric is a delta, or leave it. The rule was not changed; an xfail test records it.

## Request for the integrator

None. No frozen file was touched.

`DIFF-GAUGE-D03`: a gauge whose name ends in `_count`, declared a gauge in the name map, is written as `average(x)` but stored as a delta by the name-suffix rule unless it is relabelled to a gauge type (rig finding 2). Fix is the same relabel; no rule change.
