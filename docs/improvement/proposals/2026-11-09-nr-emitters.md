# Proposal: New Relic notification and condition emitters

Branch `improve/nr-emitters`, started from `integration/all-threads`. Status: waiting for the owner.
Source of the requirements: `docs/research/newrelic-notifications-and-nrql-reference.md`, Part 3.
Everything is offline. No network, no Terraform init, plan or apply, no `importer apply`.

## What changed

1. Destination and channel model (`core/notification_ir.py`, `core/emit/notifications.py`).
   `NrDestination` is one destination plus its channel, for all 14 types. `SPECS` has, per type,
   the channel type (SERVICE_NOW is SERVICENOW_INCIDENTS, JIRA is JIRA_CLASSIC), the allowed
   destination and channel property keys with required flags, the allowed auth kinds, whether a
   script may create it, and the human steps (Slack OAuth, Teams app install, ServiceNow
   application, the AWS side of EventBridge) as data. Unknown properties are refused. A secret
   field holds a `SecretRef` (a name); Terraform writes `var.<name>` and a sensitive `variable`
   block, NerdGraph needs the value passed to `apply_notifications(..., secrets=...)` and refuses
   before the first write when one is missing. Slack, Slack collaboration and legacy Slack are
   refused with a reason (Terraform and the API cannot create them). Plans carry the new
   destinations in `typed_destinations`; the old email and webhook path now goes through the same
   emitter and produces the same bytes.
2. Workflow model. `tag_predicate()` makes `accumulations.tag.<name>`. Optional
   `notification_triggers`, `muting_rules_handling`, up to two `enrichments`. `workflow_violations`
   and `plan_violations` report an issue filter over 4,096 characters (measured as the JSON that
   carries it, the upper bound of any reading), more than two enrichments, a channel listed twice
   in one workflow, a destination index that does not exist, two plans with the same topic and
   more than 1,000 workflows. A workflow with a violation is not emitted: Terraform shows a
   `# not emitted:` comment (fixed wording, never source text), NerdGraph raises `CannotEmit`, and
   `apply_notifications` reports the plan as failed before it creates anything.
3. Muting rules. New `core/muting_ir.py` and `core/emit/muting.py`. Conditions (attribute,
   operator, values; at most 20 and 500), schedule (local start and end, IANA zone, daily, weekly
   or monthly repeat, weekdays, end date or repeat count), window-end action. Terraform
   `newrelic_alert_muting_rule` and the NerdGraph mutation. Rules are disabled by default.
4. Condition emitters. `IRMonitor.emit` (`EmitOptions`) holds streaming method (EVENT_FLOW,
   EVENT_TIMER, CADENCE), timer, fill option and value, slide-by, violation time limit, title
   template, runbook URL, ignore on expected termination and `null_safe_count` with
   `null_safe_scope`. EVENT_TIMER writes `aggregationTimer` and no delay. `terraform_file` takes
   `incident_preference`. `check_limits` turns out-of-range values into `CannotEmit` reasons:
   more than two terms or two of one severity, loss-of-signal timeout outside 30 s to 48 h, timer
   outside 5 s to 20 min, delay outside 0 to 20 min (EVENT_FLOW) or 0 to 60 min (CADENCE),
   violation time limit outside 5 min to 30 days, title over 256 characters, a runbook URL that is
   not http or https, a slide-by that does not divide the window or is under 30 s, fill option and
   value that do not belong together. `core/emit_limits_rules.py` (imported by `emit/common.py`)
   gives each wording a remediation rule. The window bounds keep the working rule of the research
   note (aggregation window 30 s to 1 h, duration 60 s to 24 h). `evaluation_offset` is not
   emitted: it is deprecated in the provider schema and cannot be combined with the streaming
   method. `evaluation_delay` exists in the schema but was not asked for and is not emitted.
5. Source fixes. Dynatrace: Teams is MICROSOFT_TEAMS with the app-install, security-code and
   team/channel-id steps; ServiceNow is SERVICE_NOW_APP; entity tags are `accumulations.tag.<name>`
   (through `tag_predicate`); Opsgenie is a WEBHOOK whose steps say a vendor payload template is
   required. CloudWatch: Lambda, SQS and Firehose subscriptions become one manual EVENT_BRIDGE
   destination per protocol, with the AWS account and region read from the topic ARN and the AWS
   side steps; the plan is marked as needing review and is kept even when no condition used the
   topic. https subscriptions can carry a warning that the receiver gets New Relic's payload and
   not the SNS envelope; it is off by default (see the table below).
6. Docs: `docs/ir-spec.md` (the `emit` row and a notification and muting section),
   `docs/what-migrates.md` (the Lambda/SQS/Firehose row and the Dynatrace paragraph, the two
   statements that became false), and `docs/improvement/lessons.d/2026-11-09-nr-emitters.md`.

## Byte-identity evidence

