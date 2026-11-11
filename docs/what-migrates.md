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
| SMS, mobile push | No New Relic equivalent | Use email, webhook, Slack or PagerDuty |
| Lambda, SQS, Firehose | Reached through a New Relic EventBridge destination, which needs steps in AWS | The plan lists them as a manual EventBridge destination: associate the partner event source with an event bus and add a rule that targets the function, queue or stream |
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

## Grafana dashboards

No dashboard is built in New Relic yet. Two commands read Grafana dashboards and say how much of
them could convert; they are offline apart from the read, and nothing is created anywhere. The
reading and the plan are described here; the conversion comes next.

- **Read:** `importer extract-grafana-dashboards --out <folder>` reads dashboards, folders and
  library panels from a Grafana stack with `GET` calls on four fixed paths (`/api/search`,
  `/api/dashboards/uid/{uid}`, `/api/folders`, `/api/library-elements`). It uses `GRAFANA_URL` and
  `GRAFANA_SA_TOKEN` under the same host rule as `extract-grafana`; the token needs to read
  dashboards, folders and library panels, which the alert-only token may not. The `/apis`
  dashboard API is not used. These paths have not been run against a live stack yet.
- **What is written:** `dashboards/dashboard-<hash>.json` (one per dashboard; the exact uid is
  recorded inside), `folders.json` and `library-panels.json`, in a new folder (mode 0700, files
  0600) that appears only when the run finishes; a stopped run leaves nothing. The files are
  dashboard JSON with credentials removed first: values under keys such as `password`,
  `secureJsonData`, `token`, `authorization`, `headers`, `basicAuth*`, URL user info, `?token=`
  style query values, bearer values and token-shaped words. The number removed is printed. This is
  a safety net: a secret typed into a title or description is only caught if it looks like one of
  those patterns, so review the files before sharing.
- **Shapes read:** the classic JSON, the same object under `spec` of a v1 resource, and the v2
  resource (`elements` with a Grid, Rows, Tabs or AutoGrid layout), as a file, a list, an API
  response or a folder. Old schemas are read as stored (`rows` with `span`, string data source
  names, old `hide` values). A dashboard that cannot be read is listed with a fixed reason and the
  others load. Limits, which cut the input and say so rather than stop the run: 10,000 panels per
  dashboard, 200 nested levels, 5,000 dashboards and 10,000 files (the first ones are kept). A
  dashboard whose uid repeats an earlier one is left out once and noted; plan files written by
  `plan-grafana-dashboards` are skipped. A UTF-8 byte order mark is accepted.
- **Plan:** `importer plan-grafana-dashboards --in <folder> --out <folder>` writes `report.md` and
  `plan.json`. Every panel is placed at a tier, or stays manual with one specific reason:
  - T1: a Prometheus selector with matchers and an optional `avg`, `sum`, `min` or `max` (what the
    alert converter reads); a LogQL count. A regex matcher is T1 only in the forms the alert
    converter accepts (plain words, a prefix or suffix, alternatives of plain words). Any other
    regex (`.*`, `.+`, `(?i)`, anchors, `a|`) needs `RLIKE`, which is not verified in New Relic, so
    the query stays manual.
  - T2: also `rate`, `irate`, `increase`, `histogram_quantile` over `_bucket` series, `topk`, and
    LogQL `rate`.
  - T3: also arithmetic between those terms and numbers, and `count`, when every term is grouped
    by the same labels. A variable as a group-by label stays manual.
  - A panel is never placed above what it depends on. It is manual if it uses a variable that
    stays manual, a time override (`timeShift`, `timeFrom`, which act like `offset`), or a
    transformation. A stat or table with value mappings, field overrides, a unit that rescales the
    value, or a reducer other than the latest value is placed one tier lower, and manual if it was
    at T3. Instant queries, a table format, a query interval and legend text around labels are
    noted on the panel.
  - Panels of type gauge, bar gauge, bar chart, heatmap, histogram and logs have a New Relic widget
    that the importer cannot create yet; they are counted separately ("with extra widget types").
    State timeline, status history, alert list, geomap and the other types in the report have no
    widget at all.
- **Also reported:** panel types and data sources; variables (convert, drop, manual); variables
  used but not defined and defined but not used; annotations (dropped), links, rows, repeats.
- **Manual, with steps:** transformations, library panels missing from the export, other data
  sources (CloudWatch, SQL, Elasticsearch, InfluxDB, Azure Monitor and so on), server-side
  expressions, comparisons, vector matching, `offset`, subqueries, `*_over_time`, `label_replace`,
  nested aggregation, raw counters, LogQL parser stages, and variables that are ad hoc, nested or
  use a regex with lookahead.
