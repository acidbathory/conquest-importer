"""Playbooks for Grafana alert rules that did not become a New Relic condition."""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L

LATENCY = (
    "Measured on a converted rule (60 s window, 2 minute duration): the incident opened 5 min "
    "12 s after the first breaching sample and closed 4 min 8 s after recovery; Grafana with a "
    "1 minute evaluation would open after about 2 to 3 minutes. New Relic evaluates separate "
    "windows, so a window that is mostly baseline can still open (79 s of high data, 221 s of "
    "low, value 1.6 above the 1.5 threshold)."
)
CONDITION_RULES = (
    "An alert condition query is one NRQL query with one aggregated value per window. It "
    "cannot use subqueries, joins, COMPARE WITH, LIMIT or lookup tables; nested aggregation "
    "works only with a faceted inner query and the same window size."
)


def reg(titles: tuple[str, ...] | str, pb: Playbook) -> None:
    register_playbook(titles, pb)


STEP_CREATE = (
    "Create the condition: Alerts > Alert conditions > + New alert condition > Write your own "
    "query, paste the NRQL, set the threshold and duration. Create it disabled, compare it "
    "with the Grafana panel for a day, then enable it."
)

reg(
    (
        "PromQL counter rate without sum",
        "PromQL counter rate aggregation",
        "PromQL counter rate reducer",
        "PromQL counter range",
        "PromQL rate or over-time function",
        "PromQL counter metric",
        "PromQL counter name",
    ),
    Playbook(
        source="rate() or increase() of a counter, tested per series or reduced over a "
        "range, against a threshold.",
        newrelic="An NRQL condition on `rate(sum(<metric>), 1 minute)` (or `sum()` over the "
        "window for an increase) with a FACET when each series must be tested alone.",
        steps=(
            "Check how the counter is stored: `FROM Metric SELECT keyset() WHERE "
            "metricName = '<metric>'` lists its attributes; a counter arrives as the "
            "change per interval (a `count`). If it is a gauge or the name does not end in "
            "_total, _count or _bucket, relabel it at remote write with "
            "`newrelic_metric_type=counter`.",
            "Per-series test (Grafana default): `FACET <labels>`; the condition then opens one "
            "incident per series. Total across series: no FACET.",
            "The Grafana range ([5m]) becomes the aggregation window; round it to whole "
            "minutes (60 s or more).",
            "Scale the threshold to the unit: per second or per window.",
            STEP_CREATE,
        ),
        differs="A Grafana range slides at every evaluation; a New Relic window is separate "
        "and aligned to the clock. " + LATENCY,
        measured="Rate rule 'sum(x) / 300' matched on delta counts (28 points per window, "
        "1.12 to 2.8 as expected); bucket size that is not a multiple of the scrape interval "
        "makes a rate swing (5 s buckets on 10 s data: 0 and 2.4 around 1.2).",
        effort_minutes=30,
        template="SELECT rate(sum(<metric>), 1 minute) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.STREAMING, L.REMOTE_WRITE, L.TF_CONDITION),
    ),
)
reg(
    (
        "PromQL equality test on a bare selector",
        "PromQL comparison inside the query",
        "Grafana rule that cannot fire",
        "PromQL operator or comparison",
    ),
    Playbook(
        source="The query filters with a comparison (`up == 0`) and the rule then applies "
        "its own threshold to what is left.",
        newrelic="Put the comparison into the condition threshold: query the plain value "
        "and set the operator and number.",
        steps=(
            "Query the metric without the comparison: `SELECT min(<metric>) FROM Metric "
            "FACET <labels>`.",
            "Set the threshold to the comparison: `up == 0` is critical when below 1 for the "
            "duration.",
            "If Grafana's rule can never fire (the filter keeps value 0 and the threshold "
            "asks for something else), decide what it was meant to detect before you "
            "create anything.",
            STEP_CREATE,
        ),
        differs="One incident per facet instead of one per series that matches. " + LATENCY,
        effort_minutes=15,
        template="SELECT min(<metric>) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.TF_CONDITION),
    ),
)
reg(
    ("PromQL ratio not converted", "Grafana math expression"),
    Playbook(
        source="A ratio or calculation of two metrics (error rate = errors / requests), "
        "written as PromQL division or as a math expression over two queries.",
        newrelic="One NRQL query with both aggregates and the division; use filter() for a "
        "subset of one metric. The result is one value per window.",
        steps=(
            "Both metrics must be in the `Metric` event (check "
            "`FROM Metric SELECT uniques(metricName) WHERE metricName IN ('<metric>')`).",
            "Write the division in one SELECT with `filter(sum(<metric>), WHERE <label> LIKE "
            "'5%') / sum(<metric>) * 100`.",
            "Set the threshold on the percentage. A zero denominator gives no data, so set "
            "gap filling to 'static 0' if no traffic should not alert.",
            STEP_CREATE,
        ),
        differs="Subqueries and joins are not available in conditions (documented), so the "
        "two metrics must be in the same event. " + LATENCY,
        effort_minutes=30,
        template="SELECT filter(sum(<metric>), WHERE <label> LIKE '5%') / sum(<metric>) * 100 "
        "FROM Metric",
        links=(L.CONDITION, L.SUBQUERY, L.NRQL, L.TF_CONDITION),
    ),
)
reg(
    ("PromQL absent() threshold", "PromQL absent()"),
    Playbook(
        source="absent(metric) returns 1 when the series is missing; the rule fires on that.",
        newrelic="Loss of signal on a condition over the metric (expiration time = how long "
        "it may be silent), or a count below 1.",
        steps=(
            "Create a condition `SELECT count(*) FROM Metric WHERE metricName = '<metric>' "
            "AND <label> = '<value>'`.",
            "Turn on signal loss: open a violation after the silence you want (for example 5 "
            "minutes). The loss of signal setting is in the condition's advanced signal "
            "settings.",
            "Alternatively alert when `count(*)` is below 1 with gap filling 'static 0'.",
            STEP_CREATE,
        ),
        differs="Signal loss counts silence from when the last data point arrived; "
        "Grafana looks at the query result at each evaluation. Signal loss is not supported "
        "for nested queries (documented).",
        effort_minutes=20,
        template="SELECT count(*) FROM Metric WHERE metricName = '<metric>'",
        links=(L.STREAMING, L.CONDITION, L.TF_CONDITION),
    ),
)
reg(
    (
        "PromQL aggregation across series",
        "Grafana reduce mode",
        "LogQL range query reducer",
        "Grafana reducer not converted",
        "Grafana query time range",
    ),
    Playbook(
        source="The rule first combines series (sum/avg across series) and then reduces over "
        "time (last, mean, max, count).",
        newrelic="One aggregate per window; the aggregation window is the lookback. The "
        "reducer maps to the aggregate: last = latest(), mean = average(), max = max(), "
        "median = percentile(attr, 50), count = count(*).",
        steps=(
            "Pick the reducer's meaning: the highest value over 5 minutes is `max()` with a 5 "
            "minute window.",
            "A total across series at one moment: `sum()` over a window of one scrape "
            "interval (a longer window adds samples; see the gauge measurement).",
            "Set the window (60 s to 1 h) to the Grafana lookback.",
            STEP_CREATE,
        ),
        differs="Grafana evaluates a sliding lookback; New Relic uses separate windows. Sliding "
        "windows exist as an option (`SLIDE BY`). " + LATENCY,
        measured="sum() of a gauge was 4x per 60 s bucket and 41x over 10 minutes on real "
        "node_exporter data.",
        effort_minutes=20,
        template="SELECT max(<metric>) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.STREAMING, L.NRQL),
    ),
)
reg(
    (
        "Grafana recording rule",
        "Data source-managed rule",
        "Rule-file rule without a comparison",
        "Grafana rule not readable",
        "Grafana rule file group not readable",
    ),
    Playbook(
        source="A recording rule saves a query result as a new metric; a ruler-managed rule "
        "runs in Mimir or Loki, outside Grafana.",
        newrelic="Recording rules do not alert; the closest New Relic thing is an NRQL "
        "query inside the condition that needs it, or an events-to-metrics rule for a "
        "derived metric that many consumers read.",
        steps=(
            "List the alert rules and dashboards that read the recorded metric.",
            "Write the recording rule's expression into each condition or widget as NRQL. For "
            "a metric many things read, keep computing it upstream (Prometheus, Alloy) and "
            "send it to New Relic like any other metric.",
            "Ruler-managed alert rule: rebuild it as an NRQL condition on the same data once "
            "that data is in New Relic.",
            "Rule file errors: fix the group (it needs a name) and export again; JSON only "
            "(`yq -o=json rules.yml`).",
        ),
        differs="No pre-computed series in New Relic unless you send one.",
        effort_minutes=30,
        no_equivalent=True,
        links=(L.CONDITION, L.REMOTE_WRITE, L.NRQL),
    ),
)
reg(
    (
        "Grafana pipeline not converted",
        "Grafana classic condition",
        "Grafana SQL expression",
        "Grafana resample expression",
        "Grafana rule with several queries",
    ),
    Playbook(
        source="The rule is a chain of query, reduce, math, resample, classic condition or "
        "SQL nodes, or reads several queries or data sources.",
        newrelic="One NRQL condition per test. AND/OR between tests: one condition each, joined "
        "by a compound alert or by one workflow.",
        steps=(
            "Open the rule in Grafana; write down what the final condition node compares.",
            "Classic condition with AND: one New Relic condition per test; both open incidents "
            "must be visible to the on-call (same policy, same workflow).",
            "Resample: set the aggregation window of the condition to the resample interval.",
            "Several data sources: bring the data into New Relic, write one query, or one "
            "condition per query.",
            "SQL expression: no equal; rewrite the logic as NRQL.",
            STEP_CREATE,
        ),
        differs="Tests are evaluated separately; an AND across two incidents is not one "
        "alert. " + CONDITION_RULES,
        effort_minutes=45,
        links=(L.CONDITION, L.WORKFLOWS, L.NRQL),
    ),
)
reg(
    ("Grafana range threshold", "Grafana threshold not converted"),
    Playbook(
        source="A threshold of within a range or outside a range, or an operator or value "
        "the importer does not read.",
        newrelic="Two conditions on the same query (one per bound), or one condition with a "
        "critical and a warning threshold.",
        steps=(
            "Below the lower bound: condition with 'below' and the lower number.",
            "Above the upper bound: a second condition with 'above' and the upper number.",
            "Within a range: use a nested NRQL `if(...)` returning 1 inside the range and "
            "alert above 0.",
            STEP_CREATE,
        ),
        differs="Two incidents instead of one for an 'outside' rule.",
        effort_minutes=15,
        template="SELECT average(<metric>) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.TF_CONDITION),
    ),
)
reg(
    ("Data source not supported", "Grafana data source without a New Relic data path"),
    Playbook(
        source="The rule queries a data source other than Prometheus or Loki (CloudWatch, "
        "Azure Monitor, SQL, Elasticsearch, Grafana's own usage metrics).",
        newrelic="Depends on the data path: cloud integrations and Metric Streams carry "
        "CloudWatch, Azure and Google metrics; there is nothing for SQL or Elasticsearch "
        "queries.",
        steps=(
            "CloudWatch: convert the alarm itself with `convert --source cloudwatch`, or use "
            "the cloud converters' metric names.",
            "Otherwise find the signal in New Relic data (agents, OpenTelemetry, logs) and "
            "write the NRQL condition.",
            "If no data path exists, keep that alert in Grafana and say so in the migration plan.",
        ),
        differs="Different metric names, units and delay (polling or streaming).",
        effort_minutes=60,
        no_equivalent=True,
        links=(L.CONDITION, L.OTLP),
    ),
)
reg(
    ("PromQL histogram_quantile",),
    Playbook(
        source="histogram_quantile(0.95, rate(x_bucket[5m])) against a latency threshold.",
        newrelic="`bucketPercentile(<metric>_bucket, 95)` in a query; whether the condition "
        "editor accepts it in your account was not verified. Fallback: the share of requests "
        "slower than the limit, as a ratio.",
        steps=(
            "Try the query in the condition editor: `SELECT bucketPercentile(<metric>_bucket, "
            "95) FROM Metric`. If it is refused, use the fallback.",
            "Fallback: `SELECT filter(sum(<metric>_bucket), WHERE le = '+Inf') - "
            "filter(sum(<metric>_bucket), WHERE le = '0.5')` over `filter(sum(...), WHERE le = "
            "'+Inf')` as a slow-request ratio, and alert above the allowed share.",
            STEP_CREATE,
        ),
        differs="A share-slower-than-limit alert is not the same number as a p95; pick the "
        "bound from the Grafana threshold. UNVERIFIED: bucketPercentile in alert conditions.",
        effort_minutes=45,
        template="SELECT bucketPercentile(<metric>, 95) FROM Metric",
        links=(L.CONDITION, L.PROMQL_SUPPORTED),
    ),
)
reg(
    ("PromQL feature with no alert-condition equivalent",),
    Playbook(
        source="offset, a subquery, vector matching, topk/bottomk or without() inside an "
        "alert expression.",
        newrelic="No equivalent inside a condition (no subqueries, joins, COMPARE WITH or "
        "LIMIT). Workaround: simpler conditions, an anomaly (baseline) condition for "
        "'worse than yesterday', or a derived metric computed upstream.",
        steps=(
            "offset (compare with the past): use an anomaly condition (it learns the baseline) "
            "or alert on a fixed threshold taken from the history.",
            "topk: a FACET with one condition on the facets that matter, or one condition per "
            "top item.",
            "Vector matching / joins: put both metrics on the same event, or compute the "
            "joined metric upstream (a recording rule in Prometheus) and send it.",
            "without(): list the labels to keep in FACET.",
        ),
        differs=CONDITION_RULES,
        effort_minutes=60,
        no_equivalent=True,
        links=(L.CONDITION, L.ANOMALY, L.SUBQUERY),
    ),
)
reg(
    "PromQL label matcher not converted",
    Playbook(
        source="A label matcher with a general regular expression.",
        newrelic="`WHERE attr RLIKE '<regex>'`, `LIKE`, or `IN (...)` in the condition query.",
        steps=(
            "Write the filter in the NRQL: `WHERE <label> RLIKE '<regex>'`; test the regex on "
            "the values returned by `uniques(<label>)`.",
            "A matcher that also matches a missing label needs `OR <label> IS NULL`.",
        ),
        differs="RLIKE uses RE2-style regex, not the Prometheus one in detail.",
        effort_minutes=10,
        template="SELECT average(<metric>) FROM Metric WHERE <label> RLIKE '<regex>' FACET "
        "<labels>",
        links=(L.NRQL, L.CONDITION),
    ),
)
reg(
    ("PromQL name not valid in NRQL", "PromQL not converted"),
    Playbook(
        source="A PromQL expression the converter does not read, or a name with characters "
        "NRQL does not accept (colons from recording rules).",
        newrelic="An NRQL condition written by hand on the metric as it is stored.",
        steps=(
            "Find the stored name: `FROM Metric SELECT uniques(metricName) WHERE metricName "
            "LIKE '%<part>%'`.",
            "Write the condition: `SELECT <aggregate>(<metric>) FROM Metric WHERE <label> = "
            "'<value>' FACET <labels>`.",
            "Recording rule names with colons: send the underlying metrics and write the "
            "rule's expression, or relabel the recorded metric to a plain name.",
            STEP_CREATE,
        ),
        differs="See the other notes on latency and windows. " + LATENCY,
        effort_minutes=30,
        template="SELECT average(<metric>) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.NRQL, L.REMOTE_WRITE),
    ),
)
reg(
    (
        "LogQL parser stage or unwrap",
        "LogQL function not converted",
        "LogQL regular expression line filter",
        "LogQL filter text with a wildcard character",
        "LogQL label matcher not converted",
        "LogQL not converted",
    ),
    Playbook(
        source="A LogQL alert: count or rate of log lines, with parser stages (json, logfmt, "
        "pattern), unwrap, regex filters or labels.",
        newrelic="An NRQL condition on the `Log` event; parse fields once with a log parsing "
        "rule so they are attributes the condition can filter and aggregate.",
        steps=(
            "Forward the logs to New Relic and find the attribute names "
            "(`FROM Log SELECT keyset()`).",
            "Create a parsing rule (Logs > Parsing) that extracts the fields the pipeline "
            "extracted.",
            "Write: `SELECT count(*) FROM Log WHERE message LIKE '%<text>%' AND <label> = "
            "'<value>'`; for unwrap use `SELECT average(<attr>)`.",
            "Regex line filter: `message RLIKE '<regex>'`.",
            STEP_CREATE,
        ),
        differs="Parsing applies to new logs only. Loki labels are New Relic attributes only "
        "if the forwarder sends them (names unconfirmed).",
        effort_minutes=40,
        template="SELECT count(*) FROM Log WHERE message LIKE '%<text>%'",
        links=(L.PARSING, L.CONDITION, L.NRQL),
    ),
)
reg(
    ("Grafana window too long", "Grafana pending period too long"),
    Playbook(
        source="A lookback window over one hour or a pending period over 24 hours.",
        newrelic="The longest window is one hour and the longest threshold duration 24 hours.",
        steps=(
            "Use the 1 hour window with a threshold scaled to your longer window, or a "
            "longer duration made of repeated windows.",
            "For a daily or weekly comparison, use an anomaly condition (seasonality).",
        ),
        differs="A 6 hour average is not an hourly one; check the number against history.",
        effort_minutes=20,
        links=(L.CONDITION, L.ALERT_LIMITS, L.ANOMALY),
    ),
)
reg(
    ("Grafana no-data and error states", "Grafana keep firing or recovery threshold"),
    Playbook(
        source="No-data and error states raise their own alert; keep-firing and a recovery "
        "threshold hold an alert open.",
        newrelic="Loss of signal on the condition for no data; gap filling for missing "
        "points; a longer threshold duration or recovery options against flapping. A failing "
        "query has no equivalent.",
        steps=(
            "No data should alert: enable loss of signal with the silence you want.",
            "No data should be ignored: leave loss of signal off, or fill gaps with the last "
            "value.",
            "Flapping: lengthen the threshold duration.",
            "Watch the data pipeline itself (an alert on the ingest of the metric) instead of "
            "query errors.",
        ),
        differs="With no expiration set, New Relic never opens an incident when data stops "
        "(tested); whether an open incident then stays open was not tested.",
        effort_minutes=15,
        links=(L.STREAMING, L.CONDITION),
    ),
)