`tests/fixtures/emitter-output-hashes.json` holds the sha256 of every file the converters write,
taken from `integration/all-threads` before any change, and `tests/test_emitter_byte_identity.py`
asserts it. Groups: `convert` for cloudwatch (43 files), cloudwatch-real (19), gcp (9), dynatrace
(12), grafana (29); notification plans, report and Terraform for AWS (3), Dynatrace (4) and Google
(4). It passes on the final branch with the hashes unchanged since the first commit, so no
alert NRQL, NerdGraph JSON, `main.tf`, notification plan, notifications Terraform, report or saved
IR changed for any fixture. The `tests/fixtures/notifications-aws/*` files are untouched. No file
under `tests/golden/` and not `docs/improvement/baseline.json` was touched.

The test caught one real slip while I worked: a new field on the shared plan model appeared in
the Google plan files, because that writer dumps the whole model. The plan models now omit the
new fields when they hold their default.

## What would change if a default flipped

Measured by setting one field of `EMIT_DEFAULTS` in `core/emit/common.py` at a time, running the
hash comparison and the whole test suite, then restoring it. Nothing is flipped in this branch.
"Pinned tests" are tests that fail because they assert today's bytes; the list is by file.
The one golden file, `tests/golden/cloudwatch_expected.json`, is not affected by any flip (it
checks conversion, not emitted text).

| Flip | Fixture files that change | Tests that fail | Notes |
|---|---|---|---|
| `incident_preference = "PER_CONDITION"` (Terraform policy) | `main.tf` in cloudwatch, cloudwatch-real, gcp, dynatrace, grafana (5 files, one line each) | `test_emitter_byte_identity` (5 groups); 2 of the new `test_nr_conditions` tests that assert the default | Matches `apply.py`. Research item 1; needs no live test |
| `EVENT_TIMER` for cloudwatch and gcp metric and heartbeat signals (timer, no delay) | cloudwatch: `main.tf` and 18 `nerdgraph/*.json`; cloudwatch-real: `main.tf` and 8; gcp: `main.tf` and 2. None in dynatrace, grafana or notifications | `test_emitter_byte_identity` (cloudwatch, cloudwatch-real, gcp); `test_cloudwatch.py::test_terraform_uses_cloudwatch_label_and_occurrences`, `::test_nerdgraph_uses_cloudwatch_settings`; `test_grafana_emit.py::test_the_saved_ir_of_the_other_sources_is_byte_identical_to_before_grafana`; 2 new tests | Research item 2. Needs live test 2. Log and APM counts keep EVENT_FLOW |
| `muting_rules_handling = "DONT_NOTIFY_FULLY_MUTED_ISSUES"` | `notifications-aws/notifications.tf` (1 line) | `test_dynatrace_notifications.py::test_the_aws_notification_output_is_byte_identical`; `test_emitter_byte_identity[notifications/aws]`; 1 new test | Only worth it once muting rules are migrated; research item 7. The NerdGraph workflow input also changes (not in a fixture) |
| `count_fill = ("STATIC", 0.0)` for log and APM counts | dynatrace: `main.tf` and 2 `nerdgraph`; gcp: `main.tf` and 1; grafana: `main.tf` and 2 | `test_emitter_byte_identity` (gcp, dynatrace, grafana); `test_grafana_emit.py` saved-IR test | UNVERIFIED that fill STATIC 0 cures the empty window (live test 4) |
| `null_safe_count = True` (all filters inside `filter(count(*), WHERE ...)`, no outer WHERE) | Same files as the fill flip (dynatrace 3, gcp 2, grafana 3) | 34 tests: `test_emitter_byte_identity` (3 groups), `test_dynatrace.py` (3), `test_dynatrace_data_check.py` (1), `test_dynatrace_dql.py` (9), `test_gcp.py` (4), `test_grafana.py` (11), `test_grafana_emit.py` (1), `test_log_filters_and_inventory.py` (1) and others | Without `null_safe_scope` the signal is account-wide: it is 0 whenever any log row arrives. Needs the entity filter per source before it is a good default; also `below` conditions only, per research item 10 |
| `notification_triggers = ("ACTIVATED", "ACKNOWLEDGED", "CLOSED")` | `notifications-aws/notifications.tf` | `test_the_aws_notification_output_is_byte_identical`; `test_emitter_byte_identity[notifications/aws]`; 1 new test | Research item 9: needs live test 7 (server defaults) first |

Other behaviour changes that are on in this branch and so do not appear above, because no fixture
exercises them: the bound checks (a monitor outside them is now "not emitted" where the API would
have refused it), Teams, ServiceNow and Opsgenie in Dynatrace plans, Lambda/SQS/Firehose in AWS
plans, and `accumulations.tag.<name>` in Dynatrace tag filters.

The https warning is also a flip, measured by hand: with `warn_https_payload=True` the AWS plan
`topic-0001.json` gains a `notes` list (one file, `notifications.tf` and `report.md` unchanged,
because `cli.py` does not print plan notes for AWS). The brief says AWS https output stays
byte-identical, so the warning is off by default; `cli.py` does not pass the flag yet.

