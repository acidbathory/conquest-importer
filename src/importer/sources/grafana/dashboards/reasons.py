"""Every reason the dashboard coverage plan can give, as fixed text.

Reasons never contain text from the dashboard: a panel type or data source type appears only as
a label from the tables in this package. Each reason starts with `dashboard ` so the anchored
taxonomy and remediation rules of this package cannot claim another source's reasons.
"""

# --- panels ---
P_NO_WIDGET = "dashboard panel type {label} has no New Relic widget"
P_WIDGET_GAP = (
    "dashboard panel type {label} maps to a New Relic widget the importer cannot create yet"
)
P_TRANSFORM = (
    "dashboard panel has transformations, which run in the browser and cannot be reproduced "
    "in a query"
)
P_LIBRARY = "dashboard panel is a library panel that is not in the export, so its query is unknown"
P_NO_QUERY = "dashboard panel has no query to convert"
P_EMPTY_QUERY = "dashboard query is empty or is not text"
P_EXPRESSION = "dashboard panel uses a server-side expression over other queries"
P_REUSE = "dashboard panel reuses the result of another panel that is missing or does not convert"
P_DS_UNSUPPORTED = (
    "dashboard data source {label} is not supported; only prometheus and loki convert"
)
P_DS_UNRESOLVED = (
    "dashboard data source could not be told from the panel (a template variable with no "
    "known value, or no reference)"
)

P_VARIABLE = "dashboard panel uses a variable that stays manual, so its filter cannot be written"
P_TIME_OVERRIDE = "dashboard panel overrides the time range (timeFrom or timeShift)"
P_OPTIONS = (
    "dashboard panel has options that change what it shows (value mappings, overrides, "
    "a unit conversion or a calculation other than the latest value), and its query is already "
    "at the highest tier"
)

# notes on a panel that converts; they are not reasons
N_INSTANT = (
    "note: an instant query or table format gives one value per series, not a chart over time"
)
N_INTERVAL = "note: the query sets its own interval; New Relic uses an automatic bucket"
N_LEGEND = "note: legend text around {{labels}} is lost; the facet value is the legend"
N_DOWNGRADED = (
    "note: value mappings, overrides, a unit or a reducer change what it shows, "
    "so the tier is one lower"
)

# --- queries (PromQL) ---
Q_TOO_LONG = "dashboard query is longer than the importer reads"
Q_VAR_FORMAT = "dashboard query uses a variable format such as ${name:csv}"
Q_OFFSET = "dashboard query uses offset"
Q_SUBQUERY = "dashboard query uses a subquery"
Q_VECTOR = "dashboard query uses vector matching (on, ignoring, group_left or group_right)"
Q_BOTTOMK = "dashboard query uses bottomk or limitk"
Q_TOPK_ARG = "dashboard query uses topk with a count that is not a plain number"
Q_ABSENT = "dashboard query uses absent()"
Q_OVER_TIME = "dashboard query uses an _over_time function"
Q_RANGE_FN = "dashboard query uses a range function other than rate, irate and increase"
Q_DELTA = (
    f"{Q_RANGE_FN}: delta() of a gauge is the last sample minus the first, stretched to the "
    "window, which latest() minus earliest() does not reproduce"
)
Q_WITHOUT = "dashboard query uses without()"
Q_COMPARE = "dashboard query uses a comparison, a set operator or an operator inside an aggregation"
Q_LABEL_FN = "dashboard query rewrites labels with label_replace or label_join"
Q_FUNCTION = "dashboard query uses a function with no New Relic counterpart in the converter"
Q_TIME = (
    f"{Q_FUNCTION}: time() is the moment of evaluation, and a chart has no such value per bucket"
)
Q_ROUND_STEP = (
    f"{Q_FUNCTION}: round() to a multiple that is not 1 or a power of ten (NRQL rounds to "
    "decimal places)"
)
Q_NESTED = "dashboard query aggregates an aggregation"
Q_COUNTER = "dashboard query reads a counter metric (_total, _count, _sum or _bucket) without rate"
Q_REGEX = (
    "dashboard query has a regex matcher that RLIKE cannot carry exactly (it matches an empty or "
    "missing label value, or uses syntax outside the checked subset)"
)
Q_BINARY_LABELS = "dashboard query combines series that are grouped by different labels"
Q_GROUP_VARIABLE = "dashboard query groups by a variable"
Q_MATCHER = "dashboard query has a label matcher the converter does not read"
Q_NAME = "dashboard query has a metric or label name that is not valid in NRQL"
Q_HISTOGRAM = "dashboard query uses histogram_quantile in a shape the converter does not read"
Q_RATE_SHAPE = "dashboard query uses rate, irate or increase on something other than a selector"
Q_NOT_SIMPLE = "dashboard query is not a shape the converter reads"

# --- queries (LogQL) ---
QL_PARSER = "dashboard logql query uses a parser stage or unwrap"
QL_FUNCTION = "dashboard logql query uses a function other than count_over_time and rate"
QL_REGEX = "dashboard logql query uses a regular-expression line filter that is not a plain word"
QL_WILDCARD = "dashboard logql query has line filter text with a wildcard character (* or %)"
QL_MATCHER = "dashboard logql query has a label matcher the converter does not read"
QL_NOT_SIMPLE = "dashboard logql query is not a count of a stream selector with line filters"

# --- variables ---
V_QUERY_REGEX = (
    "dashboard variable regex uses a pattern the converter cannot verify (lookahead, "
    "a backreference, a capture group or any regex beyond plain words)"
)
V_QUERY_SHAPE = (
    "dashboard variable query is not label_values(), query_result(selector) or metrics(), "
    "so it has no uniques() form"
)
V_QUERY_RESULT = (
    "dashboard variable query is query_result() of something other than a selector or one "
    "grouped selector (a function, an or, a topk), or has no regex that picks one label value"
)
V_QUERY_RESULT_LABEL = (
    "dashboard variable query_result() groups by one label but its regex captures a different "
    "label, so the values are not the grouped ones"
)
V_QUERY_DS = "dashboard variable query runs on a data source other than prometheus"
V_NESTED = "dashboard variable query depends on a variable that stays manual"
V_CYCLE = "dashboard variable query depends on itself or on a cycle of variables"
V_ADHOC = "dashboard variable is an ad hoc filter or group-by, which has no New Relic variable"
V_NAME = "dashboard variable name is not letters, digits and underscore"
V_DUPLICATE = "dashboard variable name is used twice"
V_KIND = "dashboard variable type is not one the converter knows"

# --- dashboard level ---
A_DROPPED = (
    "dashboard annotations are not carried over; New Relic dashboards have no annotation layer"
)

# word shown for a panel type or data source type that is not in the tables
OTHER_LABEL = "other"
