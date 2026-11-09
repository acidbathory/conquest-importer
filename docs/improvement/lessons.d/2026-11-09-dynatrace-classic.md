# Lessons: cycle `dynatrace-classic` (2026-11-09)

| Defect | How it hid | Check that catches the class |
|---|---|---|
| Three older rules answered this source's new reasons before its own rules did (words "anomaly detection", "no New Relic equivalent" and "forecast" in my wording) | Rules are first-match and every reason still got some steps, just the wrong ones. Found only because the new test asks which rule wins | `tests/test_dynatrace_classic_rules.py`: each rule has one real example reason that must win, every reason of the source's fixtures must be answered by one of its own rules, and its rules must not change the answer for any reason of the other sources' fixtures. The check needs no list of other sources' examples: it takes their reasons from the fixtures they register |
| A new source broke tests that enumerate sources (`test_sources`, `test_improve`), and a new command module broke `test_extension_points` (it expected `load_all` to return exactly the test's own modules) | Those tests were written when no source or command lived outside the shared files, so the extension points were never exercised by a real user | The extension-point test now checks order and that private modules are skipped, not the full list. The two source lists still name every source by hand; each new source edits one line in each. A registry-driven version would remove the edit |
| An untrusted name quoted with `repr` still carried markup and `${...}` into a reason | `repr` removes control characters but not `${var}`, `<img>` or `|`. The first hostile fixture with a schema id showed it | `shown()` echoes only plain names (letters, digits, `_ . : / -`, at most 80 characters) and otherwise prints a placeholder. A test plants a hostile schema id, key and dimension and asserts nothing of it reaches any reason or output |
| A selector with 100,000 characters of `(`, `:x` or `"~` could take quadratic time or recurse without bound | Easy to write a recursive-descent parser that is fine on real input | The parser reads one character at a time with explicit caps on length, nesting, operands, transformations, arguments and string length; 14 adversarial inputs of up to 100,000 characters must fail in under a second |
| `peek()` returned `""` at the end of the text and `"" in "0123456789"` is true, so a selector ending after `-` raised `IndexError` | Only the end-of-input case; I saw it when re-reading the parser before the first run, not from a failing test | The adversarial list has inputs that end at every operator. Rule: compare a possibly empty character with `char and char in ...` |
| A metric the customer's tenant has but New Relic does not receive would convert and never fire | Same class as the Google metric that never arrived: nothing in the conversion shows it | Each converted condition carries the data prerequisite; a metric without a map row is manual with the message; `check-dynatrace-metrics` asks New Relic whether the signal has points. Still needs a live account to prove |
| The third copy of the data-check command body (`_run_data_check` in `cli.py`, here as `check_command`) | A command module may not import `cli.py`, so copying was the only way | Move the body to `core/` on the next merge (it needs `NewRelicClient`, `require_env` and the render and exit rule only) |

## Test order, honestly

- The loader (`models.py`) was written before its tests. I wrote the tests next and then broke the
  notification reduction on purpose to see them fail (two failed); the rest of the loader tests
  were not shown to fail.
- Extractor, parser, metric map, translator and data check: tests first. The first run failed on
  import (the module did not exist), not on assertions. Real assertion failures that followed:
  the metric map file refused its own `_comment` key; a test fixture event lacked fields the
  model requires; the page-key test used a handler that lost the schema on follow-up pages; the
  end-to-end test failed because a test server could not encode a fixture's `1e999`.
- Several expectations in my tests were wrong, not the code (a selector the map can serve was
  listed as unsupported, a duplicate-id test unpacked one value from two).

## Open

- Nothing in this source has met a tenant. Every metric map row is `unverified`, the fallback
  settings path has never answered, and the shape of a masked secret, the `rule` text of the newer
  maintenance windows and the way several entity conditions combine are guesses taken from
  documentation. The list is in the proposal.

## Round 2 (review fixes)

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A rewrite that flattened nested filter calls changed what a filter meant | Hand-picked examples all had the shape I expected | A generated set of over 300 small filter trees compared with a tiny evaluator: a conversion must mean what the source says or be refused |
| A value computed for one purpose (the unit factor) applied to another (any threshold) | The tests multiplied; nothing added | Test each operator with a scaled metric, not only the one the code was written for |
| A limit on one side (New Relic's delay) was never checked because my cap was a different number | I took the cap from the Dynatrace side | Every Dynatrace number that becomes a New Relic field is checked against the New Relic maximum, with a boundary test on both sides |
| Two checks of the same fact (the host pattern, in two places) disagreed on `//` | A helper and its caller each stripped a slash | One function returns the kind; the scheme and the paths use that result, and a table of 32 hostile addresses tests it |
| A decision made on message text (`returned 404`) | The server controls the text | Errors carry a status attribute; a test has a 403 whose text says 404 |
| Tests that only fail on the far side of a boundary | A cap test with 50 pages passes if the cap is off by one | Every cap has a test at exactly the cap and one past it; 37 mutants of the caps and allow-lists must all be killed |
| Test helpers could not be shared (`tests` is not a package) | An import of another test file failed at collection | Shared builders live in `tests/dynatrace_classic_helpers.py`, a module of its own |
