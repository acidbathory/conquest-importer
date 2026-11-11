# Proposal: pinned GCP dashboard hashes after the remaining-blocker work

Branch `improve/gcp-remaining`. Status: waiting for the owner. Nothing was regenerated.

## What fails and why

`tests/test_dashboard_ir_byte_identity.py::test_every_output_file_is_byte_identical_to_the_recorded_hash[gcp]`
fails on purpose. The recorded hashes in `tests/fixtures/dashboard-output-hashes.json` cover the
one sample GCP dashboard (`58944063589550`); four of its output files now differ
(`dashboard-ir/58944063589550.json`, `nerdgraph/58944063589550.json`, `dashboards.tf`,
`report.md`). The cause is a better conversion of the same widgets, not a format change:

- A cross-series reducer now keeps its own function when the aligner differs. Before, Google's
  `ALIGN_MEAN` followed by `REDUCE_SUM` was written as `average(x)`, which gives the mean across
  series, not the sum. Now it is `sum(x)` where each series has one row in the window (a polled
  Google metric over 300s or less; for a metric you send yourself the note says it assumes one
  point per series per period). Over several polls the pair cannot be one query and the widget
  is manual with that reason.
- Scorecard and line-chart thresholds, and a unit override, are kept in the widget (they were
  notes "not migrated"); empty `gaugeView`/`sparkChartView` keys no longer add a note.
- Text boxes with a title and no content became markdown headings; incident lists became a
  table over `NrAiIssue`.

## What the owner is asked to do

Review the diff of those four files (run `importer convert-dashboards --source gcp --in
tests/fixtures/gcp-dashboards --out /tmp/x` on this branch and on `main`), then, if it is right,
regenerate the GCP entries of `dashboard-output-hashes.json`. The CloudWatch entries and
`tests/golden/` are untouched.

## Everything else

The gate's other steps (ruff, format, mypy strict, vulture) pass. The full measurement is in
`docs/research/gcp-estate-baseline.md`, section 10.
