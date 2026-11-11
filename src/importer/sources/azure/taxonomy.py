"""Failure taxonomy patterns for the Azure reasons (anchored, registered on import)."""

from importer.core.taxonomy import Category, register_taxonomy

PATTERNS: list[tuple[Category, str]] = [
    (
        Category.UNSUPPORTED_FEATURE,
        r"^(metric alert has several criteria|dynamic threshold|availability test alert"
        r"|unknown metric alert criteria type|activity log alert|service health or resource"
        r"|smart detector alert rule|scheduled query rule (kind|publishes)|log alert has several"
        r"|legacy log alert|log alert evaluates a longer|recording rule: an azure"
        r"|alert rule type is not recognised)",
    ),
    (
        Category.MISSING_MAPPING,
        r"^(metric namespace is not an azure platform|scope is application insights"
        r"|log alert scope is not|kql table comes from|scope is not a resource"
        r"|scope lists several)",
    ),
    (
        Category.UNSUPPORTED_QUERY,
        r"^(kql query cannot|metric name cannot be written|window is (longer|shorter)"
        r"|window or evaluation frequency|comparison operator is not|threshold is not a number"
        r"|time aggregation is not|dimension cannot be written|metric alert has no readable"
        r"|log alert has no readable|log alert measures|prometheus rule (has|contains))",
    ),
    (
        Category.UNVERIFIED_NAMES,
        r"^(metric name .* (is derived|is listed in|comes from name-map)"
        r"|attribute names .* are derived"
        r"|log attribute names|azure\.resourceid is matched|metric names are read as prometheus)",
    ),
    (
        Category.APPROXIMATION,
        r"^(azure evaluates|azure severity|severity .* is not 0 to 4|automitigate"
        r"|resolveconfiguration|time aggregation (count|total)|the same metric can arrive"
        r"|targetresourceregion|dimension .* splits|the azure rule is disabled"
        r"|muteactionsduration|kql (contains|bin|negated|has|dcount|any|comparison|time filter"
        r"|matches regex)|a count over an empty window|the count is wrapped"
        r"|\d+ failing periods|the azure metric name"
        r"|the rule group has)",
    ),
    (
        Category.MANUAL_FOLLOWUP,
        r"^(the table .* (has no counterpart|is filled by|is generated inside)"
        r"|the logs must reach new relic|kql .* after the aggregation)",
    ),
]

for _category, _pattern in PATTERNS:
    register_taxonomy(_category, _pattern)
