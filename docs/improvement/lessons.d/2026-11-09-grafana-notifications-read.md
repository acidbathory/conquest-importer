## 2026-11-09: cycle `grafana-notifications-read`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| Free text kept as a "label" or a schedule field (an MQTT topic, a month name) carried a token-like value into the export | The first version kept any short text for fields that look harmless; only a fixture with a marker in every field showed it | The privacy test plants a marker in every field of every integration type and in the schedule fields, then scans every output file and every command output. Label fields are now only the three a person needs (Slack channel, Jira project and issue type), and schedule fields are rejected unless they are plain day, month, year, clock or zone text |
| A new reason shared words with another source's broad rule ("no new relic equivalent") | First-match rules: the other source's rule answered with its own steps | `test_no_other_registered_rule_comes_before_ours_for_our_reasons` walks every registered rule, for every reason constant of this package. The generic version (an example reason per rule of every source) still needs each source to export its examples |
| A time zone check that accepted any run of letters | The test used a 80-letter name that the regular expression allowed | Length is capped and the test asserts the cap; zones are validated by pattern, not by a lookup, so the test does not depend on the machine's zone database |
| A skipped rule left no trace after the file was rewritten | The count lived on the in-memory group, and the file written holds only the rules that were kept | The extractor adds a warning with the count; the privacy test asserts the skipped rule is absent from the output |
| The base branch's extension-point test assumes `commands/` holds no real module | It was written when no command existed | Reported to the integrator: filter the real command names in `tests/test_extension_points.py` (two tests fail on any branch that adds a command module) |
| A regex "safe subset" checked only the text of the pattern, so `(a?){30}a{30}` and four more hung `re` | The gate looked for repeats inside a repeated group by scanning characters, and a `?` or a counted repeat was not counted | The gate now reads the parse tree (`re._parser`), refuses any repeated group that holds a repeat of any kind, a branch, or can be empty, and a property test runs 1,500 random small patterns against 40-character subjects and asserts that every pattern the gate accepts finishes under half a second. The same test must be re-run on each new Python version |
| `re` and RE2 read some patterns differently without an error (`[[:digit:]]`, `(?i)` with non-ASCII text) | Only patterns Python rejects were treated as unsafe | A list of forms that are refused outright, with the reviewer's two cases as tests |
| A policy tree cut by a size limit was saved as complete | The mark lived on the in-memory model and the file writer took only the root node | The file carries `_incomplete` and `_problems`; a round-trip test per cause (depth, node cap, bad route). Any new field of a model that a writer drops needs a round-trip test of the whole model |
| Labels Grafana adds to every alert instance were read as the empty string | A label that is neither on the rule nor in the query was treated as absent | A fixed set of injected names is decided only from a known value; the names are marked UNVERIFIED |
| Free text kept in "option" and "label" fields could hold a 20-character secret | The only check was a character class and a length | Every option field has a type (boolean, integer range, list, fixed shape); anything else is dropped and listed. A test fills every option field of every type with a secret-shaped value |
| One item in the sanitised shape was trusted verbatim, and one raw item switched the whole list to the other path | The loader picked a path per list, not per item | Every item is rebuilt from scratch and every field is checked again; a mixed-list test |
| A recursion error from a hostile response aborted the run and left a half-written folder that blocked the next run | Files were written section by section | All files are collected first and written into a staging folder that is renamed into place, mode 0600; a failed run leaves nothing |
| A work budget shared by all rules marked rules as too large after other rules had used it | One counter for the whole call | A budget per rule and a wall-clock limit for the call |

Process note: the first loaders (contact points, policy tree, mute timings, templates, rule
files, schema table) were written before their tests, which broke the test-first rule. The tests
were added right after and found the defects in the first row. The comparison splitter, the
extractor, the verdict table, the policy flattening, the mute timing plans, the data path and the
remediation rules were written test first (the first run failed on the missing module, not on an
assertion; the assertions failed later, for example on the message of a leaked topic name).

A mutation run over the guards listed in the second review (32 mutants) left two survivors, both
equivalent: the incomplete mark in the payload writer is duplicated by the saved problem codes, and
the final slice of the flattened setting names is already covered by the loop's own cap.
