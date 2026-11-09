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
