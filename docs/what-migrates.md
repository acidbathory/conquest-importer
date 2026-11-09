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
--project <id>` reads the alert policies, channels, uptime checks and dashboards (read-only), and
`--source gcp` converts the policies. It also accepts the JSON of `gcloud alpha monitoring policies list --format=json`.

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
- **Dashboards:** `extract-gcp` also reads the dashboards (the v1 API). `importer
  convert-dashboards --source gcp --in <export>/dashboards --out <folder>` converts chart and
  scorecard widgets over a plain time series filter (same metric names, labels, aligners and
  reducers as alerts) and text widgets (as markdown). Mosaic, grid, column and row layouts are
  placed on New Relic's 12-column grid. Every other widget (logs panel, alert chart, incident
  list, MQL, PromQL, ratio and SQL queries, uptime and custom metrics) stays as a "Not migrated"
  placeholder with a reason and steps. Dashboards are created private. Stacked bar charts are
  shown as area charts (New Relic bar charts have no time axis). A counter metric is charted as
  the sum of the 5 minute changes in each bucket (for a scorecard, over the whole time range),
  not as a rate; the widget's notes give the divisor. A scorecard with a group-by shows one value
  per group. Widgets that overlap in the source, or run out of the 12 columns, are moved down and
  the dashboard says how many. Never run against a real export: the fixtures are hand-made.
- **Notification channels:** `importer convert-gcp-notifications --channels <export>/channels
  --alerts <converted run> --out <folder>` writes one section per set of channels (one workflow
  to create by hand per set), the converted conditions that used it, and the steps for each
  channel type (email, webhook with token or basic authentication, Slack with its one-time OAuth,
  PagerDuty; SMS, Pub/Sub and mobile have no New Relic destination). Nothing is created
  automatically: the export deliberately holds no label values (addresses, URLs, tokens). A
  channel or check name that looks like an address, a URL or a token (contains `@`, `://`, `?`,
  `key=value`, or a long hex or base64-looking run) is replaced by the resource id in the
  export, plans and reports; other names are kept as written, so do not put secrets in names.
- **Uptime checks:** `importer convert-gcp-uptime --checks <export>/uptime-checks --out <folder>`
  lists each check with its path (query string and fragment removed, token-like segments
  masked), its period, the shortest New Relic run frequency that does not run more often than
  Google did (capped at 60 minutes, which the row says when Google ran it less often), and the
  monitor to create (ping or scripted for HTTP; TCP and group checks have no direct monitor). A
  period that cannot be read is shown as unknown, with no frequency. The host is not exported. Uptime-check alerts stay manual and point at this list.
- **Not done yet:** SLOs, log sinks and log-based metrics.
- **Report:** `importer report <run folder> --source gcp` writes the same report with Google
  Cloud wording.
- **Metric names:** checked on real data for Compute Engine and Pub/Sub only. See
  [the verification note](research/gcp-metric-names-verified.md), including one metric (VM CPU
  utilization) that did not arrive in New Relic.

### Dynatrace (phases 0 and 1)

`importer extract-dynatrace --out <folder>` reads workflows and SLOs from a Dynatrace Platform
tenant (read-only; `DYNATRACE_ENV_URL` and `DYNATRACE_API_TOKEN` in `.env`), and `--source
dynatrace` converts them. Nothing here has been run against a tenant that has workflows or SLOs:
the converter was built from scrubbed copies of three real objects and a few hand-made ones.

- **Converts (approximate):** a scheduled workflow with one `execute-dql-query` task of the form
  `fetch logs[, from:now() - Nm|Nh] | filter <test> [and <test> ...] | summarize n = count()`,
  where a test is `field == "v"`, `contains(field, "v")`, `matchesValue(field, "v")`,
  `matchesValue(field, {"a", "b"})`, `in(field, "a", "b")` or `in(field, array("a", "b"))`
  (they become `=`, `LIKE '%v%'`, `=` and `IN`), and notify tasks gated by `result("task")["records"][0]["n"] > N` (also `>=`, `<`, `<=`). It
  becomes a New Relic count of `Log` events with that threshold. The window is the query's time
  range (the threshold counts that much log), or the schedule interval when the query has none;
  one hour at most, otherwise the workflow stays manual.
- **Caveats on a converted workflow:** the logs must be forwarded to New Relic and the attribute
  names must match them; the mapping of `loglevel` to `level` is not confirmed. Dynatrace ran the
  query on a schedule and New Relic evaluates continuously. A below condition may not fire on an
  empty result. `contains()` is case-sensitive in Dynatrace and how LIKE treats case in New
  Relic was not checked, so a note says matches may differ. Notification tasks are listed but not converted. An inactive trigger or a draft
  (not deployed) workflow is noted and the condition is created disabled. A notify task that
  runs on a state other than OK keeps the workflow manual.
- **Manual, with steps:** any other DQL (metrics, several summaries, a `by:` grouping, a
  `contains()` text with `%`, `*` or `_`, a `matchesValue()` with `*`, joins, other functions),
  other task layouts, schedules longer than one hour, manual and non-Davis event triggers, Davis
  problem workflows and every SLO. The last two now come with a plan, below.
