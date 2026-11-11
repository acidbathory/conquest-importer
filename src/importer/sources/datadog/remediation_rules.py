"""Manual steps for every Datadog reason that stops a conversion. Registered on import.

Each rule is anchored on the fixed start of a reason (see `reasons.py`) and has a playbook in
`playbooks.py` with the same title. Steps are guidance for a person; nothing here is carried out by
the tool. New Relic facts come from docs/research/datadog-verified.md; what that note marks
UNVERIFIED is said to be unverified here too.
"""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"^anomaly monitor"),
        "Datadog anomaly monitor",
        [
            "Datadog fires when more than a fraction of the alert window is outside a band that "
            "its own algorithm (basic, agile or robust) learned. New Relic baseline conditions "
            "learn from one to four weeks of history and fire when the signal is a number of "
            "standard deviations from the baseline; the two numbers do not convert, so the "
            "importer does not guess one.",
            "Create a baseline condition on the same metric, disabled, with the Datadog "
            "parameters (algorithm, bounds, direction, alert window) copied into its description. "
            "Enable it after the baseline has been learned and tune the deviations on real data.",
            "A baseline does not train on FACET queries from stored data; a grouped Datadog "
            "monitor may need one condition per group or a static threshold instead.",
        ],
    ),
    (
        re.compile(r"^outlier monitor"),
        "Datadog outlier monitor",
        [
            "Datadog compares each series with its peers (DBSCAN or MAD) with a tolerance that is "
            "a multiplier on a threshold it derives from the data. New Relic outlier conditions "
            "(DBSCAN, availability varies by account) use an absolute distance, so the tolerance "
            "does not transfer.",
            "For DBSCAN create an outlier condition on the grouped query and tune the distance on "
            "history. For MAD there is no equivalent: compare each series with the group average "
            "in a static condition on a ratio, or use a dashboard.",
        ],
    ),
    (
        re.compile(r"^forecast monitor"),
        "Datadog forecast monitor",
        [
            "Datadog alerts when a linear or seasonal forecast is predicted to cross the "
            "threshold within the window. New Relic static conditions can open a violation when a "
            "prediction crosses the threshold (predict by N seconds); the model differs.",
            "Create a static condition with a prediction horizon equal to the forecast window "
            "(it is a beta setting, check your account) and compare it with the Datadog monitor "
            "for a week before relying on it.",
        ],
    ),
    (
        re.compile(r"^change alert"),
        "Datadog change alert",
        [
            "A change alert compares the value now with the value one interval earlier, as an "
            "absolute change or a percentage. NRQL COMPARE WITH is not allowed in alert "
            "conditions and no condition type holds two time ranges.",
            "Decide what the change was meant to catch. A sudden jump or drop is closest to a "
            "baseline condition; a level that must not exceed a number is a static threshold. "
            "A dashboard billboard with COMPARE WITH keeps the view.",
        ],
    ),
    (
        re.compile(r"^cumulative window"),
        "Datadog cumulative window",
        [
            "A current_* window accumulates from a calendar boundary (this hour, this day, this "
            "month) and resets there. New Relic conditions evaluate rolling windows only.",
            "Use a rolling window that covers the longest period you care about, or build the "
            "check as a scheduled report. The two do not fire at the same time.",
        ],
    ),
    (
        re.compile(r"^composite monitor"),
        "Datadog composite monitor",
        [
            "A composite monitor is a boolean expression over the status of other monitors. New "
            "Relic has compound conditions (AND, OR, NOT over up to five standard conditions in "
            "one account) but they need every component monitor converted first, and creating "
            "them through the API was not confirmed (UNVERIFIED).",
            "Convert the component monitors, then build the compound condition in the UI. Note "
            "that Datadog && returns the least severe status and || the most severe.",
        ],
    ),
    (
        re.compile(r"^service check monitor"),
        "Datadog service check",
        [
            "A service check carries a status (ok, warning, critical, unknown) per host or tag "
            "group, reported by an agent check. That data is in New Relic only if the check is "
            "sent again; the importer cannot know how.",
            "For a host agent check use a host-not-reporting condition. For a network or HTTP "
            "check use a synthetic monitor. Otherwise send the check results as custom events and "
            "write an NRQL condition on them; 'last N runs' is a threshold duration with all "
            "windows breaching.",
        ],
    ),
    (
        re.compile(r"^event monitor"),
        "Datadog event monitor",
        [
            "Datadog events and Watchdog findings are products of the Datadog platform itself. "
            "New Relic has no equivalent source unless the events are sent as custom events.",
            "Decide what the monitor protected. If the events came from a system you can also "
            "alert on directly (a deploy, a crash log), write the condition on that data.",
        ],
    ),
    (
        re.compile(r"^slo alert"),
        "Datadog SLO alert",
        [
            "The alert watches the remaining error budget or the burn rate of a Datadog SLO. New "
            "Relic service levels have fast and slow burn alert conditions, but the service level "
            "must exist first.",
            "Create the service level from the SLO's good and total event queries, then add burn "
            "rate conditions. Whether a budget-consumed condition exists in your account was not "
            "confirmed (UNVERIFIED).",
        ],
    ),
    (
        re.compile(r"^synthetics monitor"),
        "Datadog synthetics monitor",
        [
            "The monitor alerts on a Datadog Synthetics test. The test definition is a separate "
            "Datadog export and this tool does not read it.",
            "Recreate the test as a New Relic synthetic monitor (same URL or script, same "
            "locations) and give it a condition on failures. Use min_failure_duration and "
            "min_location_failed from the monitor options to choose the thresholds.",
        ],
    ),
    (
        re.compile(
            r"^(process|rum|trace analytics|ci|audit|error tracking|database monitoring|network"
            r"|cost|data observability|llm observability) monitor"
        ),
        "Datadog product-specific monitor",
        [
            "The monitor reads data that only a Datadog product collects. The conversion is not a "
            "translation of the query: first decide which New Relic product replaces the Datadog "
            "one, then write the condition on the data it collects.",
            "Examples: RUM events become browser or mobile agent data, trace analytics becomes "
            "Span data from an APM agent or OpenTelemetry, process counts come from the "
            "infrastructure agent's process samples. CI, audit, cost and network path data have "
            "no direct counterpart.",
        ],
    ),
    (
        re.compile(r"^(monitor type is not one datadog|monitor has no readable type)"),
        "Datadog monitor of an unknown type",
        [
            "The export holds a monitor type this importer does not know (Datadog adds types over "
            "time) or a monitor without a readable type or query. Open it in Datadog and decide "
            "what it protects, then write the New Relic condition by hand.",
        ],
    ),
    (
        re.compile(r"^(query could not be read|query was read only in part)"),
        "Datadog query that could not be read",
        [
            "The query is too long, too deeply nested, has no comparison with a number (a "
            "formula threshold, for example) or uses syntax the parser does not know. The reason "
            "names the first problem. Nothing was guessed.",
            "Read the query in Datadog and write the New Relic NRQL by hand. If the syntax is "
            "valid Datadog, send the query text (without data) so the parser can learn it.",
        ],
    ),
    (
        re.compile(r"^query function"),
        "Datadog query function",
        [
            "The query uses a function that changes the numbers in a way a plain NRQL aggregate "
            "does not: as_rate, rollup, fill, default_zero, rate and difference functions, "
            "time shifts, ranking, clamping or a nested space aggregation. The reason names it "
            "and says whether a later milestone is planned for it.",
            "Write the NRQL by hand: rate() or derivative() for per-second values, FACET with "
            "ORDER BY for ranking (not in conditions), filter(count(*), WHERE ...) or a static "
            "fill value for empty windows. Compare the result with Datadog on the same data "
            "before enabling.",
        ],
    ),
    (
        re.compile(r"^multi-query formula monitor"),
        "Datadog multi-query formula monitor",
        [
            "The monitor evaluates a formula over named sub-queries (options.variables, or "
            "formula() in a log or database monitor). A New Relic condition holds one NRQL "
            "query.",
            "Rebuild the formula as one NRQL query with several selects joined by arithmetic when "
            "the sub-queries read the same event; otherwise use a baseline or two conditions. "
            "default_zero(x) / default_zero(y) needs filter() and a guard against division by "
            "zero.",
        ],
    ),
    (
        re.compile(
            r"^(scope cannot|scope uses a template|tag cannot|queries in the formula group"
            r"|queries in the formula live|query has no metric)"
        ),
        "Datadog scope or grouping",
        [
            "The scope or the group-by cannot be written as one NRQL WHERE and FACET: an OR across "
            "different tags or with wildcards, NOT over a group, a tag without a key, a template "
            "variable, a tag name that is not a valid attribute, or formula queries that group "
            "differently or live on different events.",
            "Write the WHERE clause by hand. An OR over wildcard values is "
            "(a LIKE 'x%' OR a LIKE 'y%'). A template variable belongs to a dashboard or a "
            "monitor template: substitute the value you want.",
        ],
    ),
    (
        re.compile(r"^metric name cannot be written"),
        "Datadog metric name",
        [
            "The metric name has characters that cannot be an NRQL name. Find how the metric "
            "arrives in New Relic (the data explorer shows metric names) and put the pair in "
            "name-map.json, then convert again.",
        ],
    ),
    (
        re.compile(r"^window is"),
        "Datadog window outside the supported range",
        [
            "The importer converts windows from one minute to six hours: the New Relic "
            "aggregation window limit the owner's live test accepted. A longer window, such as "
            "one day, cannot be one aggregation window, and splitting it into several windows "
            "that must all breach is a different rule. Create the condition by hand with a "
            "6 hour window, or move the check to a dashboard or a scheduled query.",
        ],
    ),
    (
        re.compile(r"^(threshold is missing|comparison operator)"),
        "Datadog threshold",
        [
            "The monitor has no usable numeric threshold or comparison. Open the monitor in "
            "Datadog, read the critical and warning values, and create the condition by hand.",
        ],
    ),
    (
        re.compile(r"^log rollup"),
        "Datadog log rollup",
        [
            "The log monitor aggregates a measure (average, sum, minimum, maximum, a percentile or "
            "a cardinality) instead of counting lines. In New Relic that is SELECT average(attr) "
            "FROM Log, or uniqueCount(attr) for a cardinality; cardinality() is not supported in "
            "conditions. The attribute must exist in New Relic logs under a name you confirm.",
            "Write the NRQL by hand on the Log event and check the attribute with "
            "SELECT count(attr) FROM Log before creating the condition.",
        ],
    ),
    (
        re.compile(r"^(log search cannot|log window)"),
        "Datadog log search",
        [
            "The search uses syntax that cannot be one NRQL WHERE clause (an OR across attributes, "
            "NOT over a group, a ? wildcard, a negated range, a value that is not a number in a "
            "comparison) or the window is missing.",
            "Write the WHERE clause by hand on the Log event. Check the attribute names in a log "
            "record first: Datadog @attributes arrive under their JSON path without the @ when "
            "logs are forwarded, and reserved attributes (service, host, status) may be renamed.",
        ],
    ),
    (
        re.compile(r"^message contains \d+ @handles"),
        "Datadog notification handles",
        [
            "The message names Datadog notification handles (@slack-..., @pagerduty-..., "
            "@webhook-..., @team-..., e-mail addresses). The monitor JSON carries the names, "
            "never the URLs, keys or channel ids, so the destinations cannot be rebuilt from it. "
            "The handle names are not copied into the output.",
            "List the handles in Datadog, create a New Relic destination and channel for each "
            "(Slack and Teams need a one-time OAuth, PagerDuty needs the integration key, a "
            "webhook needs its URL and payload) and attach a workflow to the policy.",
        ],
    ),
]

register_rules(RULES)
