# Lessons

One entry per defect class. Each says what happened and the check that stops it coming back.
New entries go on top.

## 2026-11-09: cycle `dynatrace-phase1`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A manual reason written with the words "not confirmed" was sorted as "unverified names" instead of "unsupported query" | The taxonomy is first-match over the whole sentence, and the test only covered the old reasons | `test_the_new_manual_reasons_are_unsupported_query`: every new reason has a category test. Next: one generic test that classifies every reason constant a source module defines |
| A plan file written with extra fields would be read back by code that knew only the old model, which drops unknown fields | Pydantic ignores extra input, so the apply step would have built a workflow with the wrong filter | The new fields live on the shared plan model, with defaults, and the writer omits defaults, so one model reads and writes every plan. Byte-for-byte fixture test of the AWS output |
| An adversarial test string that was in fact valid input | The test asserted "raises" for a 100,000-character string that is a legal (long) value | Speed tests assert the time bound, not the outcome, unless the case is known to be invalid |
| The same count-query helper written a second time in a source package that may not import from another | `check-gcp-data` and the Dynatrace check need the same result type and report | The helper now lives in `core/data_check.py`. The Google module still has its own copy (owned by another thread); move it on the next merge |

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
