# What migrates, what does not, and why

Every item the importer reads gets one of four statuses, with the reason attached. The report
counts the first two as migrated and the last two as not migrated.

| Status | Means | You do |
|---|---|---|
| Migrated (exact) | Converted with no caveats | Review, then enable |
| Migrated, review (approximate) | Converted, but there are notes to read | Read the notes, compare numbers, then enable |
| Manual | Could not be converted automatically | Rebuild by hand, using the steps in the report |
| Unsupported | No New Relic equivalent in this prototype | Use the workaround, or leave it in AWS |

Every converted alarm and condition currently shows "Migrated, review"; none is marked exact. New
Relic's metric names are only confirmed against real data for some services (see below), and window
and missing-data rules are approximated, so every conversion carries that caveat until you run
`importer validate` on live data.

If New Relic refuses to create something, the report counts it as not migrated and shows New
Relic's reason, even though the conversion itself succeeded.

## Alarms

An alarm migrates when it meets all of these:

- It watches one metric with a Sum, Average, Minimum, Maximum or percentile (p99, p95 and so on)
  statistic.
- Its period is 60 seconds or more. New Relic limits the aggregation window to 30 seconds to 1 hour.
- Its namespace is API Gateway, Application or Network Load Balancer, classic ELB, DynamoDB, EBS,
  EC2, ECS, ElastiCache, Kinesis, Lambda, RDS, S3, SNS, SQS, or `Custom/<name>`.

For custom metrics the name may contain only letters and digits, as in `Custom/myapp`. A name with
`-`, `_` or `.` becomes manual, because its New Relic metric name isn't confirmed.

Metric math also migrates when it only adds, subtracts, multiplies or divides metrics, for example
an error rate. Dimensions become filters. Any dimension the alarm doesn't use is pinned to empty,
so New Relic doesn't add several series together.

Anomaly-detection alarms on one metric also migrate, as New Relic baseline conditions. The band
width (the second argument of `ANOMALY_DETECTION_BAND`, 2 by default) becomes the number of
standard deviations, and the direction (above, below or both) carries over. The two models learn
differently and New Relic needs a few days of data to settle, so the report marks these as
"Migrated, review".

Some details are approximated, and the report says so:

- "3 of 5" datapoints becomes "3 consecutive breaches".
- "Treat missing data as not breaching" has no New Relic equivalent. The condition stays unchanged
  when data is missing.
- Percentiles need the metric stream to send them. The generated Terraform does this.

### Alarms on log metric filters

A CloudWatch Logs metric filter turns matching log lines into a metric, and an alarm on that metric
is really an alarm on a log query. When the importer can read the filter (it needs
`logs:DescribeMetricFilters`, see [permissions](permissions.md)), the alarm becomes a New Relic
condition that counts the matching `Log` events in the log group. This works when:

- the alarm statistic is Sum and the filter publishes the value `1`, so the metric is a count of
  events;
- the filter has no dimensions;
- the pattern is plain terms (all required, or excluded with `-term`), a quoted phrase, or a JSON
  pattern that compares string fields with `=` or `!=` joined by `&&`.

Anything else (OR terms, numeric or boolean comparisons, space-delimited patterns, a value taken
from the log line) stays manual, with the reason and the steps in the report. The converted
condition returns data only after that log group is forwarded to New Relic, which matches logs on
the `logGroup` attribute that New Relic adds on its Firehose and Lambda paths. Text matching can
differ from CloudWatch in case sensitivity, so compare counts before you enable it.

| Not migrated | Why | Fix |
|---|---|---|
| Composite alarms | New Relic has no composite condition | The report reads the rule and says how to rebuild it. An OR needs nothing, because separate conditions already do it. An AND becomes one NRQL condition or workflow filtering. It lists which child alarms did convert |
| Anomaly-detection alarms that are not a plain band on one metric (for example a band on metric math) | The baseline condition needs a single metric | Recreate as a New Relic baseline condition once data is flowing |
| High-resolution alarms (under 60 s) | Metric Streams data is per minute | Use a 60 s window, or keep the alarm in CloudWatch |
| Metric math with functions (`RATE`, `FILL` and so on) | Only arithmetic is converted | Write the NRQL by hand |
| Other statistics (`SampleCount`, trimmed means and so on) | No mapping | Pick the closest supported statistic |
| Periods New Relic rejects (a one-day period, for example) | The aggregation window must be 30 s to 1 h | Rebuild with a window inside that range |
| Alarm actions other than SNS (EC2, Auto Scaling, Systems Manager) | No equivalent | Recreate the automation, triggered from a workflow webhook |