- **What the tiers do not say:** whether the metric exists in New Relic, whether names match, or
  whether the chart looks the same. The query classification reuses the alert translator's own
  PromQL and LogQL handlers and will be replaced by the real conversion.
- **Sequence:** `importer extract-grafana-dashboards --out out/gd`, then `importer
  plan-grafana-dashboards --in out/gd --out out/gd-plan`.

## Grafana dashboard conversion

`importer convert-grafana-dashboards --in <folder> --out <folder>` (or `importer
convert-dashboards --source grafana`) turns the folder written by `extract-grafana-dashboards`
into New Relic dashboards: the IR, NerdGraph payloads, `dashboards.tf` and `report.md`. It is
offline. The dashboards are created **private** by the existing `apply-dashboards`. None of this
has been run against a New Relic account; the output names (widget options, units, variable
fields) are unverified until one dashboard is read back in a sandbox.

- **Data first.** Converted queries use the Prometheus metric name as it is, because Prometheus
  remote write keeps it. The data has to reach New Relic by remote write, OTLP or Alloy under
  those names, and the labels have to arrive as attributes. Every dashboard in the report starts
  with this note; the closing section of `report.md` lists the metric names used. If a metric
  arrives under another name or on another event, give `--name-map` a JSON file
  (`{"metrics": {"node_load1": {"event": "Metric", "attribute": "node.load.1m"}}}`, or set
  `IMPORTER_GRAFANA_NAME_MAP`); it is read as strictly as the Dynatrace metric map. Run the data
  check before trusting a widget.
- **What becomes a widget.** Exactly the panels that the plan (`plan-grafana-dashboards`) puts at
  T1 to T3, plus gauge, bar gauge, bar chart and logs panels whose queries convert (the plan
  counts these separately because the IR could not create them). Time series become line charts
  (area when stacked), stat and gauge become billboards, bar gauge and bar chart become bar
  charts, pie charts become pie, tables become tables, a logs panel becomes a table of the newest
  200 lines, and a text panel becomes markdown (html is reduced to text).
- **Queries.** A selector with `avg`, `sum`, `min`, `max` becomes `average`, `sum`, `min`, `max`
  with `FACET` for the `by` labels (`LIMIT MAX`, at most 5 labels). `rate`, `irate` become
  `rate(sum(x), 1 second)` with `TIMESERIES AUTO` (only for a counter: a name ending `_total`, `_count`,
  `_sum` or `_bucket`, or a `types` hint of `counter`; any other name is refused, as in an alert, because New
  Relic stores it as a gauge holding the running total); `increase` over a literal window becomes
  `rate(sum(x), <window>)`, over `$__range` or a variable window `sum(x)`. `histogram_quantile`
  becomes `bucketPercentile(x_bucket, <100 q>)`. `topk(n, ...)` becomes `FACET ... LIMIT n`.
  Arithmetic between terms and numbers is written as one `SELECT` with `filter()` around a term
  that has an extra filter; `count()` counts samples, not series. LogQL `count_over_time` is
  `count(*) FROM Log`, `rate` is `rate(count(*), 1 second)`, line filters become `LIKE` on
  `message`. A regex matcher is written only in the forms the plan accepts (`IN`, `LIKE`).
  `$__interval`, `$__rate_interval` and `$interval` mean the automatic bucket; a fixed
  per-query `interval` such as `1m` is kept as the bucket.
- **Series names.** Static legend text becomes `AS 'text'`; a single `{{label}}` becomes a `FACET`
  on that label; text around labels keeps the facet and loses the text, with a note.
- **Options.** `unit` to the New Relic unit (a short table: percent, bytes, bytes per second,
  bits, ms, s, requests per second, hertz; others are left out with a note), `min` and `max` to
  the axis, the legend switch, stacking, decimals (kept in the IR, not written). Thresholds map
  by colour family (green success, yellow and orange warning, red critical): a billboard takes
  one warning and one critical from-value, a line chart takes bands only when Grafana draws them.
  Thresholds that go from bad to good, percentage thresholds and other colours are left out with
  a note. Table thresholds are not written (they need a column name).
- **Variables.** `label_values(metric, label)` becomes an `nrql` variable over `uniques()`
  (a plain regex filter becomes `IN` or `LIKE`); a custom list an `enum`; a text box a `string`;
  multi-value and include-all make a multi-select variable that defaults to all; a constant is
  written into the queries; interval and data source variables are dropped. Queries use
  `IN ({{name}})`. A variable inside a metric or label name, or mixed with text in a matcher, stays
  manual.
