"""Manual-step rules for CloudWatch findings. Registered with the shared engine on import."""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"log metric filter"),
        "Log metric filter",
        [
            "New Relic has no metric filters. Forward the log group to New Relic (the Firehose "
            "or the `NewRelic-log-ingestion` Lambda), then query it directly, for example "
            "`SELECT count(*) FROM Log WHERE logGroup = '<group>' AND message LIKE '%term%' "
            "TIMESERIES`.",
            "To alert on it, create an NRQL alert condition on that query. To chart it, add "
            "the query to a dashboard.",
            "If the filter pulls a number out of each line, query that field with `sum()` or "
            "`average()` instead of counting events.",
        ],
    ),
    (
        re.compile(r"eventbridge rule routes cloudwatch alarm"),
        "EventBridge rule for alarm state changes",
        [
            "Alarms reach people through workflows in New Relic. Recreate each target of the "
            "rule as a workflow destination: email or webhook for an SNS target, a webhook to "
            "an API Gateway or Lambda function URL for a Lambda target.",
            "If the rule matches particular alarm names, filter the workflow on the condition "
            "name.",
            "Systems Manager and other automation targets need the same webhook, triggered "
            "from the workflow.",
        ],
    ),
    (
        re.compile(r"eventbridge rule reacts to aws service events"),
        "EventBridge rule for AWS service events",
        [
            "To get AWS events into New Relic, create an EventBridge connection and API "
            "destination that posts the matching events to New Relic's event or log API, then "
            "query them with NRQL. New Relic's AWS documentation does not describe this route, "
            "so test it with one rule first.",
            "Keep the rule in AWS if its job is to start automation there.",
        ],
    ),
    (
        re.compile(r"synthetics canary"),
        "Synthetics canary",
        [
            "Recreate as a New Relic Synthetics monitor: a ping monitor for a URL check, a "
            "scripted API monitor for an HTTP test, or a scripted browser monitor for a "
            "browser flow. The canary script has to be adapted to New Relic's scripting API.",
        ],
    ),
    (
        re.compile(r"contributor insights rule"),
        "Contributor Insights rule",
        [
            "Rebuild as a log query that ranks contributors, for example `SELECT count(*) FROM "
            "Log WHERE logGroup = '<group>' FACET <field> LIMIT 10`. It needs the log group "
            "forwarded to New Relic.",
        ],
    ),
    (
        re.compile(r"cloudwatch rum app monitor"),
        "CloudWatch RUM app monitor",
        [
            "Add the New Relic Browser agent to the application. It records its own data, so "
            "CloudWatch RUM history does not carry over.",
        ],
    ),
    (
        re.compile(r"application signals slo"),
        "Application Signals SLO",
        [
            "Recreate as a New Relic service level (an indicator and an objective) once the "
            "service's data is in New Relic. Application Signals metrics arrive through Metric "
            "Streams.",
        ],
    ),
    (
        re.compile(r"route 53 health check on an endpoint"),
        "Route 53 health check on an endpoint",
        [
            "Recreate as a New Relic Synthetics ping monitor against the same endpoint. Keep "
            "the Route 53 check if it drives DNS failover, because New Relic cannot do that.",
        ],
    ),
    (
        re.compile(r"route 53 health check that combines"),
        "Route 53 health check built on other checks",
        [
            "This check combines other checks or follows a CloudWatch alarm. Recreate the "
            "underlying checks or alarm first. Keep it in Route 53 if it drives DNS failover.",
        ],
    ),
    (
        re.compile(r"composite"),
        "Composite alarm",
        [
            "New Relic has no composite condition. Convert each child on its own, then rebuild "
            "the combination as one NRQL condition (AND) or by routing the children to the same "
            "workflow (OR).",
        ],
    ),
    (
        re.compile(r"forecast|outlier"),
        "Forecast or outlier monitor",
        [
            "Recreate as a New Relic baseline NRQL condition for outlier-style detection. "
            "Forecast alerts have no counterpart: use a static threshold with an early margin.",
        ],
    ),
    (
        re.compile(r"scope value contains unsupported characters"),
        "Tag value with unsupported characters",
        [
            "Rename the tag value in the source, or write the condition's WHERE clause by hand "
            "with the value quoted.",
        ],
    ),
    (
        re.compile(r"no new relic equivalent for service check"),
        "Service check",
        [
            "Replace the service check with a New Relic synthetic monitor (ping or API test) "
            "for the same endpoint, and alert on its failures.",
        ],
    ),
    (
        re.compile(r"monitor type"),
        "Unsupported monitor type",
        [
            "This monitor type has no automatic conversion. Describe what it protects, then "
            "rebuild it in New Relic (SLO alerts: use Service Levels; process monitors: use "
            "infrastructure process data).",
        ],
    ),
    (
        re.compile(r"anomaly detection"),
        "Anomaly detection alarm",
        [
            "Recreate as a New Relic baseline NRQL condition on the same metric once its data "
            "is flowing (baseline conditions learn the normal range, like CloudWatch anomaly "
            "detection). Pick the direction (upper, lower or both) to match the band.",
            "Compare behaviour against the CloudWatch alarm for a few days before relying on it.",
        ],
    ),
    (
        re.compile(r"metric math|nested metric math|different (dimensions|periods|statistics)"),
        "Metric math expression",
        [
            "Write the expression by hand as one NRQL query (for example "
            "`SELECT sum(a) / sum(b) * 100 FROM Metric ...`), keeping the dimension filters.",
            "Run `importer validate` after data flows to compare it with the CloudWatch value.",
        ],
    ),
    (
        re.compile(r"more than one metric query|neither a metric nor an expression"),
        "Alarm with several queries",
        [
            "Open the alarm in CloudWatch, decide which query is the alerting signal, and rebuild "
            "that one as a single NRQL condition."
        ],
    ),
    (
        re.compile(r"high-resolution"),
        "High-resolution period",
        [
            "Metric Streams data is per minute, so sub-minute periods cannot be evaluated. "
            "Rebuild the condition with a 60 second aggregation window and accept the coarser "
            "detection time, or keep this alarm in CloudWatch.",
        ],
    ),
    (
        re.compile(r"aggregation window .* is outside"),
        "Aggregation window out of range",
        [
            "Rebuild the condition with an aggregation window inside the range quoted in the "
            "reason, choosing the value closest to the original period.",
        ],
    ),
    (
        re.compile(r"characters that are not supported"),
        "Metric or dimension name with unsupported characters",
        [
            "Rename the metric or dimension in the source if you can, or write the NRQL by hand "
            "using backticks around the attribute name.",
        ],
    ),
    (
        re.compile(r"statistic .* is not supported"),
        "Unsupported statistic",
        [
            "Pick the closest supported statistic (Sum, Average, Min, Max or a percentile "
            "configured on the metric stream) and write the NRQL by hand.",
        ],
    ),
    (
        re.compile(r"could not be created in new relic"),
        "Rejected by New Relic",
        [
            "Read the error in the reason above. It names what New Relic objected to.",
            "Fix the converted definition (the files under `alerts/nerdgraph` or "
            "`dashboards/nerdgraph`), then run the same `migrate` command again. Objects that "
            "already exist are skipped, so only this item is retried.",
            "If it keeps failing, create it by hand in New Relic and report the error as a bug.",
        ],
    ),
    (
        re.compile(r"no new relic mapping"),
        "Metric without a New Relic mapping",
        [
            "In New Relic, open the query builder and run "
            "`SELECT uniques(metricName) FROM Metric WHERE metricName LIKE 'aws.%'` to find "
            "the name your metric arrives under (the metric stream must be sending it).",
            "Create the alert condition by hand with that metric name. For a custom namespace "
            "such as `Custom/my-app`, a simpler route is publishing under a name made only of "
            "letters and digits (`Custom/myapp`), which this tool can convert.",
        ],
    ),
    (
        re.compile(r"log widget"),
        "Log widget",
        [
            "Rebuild with a NRQL query over logs (`FROM Log`). This needs log forwarding to "
            "New Relic, which this tool does not set up. Translate the Logs Insights query by "
            "hand.",
        ],
    ),
    (
        re.compile(r"alarm status widget"),
        "Alarm status widget",
        [
            "No equivalent widget is generated. Add a markdown widget linking to the New Relic "
            "alerts overview, or build a billboard from alert incident data."
        ],
    ),
    (
        re.compile(r"metric math expressions in widgets"),
        "Metric math in a widget",
        ["Write the expression by hand as NRQL in a chart widget, then compare with CloudWatch."],
    ),
    (
        re.compile(r"shorthand does not match"),
        "Metric row shorthand",
        [
            "Open the dashboard in CloudWatch, list the full metric for this row, and add the "
            "widget by hand in New Relic."
        ],
    ),
    (
        re.compile(r"widgets? not converted|no widget could be converted|no widgets"),
        "Widgets that did not convert",
        ["See the per-widget reasons in the detailed report and rebuild those widgets by hand."],
    ),
    (
        re.compile(r"no new relic equivalent"),
        "Notification type without a New Relic equivalent",
        [
            "There is no New Relic counterpart for this notification type. Use a supported "
            "destination (email, webhook, Slack, PagerDuty) and route the workflow there."
        ],
    ),
    (
        re.compile(r"plain http"),
        "Plain http endpoint",
        ["Put the endpoint behind https, then add it as a webhook destination in New Relic."],
    ),
    (
        re.compile(r"not confirmed in aws"),
        "Unconfirmed subscription",
        ["Confirm the subscription in AWS (or remove it) and re-run the migration."],
    ),
    (
        re.compile(
            r"subscription protocol|address that is not supported|url that is not supported"
        ),
        "Unsupported notification target",
        [
            "Create the destination by hand in New Relic (Alerts > Destinations) and attach it to "
            "the workflow."
        ],
    ),
    (
        re.compile(r"alarm actions .* must be configured manually"),
        "Non-SNS alarm actions",
        [
            "Alarm actions other than SNS (EC2, Auto Scaling, Systems Manager) have no automatic "
            "equivalent. Recreate them with the matching automation, triggered from a workflow "
            "webhook where possible."
        ],
    ),
]

register_rules(RULES)
