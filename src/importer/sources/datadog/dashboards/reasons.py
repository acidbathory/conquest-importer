"""The wording of every reason the Datadog dashboard converter gives.

Each reason that makes a widget a placeholder starts with a fixed phrase that `rules.py` anchors
its remediation and taxonomy rules on. Text taken from the export (type names, tags, variable
names) is added after the phrase and only through `shown()`. Reasons that the monitor translator
already words (scope, tag and metric name problems) are reused from `datadog.reasons`.
"""

from importer.sources.datadog.sanitize import shown

__all__ = ["shown"]

# -- placeholders: the widget ----------------------------------------------------------------
W_TYPE = "dashboard widget type has no converter yet"
W_NO_QUERY = "dashboard widget has no query"
W_DATA_SOURCE = "dashboard widget reads {source}, not metrics; only metric queries are converted"
W_SEVERAL = (
    "dashboard widget has several queries or requests; only a single metric query is converted"
)
W_FORMULA = "dashboard widget uses a formula over its queries, which is not converted yet"
W_FORMULA_FUNC = (
    "dashboard widget uses a formula with the function {name}(), which has no single-SELECT form"
)
W_FORMULA_NAME = "dashboard widget uses a formula that names a query that does not exist: {name}"
W_FORMULA_UNREADABLE = "dashboard widget uses a formula that could not be read: {why}"
W_FORMULA_GROUP = (
    "dashboard widget uses a formula over queries that group by different tags, which NRQL "
    "cannot combine in one query"
)
W_FORMULA_EVENTS = (
    "dashboard widget uses a formula over metrics on different New Relic events, which NRQL "
    "cannot join"
)
W_FORMULA_QUERY = "dashboard widget uses a formula over a query with {what}, which is not combined"
W_FUNCTION = "dashboard query function is not converted yet: {name}()"
W_NO_FORM = "dashboard query function has no New Relic form: {name}() ({why})"
W_FUNC_ARGS = "dashboard query function: {name}() {why}"
W_ARITHMETIC = "dashboard query uses arithmetic between series, which is not converted yet"
W_UNREADABLE = "dashboard query could not be read"
W_AGGREGATOR = (
    "dashboard query_value aggregator {name} is not converted: New Relic has no value that is "
    "the last point of a series combined across hosts"
)
W_REJECTED = "dashboard widget was refused by the New Relic dashboard model: {why}"
W_QV_GROUP = "dashboard query_value groups by a tag, which a single value cannot show"

# -- placeholders: template variables --------------------------------------------------------
V_UNDEFINED = "dashboard template variable is not defined: ${name}"
V_NAME = "dashboard template variable name cannot be a New Relic variable name: {name}"
V_FORM = "dashboard template variable is used in a form that is not converted: {what}"
V_TAGS = "dashboard template variable stands for two different tags: {what}"
V_DEFAULT = "dashboard template variable default is not a scope that can be converted: {name}"
V_INVALID = "dashboard template variable is not accepted as a New Relic variable: {name}"

# -- notes on converted widgets --------------------------------------------------------------
N_FORMULA = (
    "dashboard formula is written as one SELECT of window aggregates; Datadog combines the "
    "series point by point, so a ratio can differ (compare both on a day of data)"
)
N_RATE = (
    "rate functions are written as rate(sum(x), 1 second), which is right for a metric that "
    "arrives as counts; set the metric kind to count in name-map.json if it does"
)
N_SPACE_SUM = (
    "the space aggregator {aggr} was replaced by sum for a count or rate; the numbers are the "
    "same when each group is one series, and larger by the number of series otherwise"
)
N_ROLLUP_AUTO = (
    "rollup() bucket size is not carried over; New Relic chooses the bucket with TIMESERIES AUTO"
)
N_FILL = "fill() is not carried over; New Relic shows a gap where there is no data"
N_TOP = (
    "top() is a FACET with a LIMIT; Datadog ranks the series over the widget's time range with "
    "the function given, New Relic over the dashboard time picker"
)
N_DAILY = (
    "rollup(daily) is written as TIMESERIES 1 day; Datadog daily buckets start at midnight UTC, "
    "New Relic buckets are aligned to the time picker"
)
N_SUM_GAUGE = (
    "dashboard sum adds every data point in a bucket, where Datadog adds one value per series; "
    "if the metric reports more than once per bucket the New Relic values are larger (set the "
    "metric kind to count in name-map.json when it is a count)"
)
N_LIVE_SPAN = (
    "dashboard widget has its own time range ({span}); New Relic uses the dashboard time picker"
)
N_BARS = "bars over time are shown as an area chart"
N_FACET_LIMIT = (
    "New Relic shows at most the first 10 groups of a FACET unless the query sets a LIMIT; "
    "Datadog shows more"
)
N_VAR_NO_TAG = (
    "dashboard template variable has no tag key, so it cannot be a New Relic variable: "
    "${name} is replaced by its default ({default})"
)
N_FORMATS = "number formats, conditional formats, markers and colours are not carried over"
N_NOTE_SYNTAX = (
    "dashboard note text is copied as written; Datadog template variables, HTML and links in it "
    "are not interpreted"
)
N_MOVED = "{count} widgets overlapped and were moved down"
N_CAPPED = "{count} widgets are beyond the New Relic page limits and were not carried over"