## Dashboards

Metric widgets (line, stacked area, bar, pie, single number) and text widgets migrate, with the
grid layout approximated. Dashboards are created private.

| Not migrated | Why | Fix |
|---|---|---|
| Log widgets (Logs Insights) | Needs logs in New Relic | Forward logs, then write the NRQL over `Log` |
| Alarm status widgets | No equivalent widget | Link to your alerts overview from a text widget |
| Metrics Explorer widgets | No equivalent | Rebuild the chart by hand |
| Math inside widgets | Same limit as alarms | Write the NRQL by hand |
| Chart settings (axis limits, annotations and so on) | Not carried over | Set them in New Relic |

## Notifications

SNS topics with email and https subscriptions migrate. Each subscription becomes a destination and a
channel, and each topic becomes one workflow that routes the converted conditions that used it.
Workflows are created disabled.

| Not migrated | Why | Fix |
|---|---|---|
| SMS, Lambda, SQS, mobile push, Firehose | No New Relic equivalent | Use email, webhook, Slack or PagerDuty |
| Plain `http` endpoints | Not secure | Put the endpoint behind https |
| Unconfirmed subscriptions | Not active in AWS | Confirm or delete them, then re-run |

## Metric data

Alerts and dashboards need data behind them. The importer generates the Terraform for AWS Metric
Streams into New Relic (step 6 of [getting started](getting-started.md)) but doesn't run it,
because it needs your license key. It streams only the namespaces your alarms use.

New Relic metric names are confirmed against real data, with CloudWatch's own numbers matching
exactly, for DynamoDB, Lambda (per function), Kinesis, SQS, SNS, API Gateway, EC2 (CPU), the
Application, Network and classic load balancers, RDS, ElastiCache, EBS, S3 request metrics and ECS
services. See [the verification note](research/metric-names-verified.md) for what was tested.

Some names are not confirmed: S3 storage metrics (they arrive once a day), ECS metrics for a
cluster rather than a service, and metrics that need traffic or a healthy target to exist, such
as classic load balancer `RequestCount` and `Latency`. For those the names follow New Relic's
documented convention, so a converted query could return nothing. Run `importer validate` once
data flows.

## Other monitoring configuration

The importer also lists these when it can read them. Each one counts as an asset that needs a
person, and the report gives the steps. None of them converts automatically.

| Found in AWS | New Relic equivalent | What the report tells you |
|---|---|---|
| Log metric filters that no converted alarm uses | A log query on `Log` | Forward the log group, then query it directly |
| EventBridge rules that route alarm state changes | Workflows and destinations | Recreate each target as a workflow destination |
| EventBridge rules that react to AWS service events | None documented for receiving events | An EventBridge API destination that posts to New Relic is one route; test it first |
| Synthetics canaries | Synthetics monitors | Recreate as a ping, API or browser monitor |
| Contributor Insights rules | A log query with `FACET` | Rebuild as a query |
| RUM app monitors | The Browser agent | Add the agent; history doesn't carry over |
| Application Signals SLOs | Service levels | Recreate once the service data is in New Relic |
| Route 53 health checks | Synthetics ping monitors | Recreate; keep the check if it drives DNS failover |

Scheduled and disabled EventBridge rules are not monitoring, so they aren't counted. The report
mentions how many it left out.

## Log forwarding

Alarms converted from log metric filters count `Log` events, so they need those log groups in New
Relic. When a run converts any, it writes `logs-ingest/`, Terraform that follows New Relic's
documented setup: a Firehose delivery stream to the New Relic logs endpoint (GZIP, 1 MiB buffer),
a role for CloudWatch Logs to write to it, and one subscription filter per log group. Like the
metric stream, it is generated and never deployed, because it needs your license key. AWS allows
two subscription filters per log group, so a group that already has two needs one removed first.
`importer ingest-logs-setup --log-group /my/group` generates the same thing for groups you choose.

## Not handled at all

Forwarding logs in general (only the groups behind converted conditions get Terraform), X-Ray traces and CloudTrail. It also handles one AWS region and one
account per run. Existing New Relic objects with the same name are skipped, never updated. To apply
a fix, roll back and re-apply.

