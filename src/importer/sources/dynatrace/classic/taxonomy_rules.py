"""Taxonomy rules for the reasons of the Dynatrace classic source.

`register_taxonomy` tries these before the built-in rules, so every pattern is anchored with `^`
and lower case (the reason is lower-cased before matching).
"""

from importer.core.taxonomy import Category, register_taxonomy

RULES: list[tuple[Category, str]] = [
    (Category.MISSING_MAPPING, r"^no new relic signal is known for dynatrace metric"),
    (
        Category.MISSING_MAPPING,
        r"^dimension .* is not in the metric map|^the metrics map dimension",
    ),
    (Category.UNVERIFIED_NAMES, r"^metric name mapping "),
    (
        Category.UNSUPPORTED_FEATURE,
        r"^(alerting profile|problem notification|maintenance window|management zone"
        r"|built-in detection setting|settings schema) .*is not converted",
    ),
    (
        Category.UNSUPPORTED_FEATURE,
        r"^(baseline detectors|records mode detectors|the event is limited to a management zone"
        r"|an entity filter|a filter on an entity id|the event type \w+ does not open)",
    ),
    (
        Category.UNSUPPORTED_QUERY,
        r"^(metric selector not converted|the detector (query|has no|analyzer)|detector input "
        r"|the event has no metric|query type |the selector gives no aggregation"
        r"|aggregation .* is not supported|dimension filter operator|a filter value"
        r"|the metrics live on different|arithmetic (over|around) "
        r"|alert condition .* (needs a baseline|is not supported)|model type "
        r"|a static threshold event has no|[a-z ]+ is not a finite number$)",
    ),
    (
        Category.CANNOT_EMIT,
        r"^(the number of violating samples|violating samples \(|the query offset is"
        r"|query offset \d|aggregation window \d|duration \d)",
    ),
    (Category.UNSUPPORTED_FEATURE, r"^the settings object could not be read"),
    (
        Category.APPROXIMATION,
        r"^(\d+ of \d+ violating samples|dealertingsamples|the query offset of|missing data counts"
        r"|sensitivity \(|the dynatrace model|new relic needs one to four weeks"
        r"|dynatrace allows a baseline model|davismerge is ignored"
        r"|the :(count|median|value|last)|default\(|the sort is not applied"
        r"|the series limit is not applied|rate per minute|the added |setunit only|smooth\("
        r"|the metric map gives a fixed expression|splitting by an entity dimension"
        r"|a case-insensitive|a like filter)",
    ),
    (
        Category.MANUAL_FOLLOWUP,
        r"^(data prerequisite:|the dynatrace (metric event|detector) was disabled"
        r"|entity filter conditions)",
    ),
]

for category, pattern in RULES:
    register_taxonomy(category, pattern)
