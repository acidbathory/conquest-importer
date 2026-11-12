"""The wording of every reason the Datadog source gives, in one place.

A reason that makes a monitor manual or unsupported starts with a fixed phrase that
`remediation_rules.py` and `taxonomy.py` anchor on, so a reworded sentence cannot silently lose its
manual steps. Text from the export (names, queries) is inserted only after that phrase, shortened
and stripped of control characters by `sanitize.shown`. Where a monitor family is approximate-
capable later, the reason names the milestone of docs/research/datadog-verified.md section 14.1 that
will build it; nothing named there exists yet.
"""

# -- manual: monitor families (section 3 and 11.1 of the research note) --------------------------
M_ANOMALY = (
    "anomaly monitor: Datadog compares each point with a learned band (algorithms basic, agile "
    "or robust) and fires when a fraction of the alert window is outside it; New Relic baseline "
    "conditions use a different model and a different trigger rule. A disabled baseline condition "
    "carrying these parameters is planned for milestone D5"
)
M_OUTLIER = (
    "outlier monitor: Datadog flags a series that departs from its peers (DBSCAN or MAD with a "
    "tolerance); New Relic outlier conditions take an absolute DBSCAN distance, so the tolerance "
    "does not transfer. DBSCAN is planned for milestone D5, MAD stays manual"
)
M_FORECAST = (
    "forecast monitor: Datadog alerts when a linear or seasonal forecast is predicted to cross "
    "the threshold; New Relic has predictive thresholds (predict_by) with a different model. "
    "Planned for milestone D5"
)
M_CHANGE = (
    "change alert: Datadog compares the value with the value an interval earlier (change or "
    "pct_change); a New Relic condition cannot compare two windows and NRQL COMPARE WITH is not "
    "allowed in conditions. No automatic conversion is planned; use a baseline condition or a "
    "fixed threshold"
)
M_CUMULATIVE = (
    "cumulative window: the current_* window grows from a calendar boundary and resets; a New "
    "Relic condition has only rolling windows, so there is no equivalent"
)
M_COMPOSITE = (
    "composite monitor: a boolean expression over other monitors; New Relic compound conditions "
    "(AND, OR, NOT over up to five conditions) need every component monitor converted first. "
    "Planned for milestone D5"
)
M_SERVICE_CHECK = (
    "service check monitor: counts statuses (ok, warning, critical) of a check that an agent "
    "reports; that data exists in New Relic only if the check is re-ingested. Decide whether a "
    "host-not-reporting condition, a synthetic monitor or an NRQL condition on re-ingested "
    "results replaces it"
)
M_EVENT = (
    "event monitor: alerts on Datadog events or Watchdog findings; New Relic has no source-side "
    "equivalent unless the events are sent as custom events. Decide what replaces it"
)
M_SLO = (
    "slo alert: alerts on the error budget or burn rate of a Datadog SLO; New Relic service levels "
    "have fast and slow burn alert conditions but the SLO object must be converted first. "
    "Approximate conversion is possible later; not built"
)
M_SYNTHETICS = (
    "synthetics monitor: alerts on a Datadog Synthetics test, which is defined in a separate "
    "export; recreate the test as a New Relic synthetic monitor and give it a condition"
)
M_PROCESS = (
    "process monitor: counts processes matching a search on a host; needs the infrastructure "
    "agent's process samples, which are not enabled by default (UNVERIFIED)"
)
M_RUM = (
    "rum monitor: alerts on Datadog Real User Monitoring events; the data exists in New Relic only "
    "after the browser or mobile agent replaces the Datadog RUM SDK"
)
M_TRACE = (
    "trace analytics monitor: alerts on Datadog APM spans or trace analytics; the data exists in "
    "New Relic only after an APM agent or OpenTelemetry tracing replaces the Datadog tracer"
)
M_CI = (
    "ci monitor: alerts on Datadog CI Visibility pipeline or test events, which have no New Relic "
    "counterpart without a separate integration"
)
M_AUDIT = (
    "audit monitor: alerts on Datadog Audit Trail events, which describe Datadog itself and have "
    "no New Relic counterpart"
)
M_ERROR_TRACKING = (
    "error tracking monitor: alerts on Datadog Error Tracking issues; New Relic errors inbox "
    "issues are separate objects with their own alerting"
)
M_DATABASE = (
    "database monitoring monitor: alerts on Datadog Database Monitoring query samples; New Relic "
    "database data comes from a different integration"
)
M_NETWORK = (
    "network monitor: alerts on Datadog network performance or network path data, which has no "
    "New Relic counterpart without a separate integration"
)
M_COST = (
    "cost monitor: alerts on Datadog Cloud Cost Management data, which has no New Relic "
    "counterpart in an alert condition"
)
M_DATA_OBSERVABILITY = (
    "data observability monitor: alerts on Datadog data quality or data jobs; the query grammar is "
    "not documented in the sources read (UNVERIFIED) and New Relic has no counterpart"
)
M_LLM = (
    "llm observability monitor: alerts on Datadog LLM Observability data; the query grammar is not "
    "documented in the sources read (UNVERIFIED)"
)
M_UNKNOWN_TYPE = "monitor type is not one Datadog documents, so its query cannot be read"
M_NO_TYPE = "monitor has no readable type or query"

