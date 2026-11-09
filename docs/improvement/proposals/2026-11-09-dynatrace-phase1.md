# Proposal: Dynatrace source, phase 1

Branch `improve/dynatrace-phase1`, from `integration/all-threads`. Status: waiting for the owner.
Offline work only: fixtures, no network, no tenant, no New Relic account.

## What changed

1. Davis problem workflows get a New Relic notification plan. `convert-notifications --source
   dynatrace --topics <export folder>` writes, per workflow, a plan file, a report and Terraform.
   Email recipients become email destinations and channels. Slack, PagerDuty, Jira, Teams,
   ServiceNow and HTTP tasks become manual entries with the closest New Relic destination type and
   the steps for the secret or sign-in. Other actions are manual with a reason. Entity tags on the
   trigger become issue-filter predicates; a workflow is planned only when there is one, because a
   workflow with no filter would send every issue. Categories, custom filter, severity threshold
   and notify-on-close are notes. Every plan is marked `needs_review`. Recipient addresses appear
   in the plan and the Terraform only, never in notes or the report.
2. SLOs get a service level plan (`plan-service-levels`). There was no service level code in the
   repository. The plan keeps the target, the period (1, 7 or 28 days) and notes, lists the
   Dynatrace metric names the indicator reads, and leaves the good and valid event queries as
   TODO. The Terraform file is a commented skeleton. No data path is invented.
3. The DQL log-count pattern accepts `contains()`, `matchesValue()` (one value or a set) and
   `in()` (two spellings), joined by `and`. They map to `LIKE '%v%'`, `=` and `IN`. The parser
   moved from `translate.py` to a new `dql.py`. A `by:` clause stays manual, now with a specific
   reason.
4. `doctor --source dynatrace` (offline) and `check-dynatrace-data` (asks New Relic whether
   matching `Log` events exist; when none do, asks whether any logs arrive, so the note says
   "no log events at all" or "no matching events: the filters may not match the attribute names,
   or the event did not occur"). Exit 2 means no matching events in the window; it is not proof the
   condition is broken. The shared result type and report moved to
   `core/data_check.py`.
5. `docs/research/dynatrace-native-coverage.md` has a new section listing what one real Davis
   anomaly detector would settle, with the places to look first.

`docs/what-migrates.md` (Dynatrace) and the remediation rules are updated.

## Result

| | Before | After |
|---|---|---|
| Dynatrace fixtures converted | 2 of 8 (25%) | 2 of 8 (25%) |
| AWS and GCP fixtures | 27 of 32, 3 of 4 | unchanged |
| Tests | 725 on the phase 0 branch | 914 passed, 24 skipped on this branch |
| Gate | | GATE PASSED |

The conversion rate does not move. Plans are separate outputs: a Davis workflow and an SLO are still
counted as manual, because the plan is not an alert condition. No fixture has a `contains()`,
`matchesValue()` or `in()` query, so the wider DQL form shows only in unit tests. This work adds
steps and safety, not a higher number.

AWS output is unchanged by the notification changes: `tests/fixtures/notifications-aws/` holds the
plan JSON, Terraform and report from before the change, and a test compares the new output byte
for byte (the files were also diffed by hand against a run on the previous commit).

## Risks and what is unverified

- New Relic side names are from memory, not from provider or API documentation: the destination
  type names (`SLACK`, `PAGERDUTY_SERVICE_INTEGRATION`, `JIRA`, `SERVICE_NOW`, `WEBHOOK`), the
  issue filter attribute `tags.<name>`, the 1, 7 and 28 day service level periods, that a service
  level has no warning level, and every attribute in the commented `newrelic_service_level`
  skeleton. A Teams destination type is not claimed; the plan uses a webhook.
- The shape of `entityTags` and `entityTagsMatch` in a Davis trigger is a guess; the one real
  object has `{}` and `null`. An unreadable form becomes a note.
- `contains()` is case-sensitive in Dynatrace. Whether LIKE is case-sensitive in New Relic was not
  checked; the converted condition carries a "matches may differ" note. Texts with `%`, `*` or `_`
  stay manual because LIKE could read them as wildcards. `_` may be safe; it was left out because
  I could not confirm it.
- `in(field, "a", "b")` and `in(field, array(...))` and the `matchesValue(field, {...})` set form
  are accepted as membership tests. Only the set form comes from a real export (a Davis event
  filter). The other DQL spellings are from memory.
- No data check or doctor call was run against a real tenant or account; the tests use fakes.
- `check-gcp-data` still carries its own copy of the helper now in `core/data_check.py`.

## For the owner

1. Merge decision, with the other two branches (see the shared-file list in the report).
2. Whether a Davis plan with destinations but no mappable filter should stay as it is (destinations
   only, no workflow) or get a workflow filtered on a priority you choose.
3. Whether to keep `plan-service-levels` as its own command or fold it into `convert`.
4. A live read of one Davis anomaly detector, once you create it (questions in the research file).
5. Review of the New Relic names above against the provider documentation before any apply.

## Round 2 (review fixes)

- Email pattern tightened and anchored with `\Z` (a trailing newline used to pass). This also
  changes the AWS path: addresses with `$`, braces, a backtick, non-ASCII characters, a hyphen at a
  label edge, consecutive dots or a one-letter last label are now refused. Owner: confirm that is
  acceptable for real SNS subscriptions.
- Unreadable recipients, categories, tag templates and empty workflows always leave a note.
  Destinations are capped at 20 and other tasks at 50 per plan, plans at 2000 per run.
- Names keep the destination index; repeated or long titles get a short hash. A non-empty
  `--out` is refused for the Dynatrace notification plans.
- `apply-notifications` needs `--include-needs-review` for plans marked needs_review, prints how
  many need review, and says plans with no workflow create nothing.
- `matchesValue()` and `in()` carry a "matches may differ" note (case and arrays: UNVERIFIED).
- Field names with an empty part (`a.`, `a..b`) stay manual; two different equal values on one
  field joined by and get a note.
- One token rule and one URL rule live in `dynatrace/extract.py` and are used by doctor.
