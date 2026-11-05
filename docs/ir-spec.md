# IR Spec (v0.1, draft for review)

The IR is the vendor-neutral middle of the pipeline. The source of truth is `src/importer/core/ir.py`; this document explains the rules.

```
Source JSON -> source models -> [translate] -> IRMonitor -> [emit] -> NerdGraph JSON | Terraform HCL
                                                          \-> [report] -> report.md
```

## IRMonitor fields

| Field | Meaning |
|---|---|
| `source_system`, `source_id`, `source_type` | Where it came from. Lets a second vendor (Dynatrace) reuse the emitters |
| `name`, `tags` | Copied through. **Untrusted text**: only emitters' escaping functions may place it in output |
| `signal` | `kind` (metric, log, apm, heartbeat, composite, unknown), `raw_query`, `window_seconds`, and exactly one structured body: `metric` (terms + arithmetic expression tree + scope + group-by), `count` (log/APM filters + group-by) or `heartbeat` |
| `thresholds` | critical/warning with operator and value, **already in New Relic units** (metric scale applied) |
| `no_data` | notify flag and timeout minutes |
| `notification` | message template and `@handles`. Channels are never migrated, only listed |
| `confidence` | `exact`, `approximate`, `manual`, `unsupported` |
| `reasons` | Why the confidence is below exact. Every downgrade must add a reason |
| `unmapped` | Source fields and options we did not translate. Kept so nothing is silently dropped |

## Confidence rules
- **exact:** converted with no caveats. Still subject to the global notes in the report.
- **approximate:** emitted, but a human should review the listed reasons (unverified tag mapping, dropped recovery thresholds, unit approximations, notification handles, template variables).
- **manual:** a known monitor type or form we cannot convert yet. Not emitted. The reason says what is missing.
- **unsupported:** no equivalent concept (for example composite monitors). Not emitted.

## Invariants
1. The translator never raises on a monitor. Any failure becomes `manual` or `unsupported` with a reason.
2. Unknown fields and options go into `unmapped`.
3. Emitters refuse anything that is not `exact` or `approximate` (`CannotEmit`).
4. All untrusted text reaches outputs through: `quote_string`/`identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables (NerdGraph), `md_cell` (report).
5. Conditions are emitted **disabled**.

## Metric mapping
Each source keeps its own mapping tables next to its translator (for CloudWatch, `cloudwatch_map.json`
and its dimension and suffix files). Names are unverified until checked against live New Relic data,
and every conversion says so. See `docs/research/metric-names-verified.md`.

## CloudWatch
Input is the JSON from `aws cloudwatch describe-alarms` (a file or folder); no AWS credentials are handled. Mapping:
- **Period / EvaluationPeriods / DatapointsToAlarm** become the aggregation window, the breach duration and the occurrences setting. N of N is `all`, 1 of N is `at_least_once` (both exact); other M of N is approximated as M consecutive breaches and flagged.
- **Statistic** Average/Sum/Minimum/Maximum are supported. Percentiles, SampleCount and anomaly detection are manual.
- **Metric names** follow New Relic AWS Metric Streams (`aws.<service>.<Metric>`, dimensions `aws.<service>.<Dimension>`) via `cloudwatch_map.json`. Unverified: the sandbox held no AWS data.
- **Metric math:** arithmetic over metrics with the same period, statistic and dimensions. Functions (FILL, RATE, ...) are manual.
- **TreatMissingData=breaching** becomes expiration that opens a violation; other modes are flagged.
- **Composite alarms** are unsupported; alarm actions (SNS) are listed, never migrated.
- `source_id` is a stable integer derived from the alarm ARN; the original is kept in `source_key`.

## Known gaps (prototype)
Anomaly/forecast/outlier, composite, SLO/process/other types, non-count log rollups, OR in search, recovery thresholds, negated metric scopes, unit conversion inside formulas, notification channels, dashboards.

Settings that are not migrated (priority, renotify, evaluation delay, ...) are listed per monitor and downgrade it to `approximate`. Defaults and record metadata are ignored.

## Open questions
1. Window semantics: a source evaluates one aggregate over its window; the emitted conditions use the aggregation window with a breach duration. Should the emitter use sliding windows instead?
2. Should notification handles be mapped through a user-supplied file (handle to New Relic destination)?
3. Should sources share a table of common attribute names, or keep mapping their own?

## Google Cloud
Input is the JSON from `gcloud alpha monitoring policies list --format=json`. One IR monitor is made per condition.
- **Threshold conditions** become metric conditions: `alignmentPeriod` is the aggregation window, `duration` the breach duration, `perSeriesAligner` the aggregation, `groupByFields` the facet, and `metric.labels` / `resource.labels` equality terms the scope.
- **Metric names** follow New Relic's documented rule (`gcp.<service>.<metric path>`, counters end in `_count`). Unverified.
- **Log-match conditions** with plain terms become a count of `Log` events; ranges and OR stay manual.
- **Absence, MQL, PromQL, SQL, ratio and forecast** conditions are manual with steps. Notification channels are listed, never migrated.
