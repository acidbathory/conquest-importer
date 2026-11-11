"""Manual steps for every Azure reason that stops a conversion. Registered on import.

Each rule is anchored on the fixed start of a reason (see `reasons.py`) and has a playbook in
`playbooks.py` with the same title. Steps are guidance for a person; nothing here is carried out
by the tool. New Relic facts come from docs/research/azure-monitor-alerting.md and
azure-datapath.md; what those documents mark UNVERIFIED is said to be unverified here too.
"""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"^metric alert has several criteria"),
        "Azure metric alert with several criteria",
        [
            "Azure fires this alert only when every criterion is true. New Relic has no "
            "composite condition, so the AND cannot be kept. The steps above list each "
            "criterion converted alone.",
            "Decide which criterion should page. Create that one alone, and keep the others as "
            "warning conditions or as dashboard charts, so nothing is lost silently.",
        ],
    ),
    (
        re.compile(r"^dynamic threshold"),
        "Azure dynamic threshold",
        [
            "Azure learns the normal range with its own model (it needs several days of history) "
            "and offers High, Medium and Low sensitivity. New Relic baseline conditions use a "
            "different model and are set in standard deviations; there is no published "
            "equivalence, so the importer does not guess one.",
            "Either create a baseline condition on the metric and tune the deviations against "
            "a week of history, or read the recent range in Azure and set a static threshold.",
        ],
    ),
    (
        re.compile(r"^availability test alert"),
        "Azure availability test alert",
        [
            "The alert counts failing locations of an Application Insights availability test. "
            "Recreate the check as a New Relic Synthetics monitor and give it an alert "
            "condition on failures from the locations that matter.",
        ],
    ),
    (
        re.compile(
            r"^(unknown metric alert criteria type|alert rule type is not recognised"
            r"|scheduled query rule kind is not)"
        ),
        "Azure rule of an unknown type",
        [
            "The export holds a rule type or kind this importer does not know (Azure adds "
            "values over time). Read the rule in the Azure portal and write the New Relic "
            "condition by hand.",
        ],
    ),
    (
        re.compile(
            r"^(metric namespace is not an azure platform|scope is application insights"
            r"|log alert scope is not)"
        ),
        "Azure data that the integration does not collect",
        [
            "The Azure Monitor integration collects platform metrics only. Custom and guest "
            "metrics, Application Insights data and Azure Data Explorer are not read by it.",
            "Send the data to New Relic another way (the OpenTelemetry collector, an APM agent "
            "or the infrastructure agent), find the metric name it arrives under, then write "
            "the condition on that name.",
        ],
    ),
    (
        re.compile(
            r"^(metric alert has no readable|log alert has no readable|comparison operator is not"
            r"|threshold is not a number|time aggregation is not|dimension cannot be written"
            r"|metric name cannot be written|window or evaluation frequency)"
        ),
        "Azure rule field the importer cannot read",
        [
            "A field of the rule has a value the importer cannot turn into a condition (shown "
            "in the reason). Open the rule in the Azure portal, check the value, and write the "
            "condition by hand, or fix the rule and export again.",
        ],
    ),
    (
        re.compile(r"^window is (longer|shorter)"),
        "Azure window outside the supported range",
        [
            "The importer accepts windows from one minute to one hour. For a longer window, "
            "create the condition by hand once the longest window New Relic accepts has been "
            "checked in your account (the documentation pages disagree). A shorter evaluation "
            "of the same rule is not equivalent.",
        ],
    ),
    (
        re.compile(r"^(scope is not a resource|scope lists several)"),
        "Azure scope that one condition cannot cover",
        [
            "A New Relic condition matches one resource id or one prefix. Create one "
            "condition for each resource group or subscription the rule covers (the steps "
            "above list them), or add the resource ids to the rule in Azure first.",
        ],
    ),
    (
        re.compile(r"^kql query cannot be converted: (join|lookup|union) has no"),
        "Azure KQL join or union",
        [
            "A condition reads one event type, so a join, lookup or union of two tables has no "
            "NRQL form. Decide what the join was for. A lookup against a short fixed list "
            "becomes `IN ('a', 'b')`. A comparison of two data sets becomes two conditions, "
            "or one condition on the data set that matters and a dashboard chart for the "
            "other.",
        ],
    ),
    (
        re.compile(
            r"^kql query cannot be converted: (mv-expand|mv-apply|parse|function extract\(\)"
            r"|function [a-z_]+\(\) has no New Relic form \(in (extend|project)\))"
        ),
        "Azure KQL parsing or expansion",
        [
            "The query splits, parses or computes a column (`parse`, `extract` with regex "
            "captures, `mv-expand`, or `extend` with a function). NRQL cannot create columns "
            "inside a condition.",
            "Parse the value at ingest: add a parsing rule to New Relic log parsing so the "
            "captured value becomes an attribute, then write the condition on that attribute "
            "(`SELECT count(*) FROM Log WHERE <attribute> = '<value>'`). For a plain match "
            "`WHERE message RLIKE r'.*pattern.*'` works without parsing.",
        ],
    ),
    (
        re.compile(
            r"^kql query cannot be converted: (make-series|series_\* function|evaluate|scan"
            r"|absence check)"
        ),
        "Azure KQL time series or absence check",
        [
            "Series functions (`make-series`, `series_*`, `evaluate`) compute anomalies or "
            "forecasts inside the query. Use a baseline condition on the underlying NRQL "
            "aggregate instead, and tune the deviations against a week of history.",
            "An absence check (`max(TimeGenerated)` compared with `ago()`) is a loss-of-signal "
            "condition: `SELECT count(*) FROM Log WHERE <identifying filter> FACET <key>` "
            "with the signal-loss duration set to the age in the query (10 minutes for "
            "`ago(10m)`).",
        ],
    ),
    (
        re.compile(
            r"^kql query cannot be converted: (cross-resource query|materialize|let that"
            r"|statement|list)"
        ),
        "Azure KQL cross-resource query or function",
        [
            "A query over another workspace or resource (`workspace()`, `app()`, `adx()`), a "
            "`let` that defines a table or function, or `materialize` cannot be one NRQL "
            "condition.",
            "Create one condition for each workspace or resource the query reads, with the "
            "`let` body written in place, and send each workspace's logs to New Relic first.",
        ],
    ),
    (
        re.compile(r"^kql query cannot be converted"),
        "Azure KQL query",
        [
            "Converted: a table, where terms (comparisons, contains, has, startswith, in, "
            "between, matches regex, isempty, and, or, not), and one aggregate (count, "
            "countif, dcount, avg, sum, min, max, percentile, any), grouped by columns. "
            "Other operators and functions (arg_max, make_set, let functions) have no NRQL "
            "form: a condition cannot hold subqueries.",
            "Work out what the query counts, then write the NRQL by hand over the forwarded "
            "`Log` data, for example `SELECT count(*) FROM Log WHERE plugin.type = 'azure' "
            "AND <attribute> = '<value>'`. Inspect one forwarded record first; the attribute "
            "names are not confirmed.",
        ],
    ),
    (
        re.compile(r"^kql table comes from"),
        "Azure log table from an agent or service",
        [
            "Tables such as Perf, InsightsMetrics, Heartbeat, Container logs and the "
            "Application Insights tables are filled by the Azure Monitor agent or by Azure "
            "services whose data New Relic does not read.",
            "Use the New Relic equivalent: the infrastructure agent or the Kubernetes "
            "integration for host and container metrics, loss of signal on an infrastructure "
            "signal for a heartbeat, APM or OpenTelemetry for application requests and "
            "exceptions.",
        ],
    ),
    (
        re.compile(
            r"^(log alert has several conditions|legacy log alert|log alert evaluates a longer"
            r"|log alert measures|scheduled query rule publishes)"
        ),
        "Azure log alert shape",
        [
            "The rule has a shape with no single-condition equivalent: several conditions, an "
            "older rule format, a query range longer than its window, a measured column that "
            "is not a plain attribute name, or a rule that only publishes a metric.",
            "Split it into one condition per query, write the measured column as an NRQL "
            "attribute, or drop a rule that does not alert.",
        ],
    ),
    (
        re.compile(r"^(activity log alert|service health or resource health)"),
        "Azure activity log alert",
        [
            "Azure fires this rule for every matching activity event. New Relic has no source "
            "for Azure activity events until they are forwarded as logs.",
            "Option 1: deploy the Event Hub log forwarder with the matching "
            "`forward...AzureActivityLogs` flag, look at one forwarded record to learn its "
            "attribute names, then create `SELECT count(*) FROM Log WHERE plugin.type = "
            "'azure' AND <attribute> = '<value>'` above 0 over a 60 second window.",
            "Option 2: keep this rule in Azure Monitor and its action group; it needs no "
            "New Relic data. Service health and resource health events are Azure's own.",
        ],
    ),
    (
        re.compile(r"^smart detector alert rule"),
        "Azure smart detector rule",
        [
            "A smart detector is a machine-learning detector that Azure runs; New Relic has "
            "no equivalent. Replace it with a baseline condition on the signal the detector "
            "watched, for example the error rate of the application or the count of failed "
            "log lines.",
        ],
    ),
    (
        re.compile(r"^recording rule: an azure"),
        "Azure rule group without alerts",
        [
            "The group holds recording rules only; they are not alerts and have no New Relic "
            "form. Where an alert expression uses a recorded name, write the recorded "
            "expression in place.",
        ],
    ),
    (
        re.compile(r"^prometheus rule (has|contains)"),
        "Azure Prometheus rule that could not be read",
        [
            "The expression has a placeholder, no text, or a `for` value that is not an "
            "ISO 8601 duration such as PT5M. Fix the rule in Azure (or fill in the placeholder) "
            "and export again.",
        ],
    ),
]
register_rules(RULES)
