# Proposal: convert Grafana contact points, policies and mute timings to New Relic

Branch `improve/grafana-notify-convert`, from `integration/all-threads` (tip contains 8d1c9ac).
Offline only: no network, no cloud, no Terraform, no `apply`, `migrate` or `rollback` was run. No
`.env` was read. Golden files and the baseline are untouched. Nothing was pushed.

## What changed

New, all under owned paths:

- `src/importer/sources/grafana/notify_convert/` (a sub-package that loads itself):
  - `destinations.py`: a contact point integration to a typed `NrDestination`, a
    `ManualDestination`, or nothing, from the verdict table; `SecretNamer`.
  - `payload_templates.json` and `payloads.py`: the Handlebars bodies of the 12 webhook template
    types and a generic webhook body, each with its URL start, authentication, placeholders and
    unverified sentences. `handlebars.py` is a small tested evaluator (`{{var}}`, `json`,
    `escape`, `#if`, `#unless`, `#eq`, `#each`, `else`) that also checks every variable against
    the 72 documented New Relic variables and refuses a plain `{{x}}` inside a JSON body.
  - `destmap.py`: the optional destinations map, strictly validated.
  - `labels.py`, `routes.py`, `plans.py`: rule labels, the policy walked once per rule and merged
    by route, workflows with their own copies of the destinations, the 4,096 character split,
    the resolve-message split, strict muting handling.
  - `mutes.py`: mute timings to `MutingRule` (dates chosen from the run date).
  - `convert.py`, `report.py`, `outputs.py`: orchestration, markdown, files (mode 0600).
  - `reasons.py`, `remediation_rules.py`: nine new reasons with rules and anchored taxonomy.
- `src/importer/commands/grafana_notify_convert.py`: `convert-grafana-notifications`.
- Tests: `tests/test_grafana_notify_convert_{handlebars,payloads,destinations,destmap,mutes,routes,rules,cli,hostile}.py`
  (about 1,000 cases); fixtures in `tests/fixtures/grafana-notify-convert/` (API-shaped contact
  points for all 24 types and variants, a policy tree, four mute timings, a template, six rules,
  a destinations map).
- Docs: a section in `docs/what-migrates.md`, `docs/improvement/lessons.d/2026-11-09-grafana-notify-convert.md`.

No core file, `cli.py` or file of another stream was edited. The fixtures are not registered with
`register_fixtures`: they are notification objects, not rules.

## Command

    importer extract-grafana-notifications --out out/gn
    importer convert --source grafana --in <rules> --out out/ga
    importer convert-grafana-notifications --in out/gn --alerts out/ga [--rules <rules>] \
        [--destinations-map map.json] --out out/gp

Writes `notification-plans/topic-NNNN.json`, `muting-rules/rule-NNNN.json`, `notifications.tf`,
`secrets.tfvars.example`, `report.md`. `apply-notifications --in out/gp` reads the plan folder
(tested as a dry run). Exit 2 if `plan_violations` is not empty.

## Counts on the fixtures (29 contact points, 6 rules, 1 policy tree, 4 mute timings, with the map)

| What | Count |
|---|---|
| Contact points converted to a destination | 17 (email x3, webhook, PagerDuty, 12 webhook templates) |
| Manual entries (slack x2, one of them shares a name with an email; teams, jira, pushover, sns) | 6 |
| No equivalent (wecom app, LINE, mqtt, wechat, unknown type) | 5 |
| Skipped by the map (threema) | 1 |
| Rules converted and routed | 5 (the sixth, `topk`, did not convert) |
| Routes | 27 |
| Workflows planned, disabled | 16, each with one destination and its channel |
| Plan files | 21 (16 workflows and 5 routes whose contact points are all manual) |
| Muting rules | 1 (Nights); Month end is manual, Business hours is an active interval (reported), Unused has no route |
| Secrets to supply | 18 (11 URL parts, one of them named in the map; 2 bearer tokens; 2 vendor header values; 2 basic header values; 1 PagerDuty key named in the map) |

Without the map, the bare email contact point stays manual and the secret names are generated.
Without `--rules`, a policy that matches on a rule label (`team`, `severity`) is undecidable and
reported; nothing is guessed.

## Decisions

- **Secrets:** a `SecretRef` everywhere; nothing is decrypted, read or stored. Webhook URLs are
  `secure_url` blocks (prefix from the exported host, the rest a secret). Credentials are
  references; basic authentication is one Authorization header whose whole value is the secret,
  because the user name is not exported. Terraform declares `nr_secret_<lower case name>`
  variables, sensitive and without a default.
