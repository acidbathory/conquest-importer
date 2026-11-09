# Lessons

One entry per defect class. Each says what happened and the check that stops it coming back.
New entries go on top.

## 2026-11-09: cycle `grafana-phase0`

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