# -- manual: query reading and metric monitors ---------------------------------------------------
M_UNREADABLE = "query could not be read"
M_NOT_LOSSLESS = "query was read only in part, so it cannot be converted without guessing"
M_FUNCTION = "query function"
M_AS_RATE = (
    "query function: as_rate() divides by the sampling interval; rate() inside a New Relic "
    "condition is UNVERIFIED. Planned for milestone D5"
)
M_AS_COUNT_KIND = (
    "query function: as_count() on a {kind} metric needs an interval conversion that New Relic "
    "does not do; set the metric kind to count in name-map.json if it arrives as a delta count"
)
M_FORMULA_MONITOR = (
    "multi-query formula monitor: the formula refers to named sub-queries in options.variables "
    "(or formula() in the query); a New Relic condition holds one query. Planned for milestone D5"
)
M_SCOPE = "scope cannot be written as a New Relic WHERE clause"
M_TEMPLATE_VAR = "scope uses a template variable"
M_GROUP_MISMATCH = "queries in the formula group by different tags, so no single FACET fits them"
M_MIXED_EVENTS = "queries in the formula live on different New Relic events"
M_TAG = "tag cannot be written as a New Relic attribute"
M_METRIC_NAME = "metric name cannot be written as a New Relic name"
M_NO_METRIC = "query has no metric"
M_WINDOW_LONG = "window is longer than the six hours New Relic accepts for an aggregation window"
M_WINDOW_SHORT = "window is shorter than one minute"
M_THRESHOLD = "threshold is missing or not a number"
M_OPERATOR = "comparison operator is not supported"

# -- manual: log monitors ------------------------------------------------------------------------
M_LOG_ROLLUP = "log rollup: {what} is not converted yet; only count is. Planned for milestone D5"
M_LOG_SEARCH = "log search cannot be written as a New Relic WHERE clause"
M_LOG_WINDOW = "log window is missing or not a duration"

# -- approximate: notes that stay on a converted monitor -----------------------------------------
N_NAMES_UNVERIFIED = (
    "metric names are UNVERIFIED: no source says how a Datadog metric is named in New Relic. The "
    "same name on the Metric event is assumed (it holds for the OpenTelemetry and StatsD routes); "
    "run check-datadog-data, and put corrections in name-map.json"
)
N_NAMES_MAPPED = "metric names come from name-map.json; the data check has not confirmed them yet"
N_ATTRS_UNVERIFIED = (
    "tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name "
    "({names}); the OpenTelemetry route may rename some (env, service, host)"
)
N_LOG_ATTRS_UNVERIFIED = (
    "log attribute names are UNVERIFIED: {names} are assumed to arrive under the same name "
    "without the leading @; Datadog reserved attributes (service, host, status) may differ"
)
N_TIME_AGGR = (
    "the time aggregator {time} is applied to raw points in New Relic, to space-aggregated "
    "rollup points in Datadog; the space aggregator {space} is not applied separately"
)
N_RATIO = (
    "the formula is evaluated on window aggregates (a ratio of averages or sums); Datadog "
    "evaluates it point by point and then aggregates over time"
)
N_AS_COUNT = (
    "as_count() is read as a sum of delta counts; the metric kind is not known (set it in "
    "name-map.json) and delta arrival is UNVERIFIED"
)
N_KIND = (
    "the metric is a {kind} but the query has no as_count() or as_rate(); Datadog and New Relic "
    "may read it per second and per interval respectively"
)
N_SLIDE = (
    "Datadog evaluates the window every minute; a sliding window of 60s is used, which costs "
    "extra data points and has not been tried against New Relic (UNVERIFIED)"
)
N_NO_SLIDE = (
    "Datadog evaluates the window every minute; a New Relic aggregation window of {window}s is "
    "evaluated once per window, so a breach can open up to {window}s later"
)
N_LONG_WINDOW = (
    "the {window}s window is longer than an hour: aggregation windows above 3600s were accepted "
    "in the owner's live test but are not on the NRQL conditions page, and no violation can open "
    "until one full window has passed (UNVERIFIED here)"
)
N_DELAY_CLAMPED = (
    "evaluation_delay {delay}s is above the {limit}s New Relic accepts for streaming conditions; "
    "{limit}s is used"
)
N_NO_DATA = (
    "no data: Datadog notifies after {minutes} minutes without data; a New Relic condition can "
    "open a violation on loss of signal only after the signal has existed (UNVERIFIED for groups)"
)
N_NO_DATA_CLAMPED = (
    "no_data_timeframe {minutes} minutes is above the 48 hours New Relic accepts; 48 hours is used"
)
N_RESOLVE_NO_DATA = "on_missing_data is {value}: the effect on an empty window is not converted"
N_PRIORITY_WARNING = (
    "priority P{level} is recorded in the description; the only threshold became a warning term "
    "because P3 to P5 monitors should not page as critical (a policy choice, change it if wrong)"
)
N_PRIORITY = (
    "priority P{level} is recorded in the description and not mapped to a New Relic setting"
)
N_INDEX = "log index {index} is ignored: New Relic has no log index in a condition"
N_LOG_CASE = (
    "Datadog log search and a New Relic WHERE clause differ in case handling and in how free text "
    "is split into words; compare the counts on a sample (UNVERIFIED)"
)
N_LOG_FREE_TEXT = (
    "free text in the search became a LIKE match on message, which is looser or stricter than "
    "Datadog's word match"
)
N_LOG_NULL = (
    "the count is wrapped in filter() so an empty window is 0 as in Datadog; with a plain count "
    "New Relic returns NULL and a below or equals rule would not evaluate"
)
N_MESSAGE_HANDLES = (
    "message contains {count} @handles: destinations are not created from them (milestone D4); "
    "each one must be configured by hand and the handle names are not carried into the output"
)
N_CRITICAL_DIFFERS = "critical threshold in options differs from the query; using the query"
N_SCALE = "a unit scale of {scale} from name-map.json was applied to the threshold"

N_DEFAULT_ZERO = (
    "default(x, 0) is a static fill of 0 for empty windows of the signal (approximate: Datadog "
    "fills gaps in its own evaluation points, New Relic in its aggregation windows)"
)
