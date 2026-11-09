"""Manual-step rules for Dynatrace findings. Registered with the shared engine on import.

Order matters (first match wins): the workflow-shape reason mentions a DQL query, so it comes
before the rule for a DQL query that is not the supported pattern.
"""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"davis problem notification workflow"),
        "Davis problem notification workflow",
        [
            "This workflow sends a message when Davis opens a problem. It is a notification "
            "route, not an alert condition, so there is nothing to convert it to directly.",
            "In New Relic create a destination for each channel the workflow used (email, "
            "Slack, PagerDuty, Jira and so on), a notification channel on each destination, "
            "and a workflow that sends issues to those channels.",
            "Filter the New Relic workflow on issue priority or on tags, to match the "
            "categories and entity tags the Dynatrace trigger filtered on. Enter tokens and "
            "webhook URLs again; Dynatrace does not export them.",
        ],
    ),
    (
        re.compile(r"workflow trigger is"),
        "Workflow trigger not converted",
        [
            "Only a scheduled workflow with a log count converts. A manual or other event "
            "trigger has no alert condition to carry over. Decide what the workflow is for: "
            "if it reacts to an event, build a New Relic alert condition on the same data; if "
            "it is a runbook, keep it in Dynatrace.",
        ],
    ),
    (
        re.compile(r"workflow tasks"),
        "Workflow tasks not converted",
        [
            "A converted workflow has exactly one DQL query task and notify tasks that run "
            'only when a condition such as `result("task")["records"][0]["count"] > 100` '
            "holds. Read the workflow in Dynatrace, find the query and the number it is "
            "compared with, and create the NRQL alert condition by hand with that threshold.",
        ],
    ),
    (
        re.compile(r"dql query"),
        "DQL query not converted",
        [
            "Only a log count with equality filters converts. Read the DQL in the workflow and "
            "rewrite it as NRQL over the matching New Relic data: logs as `FROM Log`, metrics "
            "as `FROM Metric` (the metric names differ; check them in the data explorer), "
            "then set the threshold and window from the workflow's comparison and schedule.",
            "A query over metrics (`timeseries`), several summaries or a join needs the data "
            "sent to New Relic first; there is no automatic mapping of Dynatrace metric keys.",
        ],
    ),
    (
        re.compile(r"notify condition"),
        "Notify condition not converted",
        [
            "The importer reads one form of condition: a count from the query compared with a "
            "number using >, >=, < or <=. For anything else, work out when the notification "
            "fires and create the NRQL condition with that threshold by hand.",
        ],
    ),
    (
        re.compile(r"longer than one hour"),
        "Schedule longer than one hour",
        [
            "New Relic evaluates counts over windows of up to one hour. For a daily or "
            "multi-hour check, create the condition by hand with a one hour window and a "
            "threshold scaled to match, and check that the result means the same thing.",
        ],
    ),
    (
        re.compile(r"slo is not converted"),
        "SLO",
        [
            "In New Relic create a service level over the same data: open the service level "
            "page, choose the entity, and write the good-events and valid-events NRQL "
            "queries from the SLO's indicator (Dynatrace reports one indicator percentage; "
            "split it into good and total counts).",
            "Set the target and period from the criteria listed with this SLO, then add an "
            "alert on the service level's burn rate if the Dynatrace SLO had one.",
        ],
    ),
    (
        re.compile(r"slo criteria"),
        "SLO criteria",
        [
            "These are the target, warning level and time range of the Dynatrace SLO. Use the "
            "target as the service level objective and the time range as its period, as far "
            "as New Relic's service level periods allow.",
        ],
    ),
    (
        re.compile(r"notification tasks"),
        "Notification tasks",
        [
            "Recreate each notification as a New Relic destination and channel, and attach "
            "them to a workflow for the converted condition. Addresses, tokens and webhook "
            "URLs are not read from Dynatrace; enter them again.",
        ],
    ),
    (
        re.compile(r"trigger was inactive"),
        "Inactive trigger",
        [
            "The Dynatrace trigger was switched off, so the workflow was not running. The "
            "New Relic condition is created disabled; enable it only after reviewing it.",
        ],
    ),
]

register_rules(RULES)
