"""Failure taxonomy patterns for the Datadog reasons (anchored, registered on import)."""

from importer.core.taxonomy import Category, register_taxonomy

PATTERNS: list[tuple[Category, str]] = [
    (
        Category.UNSUPPORTED_FEATURE,
        r"^(anomaly monitor|outlier monitor|forecast monitor|change alert|cumulative window"
        r"|composite monitor|service check monitor|event monitor|slo alert|synthetics monitor"
        r"|process monitor|rum monitor|trace analytics monitor|ci monitor|audit monitor"
        r"|error tracking monitor|database monitoring monitor|network monitor|cost monitor"
        r"|data observability monitor|llm observability monitor|monitor type is not one datadog"
        r"|monitor has no readable type|multi-query formula monitor)",
    ),
    (
        Category.MISSING_MAPPING,
        r"^(metric name cannot be written|tag cannot be written|queries in the formula live)",
    ),
    (
        Category.UNSUPPORTED_QUERY,
        r"^(query could not be read|query was read only in part|query function|scope cannot"
        r"|scope uses a template|queries in the formula group|query has no metric|window is"
        r"|threshold is missing|comparison operator|log rollup|log search cannot|log window)",
    ),
    (
        Category.UNVERIFIED_NAMES,
        r"^(metric names (are unverified|come from name-map)|tag names are unverified"
        r"|log attribute names are unverified)",
    ),
    (
        Category.APPROXIMATION,
        r"^(the time aggregator|the formula is evaluated|as_count\(\) is read|the metric is a"
        r"|datadog evaluates|the \d+s window|evaluation_delay|no data:|no_data_timeframe"
        r"|on_missing_data|priority p\d|log index|datadog log search|free text in the search"
        r"|the count is wrapped|critical threshold in options|a unit scale)",
    ),
    (
        Category.MANUAL_FOLLOWUP,
        r"^message contains \d+ @handles",
    ),
]

for _category, _pattern in PATTERNS:
    register_taxonomy(_category, _pattern)
