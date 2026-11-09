# IR Spec (v0.1, draft for review)

The IR is the vendor-neutral middle of the pipeline. The source of truth is `src/importer/core/ir.py`; this document explains the rules.

```
Source JSON -> source models -> [translate] -> IRMonitor -> [emit] -> NerdGraph JSON | Terraform HCL
                                                          \-> [report] -> report.md
```

## IRMonitor fields

| Field | Meaning |
|---|---|
| `source_system`, `source_id`, `source_type` | Where it came from. Lets another source reuse the emitters |
| `name`, `tags` | Copied through. **Untrusted text**: only emitters' escaping functions may place it in output |
| `signal` | `kind` (metric, log, apm, heartbeat, composite, unknown), `raw_query`, `window_seconds`, and exactly one structured body: `metric` (terms + arithmetic expression tree + scope + group-by; a scope filter is equals, not equals, IN, NOT IN, LIKE, NOT LIKE or is-null), `count` (log/APM filters + group-by) or `heartbeat` |
| `thresholds` | critical/warning with operator and value, **already in New Relic units** (metric scale applied) |
| `no_data` | notify flag and timeout minutes |
| `notification` | message template and `@handles`. Channels are never migrated, only listed |
| `confidence` | `exact`, `approximate`, `manual`, `unsupported` |
| `reasons` | Why the confidence is below exact. Every downgrade must add a reason |
| `unmapped` | Source fields and options we did not translate. Kept so nothing is silently dropped |
| `emit` | Optional New Relic condition settings, left out of the saved IR when unset: `aggregation_method` (EVENT_FLOW, EVENT_TIMER, CADENCE), `aggregation_timer_seconds`, `fill_option` and `fill_value`, `slide_by_seconds`, `violation_time_limit_seconds`, `title_template`, `runbook_url`, `ignore_on_expected_termination`, and `null_safe_count` with `null_safe_scope`. Unset means the emitter default in `core/emit/common.py` (`EmitDefaults`), which reproduces the earlier output byte for byte. Bounds are checked at emission and a value New Relic would reject becomes a "not emitted" reason |

## Confidence rules
- **exact:** converted with no caveats. Still subject to the global notes in the report.
- **approximate:** emitted, but a human should review the listed reasons (for example unconfirmed metric names, approximated datapoint rules, unit rescaling, notification handles). Every alarm and condition the CloudWatch and Google Cloud translators emit is approximate today; none is marked exact.
- **manual:** a known monitor type or form we cannot convert yet. Not emitted. The reason says what is missing.
- **unsupported:** no equivalent concept (for example composite alarms). Not emitted.

