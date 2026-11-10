"""Playbooks for Grafana dashboard panels, variables and dashboard-level features."""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L

UNRUN = (
    "The template was not run against your data; paste it in the query builder and compare "
    "the chart with the Grafana panel before you rely on it."
)


def reg(titles: tuple[str, ...] | str, pb: Playbook) -> None:
    register_playbook(titles, pb)


# --- variables -----------------------------------------------------------------------------
reg(
    "Dashboard panel using a variable that stays manual",
    Playbook(
        source='The query filters on a dashboard variable such as cluster=~"$cluster".',
        newrelic="A New Relic dashboard variable used as {{name}} in the widget query "
        "(`WHERE cluster IN ({{cluster}})`).",
        steps=(
            "Find the variable's own reason in the plan (it is listed under the same dashboard "
            "as a variable item) and fix that first: this panel only needs the variable to exist.",
            "Open the dashboard, click Edit, then + Add variable. Type NRQL, name it like the "
            "Grafana variable, query `FROM Metric SELECT uniques(<label>) WHERE metricName = "
            "'<metric>'`, tick multi-select, default `*` (all).",
            "Edit the widget and write the filter as `WHERE <label> IN ({{name}})`; paste the "
            "converted query from the placeholder as the base.",
        ),
        differs="A New Relic variable is substituted whole into the query, so it cannot sit "
        "inside a name or next to text. 'All' is `*` and is dropped from the filter only if you "
        "set the variable to be excluded.",
        effort_minutes=15,
        template="FROM Metric SELECT uniques(<label>) WHERE metricName = '<metric>'",
        links=(L.VARIABLES,),
    ),
)
reg(
    (
        "Dashboard variable query",
        "Dashboard variable regex",
    ),
    Playbook(
        source="A variable fills its drop-down from a data source query (label_values, "
        "query_result, a regex that trims the values).",
        newrelic="A `nrql` variable (`uniques()`), or an `enum` variable with the values typed "
        "in. A regex on the values becomes `WHERE attr RLIKE '...'` in the variable query.",
        steps=(
            "Decide if the list changes often. If not, create an `enum` variable and type the "
            "values: it works in every plan and cannot break.",
            "If it changes: + Add variable, type NRQL, query `FROM Metric SELECT "
            "uniques(<label>) WHERE metricName = '<metric>' AND <label> RLIKE '<pattern>'`.",
            "For a pattern with lookahead, a backreference or a capture group (JavaScript regex), "
            "rewrite it as a plain RLIKE or as a list of values; RLIKE is not JavaScript.",
        ),
        differs="uniques() lists the labels seen in the dashboard time range, Grafana lists "
        "the latest series; a regex with captures has no counterpart.",
        effort_minutes=15,
        template="FROM Metric SELECT uniques(<label>) WHERE metricName = '<metric>'",
        links=(L.VARIABLES, L.NRQL),
    ),
)
reg(
    "Dashboard nested variable",
    Playbook(
        source="A variable's query uses another variable (instance depends on job).",
        newrelic="Nested variables, `WHERE job IN ({{job}})` in the child's NRQL.",
        steps=(
            "Check that the account is on Pro, Enterprise or Core Compute; the docs say nested "
            "variables are not available on other plans.",
            "Create the parent variable first, with a default value (a child lists nothing "
            "until its parent has a selected value).",
            "Create the child with `FROM Metric SELECT uniques(<label>) WHERE job IN ({{job}})`.",
            "If the plan does not allow nesting, make the child an unfiltered list.",
        ),
        differs="Without the plan the child shows every value, not just those of the selected "
        "parent.",
        effort_minutes=20,
        links=(L.NESTED_VARIABLES,),
    ),
)
reg(
    (
        "Dashboard ad hoc variable",
        "Dashboard interval or data source variables dropped",
        "Dashboard variable name",
        "Dashboard variable type",
    ),
    Playbook(
        source="A variable that adds any label filter or group-by at view time (ad hoc), "
        "switches the data source or sets the interval.",
        newrelic="No equivalent for ad hoc filters, data source and interval variables. "
        "Workaround: a `string` variable with the identifier replacement after FACET for a "
        "group-by; two variables (attribute as identifier, value as string) for one filter; "
        "`TIMESERIES AUTO` replaces the interval.",
        steps=(
            "Group-by: + Add variable, type List or String, output format Identifier, name it "
            "`group`, then write `FACET {{group}}` in the widgets.",
            "One filter: an identifier variable `key` and a string variable `value`, then "
            "`WHERE {{key}} = {{value}}`. This is one filter only, not a list.",
            "Interval: delete it; use `TIMESERIES AUTO` or a fixed bucket in each query.",
            "Data source: put all the data in one account, or add the account selector of the "
            "widget when several accounts are queried.",
        ),
        differs="One filter at a time instead of any number; the identifier replacement in a "
        "`WHERE` position is documented for FACET and FROM, check it in your dashboard.",
        effort_minutes=20,
        no_equivalent=True,
        links=(L.VARIABLES,),
    ),
)
reg(
    "Dashboard query using a variable that did not convert",
    Playbook(
        source="The query filters on a variable that did not become a New Relic variable.",
        newrelic="The same as 'panel using a variable that stays manual': rebuild the variable, "
        "then use {{name}}.",
        steps=(
            "Rebuild the variable (see its reason), then paste the converted query and use "
            "`{{name}}`.",
        ),
        differs="See the variable's reason.",
        effort_minutes=15,
        links=(L.VARIABLES,),
    ),
)
reg(
    (
        "Dashboard query with a variable inside a name",
        "Dashboard label matcher mixing text and a variable",
        "Dashboard query using $__interval or $__range outside a range",
    ),
    Playbook(
        source="A variable sits inside a metric or label name, or is mixed with text in a "
        'matcher (`job=~"api-$env"`), or $__interval is used in arithmetic.',
        newrelic="A New Relic variable is substituted whole, so it can stand for an attribute "
        "name (identifier), a value or a number, but not part of one.",
        steps=(
            "Make the variable's values carry the text: replace `api-$env` by a variable whose "
            "values are `api-prod`, `api-dev`, then `WHERE job IN ({{env}})`.",
            "For a variable inside a name, use an identifier variable that holds the whole "
            "attribute name, or write one widget per value.",
            "For $__interval in arithmetic, use a fixed number of seconds or `rate()`.",
        ),
        differs="The drop-down shows the longer values.",
        effort_minutes=20,
        links=(L.VARIABLES,),
    ),
)
reg(
    "Dashboard metric list with a name map",
    Playbook(
        source="A variable lists metric names (`metrics(regex)`).",
        newrelic="An `enum` variable, or `FROM Metric SELECT uniques(metricName)`.",
        steps=(
            "Type the metric names as they are in New Relic into an `enum` variable, or use "
            "`FROM Metric SELECT uniques(metricName) WHERE metricName LIKE '<prefix>%'`.",
        ),
        differs="The names in New Relic may differ from the Prometheus ones if the name map "
        "renames metrics.",
        effort_minutes=10,
        links=(L.VARIABLES,),
    ),
)

