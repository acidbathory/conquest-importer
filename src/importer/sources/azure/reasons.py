"""The wording of every reason the Azure source gives, in one place.

Each reason that makes a rule manual starts with a fixed phrase that `remediation_rules.py` and
`taxonomy` rules anchor on, so a reworded sentence cannot silently lose its manual steps. Text
from the rule itself (names, queries) is inserted only after that phrase and is shortened and
stripped of control characters by `shown()`.
"""

from importer.core.text import printable


def shown(text: object, limit: int = 120) -> str:
    """Untrusted text for a reason: printable, short, on one line."""
    return " ".join(printable(str(text), limit).split())


# -- manual: metric alerts -----------------------------------------------------------------------
M_SEVERAL = (
    "metric alert has several criteria: Azure fires only when all of them are true and New "
    "Relic has no composite condition"
)
M_DYNAMIC = (
    "dynamic threshold: Azure learns the normal range with its own model and sensitivity levels "
    "that New Relic baseline conditions do not match"
)
M_WEBTEST = "availability test alert: Azure counts failing test locations, which has no mapping"
M_CRITERIA_TYPE = "unknown metric alert criteria type"
M_NO_CRITERIA = "metric alert has no readable criteria"
M_CUSTOM_NAMESPACE = (
    "metric namespace is not an Azure platform namespace: the Azure Monitor integration does not "
    "collect custom or guest metrics"
)
M_APP_INSIGHTS = (
    "scope is Application Insights: the data comes from an APM agent or OpenTelemetry, not from "
    "the Azure Monitor integration"
)
M_NO_NAME = "metric name cannot be written as a New Relic name"
M_WINDOW_LONG = "window is longer than the six hours New Relic accepts for an aggregation window"
M_WINDOW_MISSING = "window or evaluation frequency is missing or not an ISO 8601 duration"
M_WINDOW_SHORT = "window is shorter than one minute"
M_SCOPE = "scope is not a resource, resource group or subscription id"
M_SCOPE_SEVERAL = (
    "scope lists several resource groups or subscriptions: one New Relic condition matches one "
    "resource id prefix"
)
M_OPERATOR = "comparison operator is not supported"
M_THRESHOLD = "threshold is not a number"
M_TIME_AGG = "time aggregation is not supported"
M_DIMENSION = "dimension cannot be written as a New Relic attribute"

# -- manual: log alerts --------------------------------------------------------------------------
M_KQL = "kql query cannot be converted"
M_KQL_TABLE = "kql table comes from an agent or service that New Relic does not read"
M_LOG_SCOPE = "log alert scope is not data that New Relic receives"
M_LOG_KIND = "scheduled query rule kind is not an alert that can be converted"
M_LOG_TO_METRIC = "scheduled query rule publishes a metric and is not an alert"
M_LOG_SEVERAL = "log alert has several conditions"
M_LOG_LEGACY = "legacy log alert rule shape"
M_LOG_RANGE = "log alert evaluates a longer range than its window"
M_LOG_NO_CRITERIA = "log alert has no readable condition"
M_LOG_MEASURE = "log alert measures a column that cannot be converted"

# -- manual: other families ----------------------------------------------------------------------
M_ACTIVITY = (
    "activity log alert: Azure fires on every matching activity event and New Relic has no "
    "source for Azure activity events until they are forwarded as logs"
)
M_SERVICE_HEALTH = (
    "service health or resource health alert: the event comes from Azure and is not in New Relic"
)
M_SMART = (
    "smart detector alert rule: Azure runs a machine-learning detector that New Relic does not have"
)
M_PROM = "prometheus rule is not converted"
M_PROM_RECORD = "recording rule: an Azure recording rule is not an alert"
M_UNKNOWN_FAMILY = "alert rule type is not recognised"

