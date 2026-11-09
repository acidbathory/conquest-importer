"""Manual-step rules for Grafana findings. Registered with the shared engine on import.

The engine is first-match over one list shared by every source, so each pattern is anchored to
the start of the reason and names the Grafana wording. `tests/test_grafana_rules.py` checks that
each rule wins for its own example and that no other source's rule claims these reasons.
"""

import re

from importer.core.remediation import Rule, register_rules

NRQL_LIMITS = (
    "An alert condition query runs on New Relic data only. It cannot use COMPARE WITH, "
    "subqueries, JOIN or LIMIT, so split a rule that needs them into simpler conditions."
)

RULES: list[Rule] = [
    (
        re.compile(r"^promql counter rate without sum"),
        "PromQL counter rate without sum",
        [
            "Prometheus tests each series on its own, so one busy series fires the rule. A "
            "New Relic total over all series would hide it. Either wrap the rate in "
            "`sum by (label) (...)` in Grafana so the grouping is explicit, or write a "
            "condition with a FACET on the series label (`SELECT sum(metric) / 300 FROM "
            "Metric FACET label`) and check it against the Grafana panel.",
        ],
    ),
    (
        re.compile(r"^promql equality or inequality test"),
        "PromQL equality test on a bare selector",
        [
            "A test such as `up == 0` fires for each series that has that value. One New "
            "Relic aggregation (average, minimum or maximum) cannot tell the same. Use "
            "`min(...)` or `max(...)` with a below or above test, or write a condition with "
            "a FACET on the series label and an equals threshold.",
        ],
    ),
    (
        re.compile(r"^promql counter function on a metric"),
        "PromQL counter name",
        [
            "rate() and increase() convert only for a metric New Relic stores as a count, "
            "which it guesses from a name ending _total, _count or _bucket. For another name, "
            "look the metric up in the data explorer. If it is a counter, add the label "
            "`newrelic_metric_type` with the value `counter` in the remote write relabel "
            "configuration so it is stored as a count, then use `sum(metric)` over the range "
            "(divided by the range in seconds for a rate). If it is a gauge, rate() does not "
            "apply and the condition needs a different query.",
        ],
    ),
    (
        re.compile(r"^promql counter rate aggregated"),
        "PromQL counter rate aggregation",
        [
            "Only sum() of a rate or increase converts, because New Relic adds up the "
            "changes of all series. An average, minimum or maximum of per-series rates needs "
            "one value per series: write a condition with a FACET on the series label and "
            "check it against the Grafana panel.",
        ],
    ),
    (
        re.compile(r"^promql counter rate reduced"),
        "PromQL counter rate reducer",
        [
            "The rule reduces the rate series over time (mean, max, min or sum) instead of "
            "taking the last value, which New Relic windows do not do on top of a rate. Change "
            "the Grafana reducer to last, or write the condition by hand with a window that "
            "matches the range the reducer looked back over.",
        ],
    ),
    (
        re.compile(r"^promql counter range"),
        "PromQL counter range",
        [
            "The range of rate() or increase() becomes the aggregation window, which is a "
            "whole number of minutes (60 seconds or more). Round the range in Grafana to a "
            "whole number of minutes, or write the condition by hand with a window and a "
            "threshold scaled to your range.",
        ],
    ),
    (
        re.compile(r"^promql ratio:"),
        "PromQL ratio not converted",
        [
            "A ratio converts when both sides are the same aggregation (avg of gauges, sum "
            "of counters) of different metrics with the same labels, grouping and range. An "
            "error rate that divides one metric filtered by a code by the same metric "
            "unfiltered needs `filter()`: write `SELECT filter(sum(metric), WHERE code LIKE "
            "'5%') / sum(metric) FROM Metric` by hand (check that the conditions page "
            "accepts it in your account), or create the two counts as separate conditions.",
        ],
    ),
    (
        re.compile(r"^promql absent\(\) returns 1"),
        "PromQL absent() threshold",
        [
            "absent() returns 1 when the series is missing. The rule's threshold does not "
            "fire on 1, so the rule would not alert on absence the way the query reads. "
            "Decide what the rule should do and create a loss-of-signal condition by hand, "
            "or change the threshold in Grafana to above 0 and convert again.",
        ],
    ),
    (
        re.compile(r"^promql expression compares inside the query"),
        "PromQL comparison inside the query",
        [
            "The query filters with a comparison (such as `up == 0`) and the rule then tests "
            "the value that is left with a threshold node, so the two together do not make "
            "one New Relic threshold. Move the comparison into the threshold node: query the "
            "plain metric, and set the threshold to the number and the operator.",
        ],
    ),
    (
        re.compile(r"^promql expression `.*can never fire"),
        "Grafana rule that cannot fire",
        [
            "The query keeps only series equal to one number, and the threshold node then asks "
            "for a different value, so this rule never fires in Grafana either. It is probably "
            "a mistake. Decide what it should detect, then put that comparison in the threshold "
            "(for `up == 0`, query `up` and fire when it is below 1).",
        ],
    ),
    (
        re.compile(r"^data source-managed rule: the query has no comparison"),
        "Rule-file rule without a comparison",
        [
            "The rule fires whenever its query returns any series, and the query has no "
            "comparison with a number, so there is no threshold to carry over. Find what makes "
            "the query return nothing when things are healthy and write that as a New Relic "
            "threshold on the same metric.",
        ],
    ),
    (
        re.compile(r"^grafana rule could not be read"),
        "Grafana rule not readable",
        [
            "A field of this rule has a type or value the importer does not accept (a duration "
            "that is not like 5m, a query node that is not an object, and so on). Open the rule "
            "in Grafana, check it, and recreate the condition by hand or export the rule again.",
        ],
    ),
    (
        re.compile(r"^grafana query time range"),
        "Grafana query time range",
        [
            "The reducer looks back over the query's time range, but the rule does not give a "
            "usable range. Read the range in the Grafana rule editor, then create the New "
            "Relic condition with that aggregation window (60 seconds to one hour).",
        ],
    ),
    (
        re.compile(r"^promql aggregation"),
        "PromQL aggregation across series",
        [
            "Grafana first combines the series (sum, min, max or avg across series) and then "
            "reduces that result over time. A New Relic aggregation does one or the other, so "
            "sum() over a window would add every sample of every series. Decide which you "
            "mean. For a total across series at one moment, use a NRQL query with "
            "`sum(metric)` in a window the size of one scrape and check it against the "
            "Grafana panel. For the highest or lowest value over time, use `max()` or `min()` "
            "with the matching reducer.",
        ],
    ),
    (
        re.compile(r"^logql range query"),
        "LogQL range query reducer",
        [
            "A range query returns a count for each step, and the rule reduces those counts "
            "with min, max, mean or sum. New Relic counts one window at a time. Switch the "
            "rule's query to an instant query in Grafana, or create the condition by hand with "
            "a window and threshold that match what the reducer measured.",
        ],
    ),
    (
        re.compile(r"^grafana reduce mode"),
        "Grafana reduce mode",
        [
            "The reduce node replaces non-numeric values (for example NaN or no data) with a "
            "number, so the rule can fire or stay quiet where New Relic would see no data. "
            "Decide what an empty window should mean, then use gap filling or loss of "
            "signal on the New Relic condition to match.",
        ],
    ),
    (
        re.compile(r"^recording rule:"),
        "Grafana recording rule",
        [
            "A recording rule saves the result of a query as a new metric; it does not alert. "
            "Nothing is created for it. If alert rules read the recorded metric, send the "
            "same data to New Relic and write the aggregation into those conditions.",
        ],
    ),
    (
        re.compile(r"^data source-managed rule"),
        "Data source-managed rule",
        [
            "This rule runs in the Mimir or Loki ruler, not in Grafana, and has no pipeline to "
            "read. Rebuild it as an NRQL condition on the same data once that data is in "
            "New Relic.",
        ],
    ),
    (
        re.compile(r"^grafana pipeline(?: is not one query|: the condition)"),
        "Grafana pipeline not converted",
        [
            "Only query, optional reduce, one threshold converts. Open the rule in Grafana, "
            "find what the condition node compares, and write that as one NRQL condition. "
            "Check that the condition names a node that exists.",
        ],
    ),
    (
        re.compile(r"^grafana pipeline has a math expression"),
        "Grafana math expression",
        [
            "The rule combines queries with a math expression (a ratio or a sum, for example). "
            "In New Relic write the whole calculation in one NRQL query, for example "
            "`SELECT average(a) / average(b) * 100 FROM Metric`, then set the threshold on it. "
            "Both metrics must already be in New Relic.",
            NRQL_LIMITS,
        ],
    ),
    (
        re.compile(r"^grafana pipeline has a classic condition"),
        "Grafana classic condition",
        [
            "A classic condition joins several tests with AND or OR and discards labels. "
            "Create one New Relic condition for each test, then combine them with a compound "
            "alert condition or route them in one workflow, depending on what the AND or OR "
            "meant.",
        ],
    ),
    (
        re.compile(r"^grafana pipeline has an sql expression"),
        "Grafana SQL expression",
        [
            "An SQL expression joins or filters query results inside Grafana. Rewrite the "
            "logic as NRQL over the data in New Relic, or keep the rule in Grafana.",
        ],
    ),
    (
        re.compile(r"^grafana pipeline has a resample expression"),
        "Grafana resample expression",
        [
            "A resample expression changes the time step of a query. New Relic sets the step "
            "with the aggregation window of the condition; set that to the resample window "
            "and write the query without the resample.",
        ],
    ),
    (
        re.compile(r"^grafana pipeline (?:queries more than one|has more than one query)"),
        "Grafana rule with several queries",
        [
            "The rule reads more than one query or data source. New Relic conditions read "
            "one NRQL query on New Relic data. Move each data source's data to New Relic, "
            "write one NRQL query that combines them, or create one condition per query "
            "and decide how to combine the alerts.",
        ],
    ),
    (
        re.compile(r"^grafana threshold range"),
        "Grafana range threshold",
        [
            "within_range and outside_range test two bounds at once. In New Relic create two "
            "conditions on the same query: one for the lower bound and one for the upper "
            "bound (an alert outside a range needs one condition per side).",
        ],
    ),
    (
        re.compile(r"^grafana threshold (?:operator|value)"),
        "Grafana threshold not converted",
        [
            "The importer reads one comparison (above, at least, below, at most, equal or "
            "not equal) against one finite number. Read the threshold in Grafana and create "
            "the New Relic condition with the same bound by hand.",
        ],
    ),
    (
        re.compile(r"^grafana reduce function"),
        "Grafana reducer not converted",
        [
            "count and median have no aggregation in the converted condition. Write the "
            "NRQL yourself: `count(*)` or `percentile(attribute, 50)` over the window, and "
            "set the threshold on it.",
        ],
    ),
    (
        re.compile(r"^data source type"),
        "Data source not supported",
        [
            "Only Prometheus and Loki rules convert. For another data source, first decide "
            "how its data reaches New Relic (an integration, the agent, or an exporter), "
            "then write the NRQL condition on the data as it arrives. CloudWatch and Azure "
            "Monitor data comes in through New Relic's own cloud integrations.",
        ],
    ),
    (
        re.compile(r"^promql uses (?:rate|an _over_time)"),
        "PromQL rate or over-time function",
        [
            "New Relic stores a Prometheus counter as the change in each interval, so a "
            "per-second rate or a sum over a range is not the same query. Use NRQL such as "
            "`SELECT rate(sum(metric), 1 minute) FROM Metric` for a counter, or "
            "`sum(metric)` over the aggregation window, and set the threshold for the "
            "unit you choose. Check the result against the Grafana panel before enabling.",
        ],
    ),
    (
        re.compile(r"^promql uses absent"),
        "PromQL absent()",
        [
            "absent() fires when a series disappears. In New Relic set loss of signal on a "
            "condition over the same metric (the expiration time is how long it may be "
            "silent), or use a NRQL condition that counts rows and alerts below 1.",
        ],
    ),
    (
        re.compile(r"^promql uses histogram_quantile"),
        "PromQL histogram_quantile",
        [
            "histogram_quantile reads Prometheus `_bucket` counters. New Relic has "
            "`bucketPercentile(metric, 95)` for that data in a query, but the documentation "
            "does not list it among the functions an alert condition accepts, so it was not "
            "converted and nothing was guessed. Run the query with bucketPercentile in the "
            "query builder; if the condition editor accepts it, create the condition by "
            "hand. If not, alert on a count of requests slower than the limit (the buckets "
            "above it) divided by the total count, which is an ordinary ratio. `percentile()` "
            "is for New Relic distribution metrics, not for Prometheus buckets.",
        ],
    ),
    (
        re.compile(
            r"^promql uses (?:offset|a subquery|vector matching|topk"
            r"|without)"
        ),
        "PromQL feature with no alert-condition equivalent",
        [
            "offset, subqueries, joins (on, ignoring, group_left), topk or bottomk "
            "and without() cannot be used in a New Relic alert condition. Express the same "
            "test in NRQL on New Relic data: a FACET for grouping, and a second condition "
            "instead of an offset comparison.",
            NRQL_LIMITS,
        ],
    ),
    (
        re.compile(r"^promql uses a binary operator"),
        "PromQL operator or comparison",
        [
            "The query combines series with arithmetic or compares them. For a comparison "
            "such as `up == 0`, put the bound in the threshold instead (the threshold is "
            "the comparison). For a ratio, write both metrics into one NRQL query: "
            "`SELECT average(a) / average(b) FROM Metric`.",
        ],
    ),
    (
        re.compile(r"^promql label matcher"),
        "PromQL label matcher not converted",
        [
            "Only = and != with a string, and =~ or !~ with plain words joined by | or a "
            "prefix or suffix before .*, convert. For a general regular expression, write the "
            "NRQL condition by hand with `WHERE attribute RLIKE '...'`, or list the values "
            "with IN or LIKE.",
        ],
    ),
    (
        re.compile(r"^promql metric name looks like a counter"),
        "PromQL counter metric",
        [
            "A Prometheus counter is cumulative in Grafana but stored as the change per "
            "interval in New Relic, so a threshold written for one does not fit the other. "
            "Find the rate or increase the Grafana panel shows, then use `rate()` or `sum()` "
            "over the window in NRQL and set the threshold for that unit.",
        ],
    ),
    (
        re.compile(r"^promql metric or label name"),
        "PromQL name not valid in NRQL",
        [
            "The metric or label name has a character NRQL does not accept as an attribute "
            "name (a colon, from a recording rule, for example). Find how the name arrives in "
            "New Relic with a query in the data explorer and write the condition with that "
            "name. A label called `metricName` collides with the attribute New Relic keeps the "
            "metric name in; rename the label at the source and write the condition by hand.",
        ],
    ),
    (
        re.compile(r"^promql expression"),
        "PromQL not converted",
        [
            "Only a metric selector with one avg, sum, min or max (and by) converts. Read the "
            "query, find the metric and filters, and write the NRQL condition by hand: "
            "`SELECT average(metric) FROM Metric WHERE label = 'value' FACET label`. "
            "Send the metric to New Relic first with Prometheus remote write or OTLP.",
        ],
    ),
    (
        re.compile(r"^logql uses a parser stage"),
        "LogQL parser stage or unwrap",
        [
            "json, logfmt, pattern, label filters and unwrap extract fields from the log "
            "line at query time. In New Relic, parse the field at ingest with a log parsing "
            "rule so it becomes an attribute, then filter on that attribute in the NRQL "
            "condition. A metric taken with unwrap becomes an aggregate of that attribute.",
        ],
    ),
    (
        re.compile(r"^logql uses a function"),
        "LogQL function not converted",
        [
            "Only count_over_time converts. For a rate, bytes or quantile over logs, write "
            "NRQL on the Log event, for example `SELECT count(*) FROM Log WHERE ...` over a "
            "window, and scale the threshold to the unit you choose.",
        ],
    ),
    (
        re.compile(r"^logql uses a regular-expression"),
        "LogQL regular expression line filter",
        [
            "Only plain words convert. Use `message LIKE '%word%'` for one word, or write the "
            "NRQL condition by hand with `message RLIKE '...'` for a regular expression "
            "(check that it behaves as the LogQL one did).",
        ],
    ),
    (
        re.compile(r"^logql line filter text"),
        "LogQL filter text with a wildcard character",
        [
            "A star or percent sign in the text would be read as a wildcard by NRQL LIKE. "
            "Create the condition by hand and choose another distinctive part of the line, "
            "or filter on a parsed attribute.",
        ],
    ),
    (
        re.compile(r"^logql label matcher"),
        "LogQL label matcher not converted",
        [
            "Only = and != with a non-empty string, and =~ or !~ with plain words joined by "
            "| or a prefix or suffix before .*, convert. List the values with IN or LIKE, or "
            "split the rule into several conditions.",
        ],
    ),
    (
        re.compile(r"^logql expression"),
        "LogQL not converted",
        [
            "Only a count_over_time of a stream selector with line filters, optionally in "
            "sum (by), converts. Write the NRQL condition by hand on the Log event with the "
            "same label filters and text filters; forward the logs to New Relic first.",
        ],
    ),
    (
        re.compile(r"^grafana aggregation window"),
        "Grafana window too long",
        [
            "New Relic evaluates windows of up to one hour. Create the condition by hand "
            "with a one hour window and a threshold scaled to match, and check the result "
            "means the same thing.",
        ],
    ),
    (
        re.compile(r"^grafana pending period"),
        "Grafana pending period too long",
        [
            "The pending period is longer than the 24 hours New Relic allows as a threshold "
            "duration. Shorten it, or create the condition by hand with the longest "
            "duration and review its behaviour.",
        ],
    ),
    (
        re.compile(r"^the rule was paused in grafana"),
        "Paused Grafana rule",
        [
            "The rule was paused, so it was not evaluating. The New Relic condition is "
            "created disabled. Ask the owner whether it is still wanted before you enable it.",
        ],
    ),
    (
        re.compile(r"^grafana origin:"),
        "Grafana folder, group and labels",
        [
            "The folder, group and labels are not carried onto the New Relic condition. "
            "Notification policies in Grafana route on labels such as these, so rebuild the "
            "routing as workflow filters (see the notification conversion) and decide where "
            "the folder and group should live, for example in the policy name. Whether rule "
            "labels can be used to filter New Relic issues has not been verified.",
        ],
    ),
    (
        re.compile(r"^notifications \(contact points"),
        "Grafana notifications",
        [
            "Recreate each contact point as a New Relic destination and channel, and attach "
            "them to a workflow. Rebuild notification policies as workflow filters on issue "
            "attributes. Secrets in contact points are not read; enter them again.",
        ],
    ),
    (
        re.compile(r"^(?:keep firing for|the recovery threshold) was ignored"),
        "Grafana keep firing or recovery threshold",
        [
            "Grafana can keep an alert firing for a time, or until a lower recovery value, "
            "after the breach ends. New Relic closes a violation when the value stops "
            "breaching. To reduce flapping, use a longer threshold duration or the "
            "condition's recovery options.",
        ],
    ),
    (
        re.compile(r"^(?:on no data|no data state|when the query fails)"),
        "Grafana no-data and error states",
        [
            "Grafana raises separate alerts for no data and for query errors. In New Relic "
            "use loss of signal on the condition for no data. A failing query has no "
            "equivalent; watch the data pipeline instead.",
        ],
    ),
    (
        re.compile(r"^the metric must already reach new relic"),
        "Metric must reach New Relic",
        [
            "The condition reads the metric from New Relic. Send it with Prometheus remote "
            "write or OTLP, then run a query for the metric name in the data explorer and "
            "check the name and label attributes match what the condition uses.",
        ],
    ),
    (
        re.compile(r"^the loki logs must be forwarded to new relic"),
        "Logs must reach New Relic",
        [
            "Forward the logs to New Relic (Alloy or another forwarder) and check that the "
            "label attribute names and the message attribute match the condition's query.",
        ],
    ),
]

register_rules(RULES)