- **Layout.** 24 columns become 12 by halving the edges; rows and tabs become full-width markdown
  headings, collapsed rows are shown open, and a section starts under the one before; widgets that
  would overlap are moved down. A widget taller than 12 is clamped by the IR and says so. A
  repeated panel is one widget with a `FACET` on the label the repeat variable filters.
- **Not migrated, but visible.** Every other panel is a markdown widget reading "Not migrated"
  with the reason and the first 100 characters of the original query (the report has the full
  reasons). Annotations, dashboard links, the original time range and refresh are listed in the
  dashboard's reasons, not carried over.
- **Approximations.** A stat shows the value over the whole time range, Grafana showed the latest
  point; the rate window becomes the bucket; `topk` ranks over the whole range; a gauge sum adds
  every sample in a bucket. Every widget is marked approximate.
- **Gauge sums and the guided flow.** `sum()` or `count()` of a gauge converts only when the series
  are known to be one. In the guided Grafana flow, give a Prometheus URL that runs on this machine (loopback only; the
  command alone also takes https with `--allow-host`) and the flow runs `grafana-prom-hints` read-only and hands the
  result to the dashboard conversion; without a URL the step is skipped and those panels stay
  manual. A series key means "one series seen in the last 7 days on that URL"; widgets stay
  approximate and say so. Run on its own: `grafana-prom-hints --prometheus-url ... --metrics-from ...`.

## Not handled at all

Forwarding logs in general (only the groups behind converted conditions get Terraform), X-Ray traces and CloudTrail. It also handles one AWS region and one
account per run. Existing New Relic objects with the same name are skipped, never updated. To apply
a fix, roll back and re-apply.

## Dynatrace dashboards and inventory

Read side and plans only. No New Relic dashboard, monitor or agent is created from any of this;
the plans say what could be, with what confidence, and what a person must do. Research:
`docs/research/dynatrace-dashboards-inventory-datapath.md`.

- **Extract:** `importer extract-dynatrace-inventory --out out/dynatrace-inventory` reads Platform
  dashboard documents, classic dashboards, synthetic monitors and locations, and an entity
  inventory, with GET requests to a fixed list of paths on
  `https://<tenant id>.apps.dynatrace.com`. What is saved is built from an allow-list, in files
  only the owner can read. Monitors keep their structure (methods, cleaned URLs, event and rule
  types, flags that say a body, header or credential was present) and never a body, a header or
  cookie value, a credential, typed text, script code or a validation pattern. Entities keep only
  their type, product names of their technologies and operating system (a value that does not look
  like a product name is dropped); no name, id or address. Platform dashboards keep the tile fields
  the planner reads, and their titles, queries and markdown are stored as written, so they can
  name internal systems: review the export before sharing it. An area that cannot be read is a
  warning and the others still run. The document content path and the classic, synthetic and
  entity endpoints on that host are UNVERIFIED (never called on a tenant). Smartscape counts are
  not read: they need a POST query.
- **Dashboard plan:** `importer plan-dynatrace-dashboards --in <folder or file> --out <folder>`
  writes `report.md` and `plan.json`. A data tile is exact only when every clause maps to NRQL
  with the same meaning and nothing is dropped: plain aggregations without options, plain fields,
  `==`, `!=`, `in()` and null checks, one plain sort key, a numeric limit, plain renames, and
  commands in an order NRQL keeps. It is a draft when something is dropped or not verified, and
  the note says what: an entity-selection clause (Smartscape id or primary tag), a second sort
  key, link columns, a variable in a limit, an average of averages, `matchesValue` or `contains`
  (wildcard and case rules differ from `LIKE`), an aggregation option such as `rate` or
  `default`, an interval, a time range in the query, a tile timeframe or segment, a select-all
  test, a sort by a series average. It is manual otherwise (join, lookup, append, expand, parse,
  dedup, `shift`, `scalar`, array arithmetic, computed fields and sort keys, a sort or filter after
  a limit, `bins`, Smartscape or Davis sources, unknown visualizations, code and SLO tiles).
  Display settings (thresholds and colours) are noted but do not change the verdict, which is
  about the query. Markdown tiles convert; spacers are dropped, and Dynatrace links, `dt-cdn.net`
  images, raw HTML and script-scheme links are flagged (a tile without a flag was not found unsafe
  by these checks, which is not proof). Each tile that reads Dynatrace-only data
  (self-monitoring, OneAgent host, service and process, Kubernetes and cloud metrics, security
  events, entities, Smartscape, Davis) is marked "no New Relic data source"; `cloud.aws.*` and
  `cloud.azure.*` metrics are not, because the research lists documented counterparts. Variables
  are classed the same way, and a default of `*` (or a UUID and `*`) is read as select-all. On the
  four ready-made Dynatrace dashboards in the fixtures it gives 59 data tiles: 5 exact, 21 draft,
  33 manual; 34 markdown tiles; 18 variables: 1 exact, 4 draft, 13 manual. The earlier, more
  optimistic reading gave 10, 22 and 27; the five tiles that moved to manual all use
  `scalar: true`. All 5 exact tiles read Dynatrace security events, which New Relic does not have.
  Exact copies of a dashboard (same content, any name or id) are read once and listed.
