"""The wording of every reason the Azure dashboard converter gives.

Like `reasons.py`: each reason that makes a widget a placeholder starts with a fixed phrase that
`dashboard_rules.py` anchors its remediation and taxonomy rules on. Text taken from the workbook
(names, queries) is added only after the phrase and goes through `shown()`.
"""

from importer.sources.azure.reasons import shown

__all__ = ["shown"]

# -- placeholders: workbooks ------------------------------------------------------------
UNREADABLE = "workbook content could not be read"
NO_ITEMS = "workbook has no items"
COND_VIS = (
    "workbook conditional visibility has no New Relic equivalent: the item shows only for some "
    "parameter values, and a New Relic widget is always shown"
)
PARAM_DEP = "workbook item depends on a parameter that was not migrated"
QUERY_ARG = (
    "workbook query is Azure Resource Graph: New Relic has no table of Azure resources to query"
)
QUERY_ARM = "workbook query calls an Azure Resource Manager endpoint: New Relic cannot call it"
QUERY_MERGE = "workbook query merges the results of other queries: New Relic has no merge"
QUERY_JSON = "workbook query holds static JSON data: New Relic cannot query a literal table"
QUERY_ADX = "workbook query reads Azure Data Explorer: that data is not in New Relic"
QUERY_OTHER = "workbook query type is not supported"
QUERY_EMPTY = "workbook query is empty"
ITEM_TYPE = "workbook item type is not supported"
GROUP_TEMPLATE = "workbook group loads another workbook template, which is not in this export"
METRIC = "workbook metric cannot be converted"
QUERY_KQL = "workbook query is KQL that cannot be converted"

# -- placeholders: portal dashboards -----------------------------------------------------------
PART_TYPE = "dashboard part type has no New Relic equivalent"
PART_METRIC = "dashboard metric chart cannot be converted"
PART_LOGS = "dashboard logs part has no query to convert"

# -- notes on converted widgets ----------------------------------------------------------------
N_TIME = "the time range comes from the New Relic dashboard time picker, not from the workbook"
N_RESOURCES = (
    "resource selection {what} is not migrated: the chart covers every resource of the type that "
    "the Azure Monitor integration reports (add a WHERE on azure.resourceId to narrow it)"
)
N_BAR = "bars over time are shown as an area chart"
N_SCATTER = "a scatter chart is shown as a line chart"
N_CHART_TYPE = "chart type {kind} has no New Relic counterpart and is shown as a line chart"
N_TEXT_PARAMS = "workbook parameters written in braces in the text are shown as written"
N_LINKS = "link actions (open blade, Azure action, other workbook) are shown as text, not links"
N_GROUP = "a workbook group is shown as a heading; New Relic groups cannot be collapsed"
N_COND_SET = (
    "the item was also shown only when parameter {name} has a value; the widget is shown either way"
)
N_FILTERS = (
    "the dimension filters of the metric item are not applied: add a WHERE on the dimension "
    "attribute"
)
N_TABS = "workbook tabs became pages of the dashboard"
N_VIZ = "visualization {kind} has no New Relic chart and is shown as a table"
N_TILES = "tiles are shown as one billboard for the first row"
N_GRID_FORMAT = "grid column formatters, thresholds and sorting are not migrated"
N_SPLIT = "the metric is split by {name}; the attribute name is derived and UNVERIFIED"
N_AGG_DEFAULT = "no aggregation is stated for {name}: the average is charted"
N_DOUBLE_METRIC = "several metrics are charted together; units are not aligned"
N_PORTAL_TIME = (
    "the portal time range of this dashboard is not migrated; use the dashboard time picker"
)
N_LAYOUT = "layout is approximated from the source grid"
N_MULTI_RESOURCE = "the metric is charted per resource (FACET azure.resourceId)"
N_PARTS_SKIPPED = "dashboard part {kind} is not converted: {why}"
