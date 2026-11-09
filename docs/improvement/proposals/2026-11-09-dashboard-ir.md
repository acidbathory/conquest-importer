# Proposal: dashboard IR and emitter extension (workstream A)

Branch `improve/dashboard-ir`, from `integration/all-threads` (a53a9a3). Round 2 (reviewer defects) is included below.

## What changed

Files: `src/importer/core/dashboard_ir.py`, `src/importer/core/emit/dashboard.py`, new `src/importer/core/dashboard_nrql.py`, the dashboard section of `docs/ir-spec.md`, tests `tests/test_dashboard_ir_{byte_identity,nrql,model,emit}.py`, fixtures `tests/fixtures/dashboard-output-hashes.json`, `tests/fixtures/dashboard-generated-hashes.json` and `tests/fixtures/newrelic-one-dashboard-schema.json`.

1. Table widget: `Visualization.TABLE` (`viz.table`, `widget_table`).
2. Raw NRQL: `IRWidget.raw_series` (`RawNrqlSeries`, no label), checked by `check_raw_nrql`. Rejected: SINCE, UNTIL, COMPARE WITH, `;` and fullwidth `;`, control, bidirectional and zero-width characters, unbalanced quotes, over 4096 characters. Pretty-printed queries (tabs, line breaks) pass. LIMIT stays allowed (owner decision). A chart uses `series` or `raw_series`, not both. The emitters check raw queries again.
3. `TIMESERIES AUTO`: `Series.auto_period` (auto wins over `period_seconds`; refused except on line and area).
4. Thresholds: line `threshold` with name, table `threshold` with column_name (no name), billboard `critical`/`warning` (one of each). Severity enum is `ThresholdSeverity`; values from provider docs only.
5. Options: unit, legend, y min/max, y zero in Terraform. Decimals and stacked are modelled, not emitted (UNVERIFIED). Numbers keep every digit (`number_text`).
6. Variables in both emitters, `check_variable_usage`, `check_variables_defined`. Account ids are strict integers below 10^15; defaults must be among enum items; one default unless multi-select.
7. Height: a widget above 12 is emitted at 12 in both emitters (row, column, width kept). The IR widget gets the reason `height N clamped to 12`, which `report.md` prints, and the dashboard description names the clamped widgets. `Layout` still accepts 100.
8. Layout packing: not moved (see requests).
9. Variable checks run only for a dashboard that defines variables or has raw series. AWS and GCP series keep `{{...}}` in labels and filter values as literal text.
10. Caps: 300 widgets per dashboard, 50 variables, 50 thresholds and 50 raw series per widget; numbers within 10^15.

## Byte-identity evidence

- Folder fixtures: 18 output files (IR, NerdGraph, `dashboards.tf`, `report.md`) for `cloudwatch-dashboards` and `gcp-dashboards`, hashed on `integration/all-threads` before any change (`tests/fixtures/dashboard-output-hashes.json`).
- Generated set: 120 dashboards from a seeded generator in `tests/test_dashboard_ir_byte_identity.py` (all widget types except table, facets, scope filters of every shape, hostile and 300-character titles, labels and filter values containing braces, heights up to 12, emittable and unemittable dashboards). The fingerprint of each (IR JSON, NerdGraph payload, Terraform, or the CannotEmit message) was taken by running the same generator against a `git archive` of `integration/all-threads` in `/tmp` with `PYTHONPATH` pointing at it (confirmed by printing `importer.__file__`). 73 of the 120 emit. Stored in `tests/fixtures/dashboard-generated-hashes.json`.
- The generated test failed on the round 1 code (only 39 of 120 emitted, because of the `{{...}}` checks) and passes after fix 9.

## Risks

- A clamped widget leaves a gap under it; the widgets below keep their rows. Whether NerdGraph accepts height above 12 is UNVERIFIED, so clamping is the safe choice.
- A model is mutable. The emitters re-validate raw queries only; other fields (a variable query, an option) are checked at construction.
- NerdGraph variable field names are UNVERIFIED. Widget options and thresholds are not written to NerdGraph.
- `terraform validate` in `test_terraform_for_the_new_fields_validates` passes here; it skips when the provider cannot be downloaded.
- Keyword detection is by position. A column named like a keyword and followed by something other than an operator (`SELECT as FROM Log`) is read as the clause; quote it with backticks.

## UNVERIFIED

Height 12 on NerdGraph; valid `units.unit` strings; `data_format` types for decimals; severity value list (docs only); NerdGraph id for stacked bar; NerdGraph variable and rawConfiguration option field names; New Relic's NRQL length limit (4096 is a repository cap); nested variables on the target account.

## Requests for other files (the integrator has taken 1 to 4)

1. `core/apply_dashboards.py` `with_account`: replace `ACCOUNT_ID_PLACEHOLDER` in `variables.dashboard.variables[*].nrqlQuery.accountIds`, with a guard.
2. Translators: clamp height to 12 (no longer needed for correctness, the emitters now clamp, but it removes the note).
3. `sources/gcp/dashboards.py`: move `pack()` and `_overlaps()` to a new `core/dashboard_layout.py`.
4. `core/emit/nrql.py` `widget_nrql`: optionally a nullable `period_seconds`; then `auto_period` can go.
5. NerdGraph widget options: after one sandbox read-back, extend `nerdgraph_dashboard` with the confirmed `rawConfiguration` names.