## Tests

New: `tests/test_emitter_byte_identity.py` (2 tests, 9 cases), `test_nr_destinations.py` (14),
`test_nr_workflows.py` (13), `test_nr_muting.py` (11), `test_nr_conditions.py` (22),
`test_nr_aws_notifications.py` (4); helper `tests/hcl_schema.py` and the schema extract
`tests/fixtures/provider-schema-extract.json` (from `terraform providers schema -json`, provider
3.101.12, read-only). Emitted Terraform for destinations, channels, workflows, muting rules and
conditions is checked against that extract for unknown or missing attributes and blocks.
Changed on purpose: `test_notifications.py` (Lambda, SQS reasons; Firehose and application added),
`test_dynatrace_notifications.py` (Teams, ServiceNow, Opsgenie types, tag attribute, four new
tests), `test_dynatrace_round2.py` (note wording). Each failed before the change with an assertion
(13 failures when the test edits were made before the source edits). The new-module tests for
items 1, 2 and 4 failed on import first. Slip: the muting tests (item 3) were run only after the
module existed, so they were not seen failing; they test behaviour that did not exist before.

## Risks

- Every NerdGraph input name below was written from the research note and my memory of the
  schema, not from a live call. A wrong name fails at the API and nothing is created, because
  `apply_notifications` checks every input before the first write and writes are never retried.
- The `CannotEmit` bounds can only turn a monitor that the API would have refused into a
  "not emitted" reason. No fixture hit one.
- `accumulations.tag.<name>` replaces `tags.<name>`; the research note recommends it from two
  documentation pages but a live test (test 6) decides.
- Terraform `variable` blocks for secrets are written at the top of `notifications.tf`; the
  CLI's existing header is written before them, which is valid HCL, but nothing runs
  `terraform validate` offline in this branch.
- `typed_destinations` cannot be produced by any source yet; only tests build them. The value is
  the model, the emitters and the checks that the sources can now use.

## UNVERIFIED

- NerdGraph: `auth` input (`authType` BASIC, TOKEN, CUSTOM_HEADERS; `basic`, `token`,
  `customHeaders`), `enrichments.nrql`, `enrichmentsEnabled`, `notificationTriggers`,
  `AlertsMutingRuleInput` and the `alertsMutingRuleCreate` argument names, `signal.aggregationTimer`,
  `fillOption`, `fillValue`, `slideBy`, `violationTimeLimitSeconds`, `titleTemplate`,
  `runbookUrl`, `expiration.ignoreOnExpectedTermination`.
- Muting operator spellings (the provider documentation set is used; the offline schema does not
  list them, and New Relic's own page spells some negatives differently).
- Whether the 4,096-character filter limit counts the JSON, the values or something else.
- An empty `property { key = "" value = "" }` block for destinations with no properties is the
  documented idiom for destinations; for channels with no properties it is my extension.
- The `securityCode` property for Teams, and EVENT_BRIDGE with no auth block (the note found two
  conflicting examples).
- That EVENT_TIMER with no delay is accepted, and that a timer of the window length is right.
- A null-safe count with no outer WHERE is account-wide (by the documented query order).
- `accumulations.tag.<name>` matching an entity tag.

## Requests for the integrator

1. `cli.py` (frozen): call `plan_violations(plans)` after planning and print the lines; pass
   `warn_https_payload` to `translate_topic` if the owner accepts the https warning; show the
   `result.failed` reasons of `apply_notifications`; pass `secrets` to it from the environment
   when typed destinations exist; offer `--incident-preference` and
   `--muting-handling` options that build an `EmitDefaults`; add a `convert-muting` command in
   `src/importer/commands/muting.py` once a source produces `MutingRule`.
2. `sources/dynatrace/translate.py` (not mine): add `("victorops", "victorops")` and
   `("xmatters", "xmatters")` before the `http` entry of `ACTION_LABELS`. `SERVICES` in
   `notifications.py` already has both keys, but today those tasks are labelled "other".
3. `sources/gcp/notifications.py` (not mine): the channel types could build `NrDestination`
   values with `SecretRef` placeholders (email, token webhook, basic webhook, PagerDuty,
   Teams) and show `spec_for(...).human_steps` instead of its own reason texts.
4. `core/taxonomy.py` (frozen): nothing needed; every new reason starts with `not emitted` and is
   classed as cannot be emitted. If the owner wants the limit reasons split from the structural
   ones, register `^not emitted: (more than two|loss-of-signal|aggregation (timer|delay))`.
5. Section owners of `docs/what-migrates.md`: the Dynatrace paragraph and the AWS Notifications
   table were edited only for statements that became false.
6. Owner decisions: which flips in the table to take, and when. Each one is a one-line change of
   `EMIT_DEFAULTS` plus regenerating `tests/fixtures/emitter-output-hashes.json` and updating the
   pinned tests listed.
