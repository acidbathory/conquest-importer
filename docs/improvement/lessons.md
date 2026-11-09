# Lessons

One entry per defect class. Each says what happened and the check that stops it coming back.
New entries go on top.

## 2026-11-09: cycle `gcp-next`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| The Google extractor lacked safeguards the Dynatrace extractor had (redirects as success, server text echoed unscrubbed, no size cap, no total page cap, odd token accepted) | Each extractor was written and reviewed alone; the Google tests used a fake getter, so the real getter never ran | The shared safeguards are in `core/safe_http.py` and the Google getter uses them, tested through `httpx.MockTransport` (`tests/test_gcp_http.py`, `tests/test_gcp_http_limits.py`). The Dynatrace extractor still has its own copy and is to move to `safe_http` when the branches are merged. Next: a test that lists every `http_getter` and fails if one does not use `safe_http` |
| The export was said to hold no addresses, URLs or secrets, but free-text names (display names, URL paths) were copied through | The claim was tested only with the label fields the code dropped; the planted values were never in a name | `tests/test_gcp_privacy.py` plants hostile values in names, paths and handles and asserts they appear in no output file or CLI output. `core/redact.py` is applied in the extractor and again in every planner |
| A new manual reason contained words from an older broad rule ("monitor type", "uptime check") | First-match rules: the broad rule answered with the wrong steps | Found by a test that asserts the rule title for the new reason. Same class as the shadowing entry below; the generic "each rule wins for its own example" test is still not built |

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
