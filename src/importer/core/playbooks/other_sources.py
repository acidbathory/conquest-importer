"""Playbooks for the most common blockers of the CloudWatch, Dynatrace and Google Cloud sources."""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L


def reg(titles: tuple[str, ...] | str, pb: Playbook) -> None:
    register_playbook(titles, pb)


CREATE = (
    "Create the condition: Alerts > Alert conditions > + New alert condition > Write your own "
    "query. Create it disabled, compare with the source for a day, then enable it."
)

# --- Dynatrace ------------------------------------------------------------------------------
reg(
    "Alerting profile",
    Playbook(
        source="A Dynatrace alerting profile decides which problems (by severity, tags, "
        "event types, delay) reach which notifications.",
        newrelic="A workflow: its issue filter selects the issues, its channels are the "
        "notifications.",
        steps=(
            "For each profile write the filter as a workflow issue filter (priority, policy "
            "name, `tags.<name>`).",
            "Decide how Dynatrace severity levels map to New Relic priorities (critical, "
            "high, medium, low).",
            "Attach the destinations from the matching problem notifications.",
        ),
        differs="A delay in the profile has no workflow field; use the condition's threshold "
        "duration instead.",
        effort_minutes=30,
        links=(L.WORKFLOWS, L.DESTINATIONS),
    ),
)
reg(
    (
        "Problem notification",
        "Davis problem notification workflow",
        "Notification tasks",
        "Notification type without a New Relic equivalent",
    ),
    Playbook(
        source="A notification (email, Slack, PagerDuty, Jira, Teams, ServiceNow, HTTP) sent "
        "when a problem opens or closes.",
        newrelic="A destination and channel of the same type, used by a workflow.",
        steps=(
            "Run `importer convert-notifications --source dynatrace` for a plan of the "
            "destinations and disabled workflows.",
            "Alerts > Destinations: create each destination and enter the token, sign-in or "
            "URL (Dynatrace does not export them).",
            "Rewrite message placeholders such as `{ProblemTitle}` as New Relic variables "
            "(issueTitle, priorityText).",
            "Send a test notification before you enable the workflow.",
        ),
        differs="Davis problem categories are not New Relic issue priorities.",
        effort_minutes=30,
        links=(L.INTEGRATIONS, L.WORKFLOWS, L.TEMPLATES),
    ),
)
reg(
    "Maintenance window",
    Playbook(
        source="A scheduled window in which problems are not raised or not notified.",
        newrelic="A muting rule with the same schedule, time zone and a filter on the "
        "conditions it covers.",
        steps=(
            "Alerts > Muting rules > + Add a rule: filter on `tags.<name>` or `conditionName`.",
            "Set start, end, time zone and the repeat (daily, weekly, monthly).",
            "Use the strict muting handling in the workflows so muted issues do not notify.",
        ),
        differs="New Relic still records alert events while muted; a Dynatrace window that "
        "also stopped problem detection does not match exactly.",
        effort_minutes=15,
        links=(L.MUTING, L.TF_MUTING),
    ),
)
reg(
    ("Management zone", "Filter that New Relic cannot express"),
    Playbook(
        source="A management zone is a set of entities selected by rules; conditions and "
        "dashboards can be limited to it.",
        newrelic="None as an object. Workaround: tags on entities (or separate accounts) and "
        "`WHERE tags.<name> = '<value>'` in queries and workflow filters.",
        steps=(
            "List the zone's rules (tag, host group, name pattern).",
            "Put the same tag on the New Relic entities (agent configuration or the entity "
            "tags UI) and use it in WHERE and in workflow filters.",
            "For hard separation use one account per zone.",
        ),
        differs="A tag is a label, not a computed set; membership changes when tags change.",
        effort_minutes=60,
        no_equivalent=True,
        links=(L.NRQL, L.WORKFLOWS),
    ),
)
reg(
    ("SLO", "SLO criteria"),
    Playbook(
        source="A service-level objective with an indicator, a target and a time frame.",
        newrelic="A service level (SLI with good-events and valid-events NRQL, an SLO target "
        "and period) plus an alert on its burn rate.",
        steps=(
            "Run `importer plan-service-levels` for the target and period of each SLO.",
            "Open the entity's Service levels page; add an SLI: valid events query and good "
            "events query from the Dynatrace indicator (split the percentage into good and "
            "total counts).",
            "Set the target and the time window from the criteria listed with the SLO.",
            "Add an alert on burn rate if Dynatrace had one.",
        ),
        differs="Good and valid queries must be written by hand.",
        effort_minutes=60,
        links=(L.SLM,),
    ),
)
reg(
    (
        "Dynatrace object could not be read",
        "Settings schema not read",
        "Built-in anomaly detection setting",
    ),
    Playbook(
        source="A Dynatrace settings object the importer cannot read (a field outside the "
        "schema, a schema it does not know, or tuning of built-in detection).",
        newrelic="A condition written by hand from what Dynatrace shows.",
        steps=(
            "Open the object in Dynatrace (Settings) and look at the field named in the reason.",
            "If it is an alert you rely on, recreate it in New Relic; the other objects were read.",
            "Built-in detection tuning: compare non-default values with what you want and "
            "create conditions for the cases that matter.",
        ),
        differs="Not applicable until recreated.",
        effort_minutes=30,
        links=(L.CONDITION,),
    ),
)
reg(
    (
        "Metric selector not converted",
        "Metric event with an unsupported definition",
        "Samples or window out of range",
        "Davis detector outside the supported pattern",
        "Davis records-mode detector",
        "DQL query not converted",
        "Workflow trigger not converted",
    ),
    Playbook(
        source="A metric selector, detector or query using features with no one-condition "
        "form (several metrics, :fold, :delta, :timeshift, records mode, DQL joins).",
        newrelic="One NRQL condition per metric: aggregation as a function, split as FACET, "
        "filters as WHERE; a baseline condition for comparisons with the past.",
        steps=(
            "Read the selector or query in Dynatrace and write down metric, aggregation, "
            "split-by and filters.",
            "Check that the data reaches New Relic (the metric map may be missing it).",
            "Write the NRQL: `SELECT average(<metric>) FROM Metric FACET <labels>`; for "
            ":timeshift use an anomaly (baseline) condition.",
            "Duration: pick 1 minute to 24 hours.",
            CREATE,
        ),
        differs="Dynatrace evaluates one-minute samples in a sliding window; New Relic uses "
        "separate windows (see the latency measurement for Grafana rules: about 5 minutes to "
        "open on a 60 s window with a 2 minute duration).",
        effort_minutes=40,
        template="SELECT average(<metric>) FROM Metric FACET <labels>",
        links=(L.CONDITION, L.ANOMALY, L.NRQL),
    ),
)
reg(
    ("Davis baseline detector",),
    Playbook(
        source="A Davis anomaly detector that learns a baseline.",
        newrelic="A baseline (anomaly) condition on the matching NRQL.",
        steps=(
            "Note the metric, direction and sensitivity in the Anomaly Detection app.",
            "Create a baseline condition (Alerts > + New alert condition > Anomaly), choose "
            "upper, lower or both and the sensitivity.",
            "Let it learn (the docs say 1 to 4 weeks) before you trust it.",
        ),
        differs="Dynatrace learns from 7 or 14 days, New Relic from 1 to 4 weeks; the numbers "
        "do not convert.",
        effort_minutes=30,
        links=(L.ANOMALY,),
    ),
)
reg(
    "No New Relic data for this Dynatrace metric",
    Playbook(
        source="A condition on a Dynatrace metric whose data never reaches New Relic.",
        newrelic="The New Relic agent or integration that produces the equivalent metric, or "
        "the same metric sent through OpenTelemetry.",
        steps=(
            "Install what the reason names (infrastructure agent, an integration or an OTel "
            "collector).",
            "Find the event and attribute in the data explorer; add a row to the metric map "
            "and check it with `importer validate-dynatrace-map`.",
            "Convert again.",
        ),
        differs="The condition never fires until the data arrives.",
        effort_minutes=60,
        links=(L.OTLP, L.NRQL),
    ),
)