- **Classic dashboards:** markdown and header tiles convert; metric charts are manual until a
  metric name map exists; every other tile type and every management zone filter is manual.
- **Re-instrumentation:** `importer plan-dynatrace-reinstrumentation --in <folder> --out
  <folder>` counts entities per type and per technology and names the New Relic agent or
  integration for each, from two data files whose rows are marked DOCS or UNVERIFIED (a target
  is UNVERIFIED when any row naming it is, and a spelling that matched only after changing
  underscores or hyphens is marked). It lists
  what has no migration path (OneAgent deep monitoring, PurePath, Davis, Smartscape filters,
  management zones) and counts SLOs whose window fits a New Relic rolling window.
- **Synthetics:** `importer plan-dynatrace-synthetics --in <folder> --out <folder>` maps each
  monitor to a simple, simple browser, certificate check, step, scripted API or scripted browser
  monitor, says which could be created from the JSON alone, maps locations from a table (UNVERIFIED
  rows marked) and records availability thresholds for an alert condition.
- **Not done:** converting any dashboard, variable, monitor or agent into a New Relic object, the
  metric name map, a data check against New Relic, and any live test.

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
- **Manual, with steps:** MQL, PromQL, SQL and ratio conditions, filters with
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
  Teams (MICROSOFT_TEAMS, after the New Relic app is installed in Teams), ServiceNow
  (SERVICE_NOW_APP), Opsgenie (a webhook that needs a vendor payload template) and HTTP tasks
  become manual entries with the closest New Relic destination
  type and the steps for the secret or sign-in; anything else is manual with a reason. Entity tags
  on the trigger become issue-filter predicates (`accumulations.tag.<name>`, unverified in New Relic) and a
  disabled workflow is planned only when there is such a filter; Davis categories, a custom
  filter, the severity threshold and notify-on-close are notes. Every plan is marked as needing
  review. `apply-notifications` creates a planned workflow with its destinations only when you
  add `--include-needs-review` to `--apply`; its dry run prints how many plans need review. A plan
  with destinations but no workflow (no mappable filter) gets its destinations and channels only
  through `notifications.tf`; `apply-notifications` creates nothing for it.
  `accumulations.tag.NAME` is unverified in a live account. `matchesValue()` and `in()` may ignore case or match inside arrays in
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

- **Converts (approximate):** a rule whose pipeline is a query, an optional resample, an optional
  reduce and one threshold (above, at least, below, at most, equal, not equal), or one classic
  condition on one query (reducers avg, min, max, sum, last), on one of:
  - a Prometheus selector with an optional single `avg`, `sum`, `min` or `max` and `by (...)`:
    `avg by (instance) (cpu{job="node",env!="dev"})` becomes `SELECT average(cpu) FROM Metric
    WHERE job = 'node' AND env != 'dev' FACET instance`. Matchers `=`, `!=`, `=~` and `!~` convert
    when the regex is plain words joined by `|` (IN), or a plain word with `.*` before or after
    (LIKE). A plain selector whose name ends `_total`, `_count`, `_sum` or `_bucket` stays manual.
  - a counter in `rate()` or `increase()` over a whole number of minutes, with no aggregation or
    `sum [by (...)]`, on a name ending `_total`, `_count` or `_bucket` (what New Relic stores as a
    count): `sum by (s) (rate(x_total[5m]))` becomes `SELECT sum(x_total) / 300 FROM Metric FACET
    s` with a 300 second window, and `increase(x_total[10m])` becomes `SELECT sum(x_total)` with a
    600 second window. `rate()` itself is not documented as usable in an alert condition, so
    `sum()` and arithmetic are used. Only the last value converts, not a reducer over the rate
    series.
  - a ratio `a / b`, `a / b * 100` or `100 * a / b` of two aggregates of different metrics with
    the same matchers, grouping and range: `avg` of gauges or `sum` of counter rates, as one
    SELECT. A ratio of one metric under two different filters (an error rate) needs `filter()`
    and stays manual.
  - `absent(m{...})` and `absent_over_time(m{...}[5m])` (the Grafana threshold must fire on the
    value 1): a loss-of-signal condition that counts the rows of the metric and opens a violation
    when none arrive for the pending period (or the range). It only works once the metric has
    arrived, and it fires only when no matching series reports.
  - a Loki `count_over_time({k="v"} |= "text" [5m])`, optionally in `sum (by ...)`. It becomes a
    count of `Log` events with the labels and `message LIKE` filters. The range is the window.
