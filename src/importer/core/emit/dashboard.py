"""Emit dashboards as NerdGraph JSON and Terraform. Nothing here writes to New Relic.

Dashboard text is untrusted: JSON output is serialised by json, Terraform strings go through
hcl_string(), and the text of "not migrated" placeholders through md_cell(). Queries are built by
widget_nrql (escaping through quote_string and identifier) or arrive pre-built from a translator
and pass core/dashboard_nrql.py before they get here.
"""

from typing import Any, NamedTuple

from importer.core.dashboard_ir import (
    MAX_PAGES,
    PAGE_WIDGETS,
    PROVIDER_MAX_HEIGHT,
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRVariable,
    IRWidget,
    Series,
    Threshold,
    VariableType,
    Visualization,
    WidgetKind,
    order_variables,
    provider_layout,
)
from importer.core.dashboard_nrql import check_raw_nrql, check_variables_defined
from importer.core.emit.common import CannotEmit
from importer.core.emit.nrql import widget_nrql
from importer.core.emit.terraform import hcl_string
from importer.core.ids import source_id
from importer.core.ir import Confidence
from importer.core.report import md_cell
from importer.core.text import printable

ACCOUNT_PLACEHOLDER = "ACCOUNT_ID_PLACEHOLDER"
RESOURCE_PREFIXES = {"cloudwatch": "cw_dashboard", "gcp": "gcp_dashboard"}

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
    Visualization.TABLE: "widget_table",
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


def _series_query(series: Series, timeseries: bool) -> str:
    """NRQL for one series; an automatic bucket replaces the fixed one on charts over time."""
    if timeseries and series.auto_period:
        return widget_nrql(series, False) + " TIMESERIES AUTO"
    return widget_nrql(series, timeseries)


def _chart_queries(w: IRWidget, viz: Visualization) -> list[str]:
    timeseries = viz in TIMESERIES_VISUALIZATIONS
    for raw in w.raw_series:
        check_raw_nrql(raw.nrql)  # the models are mutable, so check again on the way out
    return [_series_query(s, timeseries) for s in w.series] + [r.nrql for r in w.raw_series]


def _check_variables(dashboard: IRDashboard, queries: list[str]) -> None:
    """Variable placement and definition, for dashboards that use variables or raw queries.

    A structured series from the AWS and GCP translators is literal text: a label or filter value
    that happens to contain braces is written as it is, as it always was.
    """
    uses_variables = bool(dashboard.variables) or any(w.raw_series for w in dashboard.widgets)
    if uses_variables:
        check_variables_defined(queries, [v.name for v in dashboard.variables])


def check_variable_order(dashboard: IRDashboard) -> None:
    """Refuse variables whose queries use an undefined variable, or that come before their parent.

    The IR checks this when it is built; the models are mutable, so it is checked again here.
    """
    queries = [v.nrql for v in dashboard.variables if v.nrql]
    try:
        check_variables_defined(queries, [v.name for v in dashboard.variables])
        ordered = order_variables(dashboard.variables)
    except ValueError as exc:
        raise CannotEmit(str(exc)) from None
    if ordered != dashboard.variables:
        raise CannotEmit("a variable is defined before a variable its query uses")


def emittable_widgets(dashboard: IRDashboard) -> list[EmitWidget]:
    """Every widget ready to emit; widgets that were not converted become a text placeholder.

    A widget taller than the provider accepts is emitted at the maximum height (see
    clamp_notes for the record of it).
    """
    if dashboard.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        raise CannotEmit(f"confidence is {dashboard.confidence.value}")
    out: list[EmitWidget] = []
    for original in dashboard.widgets:
        w = original.model_copy(update={"layout": provider_layout(original.layout)})
        if w.kind == WidgetKind.CHART and w.visualization and (w.series or w.raw_series):
            try:
                queries = _chart_queries(w, w.visualization)
                _check_variables(dashboard, queries)
            except ValueError as exc:
                raise CannotEmit(f"widget {md_cell(w.title, 40)!r}: {exc}") from None
            out.append(EmitWidget(w, w.visualization, queries, ""))
        elif w.kind == WidgetKind.MARKDOWN and w.text is not None:
            out.append(EmitWidget(w, Visualization.MARKDOWN, [], w.text))
        else:
            out.append(EmitWidget(w, Visualization.MARKDOWN, [], placeholder_text(w)))
    return out


