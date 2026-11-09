# Lessons

One entry per defect class. Each says what happened and the check that stops it coming back.
New entries go on top.

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
