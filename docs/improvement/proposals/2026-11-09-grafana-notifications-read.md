# Proposal: Grafana notifications and data path, read side and pure plans

Branch `improve/grafana-notifications-read`. Offline only: no network, no cloud, no Terraform,
no `apply`, `migrate` or `rollback` was run. Golden files and the baseline are untouched.

## What changed

New, all under owned paths:

- `src/importer/sources/grafana/notifications/`: models, loaders (contact points, policy tree,
  mute timings, templates, data sources, rule files), the schema table
  (`contact_point_schema.json`, 24 types with secure, protected, required flags and a kind), the
  verdict table (`contact_point_verdicts.json`), `verdicts.py`, `matchers.py`, `policy.py`
  (`flatten_policy_tree`), `muting.py`, `templates.py`, `rulefiles.py`, `promrules.py`,
  `extract.py` (read-only getter), `remediation_rules.py`, `reasons.py`.
- `src/importer/sources/grafana/datapath/`: `plan.py` (guide files, query list), `check.py`.
- `src/importer/commands/grafana_notifications.py`: `extract-grafana-notifications`,
  `plan-grafana-datapath`, `check-grafana-data`.
- Tests: `tests/test_grafana_notifications_{models,extract,verdicts,policy,muting,promrules,rules,privacy}.py`,
  `tests/test_grafana_datapath_{plan,check}.py`; fixtures in `tests/fixtures/grafana-notifications/`.
- Docs: a section in `docs/what-migrates.md`, `docs/improvement/lessons.d/2026-11-09-grafana-notifications-read.md`.

No fixture folder is registered with `register_fixtures`: the folder holds notification objects,
not alert rules, so the conversion measure would read it wrongly. The measure is unchanged.

Secrets: the extractor sends only GET, only to allow-listed paths, never with a query string, so
`decrypt` cannot be sent (tested with the real getter on `httpx.MockTransport`). Secret values,
header values, URL paths and queries, template text are dropped at read time. Email addresses
are kept for type email only, in a field marked sensitive, and written to `contact-points.json`
only. The privacy test plants markers in every field of every type and scans all outputs.

## Verdict table counts (24 types)

| Verdict | Count | Types |
|---|---|---|
| CONVERTS | 2 | email, webhook (with warnings) |
| WEBHOOK TEMPLATE | 12 | oncall, prometheus-alertmanager, opsgenie, victorops, discord, googlechat, telegram, webex, sensugo, kafka, wecom (robot mode), dingding |
| MANUAL STEP | 6 | slack, pagerduty, teams (MICROSOFT_TEAMS, per the corrected reference), jira, pushover, sns |
| NO EQUIVALENT | 4 | threema, LINE, mqtt, wechat (wecom app mode is decided per contact point) |

## Measure and gate

Measure before and after: identical (cloudwatch 84%, dynatrace 25%, gcp 75%, grafana 29%).
Gate on this branch: ruff, ruff format, `mypy --strict` (src and tests), vulture, the measure and
the golden check pass; pytest has 2235 passed and 2 failed. The 2 failures are in
`tests/test_extension_points.py` (not mine): they assume `src/importer/commands/` holds no real
module, so any branch that adds a command module breaks them. With the 2-line change under
"Requests" they pass (11 of 11), so the gate would pass; I did not commit that change because the
file is not mine.

## Risks

- Routing: a wrong plan sends alerts to the wrong people. Undecidable cases are listed, not
  guessed; decided routes are returned beside them. Review the plan before any wave that creates
  workflows. Tag predicates are tested on the whole issue; Alertmanager tests each alert.
- Python `re` is not RE2. Patterns run only inside a safe subset (length, repeat counts, no
  nested repeats, no backreferences or lookaround, at most two unbounded repeats); others are
  undecidable. A pattern with `(a|b)+` is refused although it is harmless.
- Mute timing plans carry clock times and recurrence, not dates; the emitter must pick the dates.
- Schema and verdict tables are from the research note, not the running Grafana's
  `integrationtypeschemas`. A type or field Grafana adds later is `other` or name-only.
- Keeping setting key names and a few labels (Slack channel, Jira project and issue type) is a
  deliberate leak of non-secret detail. A person who types a secret into such a field would
  expose it; the label check drops token-like text but cannot be perfect.

## Requests for frozen or other files

1. `tests/test_extension_points.py` (integrator): make two tests ignore real command modules.
   Diff (as applied and verified locally, then reverted):
   `assert [n for n in commands.load_all(fresh) if n in ("aa_first", "zz_second")] == ["aa_first", "zz_second"]`
   and `assert "_helpers" not in commands.load_all(typer.Typer())`.
2. `core/safe_http.py`: `decode_json` accepts only JSON objects; the provisioning API returns
   arrays, so `notifications/extract.py` has its own small decoder. Add a variant that accepts a
   list and the local one can go.
3. `sources/grafana/models.py` and `translate.py`: `_ruler_datasource_rule` drops `expr`, and
   `_convert` raises the data source-managed reason before reading `data`. To use
   `promrules.to_grafana_rule`, let a rule with `datasource_managed` and a pipeline in `data`
   go through the normal path (research note 5.2 step 1).
