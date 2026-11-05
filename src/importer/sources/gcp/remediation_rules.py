"""Manual-step rules for Google Cloud findings. Registered with the shared engine on import."""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"metric-absence condition"),
        "Metric-absence condition",
        [
            "Create an NRQL alert condition on the same metric and turn on loss of signal "
            "(for example after 15 minutes without data), or use a query such as "
            "`SELECT count(*) FROM Metric WHERE metricName = '<name>'` with a threshold of "
            "below 1.",
        ],
    ),
    (
        re.compile(r"mql conditions"),
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
        re.compile(r"filter (term|not supported)|log filter"),
        "Filter not supported",
        [
            "The filter uses a feature the importer does not read (OR, regular expressions, "
            "functions such as starts_with, or a severity range). Write the matching NRQL "
            "`WHERE` clause by hand.",
        ],
    ),
    (
        re.compile(r"notification channels must be configured manually"),
        "Notification channels",
        [
            "Recreate each channel as a New Relic destination and add it to a workflow. "
            "Secrets (tokens and passwords) are never returned by Google, so enter them again.",
        ],
    ),
]
RULES_SECONDARY = frozenset({"Notification channels"})

register_rules(RULES)
