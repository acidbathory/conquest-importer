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
W_FUNCTION = "dashboard query function is not converted yet: {name}()"
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
