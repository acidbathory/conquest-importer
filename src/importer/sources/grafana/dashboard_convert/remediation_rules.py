"""Manual steps and categories for the converter's own reasons.

The remediation engine is first-match over one list shared by every source, so each pattern is
anchored at the start of the reason and names the converter's wording. A reason the planner gives
(`dashboard ...`) keeps the planner's rule. `tests/test_grafana_dashboard_convert_rules.py` checks
that every reason has a rule and that no other source's reason is claimed.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.grafana.dashboard_convert import reasons as r

C = Category
FIRST = r.PREFIX + ": "

# (reason, rule title, steps, category)
ROWS: list[tuple[str, str, list[str], Category]] = [
    (
        r.C_VAR_NAME,
        "Dashboard query with a variable inside a name",
        [
            "A New Relic variable is substituted whole into a query, so a variable inside a "
            "metric or label name cannot be carried. Write the names for the values you use, one "
            "widget per value, or give the variable a `identifier` replacement strategy and use "
            "it as a whole attribute name.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_VAR_MATCHER,
        "Dashboard label matcher mixing text and a variable",
        [
            "Rewrite the filter so the variable stands alone: `attr IN ({{var}})`. For a prefix "
            "or suffix, make the variable's values carry it (a variable query with a "
            "`LIKE` filter, or an enum with the full values).",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_BUILTIN,
        "Dashboard query using $__interval or $__range outside a range",
        [
            "The size of the time bucket is chosen by New Relic (`TIMESERIES AUTO`), so a "
            "formula that uses the bucket in arithmetic has no counterpart. Replace it with a "
            "fixed number of seconds or rebuild the formula with `rate()`.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_EVENTS,
        "Dashboard query over metrics on different New Relic events",
        [
            "One NRQL query reads one event. The name map puts these metrics on different "
            "events. Send both metrics to the same event, or split the panel in two widgets and "
            "do the arithmetic in your head or in a third widget with `FROM` subqueries.",
        ],
        C.MISSING_MAPPING,
    ),
    (
        r.C_FILTERS,
        "Dashboard percentile next to terms with other filters",
        [
            "`bucketPercentile` cannot be wrapped in `filter()`. Build the percentile as its own "
            "widget with its filters in `WHERE`, and the other term in another widget.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_SHAPE,
        "Dashboard query the converter does not write",
        [
            "The coverage plan read this query as convertible but the converter has no NRQL for "
            "its exact shape. Write the NRQL by hand from the query text in the placeholder. "
            "Please report the query shape so the converter can learn it.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_NAME,
        "Dashboard name that NRQL rejects",
        [
            "A metric or label name has a character NRQL does not accept as an attribute name. "
            "Use the name the metric has in New Relic (check it with `keyset()`) and put it in "
            "the name map.",
        ],
        C.MISSING_MAPPING,
    ),
    (
        r.C_NRQL,
        "Dashboard converted query refused by the checks",
        [
            "The converted query is longer than the importer's limit or fails its structural "
            "checks. Shorten the expression or write the NRQL by hand.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_VARIABLE_UNDEFINED,
        "Dashboard query using a variable that did not convert",
        [
            "The variable this query filters on did not become a New Relic variable (see its "
            "own reason). Rebuild the variable by hand, then use `{{name}}` in the query.",
        ],
        C.UNSUPPORTED_FEATURE,
    ),
    (
        r.C_METRICNAME,
        "Dashboard label named metricName",
        [
            "In New Relic `metricName` is the attribute that holds the metric name, so a "
            "Prometheus label with that name collides with it. Rename the label where the data "
            "is sent (a relabel rule), then filter on the new name.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_METRICS_RENAMED,
        "Dashboard metric list with a name map",
        [
            "The variable lists Prometheus metric names, but the name map sends some metrics "
            "to other events or attributes, so `uniques(metricName)` would list other names. "
            "Write the list by hand as an `enum` variable.",
        ],
        C.UNSUPPORTED_FEATURE,
    ),
    (
        r.C_LIKE_PERCENT,
        "Dashboard pattern with a percent sign",
        [
            "NRQL `LIKE` treats `%` as a wildcard and cannot match it literally. Use `RLIKE` "
            "or a different filter, and test it against the data.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_AGG_RATE,
        "Dashboard average, minimum or maximum of rates",
        [
            "New Relic stores one change per scrape, so `rate(average(c), 1 second)` divides one "
            "sample by the whole bucket and reads too low. Use `rate(sum(c), 1 second)` and divide "
            "by `uniqueCount()` of the label that names one series (for example `cpu`), or "
            "FACET by that label and read each series.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_GAUGE_SERIES,
        "Dashboard sum or count across the series of a gauge",
        [
            "A New Relic `sum()` of a gauge adds every sample, not the latest value of each "
            "series: measured on real node_exporter data it was 4x too high per 60 s bucket "
            "(4 scrapes) and 41x as a stat over 10 minutes (41 scrapes), and 60 against 1 on "
            "a single series. `latest()` or `average()` is right only when the group holds "
            "one series, which the converter cannot see, so it did not guess.",
            "Per-series latest, summed: `SELECT sum(v) FROM (SELECT latest(metric) AS v FROM "
            "Metric FACET <labels that name one series> LIMIT MAX) SINCE 5 minutes ago`. "
            "Per-series values alone: `SELECT latest(metric) FROM Metric FACET <labels> "
            "LIMIT MAX`.",
            "For a chart where each series reports once per bucket, `SELECT sum(metric) ... "
            "TIMESERIES <your scrape interval>` is exact; check the interval first. A sum over "
            "a one-sample window (`SINCE <scrape interval> ago`) is also exact.",
            "To let the importer convert it, add the labels that name one series to the name "
            'map: `{"series": {"<metric>": ["<label>"]}}`, and group or pin by them. '
            "For count(), use `uniqueCount(<label that names one series>)`.",
        ],
        C.UNSUPPORTED_QUERY,
    ),
    (
        r.C_REUSE,
        "Dashboard panel reusing a query that did not convert",
        [
            "This panel shows the result of another panel (`-- Dashboard --`) that did not "
            "convert. Convert the source panel first, then copy its query here.",
        ],
        C.MANUAL_FOLLOWUP,
    ),
    (
        r.C_UNEXPECTED,
        "Dashboard panel could not be read",
        [
            "A value in the panel was outside what the importer accepts. Open the panel in "
            "Grafana and rebuild it in New Relic from its query.",
        ],
        C.MANUAL_FOLLOWUP,
    ),
]


def _pattern(reason: str) -> str:
    return "^" + re.escape(reason[:60])


RULES: list[Rule] = [(re.compile(_pattern(text)), title, steps) for text, title, steps, _ in ROWS]

register_rules(RULES)
for _text, _title, _steps, _category in ROWS:
    register_taxonomy(_category, _pattern(_text).lower())  # taxonomy text is lower case
register_taxonomy(C.APPROXIMATION, "^" + re.escape(r.NOTE.lower()))