- **Rule-file rules:** a rule from a Prometheus rule file or the Mimir or Loki ruler (a query
  `expr` with a comparison against a number) goes through the same handlers. The comparison is the
  threshold, the query is read as an instant query, and the note says it is a rule-file rule. The
  data source type is inferred from the query shape. A query with no comparison against a number
  is manual (no threshold), and a recording rule stays a recording rule.
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
  ignored. No-data state `Alerting` becomes loss of signal; `NoData`, `KeepLast` and `OK`, and any
  query-error state, have no equivalent and are noted. The folder, group and short plain labels
  (not secret-looking, at most 10) are listed in one note, because the IR has no field the emitters
  write for them. An annotation `runbook_url` that is a plain https address (no credentials,
  fragment or token-like parameter) becomes the condition's runbook URL; another is noted and
  dropped. The `description` or `summary` annotation is kept in the IR, but the emitters do not
  write it yet. Contact points and notification policies are not converted.
- **Manual, with steps:** longer expression chains, math across queries (the reason says how many
  queries it combines), classic conditions with more than one test, SQL expressions, mixed data
  sources or several queries, recording rules, range thresholds (`within_range`,
  `outside_range`), reducers `count` and `median`, other data source types (CloudWatch, Azure
  Monitor, Elasticsearch, SQL and so on), PromQL with `offset`, subqueries, `group_left`, `topk`,
  `histogram_quantile` (`bucketPercentile` is not documented as usable in a condition), `irate`
  and other range functions, `*_over_time`, a counter function on a name that is not a count, an
  aggregation other than `sum` over a rate, a comparison inside the query of a Grafana rule that
  also has a threshold node, other binary operators, and LogQL with parser stages, `unwrap`,
  other functions or regular-expression line filters.
- **Not done:** contact points, policies, mute timings and templates (the extractor counts them
  only), dashboards, a check that the metric exists in New Relic, and any live test.
- **Host rule:** the extractor accepts only `https://<stack>.grafana.net`. Another https host needs
  `--allow-host <host>` and must equal the host in `GRAFANA_URL`.
- **Sequence:** `importer extract-grafana --out out/grafana`, then `importer convert --source
  grafana --in out/grafana --out out/grafana/alerts`, then `importer report out/grafana --source
  grafana` (the report reads `alerts/` inside the folder you give it).
- **Behind a TLS-inspecting proxy:** set `SSL_CERT_FILE` to the corporate root certificate file.

### Grafana notifications and data path (read side and plans)

Status: the read side and pure plans exist and are tested offline. Nothing is created in New
Relic from them yet; wiring them into the notification plan is the next wave.

- **Read:** `importer extract-grafana-notifications --out <new folder>` reads contact points,
  the notification policy tree, mute timings, templates, the data source list (type and uid
  only) and the rules that Mimir or Loki rulers hold. It sends only GET requests, never with a
  query string, so contact points are never decrypted and every stored secret arrives as
  `[REDACTED]`. Secret values, header values, URL paths and queries, and template text are not
  written; email addresses are written to `contact-points.json` only. Option values are kept
  only when they have the type the schema table gives them (a boolean, an integer in range,
  one of a list); free text such as a Slack channel or a Jira project is not kept. Rule files
  keep the expression and the labels (a value that looks like an address, URL or token is
  replaced with a placeholder) and drop annotations. Files are readable by the owner only,
  and a failed run leaves no folder. Use a folder other than the one `extract-grafana` wrote.
- **Contact points:** each of the 24 integration types has a verdict. Converts: `email` (EMAIL)
  and `webhook` (WEBHOOK, with a warning that the receiver gets the New Relic payload and that
  the HTTP method, credentials, signing, TLS and custom payload are manual). Webhook template (a
  WEBHOOK destination and a payload written by hand, 12 types): oncall, prometheus-alertmanager,
  opsgenie, victorops, discord, googlechat, telegram, webex, sensugo, kafka, wecom in robot
  mode, dingding. Manual step (6): slack, pagerduty, teams, jira, pushover, sns. No equivalent
  (4, plus wecom in app mode): threema, LINE, mqtt, wechat. The destination names follow the
  New Relic reference note; none was tested on a live account.
