"""Manual steps and failure categories for every Datadog dashboard reason. Registered on import.

Anchored on the fixed start of each reason in `reasons.py`; every pattern starts with
`dashboard`, so none can shadow a monitor rule. Steps are guidance for a person; the tool carries
out none of them. Playbooks with the same titles are in `playbooks.py`.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy

RULES: list[Rule] = [
    (
        re.compile(r"^dashboard widget type has no converter yet"),
        "Datadog dashboard widget type not converted",
        [
            "Check the widget in Datadog and decide what question it answers. Most have a New "
            "Relic chart over the same data: a heatmap or distribution is a histogram() or "
            "percentile() chart, a change widget is a billboard with COMPARE WITH, a treemap or "
            "sunburst is a pie or bar over FACET. Some have none (check status, hostmap, flame "
            "graph, topology map): use the entity explorer, a service level or an alert instead.",
            "Build the chart in the New Relic query builder, then add it to the dashboard page "
            "that holds this placeholder and delete the placeholder.",
        ],
    ),
    (
        re.compile(r"^dashboard widget has no query"),
        "Datadog dashboard widget without a query",
        [
            "The widget holds no readable query (an image, a layout-only widget, or a request "
            "shape this converter does not know). Open it in Datadog; if it only decorates the "
            "dashboard, recreate it as a markdown widget, otherwise rebuild its query in NRQL.",
        ],
    ),
    (
        re.compile(r"^dashboard widget reads"),
        "Datadog dashboard widget on logs or another data source",
        [
            "Only metric queries are converted. For logs, send the logs to New Relic first, check "
            "the attribute names, then write `SELECT count(*) FROM Log WHERE ...` (add FACET and "
            "TIMESERIES as the widget needs). For RUM, APM, processes, cost and network data, "
            "find the matching New Relic event type or leave the widget out.",
        ],
    ),
    (
        re.compile(r"^dashboard widget has several queries"),
        "Datadog dashboard widget with several queries",
        [
            "One New Relic chart holds one series here. Build one widget per query, or write one "
            "NRQL query that selects several functions (`SELECT average(a), max(b) FROM Metric "
            "TIMESERIES`) when the metrics live on the same event.",
        ],
    ),
    (
        re.compile(r"^dashboard (widget uses a formula|query uses arithmetic)"),
        "Datadog dashboard formula",
        [
            "A formula combines several queries point by point. In NRQL put both terms in one "
            "SELECT when they share an event (`SELECT sum(a) / sum(b) FROM Metric`), or use a "
            "subquery. The result is a ratio of window aggregates, which differs from Datadog's "
            "point-by-point ratio; compare the two on a day of data before relying on it.",
        ],
    ),
    (
        re.compile(r"^dashboard query function"),
        "Datadog dashboard query function",
        [
            "Dashboards can do what alerts cannot, so most of these have a New Relic form: top() is"
            " FACET with ORDER BY and LIMIT, rollup() is TIMESERIES with a bucket size, "
            "timeshift() is COMPARE WITH, as_rate() and per_second() are "
            "rate(sum(x), 1 second), derivative() is derivative(). The converter applies top(), "
            "as_count(), as_rate(), per_second(), fill() and rollup() of avg, min or max (or sum "
            "with daily); a reason that names one of them says which form was not safe (an "
            "argument it does not read, a gauge, a bucket size that changes a sum). Write the NRQL "
            "by hand.",
            "weighted(), autosmooth() and the statistical functions have no direct NRQL "
            "equivalent; rebuild the chart with the closest aggregate and say so in its title.",
        ],
    ),
    (
        re.compile(r"^dashboard query could not be read"),
        "Datadog dashboard query not readable",
        [
            "The query text is not a metric query this converter reads (a typo, a function it "
            "does not parse, or an unusual form). Copy it from the widget editor in Datadog, "
            "check that it is valid there, and rebuild it in NRQL by hand.",
        ],
    ),
    (
        re.compile(r"^dashboard query_value"),
        "Datadog dashboard single value",
        [
            "A single value shows one number for the whole time range. Datadog's `last` takes "
            "the newest point of the series after combining hosts; NRQL latest() returns one raw "
            "data point, which is a different number when several hosts report. Pick avg, sum, "
            "min or max in Datadog, or write `SELECT latest(x)` only if one series reports.",
            "A single value cannot be grouped: remove the `by {tag}`, or use a table or bar "
            "chart instead.",
        ],
    ),
    (
        re.compile(r"^dashboard template variable (is not defined|name|is not accepted)"),
        "Datadog dashboard template variable not usable",
        [
            "The query uses a template variable that the dashboard does not define, or whose "
            "name or default New Relic would not accept (names are letters, digits and "
            "underscores, up to 64). Rename it in Datadog or create the New Relic variable by "
            "hand (Dashboard > + Add variable) and use `IN ({{name}})` in the query.",
        ],
    ),
    (
        re.compile(r"^dashboard template variable (is used in a form|stands for)"),
        "Datadog dashboard template variable form",
        [
            "Only a variable that stands for the values of one tag, in a filter such as "
            "`{$host}` or `{host:$host}`, becomes a New Relic variable. Group-by variables, "
            "`$var.value`, wildcards, IN lists and variables inside OR or NOT do not. Create the "
            "New Relic variable by hand (type NRQL, `SELECT uniques(tag) FROM Metric`) and write "
            "the filter as `WHERE tag IN ({{name}})`; a variable cannot be used in FACET.",
        ],
    ),
    (
        re.compile(r"^dashboard template variable default"),
        "Datadog dashboard template variable default",
        [
            "The variable has no tag key and its default is a scope the converter cannot write "
            "as a WHERE clause. Replace the variable in the query with the scope you want, or "
            "create a tag variable and filter on it.",
        ],
    ),
    (
        re.compile(r"^dashboard widget was refused"),
        "Datadog dashboard widget refused by the dashboard model",
        [
            "The converted widget broke a New Relic dashboard rule (a length, a character or a "
            "variable placement). Rebuild it in the query builder; keep variables out of FACET, "
            "LIMIT and TIMESERIES.",
        ],
    ),
]
register_rules(RULES)

TAXONOMY: list[tuple[Category, str]] = [
    (
        Category.UNSUPPORTED_FEATURE,
        r"^dashboard (widget type has no converter|widget reads|widget has several"
        r"|widget has no query)",
    ),
    (
        Category.UNSUPPORTED_QUERY,
        r"^dashboard (widget uses a formula|query |template variable (is not defined|name"
        r"|is used in a form|stands for|default|is not accepted)|widget was refused)",
    ),
    (
        Category.APPROXIMATION,
        r"^(dashboard (sum adds|widget has its own time range|note text is copied"
        r"|template variable has no tag key)|bars over time|new relic shows at most"
        r"|number formats, conditional|\d+ widgets overlapped|rate functions are written"
        r"|the space aggregator .* was replaced by sum|rollup\(\) bucket size"
        r"|fill\(\) is not carried"
        r"|top\(\) is a facet|rollup\(daily\) is written)",
    ),
    (Category.CANNOT_EMIT, r"^\d+ widgets are beyond the new relic page limits"),
]
for _category, _pattern in TAXONOMY:
    register_taxonomy(_category, _pattern)