## Other platforms

AWS CloudWatch is the supported platform. Google Cloud is in progress: `importer extract-gcp
--project <id>` reads the alert policies (read-only), and `--source gcp` converts them. It also
accepts the JSON of `gcloud alpha monitoring policies list --format=json`.

- **Converts:** metric threshold conditions (including percentile series for distribution
  metrics), metric-absence conditions and log-match conditions with plain terms.
- **Absence:** an absence condition becomes a loss-of-signal check: a count of the metric's data
  points below 1, plus an expiration that opens a violation when no data arrives for the
  duration. New Relic polls every 5 minutes, so the duration is at least 300 seconds and rounded
  up to a whole number of windows (the notes say so). Without a group-by it fires only when no
  series reports. If the metric never arrives in New Relic (VM CPU utilization in our test), the
  condition fires as soon as it is enabled; the notes say so. An absence condition whose filter,
  aggregation or metric cannot be mapped stays manual.
- **Manual, with steps:** MQL, PromQL, SQL, ratio and forecast conditions, filters with
  OR, regular expressions or functions, uptime-check alerts, and alerts on custom, Ops Agent,
  Prometheus or log-based metrics (New Relic does not document collecting those from Google
  Cloud, so a converted query would return nothing).
- **Counters:** New Relic stores one change per 5 minute poll, so counter conditions use a window
  of at least 300 seconds and a rescaled threshold (the condition's notes say by how much).
- **Log severity:** `severity>=ERROR` and the other ranges become `severity IN (...)` with the list
  of levels. The attribute name for forwarded Google Cloud logs is not confirmed.
- **Link:** `importer gcp-link-terraform --project <id> --nr-account-id <n> --alerts <run>` writes
  the Terraform that links the project, with only the services the conditions use. You review it
  and run `terraform apply` yourself (it grants IAM roles).
- **Check the data:** `importer check-gcp-data --alerts <run> --nr-account-id <n>` asks New Relic
  whether each converted condition's metric is arriving (read-only). A condition on a missing
  metric never fires, and a `sum()` on it returns 0 without an error.
- **Not done yet:** dashboards, notification channels, uptime checks and SLOs are listed by
  `extract-gcp` (channels and checks as inventory only) but not converted.
- **Report:** `importer report <run folder> --source gcp` writes the same report with Google
  Cloud wording.
- **Metric names:** checked on real data for Compute Engine and Pub/Sub only. See
  [the verification note](research/gcp-metric-names-verified.md), including one metric (VM CPU
  utilization) that did not arrive in New Relic.

### Dynatrace (phase 0)

`importer extract-dynatrace --out <folder>` reads workflows and SLOs from a Dynatrace Platform
tenant (read-only; `DYNATRACE_ENV_URL` and `DYNATRACE_API_TOKEN` in `.env`), and `--source
dynatrace` converts them. Nothing here has been run against a tenant that has workflows or SLOs:
the converter was built from scrubbed copies of three real objects and a few hand-made ones.

- **Converts (approximate):** a scheduled workflow with one `execute-dql-query` task of the form
  `fetch logs[, from:now() - Nm|Nh] | filter field == "value" [and ...] | summarize n = count()`,
  and notify tasks gated by `result("task")["records"][0]["n"] > N` (also `>=`, `<`, `<=`). It
  becomes a New Relic count of `Log` events with that threshold. The window is the query's time
  range (the threshold counts that much log), or the schedule interval when the query has none;
  one hour at most, otherwise the workflow stays manual.
- **Caveats on a converted workflow:** the logs must be forwarded to New Relic and the attribute
  names must match them; the mapping of `loglevel` to `level` is not confirmed. Dynatrace ran the
  query on a schedule and New Relic evaluates continuously. A below condition may not fire on an
  empty result. Notification tasks are listed but not converted. An inactive trigger or a draft
  (not deployed) workflow is noted and the condition is created disabled. A notify task that
  runs on a state other than OK keeps the workflow manual.
- **Manual, with steps:** any other DQL (metrics, several summaries, joins, other functions),
  other task layouts, schedules longer than one hour, manual and non-Davis event triggers, Davis
  problem workflows (these are notification routes: recreate the destination, channel and a
  workflow filtered on issue priority or tags) and every SLO (the criteria are listed; rebuild as
  a New Relic service level).
- **Not done:** classic alerting profiles and problem notifications, anomaly detectors,
  dashboards, service level creation, and any check that the converted query returns data.
- **Sequence:** `importer extract-dynatrace --out out/dynatrace`, then `importer convert
  --source dynatrace --in out/dynatrace --out out/dynatrace/alerts`, then `importer report
  out/dynatrace --source dynatrace` (the report reads `alerts/` inside the folder you give it).
- **Behind a TLS-inspecting proxy:** set `SSL_CERT_FILE` to the corporate root certificate file.

### Grafana (phase 0)

`importer extract-grafana --out <folder>` reads Grafana-managed alert rules from a Grafana Cloud
stack (read-only; `GRAFANA_URL` and `GRAFANA_SA_TOKEN` in `.env`), and `--source grafana` converts
them. The loader also reads a rule file or folder in any of the four shapes Grafana returns
(provisioning list, export, ruler, `/apis`). Nothing here has been run against New Relic. The
provisioning and export shapes come from one sandbox stack (17 paused sample rules, Grafana 13.4);
the ruler and `/apis` shapes are hand-made from the documentation.

- **Converts (approximate):** a rule whose pipeline is exactly query, optional reduce, one
  threshold (above, at least, below, at most, equal, not equal), on one of:
  - a Prometheus selector with an optional single `avg`, `sum`, `min` or `max` and `by (...)`:
    `avg by (instance) (cpu{job="node",env!="dev"})` becomes `SELECT average(cpu) FROM Metric
    WHERE job = 'node' AND env != 'dev' FACET instance`. Matchers `=`, `!=`, `=~` and `!~` convert
    when the regex is plain words joined by `|` (IN), or a plain word with `.*` before or after
    (LIKE). Names ending `_total`, `_count`, `_sum` or `_bucket` look like counters and stay manual.
  - a Loki `count_over_time({k="v"} |= "text" [5m])`, optionally in `sum (by ...)`. It becomes a
    count of `Log` events with the labels and `message LIKE` filters. The range is the window.
- **Window and pending period:** the aggregation window is the group's evaluation interval (60
  seconds when the read has none), or the Loki range, rounded up to whole minutes and at most one
  hour. The pending period (`for`) becomes the threshold duration, rounded up to whole windows and
  at most 24 hours; New Relic restarts the count when a window has no data, so it may differ.