- **Notification policies:** the tree is flattened with Alertmanager's rules (first match wins
  unless `continue`, anchored regular expressions, a missing label is the empty string, mute
  and active intervals are not inherited). A matcher on a label of the rule, `alertname` or
  `grafana_folder` is decided now, and so is one on a label the query is known not to have.
  Labels Grafana adds to every alert instance (`__alert_rule_uid__`,
  `__alert_rule_namespace_uid__`, `datasource_uid`, `ref_id`; the list is from memory and
  UNVERIFIED) are decided only when the rule carries their value, and are otherwise reported,
  never read as empty. Labels in the query's `by (...)` become tag predicates on the issue.
  A tree that was cut by a size limit or a bad route is saved as cut, and every rule is then
  reported as undecidable. Anything that cannot be
  decided (a bare selector with an unknown label set, a template in a rule label, a regular
  expression a filter cannot say, an earlier route that cannot be negated) is listed, never
  guessed. Regular expressions run only when a parser shows they are in a small safe subset;
  anything with nested repeats (even a harmless address pattern such as `(\d{1,3}\.){3}`),
  backreferences, lookaround, or forms that Go's RE2 reads differently is reported as
  undecidable. Over-rejection is intended. Group wait, group interval and repeat interval are lost.
- **Mute timings:** a time range with weekdays or days 1 to 28 of the month, a time zone, and
  years convert to muting schedules (clock times and recurrence). Negative days, days 29 to 31,
  months without a year, a run of months across the year end, a time zone that is not in the
  time zone database (`Local` included), an interval that would need more than 200 schedules,
  and a mute timing that combines days of the month with weekdays are reported. Behaviour at
  daylight saving changes is UNVERIFIED. Active time intervals are reported, not
  converted.
- **Templates:** only Grafana's default templates map to a fixed Handlebars body. A custom
  template is reported and never translated.
- **Rule files:** Prometheus, Mimir and Loki rule groups (JSON; `rules.yml` needs
  `yq -o=json` first because PyYAML is not a dependency) become rule objects the Grafana
  translator can read (annotations are dropped). An alert with one top-level comparison against a number gets the same
  query, reduce and threshold nodes as a Grafana rule; others stay data source-managed.
- **Data path:** `importer plan-grafana-datapath --region us|eu|jp --out <new folder>` writes
  a Prometheus remote write block, Alloy snippets for dual write and for OTLP, the naming
  consequences, and the NRQL a data check would run. The license key is a placeholder or the
  name of an environment variable, never a value. `importer check-grafana-data` asks New Relic
  (read-only) whether the metric or logs behind each converted condition arrive and which
  attributes they carry; it exits with code 2 when a condition would be blind.
- **Not verified on a live account:** destination behaviour, the metric names and attributes
  Prometheus data gets in New Relic, and the workflow predicate operators on list attributes.

### Grafana notification conversion

Status: `importer convert-grafana-notifications` plans New Relic destinations, workflows and
muting rules from the folder `extract-grafana-notifications` wrote. It is offline: it reads no
secret, sends nothing and creates nothing. Workflows and muting rules are planned disabled. No
payload or object was tested against a live account or a live receiving service (UNVERIFIED).

    importer extract-grafana-notifications --out out/gn
    importer convert --source grafana --in <rules file or folder> --out out/ga
    importer convert-grafana-notifications --in out/gn --alerts out/ga --rules <same rules> \
        [--destinations-map map.json] --out out/gp

- **What is written:** `notification-plans/topic-NNNN.json` (the folder layout that
  `apply-notifications --in out/gp` reads), `muting-rules/rule-NNNN.json`, `notifications.tf`
  (destinations, channels, workflows and muting rules), `secrets.tfvars.example` and `report.md`.
  Files are readable by the owner only and are never overwritten. The plans and the Terraform hold
  email addresses; the report never does.
- **Secrets:** nothing secret is read or stored. A webhook URL path, a token, a header value or a
  PagerDuty key is a name such as `GRAFANA_OPS_1A2B_URL`, and the Terraform declares a sensitive
  variable `nr_secret_<name in lower case>` for each. Supply the values in the environment
  (`export TF_VAR_nr_secret_grafana_ops_1a2b_url=...`) or in a `*.tfvars` file that stays out of
  git; `secrets.tfvars.example` lists them all. `apply-notifications` reads a destination's secret
  from the environment variable named after it in the plan (a missing variable stops the run before
  the first write; the values are never printed); see its `--help`. Whether it can create every
  kind of webhook with a secure URL is UNVERIFIED, so Terraform remains the safe route. A
  header or basic credential is one Authorization header whose whole value is the secret (the
  user name is not exported).
