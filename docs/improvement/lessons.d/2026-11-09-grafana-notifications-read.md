## 2026-11-09: cycle `grafana-notifications-read`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| Free text kept as a "label" or a schedule field (an MQTT topic, a month name) carried a token-like value into the export | The first version kept any short text for fields that look harmless; only a fixture with a marker in every field showed it | The privacy test plants a marker in every field of every integration type and in the schedule fields, then scans every output file and every command output. Label fields are now only the three a person needs (Slack channel, Jira project and issue type), and schedule fields are rejected unless they are plain day, month, year, clock or zone text |
| A new reason shared words with another source's broad rule ("no new relic equivalent") | First-match rules: the other source's rule answered with its own steps | `test_no_other_registered_rule_comes_before_ours_for_our_reasons` walks every registered rule, for every reason constant of this package. The generic version (an example reason per rule of every source) still needs each source to export its examples |
| A time zone check that accepted any run of letters | The test used a 80-letter name that the regular expression allowed | Length is capped and the test asserts the cap; zones are validated by pattern, not by a lookup, so the test does not depend on the machine's zone database |
| A skipped rule left no trace after the file was rewritten | The count lived on the in-memory group, and the file written holds only the rules that were kept | The extractor adds a warning with the count; the privacy test asserts the skipped rule is absent from the output |
| The base branch's extension-point test assumes `commands/` holds no real module | It was written when no command existed | Reported to the integrator: filter the real command names in `tests/test_extension_points.py` (two tests fail on any branch that adds a command module) |

Process note: the first loaders (contact points, policy tree, mute timings, templates, rule
files, schema table) were written before their tests, which broke the test-first rule. The tests
were added right after and found the defects in the first row. The comparison splitter, the
extractor, the verdict table, the policy flattening, the mute timing plans, the data path and the
remediation rules were written test first (the first run failed on the missing module, not on an
assertion; the assertions failed later, for example on the message of a leaked topic name).
