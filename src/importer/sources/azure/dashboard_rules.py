"""Manual steps and failure categories for every Azure dashboard reason. Registered on import.

Anchored on the fixed start of each reason in `dashboard_reasons.py`; a test fails when a
placeholder on a fixture has no rule. Steps are guidance for a person; the tool carries out none.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy

RULES: list[Rule] = [
    (
        re.compile(r"^workbook content could not be read"),
        "Azure workbook content not readable",
        [
            "The export holds the workbook but not its content, or the content is not valid "
            "JSON. Read it again with `extract-azure --dashboards` (it asks for canFetchContent), "
            "or open the workbook in the portal, choose Edit, Advanced editor, and save the "
            "Gallery Template JSON as a `.workbook` file next to the export.",
        ],
    ),
    (
        re.compile(r"^workbook has no items"),
        "Azure empty workbook",
        ["Nothing to convert: the workbook has no items. Delete it from the export."],
    ),
    (
        re.compile(r"^workbook conditional visibility"),
        "Azure workbook conditional visibility",
        [
            "A New Relic widget is always shown. If the condition picks one of several views, "
            "make each view its own dashboard page (the importer does this for tabs), or give "
            "the dashboard a variable and build one widget per value.",
            "If the item is hidden unless something is wrong, chart it unconditionally and use "
            "an alert condition for the unhealthy case instead.",
        ],
    ),
    (
        re.compile(r"^workbook item depends on a parameter that was not migrated"),
        "Azure workbook parameter not migrated",
        [
            "The item uses a parameter that is filled from a query another parameter feeds "
            "(dependent parameters), a Resource Graph query, or a type with no New Relic "
            "variable. Pick the value you want, write it into the query by hand, or create a "
            "New Relic NRQL variable over `uniques()` of the attribute and use `IN ({{name}})`.",
        ],
    ),
    (
        re.compile(r"^workbook query is Azure Resource Graph"),
        "Azure Resource Graph query in a workbook",
        [
            "Resource Graph answers questions about Azure inventory (which resources exist, "
            "their tags and properties). New Relic only knows resources that send data. Use the "
            "entity explorer or an NRQL query such as `SELECT uniques(azure.resourceId) FROM "
            "Metric WHERE collector.name = 'azure-monitor'` for the resources that report, and "
            "keep inventory questions in Azure.",
        ],
    ),
    (
        re.compile(r"^workbook query (calls an Azure Resource Manager|merges|holds static)"),
        "Azure workbook data source without a New Relic form",
        [
            "ARM endpoint, merge and JSON data sources shape data inside the workbook. Replace "
            "the item with an NRQL chart over the underlying metric or log, or recreate the "
            "small table as a markdown widget.",
        ],
    ),
    (
        re.compile(r"^workbook query (reads Azure Data Explorer|type is not supported|is empty)"),
        "Azure workbook query source not supported",
        [
            "This query reads a source that does not reach New Relic through the Azure Monitor "
            "integration. Decide whether the data should be sent to New Relic (see the data "
            "path in the report) and then write the NRQL chart by hand.",
        ],
    ),
    (
        re.compile(
            r"^workbook query is KQL that cannot be converted: the query uses workbook param"
        ),
        "Azure parameterised workbook query",
        [
            "The query has a workbook parameter written in braces. A time-range filter "
            "(`where TimeGenerated {TimeRange}`) and a grain (`bin(TimeGenerated, "
            "{TimeRange:grain})`) are converted; the dashboard time picker sets the range. "
            "Any other use is not guessed.",
            "Subscription, resource group and resource parameters: New Relic has no such scope. "
            "Filter on the attribute that carries the resource (for example `azure.resourceId` "
            "or `azure.subscriptionId`) with a dashboard variable over `uniques()` of it, and use "
            "`IN ({{name}})`.",
            "Dropdown and text parameters: create a New Relic dashboard variable with the same "
            "values and write `{{name}}` in the NRQL by hand after the KQL is rewritten (the "
            "query also needs the other changes named in the report). `{P:start}` and "
            "`{P:end}` become `SINCE` and `UNTIL`.",
        ],
    ),
    (
        re.compile(r"^workbook query is KQL that cannot be converted: case-insensitive comp"),
        "Azure workbook KQL case-insensitive comparison",
        [
            "`=~` compares text ignoring case; NRQL `=` and `LIKE` are case-sensitive, so the "
            "importer does not guess. If the values have one known spelling, filter on that "
            "spelling. Otherwise test an `RLIKE` with the `(?i)` flag in the query builder "
            "before using it, and keep the rest of the query as converted.",
        ],
    ),
    (
        re.compile(r"^workbook query is KQL"),
        "Azure workbook KQL query",
        [
            "The query is Kusto (KQL) over Log Analytics or Application Insights. Forward the "
            "logs to New Relic first (Event Hub forwarder or the Azure Monitor logs route), then "
            "rewrite the query in NRQL over `Log` or the event the data arrives as. The query "
            "text is in the IR file of the dashboard.",
        ],
    ),
    (
        re.compile(r"^workbook item type is not supported"),
        "Azure workbook item type not supported",
        ["This item type has no New Relic widget. Rebuild the idea with an NRQL chart by hand."],
    ),
    (
        re.compile(r"^workbook group loads another workbook template"),
        "Azure linked workbook template",
        [
            "The group shows another workbook template by its id. Export that workbook as well "
            "and convert it as its own dashboard.",
        ],
    ),
    (
        re.compile(r"^workbook metric cannot be converted"),
        "Azure workbook metric not convertible",
        [
            "The metric name, namespace or aggregation cannot be written as a New Relic "
            "metric (custom and guest metrics are not collected by the Azure Monitor "
            "integration). Check the metric in the data explorer; if it arrives under another "
            "name, put it in name-map.json and convert again.",
        ],
    ),
    (
        re.compile(r"^dashboard part type has no New Relic equivalent"),
        "Azure portal dashboard part",
        [
            "This tile shows an Azure portal view (a resource card, health, map, video, "
            "button). New Relic has no widget for it. Link to the entity in New Relic from a "
            "markdown widget, or chart the metric the tile summarises.",
        ],
    ),
    (
        re.compile(r"^dashboard (metric chart cannot be converted|logs part has no query)"),
        "Azure portal dashboard chart not convertible",
        [
            "The part could not be read as a metric or a log query. Open it in the portal, note "
            "the metric or query, and build the NRQL chart by hand.",
        ],
    ),
]

register_rules(RULES)

PATTERNS: list[tuple[Category, str]] = [
    (
        Category.UNSUPPORTED_FEATURE,
        r"^(workbook (conditional visibility|item type|group loads)|dashboard part type)",
    ),
    (
        Category.UNSUPPORTED_QUERY,
        r"^(workbook query|workbook metric cannot|dashboard (metric chart|logs part))",
    ),
    (Category.MISSING_MAPPING, r"^workbook item depends on a parameter"),
    (Category.MANUAL_FOLLOWUP, r"^workbook (content could not|has no items)"),
    (
        Category.APPROXIMATION,
        r"^(the time range comes from|resource selection|bars over time|a scatter chart"
        r"|chart type|workbook parameters written|link actions|a workbook group|workbook tabs"
        r"|the item was also shown|the dimension filters"
        r"|visualization .* has no|tiles are shown|grid column|the metric is split|no aggregation"
        r"|several metrics|the portal time range|the metric is charted per|layout is approximated"
        r"|dashboard part .* is not converted)",
    ),
]
for _category, _pattern in PATTERNS:
    register_taxonomy(_category, _pattern)