# --- CloudWatch -----------------------------------------------------------------------------
reg(
    "Composite alarm",
    Playbook(
        source="A CloudWatch composite alarm combines other alarms with AND, OR, NOT.",
        newrelic="No composite condition. One NRQL condition for each child; OR is routing "
        "both to one workflow; AND is one NRQL condition over both signals when they share "
        "an event.",
        steps=(
            "Convert the children first (`importer convert`).",
            "OR: send both children's incidents to the same workflow.",
            "AND: write one query that is true only when both are, which works when both "
            "signals are in the same event, or accept two incidents.",
            "NOT is not expressible; alert on the opposite state.",
        ),
        differs="Two incidents instead of one combined state.",
        effort_minutes=45,
        no_equivalent=True,
        links=(L.CONDITION, L.WORKFLOWS),
    ),
)
reg(
    ("Metric math expression", "Metric math in a widget"),
    Playbook(
        source="CloudWatch metric math (a ratio, a sum, functions) over several metrics.",
        newrelic="One NRQL query with the arithmetic between aggregates.",
        steps=(
            "Write: `SELECT sum(<a>) / sum(<b>) * 100 FROM Metric WHERE <dimension filters>`.",
            "Both metrics must be in the same event (AWS Metric Streams data is).",
            "Compare with the CloudWatch value after data flows (`importer validate`).",
        ),
        differs="Functions such as RATE or FILL have no direct equal.",
        effort_minutes=30,
        template="SELECT sum(<metric>) / sum(<metric>) * 100 FROM Metric",
        links=(L.NRQL, L.CONDITION),
    ),
)
reg(
    "High-resolution period",
    Playbook(
        source="A CloudWatch alarm with a period under 60 seconds.",
        newrelic="The shortest aggregation window is 1 minute; Metric Streams data is per minute.",
        steps=("Rebuild with a 60 second window, or keep this alarm in CloudWatch.",),
        differs="Detection is coarser.",
        effort_minutes=10,
        links=(L.STREAMING, L.CONDITION),
    ),
)
reg(
    ("Aggregation window out of range", "Metric or dimension name with unsupported characters"),
    Playbook(
        source="A window or name outside what a New Relic condition accepts.",
        newrelic="A window of 1 minute to 1 hour; backticks around odd attribute names.",
        steps=(
            "Choose the allowed window nearest the original period.",
            "Write the NRQL with backticks around the attribute name.",
        ),
        differs="A different window changes how quickly the condition notices.",
        effort_minutes=10,
        links=(L.CONDITION, L.ALERT_LIMITS),
    ),
)
reg(
    (
        "Widgets that did not convert",
        "Log widget",
        "Alarm status widget",
        "Widget without a usable query",
        "Dashboard widget type not converted",
        "Dashboard query in another language",
    ),
    Playbook(
        source="Dashboard widgets that did not convert (log queries, alarm lists, queries in "
        "another language, empty widgets).",
        newrelic="A chart on NRQL over the matching data; a log table over `Log`; a markdown "
        "widget linking to alerts.",
        steps=(
            "Open the placeholder, note what the widget showed.",
            "Log widgets: forward the logs, then `SELECT count(*) FROM Log WHERE ... FACET`.",
            "Alarm lists: a markdown link to the alerts overview or an issues table.",
            "Empty widgets: leave out.",
        ),
        differs="Different names and units than the source.",
        effort_minutes=20,
        links=(L.CHARTS, L.NRQL, L.PARSING),
    ),
)

# --- Google Cloud ---------------------------------------------------------------------------
reg(
    (
        "Monitoring Query Language condition",
        "Uptime check alert",
        "Metric New Relic does not collect from Google Cloud",
    ),
    Playbook(
        source="A Google alert on MQL, an uptime check, or a metric New Relic does not pull "
        "from Google Cloud.",
        newrelic="An NRQL condition on the matching `gcp.` metric; a Synthetics monitor for "
        "an uptime check; the infrastructure agent or OpenTelemetry for agent and custom "
        "metrics.",
        steps=(
            "MQL: read the query, work out metric, aggregation and threshold, write the NRQL.",
            "Uptime check: `importer convert-gcp-uptime` lists the checks; create a ping or "
            "scripted API monitor in the same locations and alert on its failures.",
            "Other metrics: install the agent or send OTLP, then write the condition.",
        ),
        differs="Google polling adds latency (first data 2 to 5 minutes after linking).",
        effort_minutes=45,
        links=(L.CONDITION, L.OTLP),
    ),
)
