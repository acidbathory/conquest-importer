# Lessons

One entry per defect class. Each says what happened and the check that stops it coming back.
New entries go on top.

## 2026-11-09: cycle `dynatrace-phase0`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A huge number became `inf` and broke Terraform, NerdGraph and the report for the whole run | Conversion looked fine; only the emitters and `report` failed | Every number from an export passes `isfinite` at translation; tests run `convert` then `report` |
| Notes were cut by the report's 300 character cell, so the key caveats vanished | Tests checked reasons, not the rendered report | Specific notes come first; a test renders the report |
| The documented command sequence did not work | Docs were written from the code, never run | An end-to-end CLI test for every documented sequence |
| Error text could echo the API token | The fake getter in tests never hit the real error path | Drive the real getter with `httpx.MockTransport` |
| Tests written after the code | The fixer slipped on test-first | The reviewer checks that tests fail without the change |
| Same helper in four places (`source_id`) | Copy at each new source | Moved to `core/ids.py`; the `new-source` skill says to search `core/` first |

## 2026-11-09: cycle `dynatrace-phase1`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A manual reason written with the words "not confirmed" was sorted as "unverified names" instead of "unsupported query" | The taxonomy is first-match over the whole sentence, and the test only covered the old reasons | `test_the_new_manual_reasons_are_unsupported_query`: every new reason has a category test. Next: one generic test that classifies every reason constant a source module defines |
| A plan file written with extra fields would be read back by code that knew only the old model, which drops unknown fields | Pydantic ignores extra input, so the apply step would have built a workflow with the wrong filter | The new fields live on the shared plan model, with defaults, and the writer omits defaults, so one model reads and writes every plan. Byte-for-byte fixture test of the AWS output |
| An adversarial test string that was in fact valid input | The test asserted "raises" for a 100,000-character string that is a legal (long) value | Speed tests assert the time bound, not the outcome, unless the case is known to be invalid |
| The same count-query helper written a second time in a source package that may not import from another | `check-gcp-data` and the Dynatrace check need the same result type and report | The helper now lives in `core/data_check.py`. The Google module still has its own copy (owned by another thread); move it on the next merge |

## 2026-11-09: cycle `grafana-phase0`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| Aggregation layers mixed up: a PromQL aggregate across series was used as the New Relic aggregation over time, and the Grafana reducer was ignored | The query looked right and the numbers only differ with the scrape interval and the number of series | A table of the (aggregate, reducer) pairs that one NRQL aggregation expresses exactly; every other pair is manual. Tests per pair |
| A query time range that the reducer depends on was never read | Every sample rule used reducer last, which does not need it | Window tests assert what happens to `relativeTimeRange` in every case, and the note says whether it was used or ignored |
| A new field on a shared model changed the saved output of every source | No test compared saved IR | `ir-before-grafana.json` holds hashes of the saved IR of the older sources; a test converts their fixtures and compares |
| One malformed rule stopped the whole load | Fixtures were all well formed | Per-rule validation; tests with each malformed field and with 100,000-deep nesting through the CLI |

| Defect | How it hid | Check that catches the class |
|---|---|---|
| New source's reasons were answered by another source's loose rule (`reducer`, `over New Relic's limit`, `valid New Relic attribute name`) | Each reason still got some guidance, just the wrong text | `test_a_specific_rule_is_not_shadowed_by_a_broader_one` lists an example reason per Grafana rule. Next: one generic test over all sources |
| A new rule could claim another source's reason | Only visible by reading the output | `test_the_grafana_rules_do_not_claim_reasons_of_other_sources` (uses a saved list of the other sources' reasons) |
| New taxonomy wording could move other sources' categories | Categories only show in the measure | `test_the_categories_of_the_other_sources_are_unchanged` against `tests/fixtures/taxonomy_before_grafana.json` |

## 2026-11-09: cycle `gcp-absence`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A new signal kind (heartbeat) went through code that only knew the metric kind (`check-gcp-data`) | The tool answered "no metric name to check", which was false | Every consumer of a signal kind needs a test per kind. Next: a test that builds one monitor of each kind and runs every consumer |
| Out-of-range values converted as approximate and failed only when emitting | Reported as converted, then silently not created | Translation checks the emitter limits (`_check_limits`) |
| A dropped input (group-by field) changed what a note said | The note described a different condition | Dropping input always adds a note |

## 2026-11-09: found while hardening the Google source

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A broad rule pattern shadowed a specific one (`forecast`, `metric math`) | Rules are first-match; both reasons still got some guidance | `test_a_specific_rule_is_not_shadowed_by_a_broader_one`. Next: a generic test that gives each rule an example reason and asserts that rule wins |
| An unclosed `httpx.Client` | Nothing failed; the process exits | Context manager API plus `ResourceWarning` as an error in pytest |
| `register_rules` appended on every import | Only visible on reload | Registration is idempotent; test reloads a rules module |
| A converted condition on a metric that never arrives | `sum()` of nothing is 0, not an error | `check-gcp-data` (needs a live account) |
| Counter metrics converted with a per-second threshold on a 60 second window | New Relic stores one change per 5 minute poll | Counter window and scale in `_counter_window`, tested; confirmed on live data |
| A duration that is not a whole number of windows dropped the condition | Reported as "not emitted" | Rounded up with a note, tested |
| Ten reasons with no manual steps | The generic "no guidance" text | `improve-measure` lists unguided reasons, and the gate fails on a new one |

## Open

- Fixtures cannot show that a converted query returns data. Only live checks can.
- Two sources share one rule list. Reasons do not carry their source, so patterns must stay
  specific. A per-source rule list would remove the risk.
