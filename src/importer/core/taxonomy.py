"""Failure taxonomy: sort free-text conversion reasons into a few named categories.

Rules are tried in order and the first match wins, so order matters (see the comments).
A reason that matches nothing is "uncategorised"; a test fails when a known reason lands there,
which is the prompt to add or adjust a rule here. Categories 1-4 usually stop a conversion;
5-7 are notes on something that did convert.
"""

import re
from enum import StrEnum


class Category(StrEnum):
    MISSING_MAPPING = "missing mapping"
    CANNOT_EMIT = "cannot be emitted (New Relic limits)"
    UNSUPPORTED_FEATURE = "unsupported feature or type"
    UNSUPPORTED_QUERY = "unsupported query or expression"
    UNVERIFIED_NAMES = "unverified names"
    APPROXIMATION = "approximate conversion"
    MANUAL_FOLLOWUP = "manual follow-up"
    OTHER = "uncategorised"


_RULES: list[tuple[Category, re.Pattern[str]]] = [
    (Category.MISSING_MAPPING, re.compile(r"no new relic mapping")),
    (Category.CANNOT_EMIT, re.compile(r"^not emitted")),
    (
        # Before the query rule: "percentile statistic" and "service check" would match it.
        Category.UNSUPPORTED_FEATURE,
        re.compile(
            r"monitor type|composite|anomaly|forecast|outlier|percentile|no new relic equivalent"
            r"|high-resolution|mql conditions|promql conditions|sql conditions|metric-absence"
            r"|ratio conditions|trigger on several|not one of the google cloud service metrics"
            r"|uptime check|davis problem notification workflow|workflow trigger is a"
            r"|workflow tasks are not|workflow tasks run on|slo is not converted automatically"
            r"|longer than one hour, the longest count window"
        ),
    ),
    # Before the approximation rule: the Metric Streams note says "assumed".
    (
        Category.UNVERIFIED_NAMES,
        re.compile(
            r"unverified|convention|not yet confirmed|check the rest|not confirmed|never arrived"
            r"|not others"
        ),
    ),
    (
        # Before the query rule: "differs from the query" is a note, not a parse failure.
        Category.APPROXIMATION,
        re.compile(
            r"approximated|was ignored|were dropped|collapsed|has no equivalent|is assumed"
            r"|converted to like|differs from|may differ|on its own|not part of the query"
            r"|a counter metric"
        ),
    ),
    (
        Category.UNSUPPORTED_QUERY,
        re.compile(
            r"formula|metric math|query|expression|scope|search syntax|rollup|service check"
            r"|unit conversion|statistic|characters|has no metric|dimension|filter|aligner|reducer"
            r"|second-stage|alignment period"
        ),
    ),
    (
        Category.MANUAL_FOLLOWUP,
        re.compile(
            r"configured manually|must be confirmed|not migrated|template variables"
            r"|the dynatrace trigger was inactive|slo criteria: target|the workflow was a draft"
        ),
    ),
]


def classify(reason: str) -> Category:
    text = reason.lower()
    for category, rule in _RULES:
        if rule.search(text):
            return category
    return Category.OTHER