## Invariants
1. The translator never raises on a monitor. Any failure becomes `manual` or `unsupported` with a reason.
2. Unknown fields and options go into `unmapped`.
3. Emitters refuse anything that is not `exact` or `approximate` (`CannotEmit`).
4. All untrusted text reaches outputs through: `quote_string`/`identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables (NerdGraph), `md_cell` (report).
5. Conditions are emitted **disabled**.

## Metric mapping
Each source keeps its own mapping tables next to its translator (for CloudWatch, `cloudwatch_map.json`
and its dimension and suffix files; for Google Cloud, `gcp_verified.json` lists the names seen on live data). Names are unverified until checked against live New Relic data,
and every conversion says so. See `docs/research/metric-names-verified.md`.

## CloudWatch
Input is the JSON from `aws cloudwatch describe-alarms` (a file or folder); no AWS credentials are handled. Mapping:
- **Period / EvaluationPeriods / DatapointsToAlarm** become the aggregation window, the breach duration and the occurrences setting. N of N is `all`, 1 of N is `at_least_once` (both exact); other M of N is approximated as M consecutive breaches and flagged.
- **Statistic** Average/Sum/Minimum/Maximum are supported. Percentiles (p99, p95 and so on) convert to the per-minute `<metric>.pNN` series, which needs the metric stream's additional statistics (confirmed on API Gateway only). Single-metric anomaly detection converts to a baseline condition. SampleCount, trimmed means and anomaly bands on metric math stay manual.
- **Metric names** follow New Relic AWS Metric Streams (`aws.<service>.<Metric>`, dimensions `aws.<service>.<Dimension>`) via `cloudwatch_map.json`. Names are checked against live data for DynamoDB, Lambda, Kinesis, SQS, SNS, API Gateway, EC2 (CPU), the load balancers, RDS, ElastiCache, EBS, S3 request metrics and ECS services (`docs/research/metric-names-verified.md`). The rest follow New Relic's documented convention and are unconfirmed.
- **Metric math:** arithmetic over metrics with the same period, statistic and dimensions. Functions (FILL, RATE, ...) are manual.
- **TreatMissingData=breaching** becomes expiration that opens a violation; other modes are flagged.
- **Composite alarms** are unsupported; alarm actions (SNS) are listed, never migrated.
- `source_id` is a stable integer derived from the alarm ARN; the original is kept in `source_key`.

## Known gaps (prototype)
Composite alarms, anomaly bands on metric math, Google forecast, MQL, PromQL and SQL conditions, SLOs, log rollups other than counts, OR in log filters, recovery thresholds, unit conversion inside formulas, and migration of notification channels for Google Cloud.

Settings that are not migrated (priority, renotify, evaluation delay, ...) are listed per monitor and downgrade it to `approximate`. Defaults and record metadata are ignored.

## Open questions
1. Window semantics: a source evaluates one aggregate over its window; the emitted conditions use the aggregation window with a breach duration. Should the emitter use sliding windows instead?
2. Should notification handles be mapped through a user-supplied file (handle to New Relic destination)?
3. Should sources share a table of common attribute names, or keep mapping their own?

## Google Cloud
Input is the JSON from `gcloud alpha monitoring policies list --format=json`. One IR monitor is made per condition.
- **Threshold conditions** become metric conditions: `alignmentPeriod` is the aggregation window, `duration` the breach duration, `perSeriesAligner` the aggregation, `groupByFields` the facet, and `metric.labels` / `resource.labels` equality terms the scope.
- **Metric names** follow New Relic's documented rule (`gcp.<service>.<metric path>`). No suffix is added; Google's own counter names already end in `_count`. Names for Compute Engine and Pub/Sub are confirmed on live data (see `docs/research/gcp-metric-names-verified.md`), the rest are not. Counter conditions use a window of at least 300 seconds because New Relic polls every 5 minutes.
- **Log-match conditions** with plain terms become a count of `Log` events. Severity ranges (`severity>=ERROR`) become a list of levels. OR and regular expressions stay manual.
- **Absence, MQL, PromQL, SQL, ratio and forecast** conditions are manual with steps. Notification channels are listed, never migrated.

## Notification and muting IR

Defined in `core/notification_ir.py` and `core/muting_ir.py`.

- **Destinations.** `NrDestination` is one destination plus the one channel that uses it, for any of the 14 New Relic types (`DestinationType`). `SPECS` holds, per type, the channel type (SERVICE_NOW has channel SERVICENOW_INCIDENTS, JIRA has JIRA_CLASSIC), the destination and channel property keys with required flags, the allowed auth kinds (none, basic, token, custom header, OAuth), whether a script may create it, and the manual steps a person must do (Slack OAuth, installing the New Relic app in Teams, installing the ServiceNow application, the AWS side of EventBridge). Unknown properties are refused. A secret field holds a `SecretRef` (a name), never a value; Terraform shows `var.<name>` as a sensitive variable and NerdGraph needs the value passed in. Plans carry these in `typed_destinations`, after the email and webhook `destinations` in the index space of a workflow.
- **Workflows.** `PlannedWorkflow` has predicates (use `tag_predicate` for entity tags: `accumulations.tag.NAME`), and optional `notification_triggers`, `muting_rules_handling` and up to two `enrichments`. `workflow_violations` and `plan_violations` report what New Relic would reject: an issue filter over 4,096 characters, more than two enrichments, a channel used twice in a workflow, a destination index that does not exist, more than 1,000 workflows.
- **Muting rules.** `MutingRule` has a group of conditions (attribute, operator, values; at most 20 conditions and 500 values) and an optional schedule: local start and end time, IANA time zone, daily, weekly or monthly repeat, weekdays, and an end date or a repeat count. Terraform (`newrelic_alert_muting_rule`) and NerdGraph are emitted by `core/emit/muting.py`. Rules are disabled by default.

## Dashboards
The dashboard IR is `core/dashboard_ir.py` (`IRDashboard`, `IRWidget`, `Series`, ...). The emitters are `core/emit/dashboard.py` (NerdGraph and Terraform). Fields marked "new" were added for the Dynatrace and Grafana translators; each is optional, and a dashboard that does not use them serialises and emits exactly as before. `tests/test_dashboard_ir_byte_identity.py` proves it with the AWS and GCP fixture folders and with 120 generated dashboards whose hashes come from the emitters before the change; that holds for widget heights up to 12 and for any text in labels and filter values.

Widget types (`Visualization`): `viz.line`, `viz.area`, `viz.bar`, `viz.pie`, `viz.billboard`, `viz.markdown`, and new `viz.table` (Terraform `widget_table`, no `TIMESERIES`).

Series. A widget has `series` (a `MetricSignal`, NRQL built by `core/emit/nrql.py`) and new `raw_series` (a query the translator already built; it has no label, because a New Relic query has none). A chart uses one or the other, not both, at most 50 raw series. Raw queries are written as given.
- `Series.period_seconds` is still an integer, 1 to 86400. New `auto_period: true` writes `TIMESERIES AUTO` instead of the fixed bucket and ignores `period_seconds` (auto wins). It is refused on any widget other than line and area. A nullable `period_seconds` would be the cleaner shape but `core/emit/nrql.py` reads it as an integer.
- A raw query must pass `check_raw_nrql` (`core/dashboard_nrql.py`): one statement starting with `SELECT` or `FROM`, at most 4096 characters (a repository cap, the New Relic limit is UNVERIFIED), no `;` or fullwidth semicolon outside a string, balanced quotes (a string literal cannot span lines), no control characters other than tab, line feed and carriage return (so pretty-printed queries pass), no line or paragraph separators, bidirectional overrides or zero-width characters (they could hide a keyword), and none of `SINCE`, `UNTIL`, `COMPARE WITH` (the dashboard time picker owns the window). `LIMIT` and `TIMESERIES` are allowed. The checker looks at the query with string literals and backtick names blanked out. A keyword followed by a comparison operator, `IS`, `IN`, `LIKE`, `NOT`, `AND`, `OR`, a comma or `)`, or preceded by a dot, is read as a column name (`WHERE limit > {{v}}` passes). A column named like a keyword and followed by anything else is read as the clause: quote it with backticks. Braces other than whole `{{name}}` references are malformed (`{{{v}}}` is refused). The emitters check raw queries again, because the models are not frozen. It validates structure; it does not make text safe, so the translator must still build the query with `quote_string` and `identifier`.

Variables. `IRDashboard.variables` (new, at most 50, unique names). Each `IRVariable` has `name` (letters, digits, underscore, at most 64, not starting with a digit), `title`, `type` (`nrql`, `enum`, `string`), `default_values` (one at most unless `multi_select`; for an enum each must be an item value), `multi_select` (not for `string`), `replacement` (`default`, `identifier`, `number`, `string`), `items` (enum only, unique values), `nrql` and `account_ids` (nrql only; integers from 1 to 10^15 - 1, at most 20; no accounts means the account in use), and the three options `excluded`, `ignore_time_range`, `show_apply_action`.
- A query refers to a variable as `{{name}}`. `check_variable_usage(nrql)` returns the names used and raises if one sits in `LIMIT`, `SINCE`, `TIMESERIES`, `COMPARE WITH`, `AS`, `OFFSET`, `SLIDE BY`, `UNTIL` or `WITH` (list from the New Relic template-variable documentation, see `docs/research/newrelic-notifications-and-nrql-reference.md`). A reference in a string literal belongs to the clause around it, so `AS '{{v}}'` is refused.
- `check_variables_defined` requires every variable used in a widget query to be defined. The model checks raw series on construction. The emitter checks every query of a dashboard that defines variables or has raw series, and raises `CannotEmit`. A dashboard with neither (every AWS and GCP dashboard) is not checked: braces in a label or filter value there are literal text and are written as before, even though New Relic would treat `{{x}}` in a query as a variable.
- A variable's own query may not use variables (nested variables are for some plans only, UNVERIFIED for the target account).

Limits: at most 300 widgets per dashboard, 50 variables.

Widget options (new, `IRWidget.options` and `IRWidget.thresholds`). Allowed per widget type, from the provider schema:

| Option | Widget types | Terraform output |
|---|---|---|
| `unit` | all charts | `units { unit }` (1 to 40 characters, no control characters; the strings New Relic accepts are UNVERIFIED) |
| `legend` | all charts | `legend_enabled` |
| `y_min`, `y_max` | line, area, bar | `y_axis_left_min`, `y_axis_left_max` |
| `y_zero` | line | `y_axis_left_zero` |
| `thresholds` | line, table, billboard | line: `threshold { name, severity, from, to }`; table: `threshold { column_name, severity, from, to }` (the schema has no `name` on a table threshold, so the model refuses one); billboard: `critical` and `warning` set to the `from_value` |
| `decimals` | all charts | not written (UNVERIFIED) |
| `stacked` | bar, area | not written (UNVERIFIED) |

A threshold needs `from_value`, `to_value` or both, between -10^15 and 10^15, at most 50 per widget. A billboard accepts only `critical` and `warning` thresholds with `from_value`, and only one of each (Terraform would reject a repeated attribute). Numbers are written with every digit: whole numbers as integers, others with `repr`. The enum is `ThresholdSeverity`. Severity values (`warning`, `severe`, `critical`, `success`, `unavailable`) come from the provider documentation; the provider schema does not enumerate them (UNVERIFIED).

Limits and the provider.
- Height. `Layout.height` still accepts 1 to 100, because the AWS and GCP translators clamp to 100. The provider documents 1 to 12; its schema states no limit on the attribute, and whether NerdGraph enforces 12 is UNVERIFIED. Both emitters write a taller widget at height 12 (`PROVIDER_MAX_HEIGHT`, `provider_layout`) and keep its row, column and width, so nothing overlaps; the widget below simply starts lower than the gap needs. The loss is recorded three ways: the widget gets the reason `height N clamped to 12` when it is built (so `report.md` shows it), the NerdGraph and Terraform description names the widgets (`Heights clamped to 12: title (30)`), and the IR keeps the original height.
- NerdGraph carries variables (`DashboardInput.variables`) but not widget options or thresholds. The field names of both are UNVERIFIED (no offline schema), and the repository rule is to keep unconfirmed names out of output. Read one dashboard back in the sandbox to confirm them, then extend `nerdgraph_dashboard`.
- UNVERIFIED: valid `units.unit` strings, `data_format` types behind `decimals`, the NerdGraph id for a stacked bar, the NerdGraph variable field names.
- `apply` (`core/apply_dashboards.py`) replaces the account placeholder in widget queries only. An `nrql` variable with no `account_ids` is written with the placeholder, so `with_account` must replace it before such a payload is sent.

Serialisation. The new fields are left out of `model_dump_json` while they hold their default (`OmitDefaults`), and load back with defaults, so IR files written before the change still load and dashboards that do not use the fields write the same bytes.

### Round 2 rules for the notification and muting IR

- **Secret names** are logical names: upper case, at least one underscore, 3 to 40 characters, not shaped like a key (AWS `AKIA`/`ASIA`, `sk-`, `ghp_`, `glsa_`, `NRAK`, Dynatrace `dt0`, long unbroken runs). Terraform variables are always `nr_secret_<lower case name>`, declared only for the destinations whose blocks are emitted. Plaintext in a secret property (Teams `securityCode`), a key-shaped value in an auth user, prefix, header key or channel property, and an invalid typed email address are refused when `NrDestination` is built. The models do not echo input in validation errors. `apply_notifications` scrubs the supplied secret values from API errors.
- **Resource addresses** use a hash of the plan topic. A second plan with the same topic gets a different hash, so addresses stay unique; a topic that occurs once keeps its old address. `notifications_terraform_result` returns the text and `plan_violations`.
- **Muting operators** `ANY`, `IS_BLANK` and `IS_NOT_BLANK` take no values. The Terraform emitter refuses them (the provider needs one value). The NerdGraph emitter emits them with `values: []`; that is UNVERIFIED against a live account.
- **`apply_notifications(..., include_needs_review=False)`** skips plans marked `needs_review` and lists them in `ApplyResult.skipped_review`; other plans are created. A CloudWatch topic with a Lambda, SQS or Firehose subscription is `needs_review` (it has a manual EventBridge destination), so it is skipped without the flag even if it also has email or https channels.
- **Condition options** are range-checked before any arithmetic on them. A timer without EVENT_TIMER, a non-finite fill value and a `null_safe_scope` that covers every filter are refused.