- **Contact points:** `email` becomes an EMAIL destination from the exported addresses (checked
  with the shared pattern, 150 at most, a note above 5 for a free account); `webhook` a WEBHOOK
  with a secure URL, the Grafana credentials as references and a small JSON payload; the twelve
  webhook-template services a WEBHOOK with a payload from `payload_templates.json` (Handlebars
  that uses only real New Relic variables and prints values only through `json` or `escape`; each
  template is rendered in the tests and must give valid JSON). Every vendor payload is marked
  unverified, and the report lists what is uncertain for each. `pagerduty` becomes a
  PAGERDUTY_SERVICE_INTEGRATION with the integration key as a reference. `slack` (OAuth),
  `teams` (app install, security code, team and channel id), `jira`, `sns` (EventBridge and the
  AWS side), `pushover`, and an email with no exported address stay manual entries with the steps
  in the plan and the report. `threema`, `LINE`, `mqtt`, `wechat` and WeCom in app mode are listed
  with the reason.
- **Routes:** each converted rule is walked through the policy tree alone, and rules with the same
  receiver, filter and mute set share a workflow. A label of the query's `by (...)` becomes an
  `accumulations.tag.<label>` predicate next to the condition names. Labels of the rule need
  `--rules` (the rules `convert` read); without it, a policy that matches on a rule label is
  reported as undecidable, never guessed. A rule with simplified routing goes straight to its
  contact point. A route splits when its condition names do not fit one 4,096 character filter
  and when resolve messages are switched off for some of its destinations (the workflow then has
  no `CLOSED` trigger). Each workflow has its own copy of the destinations, because a channel
  serves one workflow. Group wait, group interval, repeat interval and the Grafana grouping are
  lost; the report says so. `plan_violations` is run over all plans; a violation (more than 1,000
  workflows, for example) is printed and the command exits with code 2.
- **Mute timings:** the exact cases of the read side (times, weekdays, a time zone, plain days of
  the month, a run of months in named years) become muting rules for the conditions of the route
  that uses the timing (and its tag predicates), with the window end doing nothing. The
  workflows of that route use `DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES`; the other workflows
  keep `NOTIFY_ALL_ISSUES`. The first window starts on the day you run the command. Everything
  else, active time intervals included, and a timing no route uses, is reported.
- **Templates:** an empty or default title and message become the fixed Handlebars bodies
  (`{{ issueTitle }}` as the email subject and the PagerDuty summary). A custom Go template is
  reported and never translated.
- **Destinations map:** `--destinations-map FILE` fills destinations that the export cannot.
  `{"version": 1, "contact_points": {"<name or uid>": {...}}}` where each entry has exactly one of
  `email` (a list of addresses), `webhook_url` (the NAME of a secret for the URL),
  `pagerduty_key` (the NAME of a secret for the key) or `skip: true`. A URL, a key or anything
  shaped like one in place of a name is refused without being echoed, as are unknown keys,
  duplicate keys, a file over 1 MB and more than 10,000 entries.
- **Not done:** an issue priority predicate (Grafana rules convert to one critical threshold, so
  the static `severity` label is decided at conversion time instead), silences, inhibit rules,
  imported Alertmanager configurations, the Slack, Teams, Jira and EventBridge destinations
  themselves, and any live test.

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

## Azure Monitor (alert rules)

Built offline from the official Azure examples; never run against a live subscription. Details and
the measured share: [runbook-azure](user/runbook-azure.md), [baseline](research/azure-baseline.md).

| Azure | New Relic | Status |
|---|---|---|
| Metric alert, one static criterion, platform metric | NRQL condition on `Metric` (`azure.<type>.<Metric>`), event timer, disabled | Approximate; metric name UNVERIFIED |
| Metric alert with several criteria (all must be true) | None: no composite condition | Manual; each criterion shown alone |
| Dynamic threshold | None that matches (baseline model differs) | Manual |
| Availability test alert | Synthetics monitor | Manual |
| Custom, guest and Application Insights metrics | Not collected by the Azure Monitor integration | Manual |
| Scheduled query rule: table, simple `where`, count or one aggregate | Count over forwarded Azure logs | Approximate; log attributes UNVERIFIED |
| Other KQL, agent tables (Perf, Heartbeat, Container, App*) | None | Manual |
| `LogToMetric` rule, smart detector rule | None | Unsupported |
| Activity log, service health, resource health alert | Count of forwarded activity logs, once forwarded | Manual |
| Prometheus rule group alert rules | Grafana PromQL translator | Approximate where it converts; data must reach New Relic |
| Severity 0 to 4 | Critical (0, 1) and warning (2 to 4); the Azure severity is kept in the description | Approximate |
| Action groups, alert processing rules | Destinations, workflows, muting rules | Separate step, not covered here |

## Azure Monitor (workbooks and portal dashboards)

