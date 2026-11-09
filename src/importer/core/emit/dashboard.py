"""Emit dashboards as NerdGraph JSON and Terraform. Nothing here writes to New Relic.

Dashboard text is untrusted: JSON output is serialised by json, Terraform strings go through
hcl_string(), and the text of "not migrated" placeholders through md_cell().
"""

from typing import Any, NamedTuple

from importer.core.dashboard_ir import (
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRWidget,
    Visualization,
    WidgetKind,
)
from importer.core.emit.common import CannotEmit
from importer.core.emit.nrql import widget_nrql
from importer.core.emit.terraform import hcl_string
from importer.core.ids import source_id
from importer.core.ir import Confidence
from importer.core.report import md_cell

ACCOUNT_PLACEHOLDER = "ACCOUNT_ID_PLACEHOLDER"
RESOURCE_PREFIXES = {"cloudwatch": "cw_dashboard"}

MUTATION = (
    "mutation CreateDashboard($accountId: Int!, $dashboard: DashboardInput!) {\n"
    "  dashboardCreate(accountId: $accountId, dashboard: $dashboard) {\n"
    "    entityResult {\n      guid\n      name\n    }\n"
    "    errors {\n      description\n      type\n    }\n  }\n}"
)
TERRAFORM_HEADER = """terraform {
  required_providers {
    newrelic = {
      source = "newrelic/newrelic"
    }
  }
}

provider "newrelic" {}
"""
TERRAFORM_WIDGETS = {
    Visualization.LINE: "widget_line",
    Visualization.AREA: "widget_area",
    Visualization.BAR: "widget_bar",
    Visualization.PIE: "widget_pie",
    Visualization.BILLBOARD: "widget_billboard",
    Visualization.MARKDOWN: "widget_markdown",
}


class EmitWidget(NamedTuple):
    widget: IRWidget
    visualization: Visualization
    queries: list[str]
    text: str  # markdown widgets only


def dashboard_id(dashboard: IRDashboard) -> int:
    """A stable integer from the source and dashboard name, for file and resource names."""
    key = f"{dashboard.source_system}:{dashboard.source_key}"
    return source_id(key)


def placeholder_text(widget: IRWidget) -> str:
    reason = "; ".join(widget.reasons)
    return (
        f"**Not migrated:** {md_cell(widget.title, 80)} ({md_cell(widget.source_type, 30)}). "
        f"{md_cell(reason, 200)}"
    )


def emittable_widgets(dashboard: IRDashboard) -> list[EmitWidget]:
    """Every widget ready to emit; widgets that were not converted become a text placeholder."""
    if dashboard.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        raise CannotEmit(f"confidence is {dashboard.confidence.value}")
    out: list[EmitWidget] = []
    for w in dashboard.widgets:
        if w.kind == WidgetKind.CHART and w.visualization and w.series:
            timeseries = w.visualization in TIMESERIES_VISUALIZATIONS
            try:
                queries = [widget_nrql(s, timeseries) for s in w.series]
            except ValueError as exc:
                raise CannotEmit(f"widget {md_cell(w.title, 40)!r}: {exc}") from None
            out.append(EmitWidget(w, w.visualization, queries, ""))
        elif w.kind == WidgetKind.MARKDOWN and w.text is not None:
            out.append(EmitWidget(w, Visualization.MARKDOWN, [], w.text))
        else:
            out.append(EmitWidget(w, Visualization.MARKDOWN, [], placeholder_text(w)))
    return out


def _query(nrql: str) -> dict[str, Any]:
    return {"accountIds": [ACCOUNT_PLACEHOLDER], "query": nrql}


def nerdgraph_dashboard(dashboard: IRDashboard) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = []
    for w, viz, queries, text in emittable_widgets(dashboard):
        raw: dict[str, Any] = (
            {"text": text}
            if viz == Visualization.MARKDOWN
            else {
                "nrqlQueries": [{"accountIds": [ACCOUNT_PLACEHOLDER], "query": q} for q in queries]
            }
        )
        widgets.append(
            {
                "title": w.title,
                "layout": w.layout.model_dump(),
                "visualization": {"id": viz.value},
                "rawConfiguration": raw,
            }
        )
    return {
        "query": MUTATION,
        "variables": {
            "accountId": ACCOUNT_PLACEHOLDER,
            "dashboard": {
                "name": dashboard.name,
                "description": f"Imported from a {dashboard.source_system} dashboard. Review it.",
                "permissions": "PRIVATE",
                "pages": [{"name": dashboard.name, "widgets": widgets}],
            },
        },
    }


def dashboard_resource(dashboard: IRDashboard, source_id: int) -> str:
    prefix = RESOURCE_PREFIXES.get(dashboard.source_system, "src_dashboard")
    lines = [
        f'resource "newrelic_one_dashboard" "{prefix}_{source_id}" {{',
        f"  name        = {hcl_string(dashboard.name)}",
        '  permissions = "private"',
        "",
        "  page {",
        f"    name = {hcl_string(dashboard.name)}",
    ]
    for w, viz, queries, text in emittable_widgets(dashboard):
        lines += [
            "",
            f"    {TERRAFORM_WIDGETS[viz]} {{",
            f"      title  = {hcl_string(w.title)}",
            f"      row    = {w.layout.row}",
            f"      column = {w.layout.column}",
            f"      width  = {w.layout.width}",
            f"      height = {w.layout.height}",
        ]
        if viz == Visualization.MARKDOWN:
            lines.append(f"      text   = {hcl_string(text)}")
        for q in queries:
            lines += ["", "      nrql_query {", f"        query = {hcl_string(q)}", "      }"]
        lines.append("    }")
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def dashboards_terraform(items: list[tuple[int, IRDashboard]]) -> str:
    return TERRAFORM_HEADER + "".join("\n" + dashboard_resource(d, i) for i, d in items)
