# Proposal: add `datadog` to docs/improvement/baseline.json

Not applied. `docs/improvement/baseline.json` was not regenerated; the owner approves the baseline.

`FIXTURES` in `src/importer/core/improve.py` now lists `datadog: ["tests/fixtures/datadog"]` (70 real
integration monitors plus 14 synthetic monitor cases; the dashboards in the same tree are skipped by the
loader). `compare` only reads sources already in the baseline, so the gate passes without the change.

Measured now (`importer improve-measure`): 84 monitors, 54 converted (all approximate, rate 64.29 percent),
28 manual, 2 unsupported (the unknown-type and the all-null hostile cases). The 70 real monitors alone:
50 converted, 20 manual (see `docs/research/datadog-baseline.md`).

To approve: run `uv run importer improve-measure --out docs/improvement/baseline.json` on a clean tree after
reviewing the numbers above, and commit the file with the datadog block. Any later drop in `converted` for
`datadog` will then fail the gate.

Review before approving: the top blockers are anomaly (2), change alert (2), formula (2), `as_rate` (2) and
one-day windows (2). All have a reason, steps and a playbook; none has an unguided reason.