- **Notification plans for Davis problem workflows:** `importer convert-notifications --source
  dynatrace --topics out/dynatrace --out out/dynatrace/notifications` writes one plan per Davis
  workflow, a report and Terraform. Email recipients become email destinations and channels (the
  addresses are in the plan and Terraform only, not the report). Slack, PagerDuty, Jira,
  Teams, ServiceNow and HTTP tasks become manual entries with the closest New Relic destination
  type and the steps for the secret or sign-in; anything else is manual with a reason. Entity tags
  on the trigger become issue-filter predicates (`tags.<name>`, unverified in New Relic) and a
  disabled workflow is planned only when there is such a filter; Davis categories, a custom
  filter, the severity threshold and notify-on-close are notes. Every plan is marked as needing
  review. `apply-notifications` creates a planned workflow with its destinations only when you
  add `--include-needs-review` to `--apply`; its dry run prints how many plans need review. A plan
  with destinations but no workflow (no mappable filter) gets its destinations and channels only
  through `notifications.tf`; `apply-notifications` creates nothing for it. `tags.NAME` is
  unverified in New Relic. `matchesValue()` and `in()` may ignore case or match inside arrays in
  Dynatrace (UNVERIFIED, from memory), so a converted condition carries a note.
- **Service level plans for SLOs:** `importer plan-service-levels --in out/dynatrace --out
  out/dynatrace/service-levels` writes one plan per SLO with the target, the period (only 1, 7 or
  28 days map; unverified) and notes, a report, and a commented-out `newrelic_service_level`
  skeleton. The good and valid event queries are TODO: the indicator reads Dynatrace metrics
  that do not exist in New Relic, and no data path is invented. A warning level is dropped with a
  note. Nothing is created.
- **Checks:** `importer doctor --source dynatrace` checks the token and tenant address offline
  (no network call) and warns when `SSL_CERT_FILE` is not set. `importer check-dynatrace-data
  --alerts out/dynatrace/alerts --nr-account-id <id>` asks New Relic whether matching `Log` events
  exist for each converted condition (read-only). Exit 2 means a condition had no matching
  events in the window; that is not proof it is broken, because the event may simply not have
  occurred in those hours.
- **Not done:** classic alerting profiles and problem notifications, anomaly detectors,
  dashboards, service level creation, and routing a converted condition to its notification plan.
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
- **Aggregation layers:** Grafana aggregates across series (the PromQL `avg`, `sum`, `min`,
  `max`) and then reduces over time (the reduce node). One New Relic aggregation does both only
  when they are the same operation, so these convert: `avg` with reducer last or mean, `max` with
  reducer max, `min` with reducer min, and a bare selector with any of last, mean, min, max, sum.
  Any other pair (for example `sum(x)` with any reducer, or `max(x)` with mean) stays manual, with
  steps; a plain `sum` would add every sample of every series in the window.
- **Window:** a reducer that looks back (mean, min, max, sum, on a range query) uses the query's
  `relativeTimeRange.from` as the aggregation window, in whole minutes from 60 seconds to one hour;
  a missing, zero or longer range is manual. Reducer last, and any instant query, ignore the range
  and use the group's evaluation interval (60 seconds when the read has none); the rule says which
  happened. A Loki count takes its window from the `[5m]` range, and only reducer last (or an
  instant query) converts. Windows are rounded up to whole minutes.
- **Pending period:** the pending period (`for`) becomes the threshold duration, rounded up to
  whole windows and at most 24 hours; New Relic restarts the count when a window has no data, so it
  may differ. Reduce mode `dropNN` is noted; `replaceNN` and unknown modes stay manual.
- **Reading problems:** a rule with a malformed field (a duration that is not like `5m`, a query
  node that is not an object) is kept as a manual item with a fixed reason and the other rules
  load. `isPaused` is read as a boolean ("true" and "false" are accepted); any other value is
  treated as paused and noted. Identical copies of a rule are merged; two rules with one uid and
  different content both stay, with distinct keys and a note. A file nested too deeply is refused
  with a message.
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

### Dynatrace classic (metric events and Davis detectors)

`importer extract-dynatrace-classic --out <folder>` reads Settings 2.0 objects from a Dynatrace
tenant (read-only GET requests for a fixed list of schema ids; `DYNATRACE_ENV_URL` and
`DYNATRACE_API_TOKEN` in `.env`), and `--source dynatrace-classic` converts them. A Platform host
(`https://<tenant>.apps.dynatrace.com`, bearer token) is read through the classic Environment API
proxy first and through the Platform settings service when the proxy answers 404 for a schema; a
classic SaaS host (`https://<tenant>.live.dynatrace.com`, API token) uses `/api/v2/settings`.
Nothing here has been run against a tenant that holds these objects: the converter was built from
the schema tables in the Dynatrace documentation (21 docs-derived fixtures) and hand-made objects.
Every row of the metric map is unverified. Notification objects are reduced at read time (type,
name, enabled flag, profile id), so no secret, webhook URL or recipient is exported.