# --- dashboard-level features --------------------------------------------------------------
reg(
    ("Dashboard annotations", "Dashboard annotations dropped"),
    Playbook(
        source="Annotation queries draw events (deploys, incidents) as vertical lines on charts.",
        newrelic="Change tracking markers on charts of an entity (created through NerdGraph, the "
        "CLI or a CI step). There is no query-based annotation layer on a dashboard.",
        steps=(
            "List what the annotation query showed (deployments, alerts, a tag).",
            "For deployments: add a change tracking call to the pipeline "
            "(`changeTrackingCreateEvent` in NerdGraph) for the entity the charts show.",
            "For anything else, add a table widget over the event source "
            "(`FROM <event> SELECT timestamp, title`) next to the charts.",
        ),
        differs="Markers attach to an entity and show on charts of that entity, not on every "
        "chart of the dashboard; the old events are not copied.",
        effort_minutes=60,
        no_equivalent=True,
        links=(L.CHANGE_TRACKING,),
    ),
)
reg(
    "Dashboard links dropped",
    Playbook(
        source="Links at the top of the dashboard to other dashboards, runbooks or tools.",
        newrelic="A markdown widget holding the links, or the link setting of one widget. "
        "No link bar.",
        steps=(
            "Edit the dashboard, add a markdown widget at the top of the page and write the "
            "links as `[text](https://...)`.",
            "For a link to another converted dashboard, copy its New Relic URL after both exist.",
        ),
        differs="Links that carry Grafana variables or the time range (`includeVars`, "
        "`keepTime`) lose those parameters.",
        effort_minutes=10,
        links=(L.CHARTS,),
    ),
)
reg(
    ("Dashboard repeated rows shown once", "Dashboard tabs flattened"),
    Playbook(
        source="A row or panel repeats once for every value of a multi-value variable; tabs "
        "group rows.",
        newrelic="One widget with a FACET on the variable's label draws every value; pages "
        "(tabs) of a dashboard.",
        steps=(
            "For a repeat: edit the widget, add `FACET <label>`, keep the variable filter. "
            "Every selected value becomes a series, or a row in a table.",
            "For tabs: add a page in the dashboard for each tab and move the widgets with the "
            "headings.",
        ),
        differs="A chart per value becomes one chart with a series per value.",
        effort_minutes=15,
        links=(L.CHARTS,),
    ),
)
reg(
    "Dashboard over the widget limit",
    Playbook(
        source="A dashboard with more panels than one New Relic page holds.",
        newrelic="More pages in the same dashboard (150 widgets per page, 25 pages).",
        steps=(
            "The converter already splits a large dashboard into pages at the Grafana rows. "
            "Only widgets beyond 25 pages are left out: split the Grafana dashboard by row and "
            "convert each part, or add a page by hand.",
            "Drop panels nobody opens: check the Grafana usage data first.",
        ),
        differs="A Grafana row becomes a page named after it; a row over 150 widgets is split.",
        effort_minutes=2,
        links=(L.CHARTS,),
    ),
)

