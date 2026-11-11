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
            "Azure fires this alert only when every criterion is true. The importer joins "
            "criteria into one query with if(test AND test, 1, 0) when they share the scope, "
            "dimensions and window; this rule did not (the reason says why). The steps above "
            "list each criterion converted alone.",
            "Either rewrite the criteria by hand as one query (the cookbook method 'AND of two "
            "tests with if()'), or decide which criterion should page and keep the others as "
            "warning conditions or dashboard charts, so nothing is lost silently.",
        ],
    ),
    (
        re.compile(r"^dynamic threshold"),
        "Azure dynamic threshold",
        [
            "A dynamic threshold becomes an approximate New Relic baseline condition when its "
            "sensitivity (High, Medium, Low) and operator are readable. This one was not "
            "readable (the reason says what). Create a baseline condition on the metric by "
            "hand and tune the standard deviations against a week of history, or read the "
            "recent range in Azure and set a static threshold.",
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
            "The importer accepts windows from one minute to six hours (the NerdGraph schema "
            "limit). A longer window, such as one day, cannot be one aggregation window: "
            "create the condition by hand with the longest window and a threshold duration, "
            "or move the check to a dashboard. A shorter evaluation of the same rule is not "
            "equivalent.",
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
        re.compile(r"^kql query cannot be converted"),
        "Azure KQL query",
        [
            "Only a table, simple where terms on columns, and a count or one aggregate of a "
            "column are converted. Joins, let, regular expressions, parse, extend, arg_max "
            "and other operators have no NRQL form (a condition cannot hold subqueries).",
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
            "Azure fires this rule for every matching activity event. The importer converts a "
            "condition of equals tests (one field may list several values) and a single "
            "containsAny value into a count of forwarded activity log records; this rule has "
            "something else (the reason says what): an OR across fields, several containsAny "
            "values, several scope prefixes, a tenant scope, or a field the record does not "
            "carry (resourceGroup, resourceType, caller).",
            "Option 1: deploy the Event Hub log forwarder with the matching "
            "`forward...AzureActivityLogs` flag, look at one forwarded record to learn its "
            "attribute names, then create `SELECT count(*) FROM Log WHERE plugin.type = "
            "'azure' AND <attribute> = '<value>'` at or above 1 over a 60 second window; use "
            "one condition for each OR branch or scope prefix.",
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
