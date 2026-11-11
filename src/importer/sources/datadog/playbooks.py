"""Playbooks for the Datadog remediation rules, same titles as `remediation_rules.py`.

All text is fixed; none of it comes from an export. Effort figures are rough planning numbers.
"""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L


def _reg(title: str, **fields: object) -> None:
    register_playbook(title, Playbook(**fields))  # type: ignore[arg-type]


CREATE = (
    "Create the condition: Alerts > Alert conditions > + New alert condition > Write your own "
    "query. Create it disabled, compare it with the Datadog monitor for a day, then enable it."
)

_reg(
    "Datadog anomaly monitor",
    source="A metric monitor that fires when part of the alert window is outside a band that "
    "Datadog learned with the basic, agile or robust algorithm.",
    newrelic="A baseline NRQL condition on the same query, tuned by hand.",
    steps=(
        "Write the NRQL for the metric (the report shows the inner query of the monitor).",
        "Create a baseline condition on it, disabled, with direction upper only, lower only or "
        "both as the Datadog direction says.",
        "Copy the Datadog algorithm, bounds, alert window and seasonality into the description.",
        "After one to four weeks of history, tune the standard deviations and enable it.",
    ),
    differs="Different model, no fraction-of-window rule, and no training on FACET queries from "
    "stored data. Thresholds are not transferable.",
    effort_minutes=45,
    links=(L.ANOMALY, L.CONDITION),
)
_reg(
    "Datadog outlier monitor",
    source="A grouped monitor that fires when one series departs from the group (DBSCAN or MAD).",
    newrelic="An outlier condition (DBSCAN) on the grouped query; none for MAD.",
    steps=(
        "Write the grouped NRQL query with FACET on the group tag.",
        "Create an outlier condition on it and pick the distance by looking at history.",
        "For MAD, alert on each series divided by the group average in a static condition.",
    ),
    differs="The Datadog tolerance is a multiplier on a derived threshold; the New Relic distance "
    "is absolute.",
    effort_minutes=60,
    links=(L.ANOMALY, L.CONDITION),
)
_reg(
    "Datadog forecast monitor",
    source="A monitor that fires when a forecast is predicted to cross the threshold.",
    newrelic="A static condition with a prediction horizon (predict by).",
    steps=(
        "Write the NRQL for the metric.",
        "Create a static condition with the Datadog threshold and a prediction horizon equal to "
        "the forecast window.",
        "Run it next to the Datadog monitor for a week.",
    ),
    differs="Linear versus seasonal models and the 'any part of the bounds' rule differ; "
    "prediction is a beta setting in some accounts.",
    effort_minutes=45,
    links=(L.CONDITION, L.STREAMING),
)
_reg(
    "Datadog change alert",
    source="A monitor on the change (absolute or percent) of a value against an earlier interval.",
    newrelic="No condition compares two windows. A baseline condition or a static threshold "
    "on the level; COMPARE WITH only in dashboards.",
    steps=(
        "Ask what the change was meant to catch: a spike, a drop or a level.",
        "Spike or drop: a baseline condition on the metric. Level: a static threshold.",
        "Keep the percent change on a dashboard billboard with COMPARE WITH.",
    ),
    differs="The condition looks at the value, not at the change from an earlier value.",
    effort_minutes=30,
    no_equivalent=True,
    links=(L.ANOMALY, L.NRQL),
)
_reg(
    "Datadog cumulative window",
    source="A monitor on a window that grows from a calendar boundary and resets.",
    newrelic="A rolling window of the same length is the closest.",
    steps=(
        "Pick the rolling window that covers the period (for example 1 hour for current_1h).",
        "Create the static condition on it and accept that it does not reset at the boundary.",
    ),
    differs="A rolling window drops old data continuously; the cumulative window drops it all "
    "at once.",
    effort_minutes=20,
    no_equivalent=True,
    links=(L.CONDITION, L.STREAMING),
)
_reg(
    "Datadog composite monitor",
    source="A boolean expression over the status of other monitors.",
    newrelic="A compound condition over up to five standard conditions in one account.",
    steps=(
        "Convert every component monitor first and note their New Relic condition names.",
        "In the UI, create a compound condition from the components with AND, OR or NOT.",
        "Check that the component conditions use the same facets if matching is wanted.",
    ),
    differs="Datadog && returns the least severe status and || the most severe; New Relic treats "
    "any threshold of a component as triggered.",
    effort_minutes=45,
    links=(L.CONDITION, L.ALERT_LIMITS),
)
_reg(
    "Datadog service check",
    source="A monitor on the statuses a Datadog agent check reports, per host or tag group.",
    newrelic="A host-not-reporting condition, a synthetic monitor, or an NRQL condition on "
    "check results sent as custom events.",
    steps=(
        "Find what the check observes (a host, a port, an HTTP endpoint).",
        "Host liveness: create a host-not-reporting condition. Endpoint: create a synthetic "
        "monitor. Anything else: send results as custom events.",
        "'Last N runs' is a threshold duration of N windows with all windows breaching.",
        CREATE,
    ),
    differs="The check statuses do not exist in New Relic unless they are sent.",
    effort_minutes=60,
    links=(L.CONDITION, L.STREAMING),
)
_reg(
    "Datadog event monitor",
    source="A monitor on Datadog events or Watchdog findings.",
    newrelic="A condition on the system the events describe, or on custom events.",
    steps=(
        "Open the monitor and note what produced the events.",
        "Alert on that system's data directly, or send the events as custom events.",
        CREATE,
    ),
    differs="Watchdog is a Datadog detector and has no New Relic equivalent.",
    effort_minutes=45,
    no_equivalent=True,
    links=(L.CONDITION, L.NRQL),
)
_reg(
    "Datadog SLO alert",
    source="A monitor on the error budget or burn rate of a Datadog SLO.",
    newrelic="A service level with fast and slow burn rate alert conditions.",
    steps=(
        "Create the service level from the SLO's good and total event queries.",
        "Add burn rate alert conditions for the same windows.",
        "Check whether a budget-consumed condition is available in your account.",
    ),
    differs="Budget calculation windows and the burn rate thresholds differ.",
    effort_minutes=60,
    links=(L.SLM, L.CONDITION),
)
_reg(
    "Datadog synthetics monitor",
    source="A monitor on a Datadog Synthetics test.",
    newrelic="A New Relic synthetic monitor with an alert condition on its failures.",
    steps=(
        "Recreate the test as a synthetic monitor from the same URL or script and locations.",
        "Add a condition on failed checks; set the failing locations from min_location_failed.",
    ),
    differs="Check scheduling and location names differ.",
    effort_minutes=45,
    links=(L.CONDITION,),
)
_reg(
    "Datadog product-specific monitor",
    source="A monitor on data that a Datadog product collects (RUM, APM traces, CI, audit, "
    "database, network, cost, processes, data quality, LLM).",
    newrelic="Decided per product: browser or mobile agent, APM or OpenTelemetry spans, the "
    "infrastructure agent, or none.",
    steps=(
        "Decide which New Relic product replaces the Datadog one.",
        "Send the data to New Relic and find its event type in the data explorer.",
        "Write the NRQL condition on that event type.",
        CREATE,
    ),
    differs="The data is Datadog-specific; names and meaning change with the product.",
    effort_minutes=90,
    no_equivalent=True,
    links=(L.CONDITION, L.OTLP),
)
_reg(
    "Datadog monitor of an unknown type",
    source="A monitor type this importer does not know, or one without a readable query.",
    newrelic="Decided case by case.",
    steps=("Open the monitor in Datadog and read what it protects.", CREATE),
    differs="Unknown.",
    effort_minutes=30,
    links=(L.CONDITION,),
)
_reg(
    "Datadog query that could not be read",
    source="A monitor query the parser could not read completely.",
    newrelic="The same check written by hand in NRQL.",
    steps=(
        "Read the reason: it names the first problem (length, depth, missing comparison, syntax).",
        "Write the NRQL for the metric or the logs by hand.",
        CREATE,
    ),
    differs="Nothing was guessed, so nothing was converted.",
    effort_minutes=30,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Datadog query function",
    source="A metric query that uses a function beyond a plain aggregate.",
    newrelic="Usually expressible in NRQL (rate(), derivative(), filter(), a fill value), but "
    "not always inside a condition.",
    steps=(
        "Per second values: rate(sum(<metric>), 1 second) or derivative(<metric>, 1 second); "
        "whether they are accepted inside a condition is UNVERIFIED, test it.",
        "Empty windows: filter(count(*), WHERE ...) or a static fill value.",
        "Ranking and LIMIT are not allowed in a condition: alert per facet instead.",
        CREATE,
    ),
    differs="Order of time and space aggregation and interpolation differ, so numbers can differ "
    "for the same data.",
    effort_minutes=30,
    links=(L.NRQL, L.CONDITION, L.STREAMING),
    template="SELECT average(<metric>) FROM Metric FACET <labels>",
)
_reg(
    "Datadog multi-query formula monitor",
    source="A monitor that evaluates a formula over named sub-queries.",
    newrelic="One NRQL query with several selects combined by arithmetic.",
    steps=(
        "List the sub-queries and the formula.",
        "If they read the same event, write one SELECT with the arithmetic and a guard for "
        "division by zero.",
        "Otherwise create one condition per sub-query and decide how they combine.",
        CREATE,
    ),
    differs="Datadog evaluates the formula point by point; NRQL combines window aggregates.",
    effort_minutes=45,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Datadog scope or grouping",
    source="A scope or group-by that is not a list of equalities.",
    newrelic="A hand-written WHERE clause and FACET.",
    steps=(
        "Write the WHERE with AND, OR and LIKE as the scope says.",
        "Put the group tags in FACET (at most 5 in a widget, up to 20000 values in a condition).",
        "A template variable stands for one value in Datadog: substitute it.",
        CREATE,
    ),
    differs="A tag spelled the same may arrive under another attribute name.",
    effort_minutes=25,
    links=(L.NRQL, L.CONDITION),
)
_reg(
    "Datadog metric name",
    source="A metric whose name cannot be an NRQL name.",
    newrelic="The same metric under the name it arrives with.",
    steps=(
        "Search the data explorer for the metric.",
        "Add the pair to name-map.json and convert again.",
    ),
    differs="Names depend on the route the data took.",
    effort_minutes=15,
    links=(L.NRQL, L.OTLP),
)
_reg(
    "Datadog window outside the supported range",
    source="A monitor window longer than six hours or shorter than a minute.",
    newrelic="No aggregation window that long; a dashboard or a scheduled query for daily counts.",
    steps=(
        "Create a condition with a 6 hour window and a threshold scaled to it, or move the "
        "check to a dashboard.",
    ),
    differs="Several short windows that must all breach are not one long window.",
    effort_minutes=30,
    no_equivalent=True,
    links=(L.STREAMING, L.CONDITION),
)
_reg(
    "Datadog threshold",
    source="A monitor without a usable numeric threshold or comparison.",
    newrelic="A static condition with the values read from Datadog.",
    steps=("Read the critical and warning values in Datadog.", CREATE),
    differs="None.",
    effort_minutes=15,
    links=(L.CONDITION,),
)
_reg(
    "Datadog log rollup",
    source="A log monitor that aggregates a measure instead of counting lines.",
    newrelic="SELECT average(attr) FROM Log (or sum, min, max, percentile, uniqueCount).",
    steps=(
        "Check that the attribute exists in New Relic logs.",
        "Write the NRQL on the Log event with the aggregate and the facets.",
        "cardinality is uniqueCount, which is not supported in conditions.",
        CREATE,
    ),
    differs="Percentiles and cardinality are computed differently.",
    effort_minutes=30,
    links=(L.NRQL, L.CONDITION),
    template="SELECT average(<metric>) FROM Log FACET <labels>",
)
_reg(
    "Datadog log search",
    source="A log search that is more than ANDed equalities.",
    newrelic="A hand-written WHERE clause on the Log event.",
    steps=(
        "Open one log record in New Relic and note the attribute names.",
        "Translate the search to WHERE with AND, OR, NOT and LIKE.",
        CREATE,
    ),
    differs="Case handling and free-text splitting differ.",
    effort_minutes=30,
    links=(L.NRQL, L.PARSING),
)
_reg(
    "Datadog notification handles",
    source="@handles in the monitor message that notify people and tools.",
    newrelic="Destinations and channels in a workflow attached to the policy.",
    steps=(
        "List the handles in the Datadog message (the importer does not copy them).",
        "Create a destination for each: e-mail, Slack, PagerDuty, webhook, Teams.",
        "Create a workflow on the policy that uses the channels.",
    ),
    differs="Slack and Teams need a one-time OAuth; secrets are never in the export.",
    effort_minutes=30,
    links=(L.DESTINATIONS, L.WORKFLOWS, L.INTEGRATIONS),
)