- **Converts (approximate):**
  - A metric event (`builtin:anomaly-detection.metric-events`) with a static threshold, on a metric
    key or on a selector, whose metric is in the metric map. `violatingSamples` out of `samples`
    one-minute samples becomes a breach that lasts `violatingSamples` minutes in every window;
    when `violatingSamples` is below `samples` this is stricter than Dynatrace and a note says so.
    `queryOffset` moves the aggregation delay only (`max(120 s, offset)`); an offset over 20 minutes
    is manual because New Relic's aggregation delay is at most 1200 s. `alertOnNoData` becomes loss of signal.
    `dealertingSamples` has no equivalent and is noted. A disabled event is converted (all
    conditions are created disabled) with a note.
  - `AUTO_ADAPTIVE_THRESHOLD` and `SEASONAL_BASELINE` become a baseline condition in the direction
    of `alertCondition`. The sensitivity (`signalFluctuation`, `tolerance`) has no principled
    conversion: the band is fixed at 3 standard deviations and a note says to tune it.
  - A selector with one or more metrics, `:avg :min :max :sum :count :median :percentile(n)
    :value :last`, `:splitBy` (FACET), `:filter` with `eq ne prefix suffix contains existsKey and
    not` (WHERE) and `or` only as direct `eq` tests on one dimension (an IN list), `+ - * /` between metrics of the same New Relic event, `:rate(1m)` (as a
    sum), and `:default`, `:sort`, `:limit`, `:names`, `:parents`, `:setUnit`, `:smooth` (noted,
    not applied). The parser is strict and linear-time; anything else makes the event manual.
  - Dimension and entity-name filters, when the dimension is in the metric map for the metric and
    the value is a name. A value that looks like a Dynatrace entity id (`HOST-0123456789ABCDEF`) is
    manual: New Relic has names, so the id must be resolved first. Splitting by an entity dimension
    works, and the facet labels change from ids to names (a note says so). A threshold on a metric
    with a unit conversion survives `*` or `/` by a constant only; `+` and `-` are manual.
  - Event types that open a problem (`ERROR`, `AVAILABILITY`, `SLOWDOWN`, `RESOURCE`,
    `CUSTOM_ALERT`). `INFO`, `CUSTOM_ANNOTATION`, `CUSTOM_DEPLOYMENT` and `CUSTOM_CONFIGURATION`
    open no problem and are manual. `davisMerge` is ignored and noted.
  - A Davis detector (`builtin:davis.anomaly-detectors`) with a static threshold over
    `timeseries avg|min|max|sum(metric)` and an optional `by:{...}`.
- **Data prerequisite:** a converted condition watches a New Relic signal that exists only when an
  agent, integration or exporter sends it. Each conversion says which one (the infrastructure
  agent for host metrics, an APM agent for service metrics, a cloud integration, or the same
  metric through OpenTelemetry). A metric with no row in the map, or a row with status `none`, is
  manual and gives that message.
- **Manual, with steps:** a management zone, tag, entity-id or regular-expression filter; a
  metric with no map row; `:fold`, `:delta`, `:rollup`, `:timeshift`, `:partition`, `:histogram`,
  `:merge`, `:toUnit`, an entity selector or a series filter; several metrics in one selector;
  baseline and records-mode detectors; any other DQL; `OUTSIDE` on a static threshold.
- **Listed, not converted:** alerting profiles, problem notifications, maintenance windows,
  management zones and the built-in anomaly detection settings are manual items, one per object,
  with the steps for each. Their conversion (workflows, destinations, muting rules) comes later.
- **A metric map of your own:** the shipped map has seven rows, all unverified. Write a JSON file
  with the same layout, check it with `importer validate-dynatrace-map FILE`, and name it in
  `IMPORTER_DYNATRACE_METRIC_MAP` when you convert.
- **The rate:** `importer summarize-dynatrace-classic --alerts <out>/alerts` prints the conversion
  rate and, on its own line, how many rows only list an alerting profile, notification, maintenance
  window, management zone or built-in setting. Those rows count as not converted; the line shows
  the rate without them.
- **Checks:** `importer check-dynatrace-metrics --alerts <out>/alerts --nr-account-id <id>` asks
  New Relic (read-only count queries, with the condition's own filters) whether the signal behind
  each converted condition has data.
  Exit 2 means at least one has none, so the condition could never fire.
- **Sequence:** `importer extract-dynatrace-classic --out out/dynatrace-classic`, then `importer
  convert --source dynatrace-classic --in out/dynatrace-classic --out out/dynatrace-classic/alerts`,
  then read `out/dynatrace-classic/alerts/report.md`. A duration limit is UNVERIFIED: the New Relic
  pages disagree (2 hours for an alert event, 24 hours in the emitter), and a Dynatrace window of
  60 samples is 1 hour, inside both. The HTML report (`importer report`) has no
  profile for this source yet.

Azure and other platforms are planned; see the [roadmap](../README.md#roadmap).