4. `core/notification_ir.py` (frozen, being changed elsewhere): wave 2 needs the fields listed
   below; I did not depend on any of them.

## What wave 2 needs to wire these plans into the notification IR

- Destinations: a webhook destination with a secure URL and auth placeholders (a variable name,
  no default, no value), a payload template body, and a list of manual steps per destination;
  for MANUAL STEP and NO EQUIVALENT a manual destination carrying the steps and reasons from
  `contact_point_verdict`. Email addresses come from `ContactPoint.addresses` and must not
  appear in reports.
- Workflows: one per `RoutingEntry` (receiver, condition names, predicates as attribute,
  operator, values), the incident preference, and `muting_rules_handling` set to the strict
  value on workflows whose route has a mute timing; `disableResolveMessage` removes `CLOSED`
  from the triggers. The 4,096-character filter split is already done in the plan.
- Muting rules from `MutingPlan` (schedule, time zone, `DO_NOTHING` on window end), scoped to
  the condition names of the route that carries the timing; report overlap with other routes.
- The translator must keep rule labels and the `by (...)` label set so `RuleLabels` can be built
  (static labels, folder title, `by_labels`; `None` for a bare selector).
- Active time intervals (complement muting rules) are not done.
- Golden files will change when this is wired; they need a human review.

## UNVERIFIED

Carried over from the research note: which Grafana release added each type; that `wechat` has no
v1 config; PagerDuty key interchangeability; Telegram JSON bodies and Kafka content type; vendor
API shapes; whether channel payloads are stored encrypted; how a workflow predicate treats a
missing tag; any repeat or group-wait setting for workflows; MONTHLY recurrence day choice, DST
and post-window behaviour; counter delta conversion point; colon and dot handling in metric
names; Loki label to attribute names; Tempo span schema; `rate()` in conditions; histogram
quantiles in NRQL; Grafana alerting on New Relic's Prometheus API; Grafana Cloud metrics being
Mimir; the `annotations.title` mapping.

Added by this work:

- STARTS_WITH and ENDS_WITH on list attributes such as `accumulations.tag.*` (the documentation
  lists them for strings only); the plan emits them with a note.
- How Alertmanager unescapes quoted matcher values (only `\"`, `\\`, `\n`, `\t` are undone here).
- That `wecom` robot mode is the case where `url` has a value.
- The shape of `keyset()` rows (a `key` column) and `uniques()` rows (a list value), and the use
  of `metricName` and `prometheus_server` in the check queries; the fake client follows this
  assumption, not a live answer.
- That a muting rule accepts an end of 00:00 on the next day for `24:00`.
- That Grafana's default title and message templates are called as
  `{{ template "default.title" . }}` and `{{ template "default.message" . }}`; other default
  names are treated as custom.
- The Alloy `write_relabel_config` attribute names and the `auth` link in the OTLP exporter.
- The mapping of `group_by` (`alertname`, folder plus alertname, or `...`) to an incident
  preference.

## Round 2 (second review)

All seven must-fix items and the should-fix items were done; see the commits after the merge of
`integration/all-threads`. Owner decisions applied: no new dependency (the regex gate is strict
instead, and rejects more patterns), rule annotations are dropped at read time.

- Incomplete policy trees are saved with `_incomplete` and `_problems`, honoured on load, and warned
  about in the command summary.
- The regex gate parses the pattern with the stdlib parser and refuses nested repeats, branches in
  repeated groups, empty-matching repeated groups, lookaround, backreferences, atomic and
  possessive forms, scoped flags, and the forms where RE2 differs. It refuses harmless patterns such
  as `(\d{1,3}\.){3}\d{1,3}`; those are reported undecidable. There is no wall-clock guard on a
  single match (that needs a thread or process); the gate and the length caps are the protection.
  It uses `re._parser`, a private module: the gate fails closed if it cannot be imported, and a test
  pins Python 3.12 or later.
- Grafana-injected labels (UNVERIFIED names) are decided only from a known value.
- The extractor writes everything or nothing, with mode 0600, and a recursion error is a clean
  error.
- Every contact point item is rebuilt from scratch; option fields are typed; free-text labels are no
  longer kept; names and uids that look like an address or token become a stable reference
  (`redacted-<hash>`), and policy receivers and interval names get the same treatment so links hold.
- Mute timing zones are checked against the time zone database (`Local` is reported, as before);
  an interval that would exceed 200 schedules is reported whole.
- Redefined default templates make the default calls custom (whether Grafana allows redefining a
  default name is UNVERIFIED).
- Rule files: annotations dropped; label values and expressions that look like secrets are replaced
  with a placeholder and a note; matcher values that look like secrets make the node undecidable.

Surviving mutants: 2 of 32 (both equivalent, see lessons). Before the round the reviewer listed 12
survivors; each now has a test.

Decisions for the owner:
1. `Local` as a mute timing zone is reported as manual (the server's zone is not known to the
   importer). Say if it should be accepted as UTC instead.
2. Free-text fields (Slack channel, Jira project) are no longer kept at all. A person reviewing the
   plan sees only the field names.
3. The regex over-rejection, including counted repeats inside repeated groups.

Requests for the integrator (still open): `core/safe_http.decode_json` with a `want` parameter that
allows a list; the shared time zone and clock patterns could move to `core` if another source needs
them.