def pages_of(
    dashboard: IRDashboard, emitted: list[EmitWidget]
) -> list[tuple[str, list[EmitWidget]]]:
    """The widgets grouped by page, pages in order of first use. A widget with no page sits on
    the dashboard's own page, so a dashboard that names no pages is one page as it always was."""
    pages: dict[str, list[EmitWidget]] = {}
    for item in emitted:
        pages.setdefault(item.widget.page or dashboard.name, []).append(item)
    if len(pages) > MAX_PAGES:
        raise CannotEmit(f"{len(pages)} pages; New Relic holds {MAX_PAGES}")
    for name, items in pages.items():
        if len(items) > PAGE_WIDGETS:
            raise CannotEmit(f"page {md_cell(name, 40)!r} has {len(items)} widgets")
    return list(pages.items()) or [(dashboard.name, [])]


def clamp_notes(dashboard: IRDashboard) -> str:
    """A sentence naming the widgets whose height was reduced, or an empty string."""
    clamped = [
        f"{printable(w.title, 40)} ({w.layout.height})"
        for w in dashboard.widgets
        if provider_layout(w.layout) is not w.layout
    ]
    if not clamped:
        return ""
    return f"Heights clamped to {PROVIDER_MAX_HEIGHT}: " + ", ".join(clamped) + "."


def _query(nrql: str) -> dict[str, Any]:
    return {"accountIds": [ACCOUNT_PLACEHOLDER], "query": nrql}


def nerdgraph_variable(v: IRVariable) -> dict[str, Any]:
    """A DashboardVariableInput. UNVERIFIED: the field names are from New Relic's NerdGraph
    documentation as remembered and no offline schema covers them. Read one back in a sandbox
    before relying on it."""
    out: dict[str, Any] = {
        "name": v.name,
        "title": v.title,
        "type": v.type.value.upper(),
        "isMultiSelection": v.multi_select,
        "replacementStrategy": v.replacement.value.upper(),
    }
    if v.default_values:
        out["defaultValues"] = [{"value": {"string": d}} for d in v.default_values]
    if v.items:
        out["items"] = [
            {"value": i.value} if i.title is None else {"title": i.title, "value": i.value}
            for i in v.items
        ]
    if v.type == VariableType.NRQL:
        ids: list[Any] = v.account_ids or [ACCOUNT_PLACEHOLDER]
        out["nrqlQuery"] = {"accountIds": ids, "query": v.nrql}
    flags = (
        ("excluded", v.excluded),
        ("ignoreTimeRange", v.ignore_time_range),
        ("showApplyAction", v.show_apply_action),
    )
    options = {key: True for key, on in flags if on}
    if options:
        out["options"] = options
    return out


def _nerdgraph_widgets(items: list[EmitWidget]) -> list[dict[str, Any]]:
    widgets: list[dict[str, Any]] = []
    for w, viz, queries, text in items:
        raw: dict[str, Any] = (
            {"text": text}
            if viz == Visualization.MARKDOWN
            else {"nrqlQueries": [_query(q) for q in queries]}
        )
        widgets.append(
            {
                "title": w.title,
                "layout": w.layout.model_dump(),
                "visualization": {"id": viz.value},
                "rawConfiguration": raw,
            }
        )
    return widgets


def nerdgraph_dashboard(dashboard: IRDashboard) -> dict[str, Any]:
    """The dashboardCreate payload. Widget options and thresholds are not written here: their
    rawConfiguration names are UNVERIFIED, so only the Terraform output carries them."""
    pages = pages_of(dashboard, emittable_widgets(dashboard))
    description = f"Imported from a {dashboard.source_system} dashboard. Review it."
    if notes := clamp_notes(dashboard):
        description += " " + notes
    body: dict[str, Any] = {
        "name": dashboard.name,
        "description": description,
        "permissions": "PRIVATE",
        "pages": [{"name": name, "widgets": _nerdgraph_widgets(items)} for name, items in pages],
    }
    if dashboard.variables:
        check_variable_order(dashboard)
        body["variables"] = [nerdgraph_variable(v) for v in dashboard.variables]
    return {"query": MUTATION, "variables": {"accountId": ACCOUNT_PLACEHOLDER, "dashboard": body}}


def number_text(value: float) -> str:
    """A number with every digit kept: a whole number as an integer, anything else as repr."""
    if value.is_integer() and abs(value) < 2**53:
        return str(int(value))
    return repr(float(value))


def _flag(value: bool | None) -> str | None:
    return None if value is None else str(value).lower()