# -- notes on a converted rule -------------------------------------------------------------------
N_NAME_DERIVED = (
    "metric name {name} is derived from the documented naming rule for the Azure Monitor "
    "integration and is UNVERIFIED: it has not been seen arriving; run check-azure-data and, if "
    "it differs, put the real name in name-map.json"
)
N_NAME_MAPPED = (
    "metric name {name} comes from name-map.json and has not been checked against New Relic; "
    "run check-azure-data"
)
N_ATTR_DERIVED = (
    "attribute names {names} are derived and UNVERIFIED: the dimension attribute naming is "
    "documented only by examples; check one metric in the data explorer or use name-map.json"
)
N_LATENCY = (
    "Azure evaluates this rule every {freq}s over the last {window}s; New Relic reads polled "
    "Azure Monitor data (at best every minute) with the event timer and so opens later than "
    "Azure, and windows are aligned to the clock rather than to the rule"
)
N_LATENCY_LOG = (
    "Azure evaluates this rule every {freq}s over the last {window}s; the logs reach New Relic "
    "through an Event Hub forwarder with a delay, so it opens later than Azure"
)
N_LOG_NULL_OPEN = (
    "a count over an empty window is NULL in New Relic, so a below or equals rule does not "
    "evaluate when no log arrives; this query has no filter to make it 0"
)
N_LONG_WINDOW = (
    "the {window}s window is longer than an hour: a New Relic condition has to collect one full "
    "window before it can open, so after creation or any edit no violation can open until a full "
    "{window}s window has passed. The NerdGraph schema allows aggregation windows up to 6 hours "
    "(21600s) while the NRQL conditions page says 120 minutes; the creation call has not been "
    "tried (UNVERIFIED), and a rejected condition should be set to 120 minutes by hand"
)
N_SLIDE = (
    "Azure evaluates every {freq}s over a {window}s window; a sliding window of {freq}s is used, "
    "which costs extra data points"
)
N_FREQ_MISMATCH = (
    "Azure evaluates every {freq}s over a {window}s window; New Relic evaluates consecutive "
    "{window}s windows, so the evaluation times differ"
)
N_SEVERITY = (
    "Azure severity {label} is written as New Relic {level}: five levels collapse to two, and "
    "the original severity is in the description"
)
N_SEVERITY_BAD = "severity {value} is not 0 to 4; it was converted as a warning"
N_AUTO_MITIGATE = (
    "autoMitigate is false: Azure notifies on every evaluation while the rule is breached and "
    "never resolves; New Relic opens one violation and notifies on its transitions"
)
N_TIME_TO_RESOLVE = (
    "resolveConfiguration.timeToResolve {value} was ignored: New Relic closes a violation at "
    "the first healthy window"
)
N_COUNT = (
    "time aggregation Count is written as count(): it counts the data points New Relic holds, "
    "which may not equal the number of samples Azure counts"
)
N_TOTAL = (
    "time aggregation Total is written as sum(); if New Relic stores the metric as a gauge, "
    "check the value against Azure"
)
N_RESOURCE_ID = (
    "azure.resourceId is matched exactly as Azure wrote it; the letter case New Relic stores is "
    "UNVERIFIED"
)
N_REGION = "targetResourceRegion {region} was not applied: add a WHERE on azure.region if needed"
N_DIM_SPLIT = (
    "dimension {name} splits the alert by value, as Azure does; New Relic raises one signal per "
    "value through FACET"
)
N_DIM_DOUBLE = (
    "the same metric can arrive with and without a dimension; if sum() or count() looks too "
    "high, add IS NULL on the dimension attribute"
)
N_DISABLED = "the Azure rule is disabled; the New Relic condition is created disabled as well"
N_MUTE = (
    "muteActionsDuration {value} has no condition setting; the same pause is a muting rule or a "
    "workflow setting in New Relic"
)
N_FILTER_CASE = (
    "KQL contains and has ignore case; the New Relic LIKE match may not, so check the matches"
)
N_LOG_ATTRIBUTES = (
    "log attribute names {names} are columns of the Azure table and UNVERIFIED as attributes of "
    "the forwarded New Relic Log event; inspect one forwarded record or use name-map.json"
)
N_LOG_TABLE = (
    "the table {table} has no counterpart in New Relic; the filter relies on the forwarded Azure "
    "logs (plugin.type azure) and the attributes above"
)
N_LOG_FORWARD = (
    "the logs must reach New Relic through the Event Hub forwarder with a diagnostic setting on "
    "the resource; without them this condition never fires"
)
N_LOG_NULL = (
    "a count over an empty window is NULL in New Relic; the count is wrapped so an empty window "
    "gives 0"
)
N_FAILING = (
    "{m} failing periods out of {n}: New Relic needs {m} consecutive windows, which is stricter "
    "than Azure counting non-consecutive ones"
)
N_RECORD_GROUP = (
    "the rule group has {count} recording rule(s); an expression that uses a recorded name needs "
    "the recorded expression written in place"
)
N_PROM_NAMES = (
    "metric names are read as Prometheus names: this condition only returns data if the "
    "metric reaches New Relic through Prometheus remote write or an OpenTelemetry collector "
    "(Azure managed Prometheus has no documented export to New Relic)"
)