- **Notes on a converted rule:** the metric must already reach New Relic (Prometheus remote write
  or OTLP) and its New Relic name and label attributes are not confirmed. A rule without a data
  source type in its query has the type inferred from the data source uid or the query shape, and
  says so. Paused rules are created disabled. `keep_firing_for` and a recovery threshold are
  ignored. No-data state `Alerting` becomes loss of signal; `NoData` and `KeepLast`, and any
  query-error state, have no equivalent. Contact points and notification policies are not
  converted.
- **Manual, with steps:** expression chains, math across queries, classic conditions, SQL
  expressions, resample, mixed data sources or several queries, recording rules, rules run by the
  Mimir or Loki ruler, range thresholds (`within_range`, `outside_range`), reducers `count` and
  `median`, other data source types (CloudWatch, Azure Monitor, Elasticsearch, SQL and so on),
  PromQL with `offset`, subqueries, `group_left`, `topk`, `histogram_quantile`, `rate` and other
  range functions, `absent()`, binary operators or comparisons, and LogQL with parser stages,
  `unwrap`, other functions or regular-expression line filters.
- **Not done:** contact points, policies, mute timings and templates (the extractor counts them
  only), dashboards, a check that the metric exists in New Relic, and any live test.
- **Host rule:** the extractor accepts only `https://<stack>.grafana.net`. Another https host needs
  `--allow-host <host>` and must equal the host in `GRAFANA_URL`.
- **Sequence:** `importer extract-grafana --out out/grafana`, then `importer convert --source
  grafana --in out/grafana --out out/grafana/alerts`, then `importer report out/grafana --source
  grafana` (the report reads `alerts/` inside the folder you give it).
- **Behind a TLS-inspecting proxy:** set `SSL_CERT_FILE` to the corporate root certificate file.

Azure and other platforms are planned; see the [roadmap](../README.md#roadmap).
