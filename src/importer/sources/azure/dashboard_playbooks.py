"""Playbooks for the Azure dashboard remediation rules (same titles as `dashboard_rules.py`).

All text is fixed; none of it comes from an export. Effort figures are rough planning numbers.
"""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L
from importer.sources.azure.dashboard_rules import RULES

# title -> (what it is in Azure, the closest New Relic construct, what differs, minutes)
BOOKS: dict[str, tuple[str, str, str, int]] = {
    "Azure workbook content not readable": (
        "A workbook whose content was not listed or is not valid JSON.",
        "Re-export the content, then convert it.",
        "Nothing converts until the content is read.",
        10,
    ),
    "Azure empty workbook": (
        "A workbook with no items.",
        "Nothing to convert.",
        "None.",
        5,
    ),
    "Azure workbook conditional visibility": (
        "An item shown only when a parameter has a value.",
        "One dashboard page per view, or one dashboard variable.",
        "A New Relic widget is always visible.",
        20,
    ),
    "Azure workbook parameter not migrated": (
        "An item that uses a parameter filled from a query or from another parameter.",
        "A dashboard NRQL variable over uniques() of an attribute.",
        "Nested variables depend on the plan; the value list comes from New Relic data.",
        20,
    ),
    "Azure Resource Graph query in a workbook": (
        "A query over Azure inventory (Resource Graph).",
        "An NRQL query over the resources that report data, or the entity explorer.",
        "Resources that send no data are not listed.",
        30,
    ),
    "Azure workbook data source without a New Relic form": (
        "A workbook item that calls an ARM endpoint, merges queries or holds literal JSON.",
        "An NRQL chart on the underlying metric, or a markdown table.",
        "The data is no longer shaped inside the dashboard.",
        30,
    ),
    "Azure workbook query source not supported": (
        "A query on a source that does not reach New Relic.",
        "Send the data to New Relic, then write the NRQL chart.",
        "Needs a new data path first.",
        60,
    ),
    "Azure workbook KQL query": (
        "A Kusto query over Log Analytics or Application Insights.",
        "An NRQL chart over Log or the event the data arrives as.",
        "KQL and NRQL differ in joins, let statements and functions; logs must be forwarded.",
        45,
    ),
    "Azure parameterised workbook query": (
        "A Kusto query that uses a workbook parameter written in braces.",
        "An NRQL chart; the time range follows the dashboard time picker and other parameters "
        "become dashboard variables.",
        "Parameter values are not guessed; scope parameters (subscription, resource) have no "
        "New Relic counterpart.",
        45,
    ),
    "Azure workbook item type not supported": (
        "A workbook item type with no widget in New Relic.",
        "An NRQL chart that shows the same idea.",
        "The item looks different.",
        20,
    ),
    "Azure linked workbook template": (
        "A group that loads another workbook by its template id.",
        "Convert that workbook as its own dashboard.",
        "The link between the two is lost.",
        15,
    ),
    "Azure workbook metric not convertible": (
        "A metric chart whose metric cannot be written as a New Relic metric name.",
        "Confirm the name in the data explorer and put it in name-map.json.",
        "Custom and guest metrics are not collected by the integration.",
        20,
    ),
    "Azure portal dashboard part": (
        "A portal tile that shows an Azure view (resource card, health, map, button).",
        "A markdown widget with a link, or a chart of the metric the tile summarises.",
        "The tile has no live view in New Relic.",
        15,
    ),
    "Azure portal dashboard chart not convertible": (
        "A portal tile whose metric or query could not be read.",
        "Build the NRQL chart by hand from what the tile shows.",
        "None once rebuilt.",
        20,
    ),
}

for _pattern, _title, _steps in RULES:
    _source, _newrelic, _differs, _minutes = BOOKS[_title]
    register_playbook(
        _title,
        Playbook(
            source=_source,
            newrelic=_newrelic,
            steps=tuple(_steps),
            differs=_differs,
            effort_minutes=_minutes,
            links=(L.TF_DASHBOARD, L.VARIABLES),
        ),
    )
