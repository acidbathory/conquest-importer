# What migrates, what does not, and why

Every item the importer reads gets one of four statuses, with the reason attached. The report
counts the first two as migrated and the last two as not migrated.

| Status | Means | You do |
|---|---|---|
| Migrated (exact) | Converted with no caveats | Review, then enable |
| Migrated, review (approximate) | Converted, but there are notes to read | Read the notes, compare numbers, then enable |
| Manual | Could not be converted automatically | Rebuild by hand, using the steps in the report |
| Unsupported | No New Relic equivalent in this prototype | Use the workaround, or leave it in AWS |

Right now most converted alarms show "Migrated, review" and not "Migrated". New Relic's metric
names are only confirmed against real data for some AWS services (see below), so every conversion
carries that caveat until you run `importer validate` on live data.

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
| Anomaly-detection alarms | New Relic's baseline model is different | Recreate as a New Relic baseline condition once data is flowing |
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

New Relic metric names are confirmed against real data for DynamoDB, Lambda (per function),
Kinesis, SQS, SNS, API Gateway, EC2 (CPU) and Application Load Balancer fixed responses. For the
other services the names follow New Relic's documented convention but aren't confirmed, so a
converted query could return nothing. Run `importer validate` once data flows.

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

## Not handled at all

Logs themselves, X-Ray traces and CloudTrail. It also handles one AWS region and one
account per run. Existing New Relic objects with the same name are skipped, never updated. To apply
a fix, roll back and re-apply.

## Other platforms

Only AWS CloudWatch is supported today. Other platforms are planned; see the
[roadmap](../README.md#roadmap).
