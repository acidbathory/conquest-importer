"""The fixed reason texts of the dashboard plan.

A reason is a constant sentence. It never contains a title, a query, a field name or any other
text from the dashboard, so nothing untrusted can reach a report or a plan through a reason.
Every text starts with one of the PREFIXES; the taxonomy and the remediation rules anchor on
them, and tests check that each text has both.
"""

QUERY = "dynatrace dashboard query:"
TILE = "dynatrace dashboard tile:"
VARIABLE = "dynatrace dashboard variable:"
NOTE = "dynatrace dashboard note:"
DATA = "dynatrace dashboard data:"
FILE = "dynatrace dashboard file:"
CLASSIC = "dynatrace classic dashboard:"

# Why a query cannot be written in NRQL. A tile with any of these is manual.
Q_UNREADABLE = f"{QUERY} the query text is too large, too deeply nested or not closed"
Q_EMPTY = f"{QUERY} the data tile has no query"
SRC_SMARTSCAPE = f"{QUERY} the query reads Smartscape topology, which New Relic does not have"
SRC_DAVIS = f"{QUERY} the query reads Davis problems or events, which New Relic does not have"
SRC_OTHER = f"{QUERY} the query starts from a data source the planner does not read"
JOIN = f"{QUERY} the query joins, looks up or appends another table"
EXPAND = f"{QUERY} the query expands arrays or parses JSON fields"
DEDUP = f"{QUERY} the query removes duplicates with dedup, which NRQL cannot express"
ARRAY_MATH = f"{QUERY} the query does arithmetic on the arrays of a time series"
SHIFT = f"{QUERY} the query shifts the time range of the series"
COMPUTED = f"{QUERY} the query computes a field from an expression"
FILTER = f"{QUERY} a filter condition cannot be written as an NRQL WHERE clause"
AGGREGATION = f"{QUERY} an aggregation has no NRQL function"
COMMAND = f"{QUERY} the query uses a command the planner does not read"
PARAMETER = f"{QUERY} the query uses a timeseries option the planner does not read"
LIMIT = f"{QUERY} the limit is neither a number nor a dashboard variable"
SCALAR = f"{QUERY} the query asks for a single value (the scalar option) instead of a series"
REORDER = (
    f"{QUERY} a command after limit would run before the limit in NRQL, which has a fixed order"
)
SORT_EXPR = f"{QUERY} the sort key is an expression, not a field"

# Why a tile cannot be written at all.
TILE_VIZ = f"{TILE} the visualization has no New Relic widget"
TILE_CODE = f"{TILE} a code tile runs JavaScript, which New Relic dashboards cannot"
TILE_SLO = f"{TILE} an slo tile points at a Dynatrace service level objective"
TILE_PROMPT = f"{TILE} a Davis prompt tile has no New Relic counterpart"
TILE_TYPE = f"{TILE} the tile type is not one the planner knows"
TILE_UNREADABLE = f"{TILE} the tile could not be read (not an object, too large or too many)"

# Why a variable cannot be a New Relic variable.
VAR_DYNATRACE_ONLY = (
    f"{VARIABLE} the values come from Smartscape, entities, security events or a field snapshot"
)
VAR_LIMIT = f"{VARIABLE} the variable sets a LIMIT, and New Relic variables cannot be used there"
VAR_SHAPE = f"{VARIABLE} the query that lists the values is not one the planner reads"
VAR_UNREADABLE = f"{VARIABLE} the variable could not be read"

