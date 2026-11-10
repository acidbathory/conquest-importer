# Proposal: an advanced remediation cookbook, and ranked methods in `plan-remediation`

Additive. No frozen file, golden file, hash file or baseline changed, and no hash file was regenerated.

## What was added

- `docs/user/remediation-cookbook.md`: 18 blockers (9 dashboard, 9 alert), each with 3 to 6 ranked methods, a
  decision tree, the questions to ask the customer, and what New Relic cannot do. Generated from
  `src/importer/core/cookbook/data/*.toml` by `scripts/improve/render_cookbook.py`; a test fails if the two differ.
- `core/cookbook/`: the data model and loader. A blocker names the remediation rule titles it explains and may add
  a regex over variant, reasons and query, so one Grafana reason (comparison or vector matching) reaches the right
  blocker (comparison, joins).
- `scripts/improve/verify_cookbook.py`: runs every NRQL example read-only against the sandbox account, runs the
  value checks, fetches each documentation URL, and writes `docs/improvement/cookbook-verification.json`.
  Tests read that record; nothing in the test suite uses the network.

## Pinned output that changes (not hash-pinned)

`importer plan-remediation` now adds, per group with a cookbook entry, a "Cookbook methods (ranked)" block, a
"What to ask the customer" list and a "recommended method" line under each checklist item. `remediation-plan.csv`
gets two columns appended at the end: `cookbook_recommended`, `cookbook_alternatives`. All earlier lines and the
first twelve columns are unchanged, and items without a cookbook entry print exactly as before. No test or hash
file pins the plan text.

## Limits

- VERIFIED-* applies to the NRQL in a method. Collector files, Prometheus rules, Terraform and GraphQL mutations
  were read in documentation and are DOC-ONLY. No condition, workflow or muting rule was created.
- Not verified: `bucketPercentile` and `predictLinear` inside an alert condition (the documentation is silent),
  and that Slack and Teams destinations can be created without a person.
- Sandbox numbers move because a telemetry rig keeps adding data; the record states the figures of the run.
