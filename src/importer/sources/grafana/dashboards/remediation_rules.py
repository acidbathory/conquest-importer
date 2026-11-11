"""Manual steps for dashboard reasons, and the taxonomy category of each.

The remediation engine is first-match over one list shared by every source, so each pattern
is anchored at the start of the reason and names the dashboard wording.
`tests/test_grafana_dashboards_rules.py` checks that each rule wins for its own example and
that no other source's reason is claimed.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy

C = Category

RULES: list[Rule] = [
    (
        re.compile(r"^dashboard panel type [a-z-]+ has no new relic widget"),
        "Dashboard panel type with no New Relic widget",
        [
            "New Relic has no widget for this panel type. Decide what the panel was for. A state "
            "timeline or status history can become a table, or a line chart of a 0 or 1 series. "
            "An alert list is replaced by the alert pages of New Relic. A dashboard list can be "
            "a markdown widget with links. Geomap, node graph, traces, flame graph, canvas, "
            "scatter and candlestick panels have no counterpart: rebuild them with another "
            "chart or leave them out.",
        ],
    ),
    (
        re.compile(r"^dashboard panel type [a-z-]+ maps to a new relic widget"),
        "Dashboard panel type the importer cannot create yet",
        [
            "New Relic has a widget for this, but the importer cannot create it yet. Build it "
            "by hand from the query that did convert: a gauge becomes a billboard with "
            "thresholds, a bar gauge or bar chart becomes a bar chart with FACET, a heatmap or "
            "histogram uses NRQL `histogram()`, and a logs panel becomes a log table over "
            "`SELECT * FROM Log`. The plan lists the query tier, so you know whether the "
            "query itself converts.",
        ],
    ),
    (
        re.compile(r"^dashboard panel uses a variable that stays manual"),
        "Dashboard panel using a variable that stays manual",
        [
            "A variable the panel's query depends on cannot be converted (see the variable's own "
            "reason in the report). Until it is rebuilt as a New Relic variable, or its values "
            "are written into the query, the filter in this panel cannot be written.",
        ],
    ),
    (
        re.compile(r"^dashboard panel overrides the time range"),
        "Dashboard panel time override",
        [
            "`timeShift` and `timeFrom` change the range of one panel, like `offset`. In NRQL use "
            "`COMPARE WITH`, or `SINCE` and `UNTIL` with the widget set to ignore the dashboard "
            "time range. How the two interact in a widget is not confirmed: check the result "
            "against Grafana.",
        ],
    ),
    (
        re.compile(r"^dashboard panel has options that change"),
        "Dashboard panel options",
        [
            "Value mappings, field overrides, a unit that rescales the value, or a reducer "
            "other than the latest value change what the panel shows, and the query is already "
            "at the highest tier. Rebuild the options by hand (thresholds for colours, `if()` "
            "or FACET CASES for mappings, the unit setting of the widget) and compare the "
            "result.",
        ],
    ),
    (
        re.compile(r"^dashboard panel has transformations"),
        "Dashboard panel with transformations",
        [
            "Grafana reshapes the result in the browser after the query. New Relic cannot do "
            "that, so the query itself has to change. A rename or a hidden column (the "
            "`organize` transformation) is `AS` and the SELECT list. Merge, join, group-by and "
            "sort become FACET, ORDER BY and LIMIT, or a subquery. Anything else needs a "
            "different panel. Check the result against Grafana.",
        ],
    ),
    (
        re.compile(r"^dashboard panel is a library panel"),
        "Dashboard library panel not in the export",
        [
            "The dashboard only names a library panel; its query lives in the library. Extract "
            "again with a token that has `library.panels:read` (the Viewer role plus library "
            "panel access), or open the library panel in Grafana and copy its query by hand. "
            "`extract-grafana-dashboards` writes the library to `library-panels.json`.",
        ],
    ),
    (
        re.compile(r"^dashboard (?:panel has no query|query is empty)"),
        "Dashboard panel without a usable query",
        [
            "The panel has no visible query, or the query text is empty or not text. Open the "
            "panel in Grafana. A panel that only shows static text should be a text panel; "
            "otherwise the query was lost or is hidden.",
        ],
    ),
    (
        re.compile(r"^dashboard panel uses a server-side expression"),
        "Dashboard server-side expression",
        [
            "The panel computes over other queries with a Grafana expression (math, reduce, "
            "resample). Write the calculation as one NRQL query: arithmetic between "
            "aggregations on the same event and facets, or a subquery. Hidden queries that fed "
            "the expression are only needed if the NRQL still uses them.",
        ],
    ),
    (
        re.compile(r"^dashboard panel reuses the result"),
        "Dashboard panel that reuses another panel's result",
        [
            "The panel shows the result of another panel (data source `-- Dashboard --`). It "
            "converts only when that other panel does. Fix the other panel first, or give "
            "this panel its own query.",
        ],
    ),
    (
        re.compile(r"^dashboard data source [a-z ]+ is not supported"),
        "Dashboard data source not supported",
        [
            "Only Prometheus and Loki queries are read. The data behind this panel lives "
            "elsewhere (CloudWatch, SQL, Elasticsearch, InfluxDB, Azure Monitor, Google Cloud "
            "Monitoring, Tempo, Graphite). If the same data reaches New Relic, write the NRQL "
            "yourself; the CloudWatch and Google dashboard converters in this tool can be used "
            "as a reference. Otherwise leave the panel out.",
        ],
    ),
    (
        re.compile(r"^dashboard data source could not be told"),
        "Dashboard data source not known",
        [
            "The panel's data source is a template variable or an input that the export does "
            "not resolve. Open the dashboard in Grafana, note which data source the variable "
            "holds, and extract again from a stack where it is set, or treat the panel as "
            "belonging to that data source's family.",
        ],
    ),
    (
        re.compile(r"^dashboard query (?:is longer than|uses a variable format)"),
        "Dashboard query too long or with a variable format",
        [
            "The query is longer than the importer reads, or uses a variable format such as "
            "`${name:csv}` that decides quoting in Grafana. Split a long query into smaller "
            "ones. For a format, write the New Relic variable with `IN ({{name}})` and pick the "
            "replacement strategy that matches the quoting you need.",
        ],
    ),
    (
        re.compile(r"^dashboard query uses (?:a comparison|vector matching)"),
        "Dashboard query comparison or vector matching",
        [
            "Comparisons (`> 0`, `== 1`), `and`, `or`, `unless`, `on()` and `group_left` filter or "
            "join series. NRQL has no filter after an aggregation. Express the condition with "
            "WHERE before the aggregation, or a subquery `FROM (SELECT ...) WHERE`, or drop "
            "the filter and use a threshold on the widget.",
        ],
    ),
    (
        re.compile(
            r"^dashboard query uses (?:offset|a subquery|absent|an _over_time|a range function"
            r"|without)"
        ),
        "Dashboard query time or range function",
        [
            "`offset` is `COMPARE WITH` or a shifted `SINCE`; `*_over_time` is an aggregate per "
            "`TIMESERIES` bucket, optionally with `SLIDE BY`; `deriv` and "
            "`predict_linear` have `derivative()` and `predictLinear()`; `delta` of a gauge is "
            "not `latest() - earliest()` (Prometheus stretches the first and last sample to the "
            "window), so write that difference by hand only if an approximation is enough and say "
            "so on the widget; `absent()` is an alert "
            "condition on a missing signal, not a chart; `without()` lists the labels to "
            "keep, which FACET needs by name. Build the NRQL by hand and compare.",
        ],
    ),
    (
        re.compile(r"^dashboard query uses (?:bottomk|topk)"),
        "Dashboard query bottomk or topk with a variable",
        [
            "`topk(n, ...)` converts to the same query with `LIMIT n`, but only when n is a "
            "plain number: New Relic does not allow a variable in LIMIT. `bottomk` has no "
            "direct form: use `ORDER BY ... ASC LIMIT n` on the faceted query.",
        ],
    ),
    (
        re.compile(r"^dashboard query (?:rewrites labels|uses a function)"),
        "Dashboard query function",
        [
            "`label_replace` and `label_join` derive a label; in NRQL a derived FACET uses "
            "`capture()` or `aparse()`. Math functions (`round`, `clamp_*`, `abs`, `ceil`) have "
            "`round()`, `clamp_min()` and `clamp_max()` in NRQL: the importer writes `round(x)`, "
            "`round(x, n)` for a multiple of 0.1, 0.01, ... and `clamp_min`/`clamp_max` with a "
            "plain number around a whole query, and leaves the rest. `round(x, 5)` is "
            "`round(x / 5) * 5`; `time()` is the evaluation instant and a chart has no such "
            "value (`aggregationendtime()` is the end of each bucket, which is a different "
            "number); `vector()` and `scalar()` have none. Rewrite the query by hand.",
        ],
    ),
    (
        re.compile(r"^dashboard query aggregates an aggregation"),
        "Dashboard query nested aggregation",
        [
            "An aggregation over another aggregation (for example `avg(sum by (a, b) (...)) by "
            "(a)`) needs a NRQL subquery `FROM (SELECT ...)`. Write it by hand and check the "
            "result against Grafana, because the inner TIMESERIES bucket changes the answer.",
        ],
    ),
    (
        re.compile(r"^dashboard query reads a counter metric"),
        "Dashboard query on a raw counter",
        [
            "A cumulative counter drawn without `rate()` has no equal in New Relic, which stores "
            "counters differently. Decide what the panel shows. For a total over the range use "
            "`increase()` in Grafana first (it converts); for a per-second view use `rate()`. "
            "Check in the sandbox how the counter arrives.",
        ],
    ),
    (
        re.compile(r"^dashboard query has a (?:label matcher|metric or label name|regex matcher)"),
        "Dashboard query label matcher or name",
        [
            "A matcher on `__name__`, a regex with lookahead or a backreference (RE2, which "
            "`RLIKE` uses, has neither), or a name that is not valid in NRQL cannot be written "
            "as is. Rewrite the matcher with plain alternatives, or use WHERE with `RLIKE` on "
            "the attribute.",
        ],
    ),
    (
        re.compile(r"^dashboard query (?:combines series|groups by a variable)"),
        "Dashboard query grouping",
        [
            "Series grouped by different labels do not match one to one, and a variable as a "
            "group-by label cannot be used after FACET in every position. Group both sides by the "
            "same labels, or use a subquery. A group-by variable can become a `string` variable "
            "with the identifier strategy; check it in the sandbox.",
        ],
    ),
    (
        re.compile(r"^dashboard query uses histogram_quantile"),
        "Dashboard histogram_quantile shape",
        [
            "Only `histogram_quantile(q, sum by (le, ...) (rate(metric_bucket[w])))` converts "
            "(to `bucketPercentile`). Other shapes, or buckets without the `_bucket` suffix or "
            "without `le` in the grouping, need a hand-written query.",
        ],
    ),
    (
        re.compile(r"^dashboard query (?:uses rate, irate|is not a shape)"),
        "Dashboard query not converted",
        [
            "The query is not one of the shapes the plan reads: a selector, optionally "
            "aggregated, with rate, increase, histogram_quantile, topk or simple arithmetic. "
            "Open the panel in Grafana, split the expression into parts you can express in NRQL, "
            "and build it by hand.",
        ],
    ),
    (
        re.compile(r"^dashboard logql query uses a parser stage"),
        "Dashboard LogQL parser stage",
        [
            "`| json`, `| logfmt`, `| pattern`, `| regexp`, `| line_format` and `unwrap` "
            "extract fields from the line. In NRQL use `aparse()` or `capture()` on the "
            "message, or ship the fields as log attributes and filter on them.",
        ],
    ),
    (
        re.compile(r"^dashboard logql query uses a function"),
        "Dashboard LogQL function",
        [
            "Only `count_over_time` and `rate` over a stream selector with line filters are "
            "read. Other functions (`bytes_rate`, `*_over_time` on unwrapped values, `topk`, "
            "`label_replace`) need a hand-written NRQL query on `Log`.",
        ],
    ),
    (
        re.compile(r"^dashboard logql query (?:uses a regular-expression|has line filter)"),
        "Dashboard LogQL line filter",
        [
            "A regular-expression line filter that is not a plain word, or text with `*` or "
            "`%`, is not carried over. Use `message RLIKE '...'` or `LIKE` with escaped "
            "wildcards in WHERE.",
        ],
    ),
    (
        re.compile(r"^dashboard logql query (?:has a label matcher|is not a count)"),
        "Dashboard LogQL not converted",
        [
            "The query is not a count or rate of a stream selector with line filters. Stream "
            "labels become attributes on `Log`; check they exist, then write the NRQL by hand.",
        ],
    ),
    (
        re.compile(r"^dashboard variable regex uses"),
        "Dashboard variable regex",
        [
            "The variable's regex is more than plain words: lookahead, a backreference, a capture "
            "group, anchors or a pattern that needs `RLIKE`, which is not verified. "
            "Filter in the variable's NRQL with WHERE conditions, or list the values "
            "to keep in an `enum` variable.",
        ],
    ),
    (
        re.compile(
            r"^dashboard variable query "
            r"(?:is not label_values|is query_result|runs on a data source)"
        ),
        "Dashboard variable query",
        [
            "Only these Prometheus variable queries become a New Relic `nrql` variable "
            "(`FROM Metric SELECT uniques(label) ...`): `label_values(metric, label)`, "
            "`label_values(label)`, `metrics(regex)` and `query_result(selector)` with a regex "
            "that captures one label value. Anything else (`query_result` of an expression, "
            "`label_names`, Loki, CloudWatch, SQL) needs the list written by hand, as an `enum` "
            "variable or an NRQL query with `uniques()` or `keyset()`.",
        ],
    ),
    (
        re.compile(r"^dashboard variable query depends on (?:a variable|itself)"),
        "Dashboard nested variable",
        [
            "A child variable can only be written when every variable its query uses is written "
            "too, and the variables do not depend on each other in a circle. Fix the parent "
            "variable first (see its own reason), or make the child independent. Nested "
            "variables also need a New Relic plan that offers them.",
        ],
    ),
    (
        re.compile(r"^dashboard variable is an ad hoc"),
        "Dashboard ad hoc variable",
        [
            "Ad hoc filters and group-by variables add any label at view time. New Relic has no "
            "such variable. A group-by can be a `string` variable used after FACET; an ad hoc "
            "filter has no counterpart.",
        ],
    ),
    (
        re.compile(r"^dashboard variable name is"),
        "Dashboard variable name",
        [
            "The name has characters other than letters, digits and underscore, or is used "
            "twice, so it cannot be a New Relic variable name. Rename it in Grafana and "
            "extract again, or name the variable by hand and replace it in the queries.",
        ],
    ),
    (
        re.compile(r"^dashboard variable type is not"),
        "Dashboard variable type",
        [
            "The variable type is not one the plan knows. Look at what it holds in Grafana and "
            "recreate it as an `nrql`, `enum` or `string` variable.",
        ],
    ),
    (
        re.compile(r"^dashboard annotations are not carried over"),
        "Dashboard annotations",
        [
            "New Relic dashboards have no annotation layer, so annotation queries are dropped "
            "and only listed. To keep deploy markers, use the change tracking feature of New "
            "Relic or add the event as a faceted series on the chart.",
        ],
    ),
]

# category of each family of reasons, by the same anchored start the rules use
TAXONOMY: list[tuple[Category, str]] = [
    (C.UNSUPPORTED_FEATURE, r"^dashboard panel type \S+ (?:has no|maps to a)"),
    (
        C.UNSUPPORTED_FEATURE,
        r"^dashboard panel (?:has transformations|uses a variable|overrides the time|has options)",
    ),
    (C.UNSUPPORTED_FEATURE, r"^dashboard data source"),
    (C.UNSUPPORTED_FEATURE, r"^dashboard variable"),
    (C.UNSUPPORTED_QUERY, r"^dashboard (?:logql )?query"),
    (
        C.UNSUPPORTED_QUERY,
        r"^dashboard panel (?:has no query|uses a server-side|reuses the result)",
    ),
    (C.MANUAL_FOLLOWUP, r"^dashboard panel is a library panel"),
    (C.MANUAL_FOLLOWUP, r"^dashboard annotations"),
]

register_rules(RULES)
for category, pattern in TAXONOMY:
    register_taxonomy(category, pattern)
