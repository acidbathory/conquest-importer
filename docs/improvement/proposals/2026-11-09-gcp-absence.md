# Proposal: convert Google metric-absence conditions

Branch `improve/gcp-absence` (3 commits on main at 3a29fbf). Status: waiting for the owner.

## Blocker

Every Google `conditionAbsent` condition was manual ("a metric-absence condition has no
automatic conversion"). It was the most common fixable blocker in the GCP fixtures.

## Change

- An absence condition becomes a Heartbeat signal: `SELECT count(*) FROM Metric WHERE
  metricName = '<gcp name>'` (plus label filters and `FACET` for group-by), threshold below 1,
  with an expiration equal to the duration.
- New Relic polls every 5 minutes, so the window is at least 300 seconds and the duration is a
  whole number of windows. Each adjustment is written in the condition's notes.
- Stays manual, with steps: unmappable filter, missing `metric.type`, uncollected metrics
  (custom, agent, Prometheus, log-based, uptime checks), several aggregation stages,
  unsupported aligners or reducers, windows above New Relic's limits, label keys New Relic
  would reject.
- Shared parts of the threshold and absence paths were extracted, not copied.
- `check-gcp-data` now checks absence conditions with their own query. It reports a metric that
  never arrives as blind, because such a condition fires as soon as it is enabled.
- A dropped group-by field is now noted, and group-by fields are de-duplicated.

## Result

| | Before | After |
|---|---|---|
| GCP fixtures converted | 2 of 4 (50%) | 3 of 4 (75%) |
| CloudWatch fixtures converted | 27 of 32 | 27 of 32 |
| Tests | 558 passed | 591 passed |

The remaining GCP blocker in the fixtures is the MQL condition (stays manual by design).
Gate: GATE PASSED, run again by the maintainer session after round 2.

## Review

An independent reviewer found six issues. Fixed in round 2: `check-gcp-data` mislabelled
absence conditions, dropped group-by fields were silent, out-of-range windows and invalid
label keys failed late at emit time instead of at translation, no emitter test, stale
`docs/ir-spec.md`. Open for the owner: an absence condition on a metric that never arrives
(for example VM CPU utilization) is still "approximate" with a note and is created disabled.

## Risks and gaps

- Not run against a live New Relic account or Google project. Whether `count(*)` with
  `metricName` and an expiration behaves as loss of signal on GCP data is unverified.
- Whether New Relic opens a violation per facet when only one facet stops is unverified.
- The multi-row sum in `check-gcp-data` is tested with a fake client only.

## Needs the owner

1. Merge the branch (or not).
2. Decide the metric-never-arrives case: keep approximate with a note, or make it manual.
3. Approve regenerating `docs/improvement/baseline.json` (GCP manual 2 to 1) after merging.
4. Approve a live check in the sandbox, with the cost named first.