- **One channel per workflow:** each route workflow has its own copy of each destination, named
  with the route number. The same contact point in two routes is two destinations in New Relic.
- **Muting:** the mute timing of a route becomes rules for that route's condition names (and its
  tag predicates); only that route's workflows use `DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES`.
  A predicate that cannot be a muting condition stops the rule rather than widening it.
- **Not converted, reported:** active time intervals, group wait, group interval, repeat interval,
  most grouping, custom Go templates, silences.

## Risks

- Every vendor payload is a guess at the service's current API. The report lists, per service,
  what is uncertain. Workflows are created disabled; the destination test in the UI is the check.
- A route's filter is tested on the whole issue; Alertmanager tests each alert. An issue that
  groups several values of a label can behave differently (the plan notes say so).
- Many `DOES_NOT_EXACTLY_MATCH` predicates (the "no sibling matched" default of a route) are the
  least tested operator on New Relic's side.
- The IR does not carry rule labels, so exact routing needs `--rules`; a bare selector and an
  aggregate without `by` look the same in the IR, so both count as an unknown label set.

## UNVERIFIED (nothing here was tested on a live account or service)

- All 12 payload shapes and their URL starts; the `escape` and `json` helpers as New Relic
  implements them (used in telegram, webex and wecom for the one-string message); Opsgenie
  closing; Kafka content type through channel headers and the format of the `headers` property.
- `secure_url` with prefix `https://host/` and a secret rest; that a scheme other than https or a
  port can be left out; the `Bearer` prefix for webex; a header value that starts with the scheme.
- `accumulations.tag.<label>` as a filter attribute; `tags.<label>` in a muting rule being the
  same tag; `STARTS_WITH` and `ENDS_WITH` on list attributes; how a missing tag is treated.
- A muting rule with neither `end_repeat` nor a count; behaviour at daylight saving changes;
  the operator spellings (the provider's); `conditionName IN` matching the converted names.
- That a workflow without `CLOSED` in `notification_triggers` omits the resolve message; that
  triggers left empty give New Relic's defaults.
- Whether a Grafana PagerDuty Events key is a valid New Relic service integration key; whether
  an EMAIL destination made through the API sends a verification mail.
- `grafana_folder` equal to the folder title in the export shape (the other shapes carry a uid,
  and the label is then unknown).

## Requests for the integrator (not my files)

1. `cli.py`, `apply-notifications`: `include_needs_review` is a parsed option that is not passed to
   `apply_notifications(...)` (line 974), so `--include-needs-review` has no effect and every
   needs_review plan is skipped. Pass it. Also add a way to supply secrets: for example
   `--secrets-from-env`, which reads `NR_SECRET_<NAME>` for every `SecretRef` of the plans and
   passes `secrets=` to `apply_notifications`. Until then the plans from this command are applied
   through `notifications.tf`.
2. `core/emit/notifications.py`: `nr_destination_input` raises for a destination with a
   `secure_url`, so a webhook from this command cannot be created by NerdGraph. If New Relic's
   NerdGraph takes a secure URL, emit it; otherwise keep it Terraform-only and say so in the
   `apply-notifications` help.
3. `core/emit/notifications.py`: make `_secret_names` public (`secret_names`); `outputs.py` imports
   the private name to write `secrets.tfvars.example`.
4. The saved IR of a Grafana rule should carry the rule's labels, the folder title and
   `notification_settings`, and whether its query's label set is known (bare selector or not),
   so `--rules` is not needed. Owner: the alert-rule translator stream.
5. `tests/hcl_schema.py`: `undeclared_variables` and `schema_errors` read `var.X` inside a quoted
   string (an escaped `$${var.x}` in a destination name) as a reference. Skip quoted strings.
6. Report profiles: nothing for notification plans yet (the command writes its own `report.md`).

## Measure and gate

Measure before and after: identical (cloudwatch 84%, dynatrace 25%, gcp 75%, grafana 29%); no
fixture folder was added to the measure. `scripts/improve/gate.sh`: GATE PASSED (ruff, ruff
format, `mypy --strict` on src and tests, vulture, pytest with resource warnings as errors, the
measure, the golden check). The new reasons are answered by their own rules, and a test shows
that no string of this package is moved by the read side's taxonomy.

Process: the tests of each module were written first and failed on the missing module (an
`ImportError`), not on an assertion; real assertion failures came in the following runs and are
listed in the lessons file. A mutation run over 19 guards found three gaps, now covered.
