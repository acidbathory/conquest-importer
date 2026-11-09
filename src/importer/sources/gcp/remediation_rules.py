"""Manual-step rules for Google Cloud findings. Registered with the shared engine on import."""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"metric-absence condition"),
        "Metric-absence condition that could not be converted",
        [
            "The reason above says what stopped the conversion (the filter, the aggregation or "
            "the metric). Create an NRQL alert condition on the same metric and turn on loss of "
            "signal (for example after 15 minutes without data), or use a query such as "
            "`SELECT count(*) FROM Metric WHERE metricName = '<name>'` with a threshold of "
            "below 1.",
        ],
    ),
    (
        re.compile(r"(?<![a-z])mql conditions"),
        "Monitoring Query Language condition",
        [
            "Google has ended MQL, so existing conditions keep working but cannot be edited in "
            "the console. Read the query, work out the metric, aggregation and threshold, and "
            "write the NRQL by hand over the matching `gcp.` metric.",
        ],
    ),
    (
        re.compile(r"promql conditions"),
        "PromQL condition",
        [
            "New Relic alert conditions are written in NRQL, not PromQL. Send the Prometheus "
            "data to New Relic (remote write or the OpenTelemetry Collector) and rebuild the "
            "condition over `Metric`.",
        ],
    ),
    (
        re.compile(r"sql conditions"),
        "SQL condition",
        ["Rewrite the query as NRQL over the matching New Relic data."],
    ),
    (
        re.compile(r"ratio conditions"),
        "Ratio condition",
        [
            "Write one NRQL query that divides the two metrics, for example "
            "`SELECT sum(<numerator>) / sum(<denominator>) FROM Metric`, and alert on it.",
        ],
    ),
    (
        re.compile(r"forecast conditions"),
        "Forecast condition",
        [
            "Recreate as a New Relic baseline condition on the same metric once data is "
            "flowing; the forecast model itself does not carry over.",
        ],
    ),
    (
        re.compile(r"trigger on several time series|second-stage aggregation"),
        "Multi-series trigger",
        [
            "Rebuild in NRQL: facet by the resource and set the aggregation in the query, then "
            "choose the threshold and duration in the condition.",
        ],
    ),
    (
        re.compile(r"uptime check inventory \(http\)"),
        "Uptime check (HTTP)",
        [
            "Create a Synthetics ping monitor for the check's URL (the host is in the Google "
            "check, not in the export) with the run frequency in the report. Use a scripted API "
            "monitor instead if the Google check had content matchers or a required status.",
        ],
    ),
    (
        re.compile(r"uptime check inventory \(tcp\)"),
        "Uptime check (TCP)",
        [
            "New Relic Synthetics has no TCP-only monitor that this tool knows of (not "
            "confirmed). If the service has an HTTP health endpoint, make a ping monitor on it. "
            "Otherwise keep the TCP check in Google, or watch the service with an infrastructure "
            "or network check.",
        ],
    ),
    (
        re.compile(r"uptime check inventory \(other\)"),
        "Uptime check (other kind)",
        [
            "This check is not a plain HTTP or TCP check (for example a group check). Look at "
            "what it watches in Google and create a ping or scripted monitor per target, or "
            "one monitor per member of the group.",
        ],
    ),
    (
        re.compile(r"uptime check"),
        "Uptime check alert",
        [
            "`importer convert-gcp-uptime` lists every uptime check with its path, period and "
            "the monitor to create; do that first.",
            "The alert watches an uptime check, and uptime checks are not migrated. Recreate the "
            "check as a New Relic Synthetics monitor (a ping monitor for a URL, a scripted API "
            "monitor for a request with assertions), choosing the same locations.",
            "Then add an alert condition on the monitor's failures, which Synthetics offers "
            "when you create it.",
        ],
    ),
    (
        re.compile(r"filter (term|not supported)|log filter"),
        "Filter not supported",
        [
            "The filter uses a feature the importer does not read (OR, regular expressions, "
            "functions such as starts_with, or a comparison on a field other than severity). Write "
            "the matching NRQL "
            "`WHERE` clause by hand.",
        ],
    ),
    (
        re.compile(r"aligner|reducer|alignment period|comparison .* is not supported"),
        "Aggregation not supported",
        [
            "The condition uses an aggregation the importer does not convert. Work out what it "
            "measures in Cloud Monitoring (metrics explorer shows the aligner and reducer), then "
            "write the equivalent NRQL: `average`, `max`, `sum` or `percentile` over the `gcp.` "
            "metric, faceted by the resource.",
        ],
    ),
    (
        re.compile(r"over new relic's limit"),
        "Window longer than New Relic allows",
        [
            "New Relic accepts a breach duration of up to 24 hours and an aggregation window of "
            "up to 1 hour. Create the condition by hand with values inside those limits, or "
            "alert on a longer period with a shorter duration and a rolled-up query.",
        ],
    ),
    (
        re.compile(r"valid new relic attribute name"),
        "Label that is not a valid attribute name",
        [
            "Find the attribute New Relic stores for this label (run `SELECT keyset() FROM "
            "Metric WHERE metricName = '<name>'`) and write the `WHERE` or `FACET` clause by "
            "hand with that name in backticks.",
        ],
    ),
    (
        re.compile(r"not one of the google cloud service metrics"),
        "Metric New Relic does not collect from Google Cloud",
        [
            "Collect the data in New Relic directly. For a metric from the Ops Agent, install the "
            "New Relic infrastructure agent or the OpenTelemetry Collector on the VM. For a "
            "custom or Prometheus metric, send it to New Relic with OpenTelemetry (OTLP) or "
            "Prometheus remote write from where it is produced.",
            "For an alert on a log-based metric, forward the logs and alert on a `Log` query "
            "instead (the same way log-match conditions convert).",
            "Then write the NRQL condition over the new metric by hand.",
        ],
    ),
    (
        re.compile(r"email channel: the address is not exported"),
        "Email channel",
        [
            "Create an email destination in New Relic (Alerts, Destinations) with the address "
            "from the Google channel, then a channel for it, and add it to the workflow listed "
            "in the notification report.",
        ],
    ),
    (
        re.compile(r"webhook channel: the url and token"),
        "Webhook channel with a token",
        [
            "Create a webhook destination with the URL, and add the token as a custom header "
            "(Google does not return it, so take it from the receiving service). Then add the "
            "channel to the workflow listed in the notification report.",
        ],
    ),
    (
        re.compile(r"webhook channel: the url, user name and password"),
        "Webhook channel with basic authentication",
        [
            "Create a webhook destination with the URL and basic authentication, entering the "
            "user name and password again (Google does not return the password). Then add the "
            "channel to the workflow listed in the notification report.",
        ],
    ),
    (
        re.compile(r"slack channel: connecting slack"),
        "Slack channel",
        [
            "In New Relic, create a Slack destination; it opens a one-time OAuth in Slack that a "
            "Slack admin has to approve. Pick the same Slack channel, then add it to the "
            "workflow listed in the notification report.",
        ],
    ),
    (
        re.compile(r"pagerduty channel: the service key"),
        "PagerDuty channel",
        [
            "Create a PagerDuty destination in New Relic with the service key from the PagerDuty "
            "service (Google does not return it), then add it to the workflow listed in the "
            "notification report.",
        ],
    ),
    (
        re.compile(r"has no new relic destination"),
        "Channel type without a New Relic destination",
        [
            "New Relic has no destination for this kind of channel (SMS, Pub/Sub, Google mobile "
            "app and the retired chat tools). Choose email, Slack, PagerDuty or a webhook that "
            "reaches the same people, and add it to the workflow listed in the notification "
            "report. A webhook can call a small receiver that forwards to Pub/Sub or SMS.",
        ],
    ),
    (
        re.compile(r"channel is not in the export"),
        "Channel missing from the export",
        [
            "The policy names a channel that channels.json does not list (deleted, or the "
            "export could not read it). Decide whether the policy still needs a notification; "
            "if so, create the destination by hand and add it to the workflow.",
        ],
    ),
    (
        re.compile(r"(mql|promql|ratio|sql) queries in widgets"),
        "Dashboard query in another language",
        [
            "Write the chart's query in NRQL over the matching `gcp.` metrics (a ratio is one "
            "query that divides two sums), or build it in the New Relic query builder, and add "
            "it to the dashboard as a chart.",
        ],
    ),
    (
        re.compile(r"dashboard widget type"),
        "Dashboard widget type not converted",
        [
            "Rebuild the widget by hand. A logs panel becomes a table or chart over `Log` (the "
            "logs must be forwarded); an alert chart or incident list becomes an issues widget "
            "or a link to the alerts page; a section header or collapsible group becomes a "
            "markdown widget.",
        ],
    ),
    (
        re.compile(r"widget has no (query|content)|widget query is not recognised"),
        "Widget without a usable query",
        [
            "Open the widget in Cloud Monitoring and note what it shows. If it matters, "
            "recreate it in New Relic from the metric it reads; if it is empty, leave it out.",
        ],
    ),
    (
        re.compile(r"notification channels must be configured manually"),
        "Notification channels",
        [
            "Recreate each channel as a New Relic destination and add it to a workflow. "
            "Secrets (tokens and passwords) are never returned by Google, so enter them again.",
            "`importer convert-gcp-notifications` lists the channel sets, the conditions each "
            "routes and the steps per channel.",
        ],
    ),
]
RULES_SECONDARY = frozenset({"Notification channels"})

register_rules(RULES)
