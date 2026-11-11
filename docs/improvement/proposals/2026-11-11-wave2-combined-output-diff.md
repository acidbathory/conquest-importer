# Proposal: what changes in the pinned output after wave 2, in plain English

Branch `integrate/wave2`. Status: waiting for the owner. Nothing was regenerated, and `tests/golden`
is untouched.

## The short version

Eleven finished branches were merged (Google names, Google extras, Google remaining blockers, four
Azure branches, CI hygiene and three Elastic research notes). Only one automated check fails because
of the merge, and it fails on purpose: the check that compares the sample Google dashboard, byte for
byte, with a saved fingerprint. One widget on that dashboard now says something different, and the
change is a correction. Nothing else that is pinned moved: not the Google alert files, not the
notification files, not the saved IR of any source, not the reason categories, not the AWS
CloudWatch dashboards, not any Azure output.

## Which pinned files change

The test is `tests/test_dashboard_ir_byte_identity.py::test_every_output_file_is_byte_identical_to_the_recorded_hash[gcp]`.
It compares every file written by `importer convert-dashboards --source gcp` on
`tests/fixtures/gcp-dashboards` with the fingerprint in `tests/fixtures/dashboard-output-hashes.json`.
Four of the files written for the one sample dashboard (`58944063589550`, "ops mosaic") differ, and
all four differ because of the same single widget:

| File | What differs |
|---|---|
| `dashboard-ir/58944063589550.json` | the saved internal form of the widget (aggregation `max` became `sum`, and its note) |
| `nerdgraph/58944063589550.json` | the query the dashboard is created with |
| `dashboards.tf` | the same query in the Terraform file |
| `report.md` | the sentence in the widget's row of the report |

## The one widget: "Open messages"

- **Fixture:** `tests/fixtures/gcp-dashboards/ops-mosaic.json`, the scorecard titled "Open messages". It
  shows the Pub/Sub metric `num_undelivered_messages`. In Google's words: take the highest value of
  each subscription within a 5 minute period (`ALIGN_MAX`), then add the subscriptions together
  (`REDUCE_SUM`).
- **What the old output said:** `SELECT max(gcp.pubsub.subscription.num_undelivered_messages) FROM Metric`,
  with the note "REDUCE_SUM and ALIGN_MAX become one max in New Relic's query". That is the highest
  value of any single subscription.
- **What the new output says:** `SELECT sum(gcp.pubsub.subscription.num_undelivered_messages) FROM Metric`,
  with the note "REDUCE_SUM after ALIGN_MAX runs over the one point each series has in the window, so it
  is sum of those points".
- **Why it is more correct:** the Google widget answers "how many messages are waiting across all
  subscriptions", which is a total. The old query answered "how many are waiting in the busiest
  subscription", a smaller and different number. New Relic reads this metric once per 5 minutes, so
  each subscription has exactly one value in the window and the maximum of one value is that value;
  adding them is exactly what Google does. The same rule is why a Google "average then sum" is no
  longer written as an average (the second Google metric fix that survived the merge).
- **Anything suspicious:** two things worth a look, neither a blocker.
  1. The note on this widget now claims "one point each series has in the window". That holds for
     metrics New Relic polls (this one is) and is checked in the code; for a metric the customer sends
     themselves the note says it is an assumption. A reader of the dashboard may not notice that
     difference.
  2. The proposal for `improve/gcp-remaining` described more changes to the pinned output (thresholds
     on scorecards, headings for empty text boxes, incident lists). None of them touches this one
     sample dashboard, which is why only this widget moved. They show up in the larger estate
     corpus (`docs/research/gcp-estate-baseline.md`), which is measured but not pinned byte for byte.

## Fixes that were merged but do not change any pinned file

- Four Google conditions compared a percent threshold with a metric that arrives as a 0 to 1 share (for
  example "memory used above 90" against a value that never exceeds 1). The threshold is now divided
  by 100. The sample alerts in `tests/fixtures/gcp` do not use those metrics, so no pinned file moved;
  tests in `tests/test_gcp_metric_names.py` cover it.
- The Azure dashboards now translate their log queries instead of leaving them as placeholders. The
  Azure dashboards are not pinned byte for byte, so no fingerprint moved; the counts in
  `tests/test_azure_dashboard_corpus.py` were updated on purpose (the public templates went from 259
  to 273 converted widgets, the quickstart templates from 6 to 7).
- The "Data path" paragraph at the foot of a report used to tell every source to run the Google
  command `plan-gcp-datapath`. It now names the Google command only in Google reports and points Azure
  reports to `docs/research/azure-datapath.md`. No pinned file contained that sentence for a source
  other than Google.

## What the owner is asked to do

1. Read the one widget above (or run `importer convert-dashboards --source gcp --in tests/fixtures/gcp-dashboards --out /tmp/x`
   here and on `main` and compare `report.md`).
2. If it is right, run the preview and then the write:

       uv run python scripts/improve/regenerate_wave2_pins.py           # preview, writes nothing
       uv run python scripts/improve/regenerate_wave2_pins.py --apply   # write the four fingerprints

   The script lists the four files above and nothing else. It stops without writing if any other
   pinned output would change, if a listed file did not change, or if `tests/golden` differs from the
   last commit. It never touches `tests/golden`.
3. Run `bash scripts/improve/gate.sh`; with the four fingerprints updated it should pass in full.
