# Alert message templates: vendor to New Relic (anonymised measurement)

Method: public New Relic docs (docs-website raw files: custom-alert-event-descriptions, title-template, message-templates, rules-limits-alerts); `python3 -I` counters on the real Grafana estate (5,039 rules; no text copied); read of src/importer/sources/grafana and core/emit in the integrate-1111 worktree; one converter run (874 of 5,039 rules emitted).
Not done: vendor-side docs for Datadog, Dynatrace, Azure, Google, CloudWatch were not fetched in this bounded round (only Grafana was measured); NR behaviour not tested live (no calls allowed).

## 1. New Relic side (docs-sourced)
- Condition `description` template: NRQL and infrastructure conditions, Handlebars, max 4,000 characters. Variables: alert-event attributes (`{{conditionName}}`, `{{targetName}}`, `{{nrqlQuery}}`, `{{runbookUrl}}`) and `{{tags.NAME}}`. For NRQL, a FACET attribute is available as `{{tags.<facet>}}`; tag names cannot contain whitespace. So a label is printable only if the query FACETs it.
- Condition `titleTemplate`: NRQL only, max 256 characters, Handlebars; ignored on loss-of-signal events (default title is used).
- Workflow message templates: Handlebars built-ins plus `json`, `escape`, `#eq`, `#contains`, `#math`, `#timezone`, `#replace`; `#if/#each/#unless`. Issue-level vars `accumulations.*` (incl. `accumulations.tag.<facet>`, `accumulations.conditionDescription`, `accumulations.runbookUrl`), `annotations.*`. `accumulations.tag.*` do not exist until an issue carries them: guard with `#if`. Use `escape` or `json` inside JSON webhook payloads.
- No NR equivalent for: Go `printf`, `humanize*`, `reReplaceAll`, `range` over `$values`, `query` (running a PromQL query in a message), `.ValueString`, `$values.B` of a multi-step expression. `$value` has no exact form: the nearest is the violating value of the evaluated NRQL signal (check the exact variable name live; UNVERIFIED), which for a rule converted to a different aggregate is not the Prometheus value.

## 2. Real Grafana estate (5,039 rules)
| Construct | Rules | Share |
|---|---|---|
| Any annotations | 4,991 | 99.0% |
| `__dashboardUid__` + `__panelId__` link annotations | 4,928 | 97.8% |
| Rule labels (team/service/severity/priority/slack...) | 4,853 | 96.3% |
| summary/description/message present | 1,379 | 27.4% |
| ...of those plain text (no `{{`) | 1,212 | 24.1% |
| ...of those templated | 167 (any annotation: 187) | 3.7% |
| `description` key / `summary` key / `message` key | 962 / 722 / 72 | 19.1 / 14.3 / 1.4% |
| `$labels.<name>` | 94 | 1.9% |
| `$labels["x"]` or `index $labels` | 21 | 0.4% |
| `$value` | 26 | 0.5% |
| `$values.X` / `.Value` | 61 / 59 | 1.2% |
| `if` / `range` / `else` | 11 / 7 / 4 | 0.2 / 0.1 / 0.1% |
| `printf` | 50 | 1.0% |
| humanize / toUpper-style functions | 2 / 14 | under 0.3% |
| pipes inside `{{ }}` | 20 | 0.4% |
| `runbook_url` key | 29 | 0.6% |
| URL inside annotation text | 85 | 1.7% |
| annotation text with quote or backslash (needs JSON escape in a webhook) | 98 | 1.9% |
| template in the title | 1 | 0.02% |
| `$labels.x` but no by/without in the rule expression (rough, so the label cannot be faceted) | 21 | 0.4% |
| no-data state OK / NoData / Alerting | 89.0 / 10.5 / 0.6% | |

Among label names referenced, the common infrastructure ones (pod, namespace, instance, cluster, service) are under 1% each; most references are to rule-specific names.

## 3. How our converter treats messages today (code and run)
- `translate.py _convert`: takes `description` (else `summary`) into `Notification.message_template`. Nothing reads that field in `core/emit` (grep: only ir.py and the sources set it). So summary, description, message are DROPPED silently. Not copied verbatim, so no placeholder prints literally.
- Emitted condition `description` is always the fixed "Imported from Grafana <key>" (874 of 874 emitted; none contain braces). `titleTemplate` is never set by the Grafana path (0 of 874).
- `runbook_url` is carried only if it passes `fields.runbook` checks: 6 of 874 emitted conditions have runbookUrl. A refused link gets a note.
- `$labels.<name>` in summary/description is used (identity_labels, max 6) only to choose FACET/group-by for an equality test on a bare selector; it does not print anything.
- `__dashboardUid__`/`__panelId__` (97.8%) are not carried; no link to the Grafana dashboard or to the converted dashboard.
- Rule labels (team, severity, service) go to an origin note and N_ROUTING (a note that routing is manual); not converted to tags or workflow filters here (notify_convert handles contact points and policies separately).
- Unverified: whether the notify_convert payload templates include the condition description (`accumulations.conditionDescription`); not read.

## 4. Mapping
| Source construct | NR form |
|---|---|
| title as alert name | condition name: yes |
| `{{ $labels.x }}` | `{{tags.x}}` in description/title template only if x is a FACET attribute; else none |
| `$value` | nearest `{{...}}` violating-value variable in workflow; value semantics differ; label as approximate |
| `$values.B` | none (no multi-step expression values) |
| `{{ if }}` / `{{ range }}` | `{{#if}}` / `{{#each}}` yes, over NR vars only |
| printf, humanize, string functions | none (`#math`, `#replace`, `#timezone` partial) |
| runbook_url | `runbookUrl` yes (done) |
| summary/description text, plain | condition `description` yes (4,000 chars); today dropped |
| dashboard/panel link | description text with URL: yes as text; no native link |
| labels for routing | workflow filter on tags: manual; N_ROUTING |
| Datadog `@mention`, `{{#is_alert}}` | none direct; `#if` on `priority`/`state` in workflow. Not measured. |

## 5. Verdict on the progress rule
Construct class at or above 5% that we mishandle and NR can carry: plain-text summary/description (24.1% of rules, dropped), the dashboard/panel link (97.8%, dropped; NR can carry it only as text in the description, needs the Grafana base URL, which the export lacks, so only the uid pair is known), and rule labels for routing (96.3%, note only). Templated constructs are all under 5% (3.7% total), so template rewriting is NOT a progress class.
Ceiling: carrying plain text into the condition `description` (escaped, 4,000 cap, guarded for `{{`) touches up to 1,212 of 5,039 rules (24.1%), but only the 874 emitted (17.3% of all rules) get a condition; the benefit is documentation text, not routing.
