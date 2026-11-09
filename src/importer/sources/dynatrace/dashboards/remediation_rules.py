"""Manual steps for every reason in reasons.py, registered with the shared engine on import.

Each rule anchors on the whole reason text (`^` plus the escaped sentence), so it can match
only its own reason and no broader rule of another source can be shadowed by it. The rule order
does not matter for the same reason. tests/test_dynatrace_dashboards_rules.py gives each rule
its example reason and checks that the first match across every registered rule is its own.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.sources.dynatrace.dashboards import reasons as r

DATA_PATH = (
    "Decide how the data will reach New Relic before building the widget: re-point the cloud "
    "integration at New Relic, instrument the workload with a New Relic agent or OpenTelemetry, "
    "or accept that the widget has no data. The report's data check cannot say which."
)
REVIEW = "Open the tile in Dynatrace and in New Relic side by side and compare the numbers."

# (reason, title, steps)
_TABLE: list[tuple[str, str, list[str]]] = [
    (
        r.Q_UNREADABLE,
        "Dashboard query too large or malformed",
        [
            "The query text is over the size limit, nested too deeply or has an unclosed string, "
            "comment or bracket. Open the tile in Dynatrace, fix or split the query, export "
            "again, or write the NRQL by hand.",
        ],
    ),
    (
        r.Q_EMPTY,
        "Data tile without a query",
        ["The tile has no query to convert. Recreate it in New Relic by hand or drop it."],
    ),
    (
        r.SRC_SMARTSCAPE,
        "Dashboard query reads Smartscape",
        [
            "Smartscape is Dynatrace's topology of hosts, processes and cloud resources. New "
            "Relic has entities and relationships, but no query reads them the same way. Use "
            "the inventory plan (`plan-dynatrace-reinstrumentation`) for the counts the tile "
            "shows, or build the list from New Relic entity data by hand.",
        ],
    ),
    (
        r.SRC_DAVIS,
        "Dashboard query reads Davis problems",
        [
            "Davis problems and event snapshots are Dynatrace's own model. New Relic issues "
            "work differently and have to be set up from the converted alert conditions. Drop "
            "the tile or build an issue-count widget by hand once alerts exist.",
        ],
    ),
    (
        r.SRC_OTHER,
        "Dashboard query from another data source",
        [
            "The planner reads `timeseries` and `fetch security.events` queries. Logs, spans, "
            "business events and other sources need their own conversion. Write the NRQL by "
            "hand over the matching New Relic event type (`Log`, `Span`, ...).",
        ],
    ),
    (
        r.JOIN,
        "Dashboard query joins another table",
        [
            "NRQL can join to a subquery on one equality, but not to Smartscape or an array "
            "lookup. Split the tile into two widgets, or do the enrichment in the data that "
            "reaches New Relic (a host tag or attribute) and query that.",
        ],
    ),
    (
        r.EXPAND,
        "Dashboard query expands or parses fields",
        [
            "Expanding arrays and parsing JSON have no NRQL form. Send the value to New Relic "
            "already split into attributes, or use NRQL `aparse` and `capture` on a log field "
            "if the data is a log line.",
        ],
    ),
    (
        r.DEDUP,
        "Dashboard query uses dedup",
        [
            "NRQL has no dedup command. Use `uniques()`, `uniqueCount()` or `latest()` with "
            "`FACET` to get one row per key.",
        ],
    ),
    (
        r.ARRAY_MATH,
        "Dashboard query does array arithmetic",
        [
            "The query sums or counts the points of a time series. NRQL aggregates over events "
            "in a time bucket, not over the array. Rewrite the calculation as one aggregation "
            "(for example `average()` of a 0/1 metric gives the share).",
        ],
    ),
    (
        r.SHIFT,
        "Dashboard query shifts the time range",
        [
            "Dynatrace `shift` moves the series back in time. NRQL has `COMPARE WITH` for a "
            "previous period and `SINCE ... UNTIL` for a fixed window. Pick the one that "
            "matches the intent, and note that the shift in the original is usually there to "
            "wait for late data.",
        ],
    ),
    (
        r.COMPUTED,
        "Dashboard query computes a field",
        [
            "A computed field has to be written as an NRQL expression in the `SELECT` list. "
            "Simple arithmetic between aggregations works (`average(a) / average(b)`); "
            "conditionals use `filter()` or `if()`.",
        ],
    ),
    (
        r.FILTER,
        "Dashboard filter condition not translatable",
        [
            "The condition uses a function or a shape the planner does not map to `WHERE`. "
            "Read it in Dynatrace and write the `WHERE` clause by hand. Dynatrace wildcard and "
            "case rules for `matchesValue()` differ from `LIKE`; test the result.",
        ],
    ),
    (
        r.AGGREGATION,
        "Dashboard aggregation without an NRQL function",
        [
            "The aggregation has no direct NRQL function. Choose the closest "
            "(`average`, `sum`, `max`, `uniqueCount`, `percentile`) and check the result "
            "against Dynatrace.",
        ],
    ),
    (
        r.COMMAND,
        "Dashboard query command not read",
        [
            "The planner does not read this DQL command. Read the query in Dynatrace and write "
            "the NRQL by hand.",
        ],
    ),
    (
        r.PARAMETER,
        "Dashboard timeseries option not read",
        [
            "The query passes an option to `timeseries` that the planner does not map. Check "
            "what it changes (bucket size, time range, missing values) and set the matching "
            "`TIMESERIES`, `SINCE` or `UNTIL` clause by hand.",
        ],
    ),
    (
        r.LIMIT,
        "Dashboard limit is not a number",
        [
            "NRQL `LIMIT` takes a number only, never an expression or a variable. Choose the "
            "number.",
        ],
    ),
    (
        r.SCALAR,
        "Dashboard query asks for a scalar",
        [
            "The scalar option returns one number per group, used to rank or colour. Write an "
            "NRQL query without TIMESERIES (a plain aggregation with FACET) for that value.",
        ],
    ),
    (
        r.REORDER,
        "Dashboard command after limit",
        [
            "In DQL the limit comes first and a later sort or filter acts on the limited rows. "
            "NRQL sorts and filters before it limits. Decide which result you want and write "
            "the ORDER BY and LIMIT by hand.",
        ],
    ),
    (
        r.SORT_EXPR,
        "Dashboard sort by an expression",
        [
            "NRQL ORDER BY takes an aggregate or a field. Rewrite the expression as an "
            "aggregate in the SELECT list and order by that.",
        ],
    ),
    (
        r.TILE_VIZ,
        "Dashboard visualization without a New Relic widget",
        [
            "New Relic has no honeycomb, band, scatter, treemap, map or record-list widget. "
            "Choose a table, bar or line widget that shows the same data.",
        ],
    ),
    (
        r.TILE_CODE,
        "Dashboard code tile",
        [
            "A code tile runs JavaScript in Dynatrace. New Relic dashboards cannot. Rebuild "
            "what it shows with NRQL, or use a New Relic app for custom logic.",
        ],
    ),
    (
        r.TILE_SLO,
        "Dashboard SLO tile",
        [
            "Plan the service level with `importer convert --source dynatrace` (the SLO "
            "plan), create it in New Relic, then add a service level widget to the dashboard "
            "by hand.",
        ],
    ),
    (
        r.TILE_PROMPT,
        "Dashboard Davis prompt tile",
        ["A Davis prompt tile has no New Relic counterpart. Drop it."],
    ),
    (
        r.TILE_TYPE,
        "Dashboard tile type unknown",
        [
            "The tile has a type the planner does not know (not markdown, data, code or slo). "
            "Open it in Dynatrace and recreate what it shows by hand.",
        ],
    ),
    (
        r.TILE_UNREADABLE,
        "Dashboard tile could not be read",
        [
            "The tile is not a JSON object, is over the size limit, or the dashboard has more "
            "tiles than the planner reads. Export the dashboard again, or split it.",
        ],
    ),
    (
        r.VAR_DYNATRACE_ONLY,
        "Dashboard variable lists Dynatrace-only values",
        [
            "The list comes from Smartscape, entities, security events or a field snapshot. "
            "Create a New Relic variable by hand with a fixed list or an NRQL query over data "
            "that does exist there, such as `SELECT uniques(host) FROM SystemSample`.",
        ],
    ),
    (
        r.VAR_LIMIT,
        "Dashboard variable sets a LIMIT",
        [
            "New Relic variables cannot be used in `LIMIT`. Fix the limit in the query at the "
            "variable's default value and remove the variable.",
        ],
    ),
    (
        r.VAR_SHAPE,
        "Dashboard variable query not read",
        [
            "The query that lists the variable's values is not one the planner reads. Write "
            "the New Relic variable by hand, as an enum or as an NRQL query.",
        ],
    ),
    (
        r.VAR_UNREADABLE,
        "Dashboard variable could not be read",
        ["The variable entry has no key or type, or is too large. Recreate it by hand."],
    ),
    (
        r.D_ENTITY,
        "Entity-selection clause dropped",
        [
            "A filter that picks Dynatrace entities (a Smartscape id or a primary tag) was "
            "removed, so the tile covers every matching series. " + REVIEW,
            "If the tile must stay narrow, filter on a tag or attribute that your New Relic "
            "data carries, with a dashboard variable.",
        ],
    ),
    (
        r.D_INTERVAL,
        "Bucket size not set",
        ["Add TIMESERIES <n> minutes if the bucket size matters."],
    ),
    (
        r.D_TIMEFRAME,
        "Time range in the query dropped",
        ["Set SINCE and UNTIL by hand if the tile must not follow the dashboard time picker."],
    ),
    (
        r.D_RATE,
        "Rate option mapped to rate()",
        ["Check that the metric is a counter in New Relic. " + REVIEW],
    ),
    (
        r.D_OPTION,
        "Aggregation option dropped",
        ["Check what the dropped option changed. " + REVIEW],
    ),
    (
        r.D_MATCH,
        "Wildcard and case rules differ",
        [
            "matchesValue and contains are not the same as LIKE: wildcard characters, case "
            "and escaping differ. Test the filter against known values. " + REVIEW,
        ],
    ),
    (
        r.D_SORT_SERIES,
        "Sort by series average",
        ["The ranking is by the aggregate over the whole time range. " + REVIEW],
    ),
    (
        r.D_TILE_TIMEFRAME,
        "Tile timeframe lost",
        ["Add SINCE and UNTIL and the widget's ignore-time-range option by hand."],
    ),
    (
        r.D_SEGMENTS,
        "Tile segment filter dropped",
        ["A segment is a saved Dynatrace filter. Rebuild it as a WHERE clause or a variable."],
    ),
    (
        r.D_SELECT_ALL,
        "Select-all test replaced",
        [
            "Set the New Relic variable's default value to * so that it selects everything, "
            "and allow multiple selection if the Dynatrace variable did.",
        ],
    ),
    (
        r.N_SETTINGS,
        "Display settings not carried over",
        ["Threshold colours, units and axis settings are not part of the query. Set them by hand."],
    ),
    (
        r.D_SORT,
        "Second sort key dropped",
        ["NRQL `ORDER BY` takes one key. Keep the more important one. " + REVIEW],
    ),
    (
        r.D_LINK,
        "Link columns dropped",
        [
            "Link columns pointed into the Dynatrace interface and are gone. Add a link to "
            "the matching New Relic page by hand if people need it.",
        ],
    ),
    (
        r.D_LIMIT,
        "Variable in a limit fixed at its default",
        ["The limit is the variable's default value now. Change the number to suit the tile."],
    ),
    (
        r.D_AVERAGE,
        "Average of averages becomes an average of points",
        [
            "Dynatrace averages the per-interval averages. NRQL averages the raw points. The "
            "values differ slightly when the intervals hold different numbers of points. " + REVIEW,
        ],
    ),
    (
        r.D_WIDGET,
        "Visualization shown with the closest widget",
        ["The closest New Relic widget looks different from the original. " + REVIEW],
    ),
    (
        r.D_VAR_NAMES,
        "Variable list needs the metric name map",
        [
            "The list reads Dynatrace metric names. They must be mapped to New Relic names "
            "before the variable returns values. The metric name map is built by another part "
            "of the importer; until it exists, write the NRQL by hand.",
        ],
    ),
    (
        r.D_VAR_DEPENDS,
        "Variable depends on another variable",
        [
            "Check in New Relic that the variable's query can use the other variable. If it "
            "cannot, give the list a fixed query or a fixed set of values.",
        ],
    ),
    (
        r.D_VAR_ENTITY,
        "Entity filter in a variable list dropped",
        [
            "The list had a filter on Smartscape entities that was removed, so it lists "
            "more values than before. Filter the list in NRQL if needed.",
        ],
    ),
    (r.NO_DATA, "No New Relic data source for the tile", [DATA_PATH]),
    (
        r.FILE_UNREADABLE,
        "Dashboard file could not be read",
        [
            "The file is not JSON, is over the size limit or is nested too deeply. Export the "
            "dashboard again with `extract-dynatrace-inventory`.",
        ],
    ),
    (
        r.FILE_SHAPE,
        "File is not a dashboard",
        ["The JSON has no `tiles`. Check that the file is a dashboard document."],
    ),
    (
        r.C_METRICS,
        "Classic dashboard chart needs the metric name map",
        [
            "Classic chart tiles read Dynatrace metrics (for example `builtin:host.cpu.usage`). "
            "They convert once each metric has a New Relic name; until then write the NRQL by "
            "hand from the metric keys listed in plan.json.",
        ],
    ),
    (
        r.C_TILE,
        "Classic dashboard tile type without a widget",
        [
            "Custom charts, USQL, entity lists, problem lists, maps and synthetic tiles have no "
            "New Relic widget. Rebuild the content from NRQL by hand or drop the tile.",
        ],
    ),
    (
        r.C_ZONE,
        "Management zone filter",
        [
            "New Relic has no management zones. Use a dashboard variable on a tag attribute, "
            "or scope by account, and check that the entities carry the same tags in New "
            "Relic.",
        ],
    ),
]

RULES: list[Rule] = [
    (re.compile("^" + re.escape(reason)), title, steps) for reason, title, steps in _TABLE
]

register_rules(RULES)