# Changes a draft makes. The tile is created only when a person asks for needs-review output.
D_ENTITY = (
    f"{NOTE} a clause that selects Dynatrace entities (Smartscape id or primary tag) was dropped, "
    "so the tile shows every matching series"
)
D_SORT = f"{NOTE} only the first sort key is kept"
D_LINK = f"{NOTE} link columns to the Dynatrace interface were dropped"
D_LIMIT = f"{NOTE} a variable in the limit is fixed at its default, because LIMIT takes no variable"
D_AVERAGE = f"{NOTE} an average of per-interval averages becomes an average of the raw points"
D_WIDGET = f"{NOTE} the visualization is shown with the closest New Relic widget"
D_INTERVAL = f"{NOTE} the bucket size (interval) is not set; TIMESERIES chooses its own"
D_TIMEFRAME = (
    f"{NOTE} a time range written in the query was dropped; the dashboard time picker applies"
)
D_RATE = f"{NOTE} the rate option becomes rate() over the same interval; the result is unverified"
D_OPTION = f"{NOTE} an option of an aggregation (such as default) was dropped"
D_MATCH = (
    f"{NOTE} matchesValue, contains, startsWith and endsWith differ from LIKE in case and wildcard "
    "rules, and the difference is not verified"
)
D_SORT_SERIES = f"{NOTE} a sort by the average of a series becomes ORDER BY on the aggregate"
D_TILE_TIMEFRAME = f"{NOTE} the tile's own timeframe is lost; the dashboard time picker applies"
D_SEGMENTS = f"{NOTE} the tile's segment filter was dropped"
D_SELECT_ALL = (
    f"{NOTE} the test for the select-all value of a variable is replaced by the New Relic "
    "default value *, which the variable must be set to"
)
N_SETTINGS = f"{NOTE} threshold colours and other display settings are not carried over"
D_VAR_NAMES = f"{NOTE} the list reads metric names, which need the New Relic metric name map"
D_VAR_DEPENDS = (
    f"{NOTE} the list depends on another variable; whether a New Relic variable query can use "
    "another variable is not confirmed"
)
D_VAR_ENTITY = f"{NOTE} a filter on Smartscape entities in the list was dropped"

# Data the tile needs, which New Relic does not receive. This does not change the verdict.
NO_DATA = (
    f"{DATA} no New Relic data source exists for the Dynatrace-only data this tile reads "
    "(self-monitoring metrics, Kubernetes metrics, security events, Smartscape or Davis)"
)

# A whole file or dashboard.
FILE_UNREADABLE = f"{FILE} the file could not be read (not JSON, too large or too deeply nested)"
FILE_SHAPE = f"{FILE} the JSON is not a dashboard"
C_METRICS = (
    f"{CLASSIC} the tile charts metrics, which need the New Relic metric name map before it can "
    "be written as NRQL"
)
C_TILE = f"{CLASSIC} the tile type has no New Relic widget"
C_ZONE = f"{CLASSIC} a management zone filter cannot be carried over"

PREFIXES = (QUERY, TILE, VARIABLE, NOTE, DATA, FILE, CLASSIC)
BLOCKERS = (
    Q_UNREADABLE,
    Q_EMPTY,
    SRC_SMARTSCAPE,
    SRC_DAVIS,
    SRC_OTHER,
    JOIN,
    EXPAND,
    DEDUP,
    ARRAY_MATH,
    SHIFT,
    COMPUTED,
    FILTER,
    AGGREGATION,
    COMMAND,
    PARAMETER,
    LIMIT,
    SCALAR,
    REORDER,
    SORT_EXPR,
    TILE_VIZ,
    TILE_CODE,
    TILE_SLO,
    TILE_PROMPT,
    TILE_TYPE,
    TILE_UNREADABLE,
    VAR_DYNATRACE_ONLY,
    VAR_LIMIT,
    VAR_SHAPE,
    VAR_UNREADABLE,
    FILE_UNREADABLE,
    FILE_SHAPE,
    C_METRICS,
    C_TILE,
)
NOTES = (
    D_INTERVAL,
    D_TIMEFRAME,
    D_RATE,
    D_OPTION,
    D_MATCH,
    D_SORT_SERIES,
    D_TILE_TIMEFRAME,
    D_SEGMENTS,
    D_SELECT_ALL,
    N_SETTINGS,
    D_ENTITY,
    D_SORT,
    D_LINK,
    D_LIMIT,
    D_AVERAGE,
    D_WIDGET,
    D_VAR_NAMES,
    D_VAR_DEPENDS,
    D_VAR_ENTITY,
    NO_DATA,
    C_ZONE,
)
ALL = (*BLOCKERS, *NOTES)