# --- panels ---------------------------------------------------------------------------------
reg(
    "Dashboard sum or count across the series of a gauge",
    Playbook(
        source="sum(metric) or count(metric) of a gauge adds the latest value of every series.",
        newrelic="A nested query: latest() per series, then sum() of those. A New Relic sum() "
        "of a gauge adds every sample, not the latest value per series.",
        steps=(
            "Find the labels that name one series (for node_exporter: instance, device, "
            "mountpoint). Look at them with `FROM Metric SELECT keyset() WHERE metricName = "
            "'<metric>'`.",
            "Chart or stat: use the template; the inner query takes the newest value of each "
            "series, the outer one sums them.",
            "If each series reports once per bucket, `SELECT sum(<metric>) ... TIMESERIES "
            "<scrape interval>` is also exact.",
            "To let the importer do it next time, add the series labels to the name map "
            '(`{"series": {"<metric>": ["<label>"]}}`) and convert again.',
            "count() of series: `SELECT uniqueCount(<label>)`.",
        ),
        differs="A nested query cannot draw a time series with one point per bucket in one "
        "widget; use it for stats, tables and bars, or chart per-series values with FACET.",
        measured="On real node_exporter data a plain sum() was 4x too high per 60 s bucket "
        "(4 scrapes) and 41x as a stat over 10 minutes (41 scrapes); 60 against 1 on a single "
        "series. average(x) * cardinality(x) matched the exact sum of nine filesystem series.",
        effort_minutes=10,
        template="SELECT sum(v) FROM (SELECT latest(<metric>) AS v FROM Metric FACET <labels> "
        "LIMIT MAX) SINCE 5 minutes ago",
        links=(L.NESTED, L.SUBQUERY, L.PROMQL_TRANSLATE),
    ),
)
reg(
    "Dashboard average, minimum or maximum of rates",
    Playbook(
        source="avg(rate(counter[5m])) is the mean of the per-series rates.",
        newrelic="rate(sum(c), 1 second) divided by the number of series, or the rate per "
        "series with FACET. rate(average(c)) divides one sample by the bucket and reads too low.",
        steps=(
            "Mean over series: `SELECT rate(sum(<metric>), 1 second) / uniqueCount(<label>)` "
            "with <label> naming one series (cpu, instance).",
            "Per-series values: add `FACET <label>` and read each series.",
        ),
        differs="New Relic's own translator writes rate(average()), which was measured about "
        "20x too small (12 cpus, 5 minute bucket, 15 s scrape).",
        measured="rate(average(c)) gave 0.00441 where the true mean of per-cpu rates was "
        "0.0883 (factor 20 = 300 s / 15 s).",
        effort_minutes=10,
        template="SELECT rate(sum(<metric>), 1 second) / uniqueCount(<label>) FROM Metric "
        "TIMESERIES AUTO",
        links=(L.NRQL, L.PROMQL_TRANSLATE),
    ),
)
reg(
    "Dashboard query comparison or vector matching",
    Playbook(
        source="A comparison or set operator filters series (`metric == 1`, `> 0`, `and`, "
        "`unless`) or joins two metrics (`on(...) group_left(...)`).",
        newrelic="WHERE before the aggregation, a nested query that filters the inner result, "
        "or a widget threshold. A join of two metrics on labels is FACET on both labels with "
        "both metrics in one SELECT.",
        steps=(
            "Filter on a value (`metric == 1`): nested query "
            "`SELECT uniqueCount(<label>) FROM (SELECT latest(<metric>) AS v FROM Metric FACET "
            "<labels> LIMIT MAX) WHERE v = 1` for a count, or the same without uniqueCount for "
            "the list.",
            "Filter on a label only: move it to WHERE in the outer query.",
            "Join (`group_left`): `SELECT latest(a), latest(b) FROM Metric FACET <join labels>` "
            "when both metrics carry the labels; if a label exists on one metric only, "
            "send it to the other (relabel) or leave the join out.",
            "A chart that only needs red above a bound: keep the plain query and add a "
            "threshold to the widget.",
        ),
        differs="Result shape differs: NRQL returns one series per facet, Prometheus one per "
        "label set; check the series count against Grafana.",
        effort_minutes=20,
        template="SELECT uniqueCount(<label>) FROM (SELECT latest(<metric>) AS v FROM Metric "
        "FACET <labels> LIMIT MAX) WHERE v = 1",
        links=(L.NESTED, L.NRQL, L.PROMQL_TRANSLATE),
    ),
)
reg(
    ("Dashboard query time or range function", "Dashboard query bottomk or topk with a variable"),
    Playbook(
        source="offset, *_over_time, delta, deriv, predict_linear, absent() or without() "
        "looking back or across time; bottomk/topk with a variable count.",
        newrelic="`SINCE ... COMPARE WITH` for offset, an aggregate with `SLIDE BY` for "
        "*_over_time, derivative() / predictLinear() for delta and prediction, `LIMIT` with a "
        "fixed number for topk.",
        steps=(
            "offset 1d: `SELECT <aggregate>(<metric>) FROM Metric TIMESERIES AUTO SINCE 1 "
            "day ago COMPARE WITH 1 day ago`.",
            "max_over_time(m[1h]): `SELECT max(<metric>) FROM Metric TIMESERIES 1 hour` or "
            "with `SLIDE BY 5 minutes` for a moving window.",
            "delta(m[5m]): `SELECT latest(<metric>) - earliest(<metric>)`.",
            "absent(): not a chart. Create an alert condition with loss of signal on the "
            "metric, or chart `count(*)` and look for zero.",
            "without(a, b): list the labels to keep in FACET by name.",
            "topk($n): fix the number in `FACET <label> LIMIT 10`; a variable cannot be used "
            "in LIMIT.",
        ),
        differs="Windows are separate buckets unless you use SLIDE BY; COMPARE WITH draws the "
        "past as a dotted line.",
        effort_minutes=20,
        template="SELECT max(<metric>) FROM Metric FACET <labels> TIMESERIES 1 hour",
        links=(L.NRQL,),
    ),
)
reg(
    (
        "Dashboard query function",
        "Dashboard query nested aggregation",
        "Dashboard query grouping",
        "Dashboard histogram_quantile shape",
    ),
    Playbook(
        source="A PromQL function with no direct NRQL form (label_replace, label_join, "
        "clamp, scalar), an aggregation of an aggregation, or grouping by different labels.",
        newrelic="NRQL nested aggregation `SELECT f(x) FROM (SELECT g(y) AS x FACET a)`; "
        "capture()/aparse() for derived labels; clamp_min()/clamp_max(); "
        "`bucketPercentile(metric_bucket, 95)` for histogram_quantile.",
        steps=(
            "Split the expression: write the inner aggregation as a faceted query, then the "
            "outer one around it.",
            "label_replace: `FACET capture(<label>, r'(?P<part>...)')` or aparse().",
            "histogram_quantile(0.95, sum by (le)(rate(m_bucket[5m]))): `SELECT "
            "bucketPercentile(<metric>_bucket, 95) FROM Metric`.",
            "Compare the chart with the Grafana panel for a known time range.",
        ),
        differs="Nested queries use one level of faceting; the outer query cannot be a time "
        "series over the facets in one widget.",
        effort_minutes=30,
        template="SELECT bucketPercentile(<metric>, 95) FROM Metric TIMESERIES AUTO",
        links=(L.NESTED, L.NRQL, L.PROMQL_TRANSLATE),
    ),
)
reg(
    (
        "Dashboard query not converted",
        "Dashboard query the converter does not write",
        "Dashboard converted query refused by the checks",
        "Dashboard query too long or with a variable format",
        "Dashboard panel could not be read",
    ),
    Playbook(
        source="A PromQL expression longer or more complex than the converter reads.",
        newrelic="NRQL written by hand, starting from New Relic's own PromQL translation.",
        steps=(
            "Try New Relic's PromQL translation of the expression, then check it: it baked in "
            "a fixed time range and did not facet plain selectors in measured runs "
            "(strip `SINCE ... UNTIL ...`, add `FACET`, use `LIMIT MAX`).",
            "Split a long expression into parts; build each part as a widget, then combine "
            "the ones that must be one number with a nested query.",
            "Compare each result with the Grafana panel over the same time range.",
        ),
        differs="New Relic's translator output was wrong or doubtful on 8 of 25 inspected "
        "expressions (nr-promql-baseline.md); treat it as a draft.",
        effort_minutes=30,
        template="SELECT <aggregate>(<metric>) FROM Metric FACET <labels> TIMESERIES AUTO",
        links=(L.PROMQL_TRANSLATE, L.PROMQL_SUPPORTED, L.NRQL),
    ),
)
reg(
    "Dashboard query on a raw counter",
    Playbook(
        source="A counter shown as its raw, ever-increasing value.",
        newrelic="Prometheus counters arrive as the change per interval (`count` type), so a "
        "raw cumulative line has no equal; the rate or the increase is what the data holds.",
        steps=(
            "Decide what the panel meant: a rate (`rate(sum(<metric>), 1 second)`) or a total "
            "in the window (`sum(<metric>)`).",
            "For a true running total, send the counter as a gauge (a relabel with "
            "`newrelic_metric_type=gauge`) or take `cumulativeSum`-style panels as an "
            "increase.",
        ),
        differs="A rate chart replaces the climbing line. Rate shape depends on the bucket "
        "being a multiple of the scrape interval (5 s buckets on 10 s data gave 0 and 2.4 "
        "around the true 1.2).",
        measured="Mean stays right at every bucket size; only the shape changes.",
        effort_minutes=10,
        template="SELECT rate(sum(<metric>), 1 second) FROM Metric FACET <labels> TIMESERIES AUTO",
        links=(L.PROMQL_SUPPORTED, L.NRQL),
    ),
)
reg(
    (
        "Dashboard query label matcher or name",
        "Dashboard name that NRQL rejects",
        "Dashboard label named metricName",
        "Dashboard pattern with a percent sign",
    ),
    Playbook(
        source="A label matcher (regex) or a metric or label name that NRQL cannot take as "
        "it is: colons from recording rules, a label called metricName, a % in a pattern.",
        newrelic="`WHERE attr RLIKE '...'`; the attribute name as New Relic stored it "
        "(check with keyset()); backticks around odd names.",
        steps=(
            "Look at the real name: `FROM Metric SELECT keyset() WHERE metricName = '<metric>'`.",
            "Regex matcher: `WHERE <label> RLIKE '<regex>'` (RE2-style, not JavaScript); "
            "a matcher that also matches a missing label needs `OR <label> IS NULL`.",
            "Recording rule names with colons: send the recorded metric under a plain name "
            "(relabel) or rebuild the recording rule's expression in the query.",
            "A label named metricName collides with the attribute that holds the metric name: "
            "rename it in the remote write relabel config.",
            "A `%` in LIKE cannot be matched literally; use RLIKE.",
        ),
        differs="Regex dialects differ (RLIKE is not JavaScript or Go).",
        effort_minutes=15,
        template="FROM Metric SELECT keyset() WHERE metricName = '<metric>'",
        links=(L.NRQL, L.REMOTE_WRITE),
    ),
)
reg(
    "Dashboard query over metrics on different New Relic events",
    Playbook(
        source="One expression combines two metrics.",
        newrelic="One NRQL query reads one event type. Either both metrics in `Metric`, or "
        "`FROM A, B`, or two widgets.",
        steps=(
            "Send both metrics to the same event type (`Metric`) so one SELECT has both.",
            "Otherwise: two widgets and a markdown note, or a nested query with a JOIN of "
            "subqueries for a table.",
        ),
        differs="A JOIN of subqueries cannot be a time series.",
        effort_minutes=30,
        links=(L.NRQL, L.SUBQUERY),
    ),
)
reg(
    "Dashboard percentile next to terms with other filters",
    Playbook(
        source="A percentile of a histogram next to other filtered terms in one expression.",
        newrelic="bucketPercentile cannot be wrapped in filter(); one widget per term.",
        steps=(
            "Build the percentile as its own widget with filters in WHERE.",
            "Build the other term in another widget.",
        ),
        differs="Two widgets instead of one number.",
        effort_minutes=15,
        template="SELECT bucketPercentile(<metric>, 95) FROM Metric WHERE <label> = '<value>'",
        links=(L.NRQL,),
    ),
)
reg(
    "Dashboard panel with transformations",
    Playbook(
        source="Transformations reshape the query result in the browser (organize, "
        "merge, join, group by, calculate field, filter by value, sort).",
        newrelic="NRQL does the shaping: AS for rename, SELECT list for hide and order, FACET "
        "for group-by, arithmetic in SELECT for calculate field, WHERE or a nested query for "
        "filter, LIMIT and table sorting. Some have none (pivot, nested tables, config from "
        "data).",
        steps=(
            "Read the transformation list (Panel > Transform in Grafana). The common ones are "
            "easy: organize = AS and the SELECT order; filter fields = the SELECT list; sort "
            "by and limit = table sort and LIMIT.",
            "calculateField: put the arithmetic in the SELECT (`SELECT a / b AS ratio`).",
            "merge or joinByField: several queries in one widget (up to 20), or one query "
            "with several aggregates for the same facet.",
            "groupBy: `FACET` plus the aggregate.",
            "Transpose, pivot, nested table: no equal. Keep the table flat and add a second "
            "widget.",
        ),
        differs="Column order and naming follow the SELECT list; browser-side formatting "
        "(cell colours by rule, nested rows) is lost.",
        effort_minutes=25,
        template="SELECT latest(<metric>) AS 'Value' FROM Metric FACET <labels> LIMIT MAX",
        links=(L.NRQL, L.CHARTS),
    ),
)
reg(
    "Dashboard panel options",
    Playbook(
        source="Value mappings, overrides, a unit conversion or a calculation (mean, max) "
        "other than the latest value change what the panel shows.",
        newrelic="Widget settings (units, thresholds, null handling) and NRQL (`if()`, "
        "`FACET CASES`, `filter()`) for mappings.",
        steps=(
            "Value mapping `1 -> Up`: `SELECT if(latest(<metric>) = 1, 'Up', 'Down')` or "
            "`FACET CASES (WHERE latest... )`.",
            "Unit: set the unit in the widget settings; a scale factor goes in the query "
            "(`* 100`).",
            "Reducer (mean over the range): use average() without TIMESERIES.",
            "Colours by threshold: widget thresholds (critical, warning).",
        ),
        differs="Colours only for red, orange, yellow and green; a 0-100 percent unit reads "
        "1 as 100%, so scale the values.",
        effort_minutes=15,
        links=(L.CHARTS, L.NRQL),
    ),
)
reg(
    "Dashboard panel time override",
    Playbook(
        source="The panel uses its own time range (relative time or time shift).",
        newrelic="`SINCE ... UNTIL ...` in the widget's NRQL overrides the dashboard time "
        "picker for that widget.",
        steps=(
            "Add `SINCE 1 hour ago` (relative time) or `SINCE 2 days ago UNTIL 1 day ago` "
            "(shift) to the widget query.",
            "Name the range in the widget title so viewers know it ignores the picker.",
        ),
        differs="A widget with its own SINCE does not follow the time picker.",
        effort_minutes=5,
        template="SELECT <aggregate>(<metric>) FROM Metric SINCE 1 hour ago TIMESERIES AUTO",
        links=(L.NRQL,),
    ),
)
reg(
    (
        "Dashboard panel type the importer cannot create yet|gauge",
        "Dashboard panel type the importer cannot create yet|bargauge",
    ),
    Playbook(
        source="A gauge or bar gauge shows one value against a minimum, a maximum and "
        "threshold colours.",
        newrelic="The gauge chart type, a bullet chart, or a billboard with thresholds.",
        steps=(
            "Convert the panel's query first (see its other reasons).",
            "Add a widget, paste the query, pick Gauge (min, max and thresholds are set in "
            "the widget) or Bullet (limit) from the chart-type menu; for several values use "
            "a bar chart with FACET.",
        ),
        differs="The importer cannot create these widget types; the chart is made in the UI.",
        effort_minutes=10,
        links=(L.CHARTS,),
    ),
)
reg(
    "Dashboard panel type the importer cannot create yet|logs",
    Playbook(
        source="A list of the newest log lines.",
        newrelic="A log table widget over `Log`.",
        steps=(
            "Add a widget, choose the Log table, query "
            "`SELECT message FROM Log WHERE <filters> LIMIT 200`.",
            "Make sure the logs reach New Relic (agent or forwarder) with the attributes the "
            "Grafana stream selector used.",
        ),
        differs="Labels of the Loki stream become log attributes only if you forward them.",
        effort_minutes=15,
        template="SELECT message FROM Log WHERE <label> = '<value>' LIMIT 200",
        links=(L.PARSING, L.CHARTS),
    ),
)
reg(
    "Dashboard panel type the importer cannot create yet",
    Playbook(
        source="A bar chart, heatmap, histogram or similar panel.",
        newrelic="Bar (FACET), heatmap or histogram (`histogram()`), or the closest chart "
        "type in the menu.",
        steps=(
            "Convert the panel's query first.",
            "Add a widget with that query and pick the chart type: Bar needs a FACET; "
            "Histogram/Heatmap need `SELECT histogram(<attr>, <width>, <buckets>)`.",
        ),
        differs="A heatmap of value buckets by facet, not by time.",
        effort_minutes=10,
        template="SELECT histogram(<metric>, 10, 20) FROM Metric FACET <labels>",
        links=(L.CHARTS, L.NRQL),
    ),
)
reg(
    "Dashboard panel type with no New Relic widget",
    Playbook(
        source="A panel type with no widget in New Relic (state timeline, status history, "
        "geomap, node graph, canvas, flame graph, plugin panels).",
        newrelic="None. Best workaround: a table or billboard over the same data; for a state "
        "timeline a table with `FACET` and `latest()`; a map becomes a table by country/city.",
        steps=(
            "Decide what the viewer used it for (is something up, where is it, what changed).",
            "Build a table or billboard grid with `SELECT latest(<metric>) FACET <labels>` and "
            "thresholds for colour.",
            "Drop the panel if nobody used it (Grafana usage data).",
        ),
        differs="No timeline of states and no map.",
        effort_minutes=20,
        no_equivalent=True,
        template="SELECT latest(<metric>) FROM Metric FACET <labels> LIMIT MAX",
        links=(L.CHARTS,),
    ),
)
reg(
    (
        "Dashboard panel that reuses another panel's result",
        "Dashboard panel reusing a query that did not convert",
    ),
    Playbook(
        source="The panel shows another panel's query result ('-- Dashboard --' data source).",
        newrelic="No sharing of results; each widget runs its own query.",
        steps=(
            "Convert the source panel first, then copy its NRQL into this widget and apply "
            "this panel's own options.",
        ),
        differs="Two queries run instead of one.",
        effort_minutes=5,
        links=(L.NRQL,),
    ),
)
reg(
    ("Dashboard data source not supported", "Dashboard data source not known"),
    Playbook(
        source="A panel queries something other than Prometheus or Loki (CloudWatch, SQL, "
        "Elasticsearch, InfluxDB, Azure, Google Cloud, Tempo).",
        newrelic="Depends on how the data gets to New Relic: cloud integrations/Metric Streams "
        "for CloudWatch, Azure and Google; agents and OpenTelemetry for the rest. NRQL has no "
        "SQL, Lucene, Flux or TraceQL.",
        steps=(
            "Find out if the same data reaches New Relic (Infrastructure, cloud integrations, "
            "logs, agents). `FROM Metric SELECT uniques(metricName) WHERE metricName LIKE "
            "'aws.%'` shows what exists.",
            "CloudWatch, Azure, Google Cloud: use the cloud dashboard converters in this tool "
            "as a reference for the New Relic names.",
            "SQL or Elasticsearch: if the data is also sent to New Relic as events or logs, "
            "write the NRQL by hand; otherwise leave the panel out.",
        ),
        differs="Names, units and dimensions are New Relic's, not the source's.",
        effort_minutes=45,
        no_equivalent=True,
        links=(L.NRQL, L.OTLP),
    ),
)
reg(
    (
        "Dashboard LogQL parser stage",
        "Dashboard LogQL function",
        "Dashboard LogQL line filter",
        "Dashboard LogQL not converted",
    ),
    Playbook(
        source="LogQL pulls fields out of the log line at query time (| json, | logfmt, "
        "| pattern, unwrap) or uses a function other than count_over_time and rate.",
        newrelic="Log parsing rules at ingest (Grok or regex) make the field an attribute; "
        "at query time `aparse()`, `capture()` and `jparse()`.",
        steps=(
            "Query time: `SELECT count(*) FROM Log WHERE message LIKE '%error%' FACET "
            "aparse(message, '* status=% *') ...` (aparse/capture) for one-off charts.",
            "Better: Logs > Parsing > Create parsing rule with a Grok pattern, so the field is "
            "an attribute for every query and alert.",
            "unwrap: the number extracted becomes `average(attr)` or `sum(attr)` over the "
            "parsed attribute.",
            "Regex line filters: `message RLIKE '<regex>'`.",
        ),
        differs="A parsing rule applies to new logs only. Grok is not LogQL regexp syntax.",
        effort_minutes=30,
        template="SELECT count(*) FROM Log WHERE message LIKE '%<text>%' TIMESERIES AUTO",
        links=(L.PARSING, L.NRQL),
    ),
)
reg(
    (
        "Dashboard library panel not in the export",
        "Dashboard panel without a usable query",
        "Dashboard server-side expression",
    ),
    Playbook(
        source="A library panel whose definition is not in the export, a panel with no "
        "query, or a server-side expression over other queries.",
        newrelic="Export the library panel too, then convert; for expressions, write the math "
        "in one NRQL SELECT.",
        steps=(
            "Re-run the extraction with library panels included (`extract-grafana-dashboards` "
            "reads `library-panels.json`).",
            "Expression `$A / $B`: `SELECT filter(sum(a)) / filter(sum(b)) FROM Metric`.",
        ),
        differs="One query instead of a chain.",
        effort_minutes=20,
        links=(L.NRQL,),
    ),
)
