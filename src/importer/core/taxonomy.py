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
            r"|high-resolution"
        ),
    ),
    # Before the approximation rule: the Metric Streams note says "assumed".
    (Category.UNVERIFIED_NAMES, re.compile(r"unverified|convention")),
    (
        # Before the query rule: "differs from the query" is a note, not a parse failure.
        Category.APPROXIMATION,
        re.compile(
            r"approximated|was ignored|were dropped|collapsed|has no equivalent|is assumed"
            r"|converted to like|differs from|may differ"
        ),
    ),
    (
        Category.UNSUPPORTED_QUERY,
        re.compile(
            r"formula|metric math|query|expression|scope|search syntax|rollup|service check"
            r"|unit conversion|statistic|characters|has no metric|dimension"
        ),
    ),
    (
        Category.MANUAL_FOLLOWUP,
        re.compile(r"configured manually|must be confirmed|not migrated|template variables"),
    ),
]


def classify(reason: str) -> Category:
    text = reason.lower()
    for category, rule in _RULES:
        if rule.search(text):
            return category
    return Category.OTHER
