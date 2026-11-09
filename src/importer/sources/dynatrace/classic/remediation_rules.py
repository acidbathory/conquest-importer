"""Manual steps for Dynatrace classic findings. Registered with the shared engine on import.

Rules are first-match over every source's reasons, so each pattern starts with `^` and names the
start of one reason this source writes. `tests/test_dynatrace_classic_rules.py` gives each rule an
example and checks that it wins over all other registered rules and claims no other source's
reason.
"""

import re

from importer.core.remediation import Rule, register_rules

MAP_HOW = (
    "write a JSON file with the same layout as the map this tool ships (a `metrics` list of "
    "rows with `keys`, `event`, `attribute`, `unit_factor`, `dimensions`, `default_aggregation` "
    "and `status`), check it with `importer validate-dynatrace-map FILE`, and name it in the "
    "environment variable IMPORTER_DYNATRACE_METRIC_MAP when you convert"
)


def _rule(pattern: str, title: str, *steps: str) -> Rule:
    return re.compile(pattern), title, list(steps)


RULES: list[Rule] = [
    _rule(
        r"^the settings object could not be read",
        "Dynatrace object could not be read",
        "The reason names the field that did not fit the schema. Open the object in Dynatrace "
        "(Settings) and look at that field; an export from a newer or older tenant can differ "
        "from the schema this importer reads.",
        "Recreate the alert in New Relic by hand from what Dynatrace shows, or fix the export "
        "file and run the conversion again. The other objects were read.",
    ),
    _rule(
        r"^metric selector not converted",
        "Metric selector not converted",
        "Read the selector in the Dynatrace event: the metric, its aggregation, `:splitBy` "
        "dimensions and `:filter` terms. The reason says which part has no one-condition "
        "equivalent (several metrics, `:fold`, `:delta`, `:rollup`, `:timeshift`, an entity "
        "selector, a series filter).",
        "Write one NRQL condition per metric with the aggregation as a function, the split as "
        "FACET and the filters as WHERE. For a comparison with the past use a New Relic "
        "baseline condition instead of `:timeshift`.",
    ),
    _rule(
        r"^no new relic signal is known for dynatrace metric",
        "No New Relic data for this Dynatrace metric",
        "A converted condition on a metric that never reaches New Relic never fires. The reason "
        "says what must be installed or sent (an agent, an integration, or the same metric "
        "through OpenTelemetry).",
        "Once the data arrives, find its event and attribute in the data explorer, then "
        f"{MAP_HOW}. Give the row status unverified, convert again, and run "
        "`importer check-dynatrace-metrics`. Mark the row verified only after you have seen "
        "the values match Dynatrace.",
    ),
    _rule(
        r"^dimension .* is not in the metric map|^the metrics map dimension",
        "Dimension not in the metric map",
        "Dynatrace dimensions are often entity ids (`dt.entity.host`); New Relic uses names "
        "such as `hostname`. Find the New Relic attribute for the same thing and add it to the "
        "`dimensions` of the metric's row in the metric map, then convert again.",
    ),
    _rule(
        r"^the metrics live on different new relic events|^arithmetic (over|around) ",
        "Metrics cannot share one condition",
        "One NRQL condition reads one event type, so a ratio of metrics from different events "
        "(host and service data, for example) cannot be one condition. Pick the metric that "
        "matters, or send both metrics to the same event, and create the condition by hand.",
        "A unit conversion between metrics is not applied inside arithmetic: convert both "
        "sides to the same unit first.",
    ),
    _rule(
        r"^(the event is limited to a management zone|an entity filter|a filter on an entity id"
        r"|dimension filter operator"
        r"|a filter value)",
        "Filter that New Relic cannot express",
        "A management zone is a set of entities built by rules in Dynatrace. Reproduce its "
        "meaning with a tag or attribute that both sides share, and add it as a WHERE term.",
        "Tag, entity id and regular-expression tests need the same tag or id in New Relic. "
        "Filter values that contain `*`, `%` or `_` cannot be matched as plain text with LIKE; "
        "use an exact match or write the NRQL by hand.",
    ),
    _rule(
        r"^(the number of violating samples|violating samples \(|the query offset is"
        r"|query offset \d|aggregation window \d|duration \d)",
        "Samples or window out of range",
        "Dynatrace evaluates one-minute samples; the importer maps `violatingSamples` minutes "
        "to the breach duration. A value of zero, more violating samples than the sliding "
        "window, an offset over 20 minutes, or a duration over 24 hours cannot be created.",
        "Pick a duration between 1 minute and 24 hours and create the condition by hand.",
    ),
    _rule(
        r"^(alert condition .* needs a baseline model|alert condition .* is not supported"
        r"|model type |a static threshold event has no|the event has no metric"
        r"|query type |the selector gives no aggregation|aggregation .* is not supported)",
        "Metric event with an unsupported definition",
        "A static threshold event is ABOVE or BELOW. OUTSIDE needs one of the baseline models. "
        "Check the event in Dynatrace and create the New Relic condition by hand from the "
        "metric, aggregation and threshold you find there.",
    ),
    _rule(
        r"^baseline detectors",
        "Davis baseline detector",
        "Open the detector in the Anomaly Detection app and note the metric, the direction and "
        "the sensitivity. Create a New Relic baseline condition on the matching data and tune "
        "the band with the `NrAiSignal` histogram. The numbers do not convert: Dynatrace "
        "learns from 7 or 14 days, New Relic from 1 to 4 weeks.",
    ),
    _rule(
        r"^records mode detectors",
        "Davis records-mode detector",
        "A records-mode detector runs a DQL query that returns records. Work out what it "
        "counts, send that data to New Relic, and write an NRQL condition over it.",
    ),
    _rule(
        r"^(the detector (query|has no|analyzer)|detector input |alert condition .* needs a"
        r" baseline detector)",
        "Davis detector outside the supported pattern",
        "Only a static-threshold detector over `timeseries avg|min|max|sum(metric)` with an "
        "optional `by:{...}` converts. Open the detector, read the query and the inputs, and "
        "create the NRQL condition by hand over the matching New Relic data.",
    ),
    _rule(
        r"^[a-z ]+ is not a finite number$",
        "Number in the export is not finite",
        "The export holds a number too large to be a real value. Check the object in "
        "Dynatrace, correct it there if it is wrong, and export again.",
    ),
    _rule(
        r"^alerting profile is not converted",
        "Alerting profile",
        "An alerting profile chooses which problems reach which notifications. Recreate its "
        "filters (severity, tags, event types, delay) in the filter of a New Relic workflow "
        "and pair it with the notifications that name it.",
        "Severity levels are problem categories, not priorities: choose how they map to New "
        "Relic priorities. A delay has no workflow field.",
    ),
    _rule(
        r"^problem notification is not converted",
        "Problem notification",
        "Recreate each notification as a New Relic destination and channel and add it to a "
        "workflow. Addresses, tokens and webhook URLs are not exported; enter them again.",
        "Placeholders such as `{ProblemTitle}` must be rewritten as New Relic variables.",
    ),
    _rule(
        r"^maintenance window is not converted",
        "Maintenance window",
        "Create a New Relic muting rule with the same schedule and time zone. A muting rule "
        "silences notifications only; the alert events are still recorded, so a window that "
        "stopped problem detection does not match exactly.",
        "Scope the rule with a tag or condition name that the converted conditions carry.",
    ),
    _rule(
        r"^management zone is not converted",
        "Management zone",
        "A zone is a set of entities built by rules. Use the same idea in New Relic with tags "
        "on entities, or accounts, and use them as WHERE terms and workflow filters.",
    ),
    _rule(
        r"^built-in detection setting is not converted",
        "Built-in anomaly detection setting",
        "These are tuning values for automatic detection. Compare any non-default value with "
        "what you want in New Relic and create a condition for the cases that matter.",
    ),
    _rule(
        r"^settings schema is not converted",
        "Settings schema not read",
        "This source reads a fixed list of schemas. If the object is an alert you rely on, "
        "recreate it by hand or ask for the schema to be added.",
    ),
    _rule(
        r"^data prerequisite:",
        "The data must reach New Relic",
        "The condition watches a New Relic signal that exists only when the agent, integration "
        "or exporter in the reason is installed. Install it, then run "
        "`importer check-dynatrace-metrics` to see which conditions still have no data.",
    ),
    _rule(
        r"^metric name mapping ",
        "Metric name mapping not confirmed",
        "The New Relic name for this metric is taken from a mapping file, not from live data. "
        "Run the query in the data explorer, compare the values with the Dynatrace chart, and "
        f"set the row's status in your map file: {MAP_HOW}.",
    ),
    _rule(
        r"^\d+ of \d+ violating samples",
        "Violating samples approximated",
        "Dynatrace opens the event when some samples in a window breach, in any order. New "
        "Relic needs the breaches in a row for the duration, which is stricter. If flapping "
        "series must alert, lower the duration or use the signal-gap fill options by hand.",
    ),
    _rule(
        r"^(dealertingsamples|the query offset of|missing data counts as|davismerge is ignored)",
        "Timing differs from Dynatrace",
        "Recovery, the evaluation offset and missing-data handling work differently. Check "
        "the condition's close behaviour and its loss-of-signal setting after you enable it.",
    ),
    _rule(
        r"^(sensitivity \(|the dynatrace model|new relic needs one to four weeks"
        r"|dynatrace allows a baseline model)",
        "Baseline model differs",
        "The Dynatrace sensitivity does not convert to a number of standard deviations. Use "
        "the `NrAiSignal` histogram of the converted condition to choose the threshold, and "
        "expect weak signal for the first days while New Relic learns.",
    ),
    _rule(
        r"^(the :(count|median|value|last)|default\(|the sort is not applied"
        r"|the series limit is not applied|rate per minute|the added |setunit only|smooth\("
        r"|the metric map gives a fixed expression|splitting by an entity dimension"
        r"|entity filter conditions|a case-insensitive|a like filter)",
        "Selector detail not applied",
        "The reason lists a detail of the Dynatrace selector or filter that the converted "
        "condition does not reproduce. Compare the New Relic chart with the Dynatrace one "
        "before you enable the condition.",
    ),
    _rule(
        r"^the event type \w+ does not open a problem",
        "Event type that opens no problem",
        "Informational Dynatrace events (annotations, deployments, configuration changes) do not "
        "open problems and do not page anyone, so an alert condition is the wrong target.",
        "Record the same information in New Relic as a change-tracking marker or a deployment "
        "marker, and keep an alert condition only for events that open problems.",
    ),
    _rule(
        r"^the dynatrace (metric event|detector) was disabled",
        "Disabled in Dynatrace",
        "The Dynatrace object was switched off, so it was not alerting. Check with its owner "
        "that it is still wanted before you enable the New Relic condition.",
    ),
]

register_rules(RULES)
