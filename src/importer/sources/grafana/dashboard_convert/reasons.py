"""Every reason and note the dashboard converter can give, as fixed text.

Reasons never contain text from the dashboard. Every one starts with `grafana dashboard convert`
so the anchored taxonomy and remediation rules of this package cannot claim another source's
reasons. A reason (`C_*`) stops a panel and turns it into a placeholder; a note (`N_*`) rides
along with a widget that was converted.
"""

PREFIX = "grafana dashboard convert"
NOTE = f"{PREFIX} note:"

# --- reasons: the panel becomes a visible placeholder ---
C_VAR_NAME = f"{PREFIX}: a dashboard variable sits inside a metric or label name"
C_VAR_MATCHER = (
    f"{PREFIX}: a label matcher mixes fixed text with a variable (a New Relic variable is "
    "substituted whole)"
)
C_BUILTIN = f"{PREFIX}: $__interval or $__range is used outside a range selector"
C_EVENTS = f"{PREFIX}: the name map puts the metrics of one query on different New Relic events"
C_FILTERS = (
    f"{PREFIX}: the terms of this expression have different filters, which a percentile cannot take"
)
C_SHAPE = f"{PREFIX}: the query is a shape the planner reads but the converter does not write"
C_NAME = f"{PREFIX}: a metric or label name is not a valid New Relic attribute name"
C_NRQL = f"{PREFIX}: the converted query is not accepted by the dashboard query checks"
C_VARIABLE_UNDEFINED = (
    f"{PREFIX}: the query uses a variable that did not become a New Relic variable"
)
C_LIKE_PERCENT = f"{PREFIX}: a pattern contains % which NRQL LIKE cannot match literally"
C_REUSE = f"{PREFIX}: the panel reuses a query that did not convert"
C_UNEXPECTED = f"{PREFIX}: the panel could not be read"

# --- notes on a converted widget ---
N_MERGED = f"{NOTE} series are not kept apart; add a FACET for the labels that identify one series"
N_INSTANT = f"{NOTE} the value is taken over the whole time range, Grafana showed the latest point"
N_RATE_WINDOW = f"{NOTE} the rate window of the query becomes the automatic New Relic bucket"
N_INCREASE = f"{NOTE} increase over a variable window is the sum per bucket"
N_COUNT = f"{NOTE} count() counts samples, not series; use uniqueCount of a label that names one"
N_TOPK = f"{NOTE} topk ranks over the whole time range, not at each step"
N_TOPK_FLAT = f"{NOTE} topk over series that are not grouped has no FACET to rank"
N_LEGEND_TEXT = f"{NOTE} legend text around labels is lost; the facet value is the legend"
N_LEGEND_LABEL = f"{NOTE} a legend label is not a grouping label of the query and was ignored"
N_FACET_LIMIT = f"{NOTE} a FACET takes at most 5 attributes; the others were dropped"
N_FIXED_INTERVAL = f"{NOTE} the fixed query interval was kept as the bucket size"
N_NAME_MAP = f"{NOTE} metric names were taken from the name map"
N_REPEAT = f"{NOTE} a repeated panel is one widget with a FACET on the repeat label"
N_REPEAT_FLAT = f"{NOTE} the panel repeats on a variable no label of the query uses, so it is one"
N_BARS = f"{NOTE} the bar drawing style is shown as lines"
N_STACK = f"{NOTE} percent stacking is shown as normal stacking"
N_UNIT = f"{NOTE} the unit has no New Relic unit and was left out"
N_PERCENTUNIT = f"{NOTE} a 0 to 1 percent unit has no New Relic unit; values are not scaled"
N_THRESHOLD_ORDER = f"{NOTE} thresholds that go from bad to good were left out"
N_THRESHOLD_COLORS = (
    f"{NOTE} threshold steps in other colours than red, orange, yellow or green were left out"
)
N_THRESHOLD_MODE = f"{NOTE} percentage thresholds were left out"
N_THRESHOLD_TYPE = f"{NOTE} this widget type takes no thresholds"
N_THRESHOLD_TABLE = f"{NOTE} table thresholds need a column name and were left out"
N_HTML = f"{NOTE} html in a text panel was reduced to plain text"
N_LOGS = f"{NOTE} a logs panel is a table of the newest 200 lines"
N_MULTI_QUERY = f"{NOTE} this single-value widget has more than one query"
N_DISPLAY = f"{NOTE} display options could not be matched to the panel and were not read"
N_UNIT_FACTOR = f"{NOTE} the unit differs in scale from the New Relic unit; values are not scaled"

# --- dashboard level ---
D_DATA = (
    "data must reach New Relic by Prometheus remote write, OTLP or Alloy under the metric "
    "names used here (names unverified; run the data check)"
)
D_COLLAPSED = "{count} collapsed rows were opened"
D_ANNOTATIONS = "annotations are not carried over"
D_LINKS = "dashboard links are not carried over"
D_TIME = "original time range: {text}"
D_REPEAT_ROWS = "{count} repeated rows are shown once"
D_TABS = "{count} tabs were flattened into one page with headings"
D_PANELS = "{done} of {total} panels converted, {placeholders} are placeholders"
D_WIDGET_CAP = "{count} widgets over the limit of 300 were left out"
D_VARIABLE_DROPPED = "{count} interval or data source variables were dropped"
D_VARIABLE_MANUAL = "{count} variables were not converted"
D_SUBSTITUTED = "{count} constant variables were written into the queries"
D_BAD_FILE = "the file could not be read: {reason}"
