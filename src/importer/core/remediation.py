"""Manual steps for things that did not migrate (or migrated with a caveat).

Same idea as taxonomy.py: ordered keyword rules over the free-text reasons, first match per
reason. `IRMonitor.remediation` (specific, computed by a source) takes precedence over these
generic steps. A test fails when a reason on an unconverted fixture has no rule, which is the
prompt to add one here. Steps are guidance for a human; none of them is carried out by the tool.
"""

import re
from dataclasses import dataclass

_RULES: list[tuple[re.Pattern[str], str, list[str]]] = [
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


_COMPILED = [(re.compile(p.pattern, re.IGNORECASE), title, steps) for p, title, steps in _RULES]


# Incidental notes that sit beside a real blocker; they never decide which group an asset is in.
SECONDARY_TITLES = frozenset({"Non-SNS alarm actions"})


@dataclass(frozen=True)
class Remedy:
    title: str  # the kind of problem, shared by every asset with it
    steps: tuple[str, ...]  # generic manual steps for that kind of problem

    @property
    def secondary(self) -> bool:
        return self.title in SECONDARY_TITLES


def remedies_for(reasons: list[str]) -> list[Remedy]:
    """The first matching rule per reason, without repeats."""
    found: list[Remedy] = []
    for reason in reasons:
        for pattern, title, steps in _COMPILED:
            if pattern.search(reason):
                remedy = Remedy(title, tuple(steps))
                if remedy not in found:
                    found.append(remedy)
                break
    return found


def steps_for(reasons: list[str]) -> list[str]:
    """Generic manual steps for a list of reasons, without repeats."""
    steps: list[str] = []
    for remedy in remedies_for(reasons):
        steps += [x for x in remedy.steps if x not in steps]
    return steps
