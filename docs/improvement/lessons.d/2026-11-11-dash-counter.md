# 2026-11-11 dash-counter

- The differential harness found that the dashboard converter wrote `rate(sum(x), 1 second)` for a
  counter with no `_total`, `_count`, `_sum` or `_bucket` suffix, which New Relic stores as a gauge
  holding the running total (wrong chart). The alert path already refused it. The dashboard
  converter now uses its own `is_counter` (type hint first, then the name suffix) in `rate_leaf`,
  the same decision it already used for a bare counter selector, so the two paths agree.
- A wrong result is better as a refusal with steps: the new reason has a rule that tells a person to
  relabel with `newrelic_metric_type=counter` on remote write or to add a `counter` type hint.
- Tests that used a gauge (`up`) or an unsuffixed name under `rate()` only to test something else
  were changed to a counter name; the converter is right to refuse them now.
- Run tests with `PYTHONPATH=$PWD/src` in a worktree, or the editable install loads the main checkout.
