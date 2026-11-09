## 2026-11-09: cycle `grafana-notify-convert`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A matcher on a rule label that the converted IR does not carry (`severity`) was read as the empty string when the query groups by other labels | The policy walk treats a label that is neither static nor in the query as absent. The saved IR has no rule labels, so every label looked absent and `severity!="critical"` matched everything. The mutation run showed the guard (labels marked unknown without `--rules`) could be deleted with no test failing, because the first test used a query with no grouping, which is undecidable anyway | A test with a grouped query and no rules, and with the rules. Every guard that stands between a known and an unknown value gets a test where the unknown value would be wrong in the quiet direction |
| New strings of this package were claimed by the read side's anchored taxonomy patterns (`^grafana contact point needs`, `^grafana mute timing`), moving their category | The read side's test removes its own rules and compares the answer for every string of the code base outside its folders. Strings of a new package (reasons, rule titles, steps) are in that set. Only the full test run found it | `test_no_string_of_this_feature_is_claimed_by_the_read_side_patterns` runs the same comparison over this package's strings. New reasons and rule titles must not start with a phrase the read side claims |
| A hand-written HCL checker saw `var.y` inside an escaped string (`$${var.y}`) as a variable reference | `tests/hcl_schema.py` finds references with a regular expression over the whole text | Hostile names are asserted to be escaped first, then the name is defused for the checker. The checker should skip quoted strings (request in the proposal) |
| A test helper that expected one reason got two (a matcher and the order it breaks) | The read side reports both for one undecidable route | Tests compare sets of reasons, not lists |
| A route that names a contact point can be reached by several rules, and a channel can serve one workflow only | The IR model has one workflow per plan, so a shared destination would silently become a shared channel | Every workflow gets its own copy of its destinations with a unique name; `plan_violations` runs over all plans; a test lists every destination name across plans |
| Titles of the same policy walk were merged by the read side by rule title, so two rules with one title would share a condition list | `flatten_policy_tree` names rules by title | Each rule is walked alone and the results are merged by (receiver, predicates, mute set, active set) with the New Relic condition names |
| A filter-size limit measured by the read side's own character rule (names plus a fixed number) can differ from the size `plan_violations` measures | Two measures of one limit | The split uses `issue_filter_size`, the same function as the violation check; tests with and without tag predicates |
| Counts that mix units (rules and routes, contact points and destinations) | A summary row said "rules routed to nothing" but added the rules of every route | Rows say what they count; the end-to-end test pins every row on the fixture |

Process note: the handlebars evaluator, the payload templates, the contact point conversion, the
destinations map, the mute timing conversion and the routes were all written test first in the
sense that the test file existed and failed first, but each failure was an `ImportError` for the
missing module, not an assertion. Real assertion failures came in the next run (a payload test
that compared a hostile title with a JSON-escaped one, a vendor with no unverified sentence, a
map that coerced `"1"` and `"yes"` to a version and a boolean). A mutation run over 19 guards left four survivors: three were real gaps (the unknown
label marker, the expired-schedule count, an unreachable `skip` branch in `_fits`, now removed) and
one is a guard that the secret-name shape check already covers (the extra URL and address check in
the destinations map; kept as a clearer message).