def _widget_attributes(w: IRWidget, viz: Visualization) -> list[str]:
    """Widget attributes named in the provider schema (newrelic_one_dashboard). `decimals` and
    `stacked` have no confirmed output name, so the IR keeps them and nothing is written."""
    o = w.options
    pairs = [
        ("legend_enabled", _flag(o.legend)),
        ("y_axis_left_min", None if o.y_min is None else number_text(o.y_min)),
        ("y_axis_left_max", None if o.y_max is None else number_text(o.y_max)),
        ("y_axis_left_zero", _flag(o.y_zero)),
    ]
    lines = [f"      {k} = {v}" for k, v in pairs if v is not None]
    if viz == Visualization.BILLBOARD:
        # The IR guarantees a billboard threshold is critical or warning with a from_value.
        lines += [
            f"      {t.severity.value} = {hcl_string(number_text(t.from_value or 0))}"
            for t in w.thresholds
        ]
    return lines


def _threshold_block(t: Threshold) -> list[str]:
    pairs = [
        ("name", t.name),
        ("column_name", t.column_name),
        ("severity", t.severity.value),
        ("from", None if t.from_value is None else number_text(t.from_value)),
        ("to", None if t.to_value is None else number_text(t.to_value)),
    ]
    body = [f"        {k} = {hcl_string(v)}" for k, v in pairs if v is not None]
    return ["", "      threshold {", *body, "      }"]


def _widget_blocks(w: IRWidget, viz: Visualization) -> list[str]:
    lines: list[str] = []
    if w.options.unit is not None:
        lines += ["", "      units {", f"        unit = {hcl_string(w.options.unit)}", "      }"]
    if viz != Visualization.BILLBOARD:
        for t in w.thresholds:
            lines += _threshold_block(t)
    return lines


def _variable_lines(v: IRVariable) -> list[str]:
    lines = [
        "  variable {",
        f"    name = {hcl_string(v.name)}",
        f"    title = {hcl_string(v.title)}",
        f"    type = {hcl_string(v.type.value)}",
        f"    replacement_strategy = {hcl_string(v.replacement.value)}",
    ]
    if v.default_values:
        defaults = ", ".join(hcl_string(d) for d in v.default_values)
        lines.append(f"    default_values = [{defaults}]")
    if v.multi_select:
        lines.append("    is_multi_selection = true")
    for item in v.items:
        lines += ["", "    item {"]
        if item.title is not None:
            lines.append(f"      title = {hcl_string(item.title)}")
        lines += [f"      value = {hcl_string(item.value)}", "    }"]
    if v.type == VariableType.NRQL:
        lines += ["", "    nrql_query {", f"      query = {hcl_string(v.nrql or '')}"]
        if v.account_ids:
            lines.append("      account_ids = [" + ", ".join(str(a) for a in v.account_ids) + "]")
        lines.append("    }")
    flags = [
        ("excluded", v.excluded),
        ("ignore_time_range", v.ignore_time_range),
        ("show_apply_action", v.show_apply_action),
    ]
    if any(on for _, on in flags):
        lines += ["", "    options {", *(f"      {k} = true" for k, on in flags if on), "    }"]
    return [*lines, "  }", ""]


def _widget_lines(w: IRWidget, viz: Visualization, queries: list[str], text: str) -> list[str]:
    lines = [
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
    else:
        lines += _widget_attributes(w, viz)
    for q in queries:
        lines += ["", "      nrql_query {", f"        query = {hcl_string(q)}", "      }"]
    if viz != Visualization.MARKDOWN:
        lines += _widget_blocks(w, viz)
    return [*lines, "    }"]


def dashboard_resource(dashboard: IRDashboard, source_id: int) -> str:
    prefix = RESOURCE_PREFIXES.get(dashboard.source_system, "src_dashboard")
    lines = [
        f'resource "newrelic_one_dashboard" "{prefix}_{source_id}" {{',
        f"  name        = {hcl_string(dashboard.name)}",
        '  permissions = "private"',
    ]
    if notes := clamp_notes(dashboard):
        lines.append(f"  description = {hcl_string(notes)}")
    lines.append("")
    check_variable_order(dashboard)
    for variable in dashboard.variables:
        lines += _variable_lines(variable)
    for number, (name, items) in enumerate(pages_of(dashboard, emittable_widgets(dashboard))):
        if number:
            lines.append("")
        lines += ["  page {", f"    name = {hcl_string(name)}"]
        for w, viz, queries, text in items:
            lines += _widget_lines(w, viz, queries, text)
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def dashboards_terraform(items: list[tuple[int, IRDashboard]]) -> str:
    return TERRAFORM_HEADER + "".join("\n" + dashboard_resource(d, i) for i, d in items)