Built offline from the official Azure examples and 30 public workbook templates; never run against
a live subscription. `extract-azure --dashboards` reads them (read-only), `convert-dashboards
--source azure` converts, `plan-azure-dashboards` counts. Evidence and measured numbers:
[azure-dashboards](research/azure-dashboards.md). A placeholder ("Not migrated" with the reason) is
never counted as converted.

| Azure | New Relic | Status |
|---|---|---|
| Workbook text item | Markdown widget | Exact (approximate when it names `{parameters}`) |
| Workbook metrics item, platform metric | Chart over `Metric` (`azure.<type>.<Metric>`), line, area or table | Approximate; metric name UNVERIFIED; native data path |
| Resource picker parameter | NRQL variable `uniques(azure.resourceId)` used as `IN ({{name}})` | Approximate |
| Resource picker filled by a Resource Graph query | The chart covers every resource of the type, with a note | Approximate |
| Static dropdown parameter | Enum variable, when a converted widget uses it | Approximate |
| Tabs (link tabs, option groups) | One dashboard page per tab | Approximate |
| Group | Heading followed by its items | Approximate |
| Links item | Markdown list; link actions are text | Approximate |
| KQL query item | NRQL through the KQL translator, chart from `visualization` (timechart/linechart to line, areachart to area, barchart to bar, piechart to pie, table or none to table, tiles or stat to billboard, graph and map to table); needs log forwarding | Placeholder until the KQL translator is connected |
| KQL query with a time-range parameter | `where TimeGenerated {TimeRange}` (or `:value`) is dropped, the dashboard time picker sets the range; `bin(TimeGenerated, {TimeRange:grain})` becomes `TIMESERIES AUTO`. `:start`, `:end`, other parameters (dropdown, text, subscription, resource) keep the placeholder, named in the reason | Approximate, or Placeholder |
| Resource Graph, ARM endpoint, merge, JSON, Data Explorer query | None | Placeholder |
| Conditional visibility other than a tab | None: a widget is always shown | Placeholder |
| Dependent parameter, unknown item type, linked template | None | Placeholder |
| Portal markdown part | Markdown widget | Exact |
| Portal metrics chart part (`MetricsChartPart`, `MonitorChartPart`) | Chart over `Metric` | Approximate |
| Portal logs part | NRQL through the KQL translator | Placeholder until connected |
| Resource cards, health, maps, video, buttons | None | Placeholder |

The live `Microsoft.Portal/dashboards` API answered 502 for every version on the owner's
subscription. `extract-azure --dashboards` then reports it as "not available" and still writes the
workbooks; portal dashboards downloaded from the portal convert as files.

## Datadog (monitors)

Built offline from exported JSON; never run against a Datadog account (none exists, and the site is
blocked on the project's network). Details and the measured share: [runbook-datadog](user/runbook-datadog.md),
[baseline](research/datadog-baseline.md), [verified reference](research/datadog-verified.md).

| Datadog | New Relic | Status |
|---|---|---|
| `query alert`, plain threshold on a metric (`avg`, `sum`, `min`, `max`; scope; `by {tag}`) | Static NRQL condition on `Metric`, disabled, `FACET` for the tags | Approximate; metric and tag names assumed unchanged, UNVERIFIED |
| Arithmetic between series (a ratio), `.as_count()` on a sum | One NRQL with several selects; sum of delta counts | Approximate; ratio of window aggregates |
| `log alert` counting lines (`rollup("count")`) | Count over `Log` with the search as WHERE | Approximate; log attributes UNVERIFIED |
| Windows `last_1m` to `last_6h` | Aggregation window (60 s slide under an hour) | Approximate; longer windows manual |
| `notify_no_data`, `no_data_timeframe`, `evaluation_delay` | Loss of signal, aggregation delay | Approximate; delay above 20 minutes and no-data above 48 hours are clamped with a note |
| Priority, tags, message | Description (handles, URLs, tokens masked), tags in the IR | Priority P3 to P5 with one threshold becomes a warning term |
| Recovery thresholds, reminders, `require_full_window`, `new_group_delay`, scheduling options | None | Listed as not migrated |
| Anomaly, outlier, forecast, composite | Baseline, outlier, predictive, compound conditions | Manual now; D5 planned |
| Change alert, cumulative window, time shift | None | Manual |
| Service check, event, SLO, synthetics, RUM, trace analytics, CI, audit, database, network, cost, process | Depends on the New Relic product that replaces it | Manual |
| Unknown monitor type, unreadable monitor | None | Unsupported |
| Dashboards, downtimes, SLO objects, notification handles | Not read by this source yet | Planned (D4, D6, D7) |

Other platforms are planned; see the [roadmap](../README.md#roadmap).
