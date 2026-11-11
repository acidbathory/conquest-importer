"""Azure portal dashboards (Microsoft.Portal/dashboards) to the dashboard IR.

A dashboard is `lenses[].parts[]`, each part a tile with a grid position and a `metadata.type`.
What converts: markdown parts, metric chart parts (`MetricsChartPart`, `MonitorChartPart`) over the
same metric names as the alert converter, and logs parts (`LogsDashboardPart`) through the KQL
swap point. Resource cards, health tiles, maps, videos and buttons are placeholders that say so.
The portal grid has no fixed width, so positions are scaled to 12 columns when they exceed it, and
any overlap is moved down. Extra lenses become pages.
"""

import re
from typing import Any

from importer.core.dashboard_ir import (
    MAX_PAGES,
    IRDashboard,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Visualization,
    WidgetKind,
)
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.azure import dashboard_kql
from importer.sources.azure import dashboard_reasons as DR
from importer.sources.azure.common import Fail
from importer.sources.azure.dashboard_common import (
    AGGREGATION_NUMBERS,
    MAX_TITLE,
    MetricScope,
    MetricSpec,
    chart_widget,
    markdown,
    metric_chart,
    placeholder,
    result,
)
from importer.sources.azure.dashboard_kql import LOG_FORWARDING
from importer.sources.azure.dashboard_models import AzureDashboard
from importer.sources.azure.names import type_of_resource_id
from importer.sources.gcp.dashboards import pack

TEMPLATE_TYPE = re.compile(r"'(Microsoft\.[A-Za-z0-9.]+(?:/[A-Za-z0-9]+)+)'")
LOG_CHARTS = {
    "line": Visualization.LINE,
    "area": Visualization.AREA,
    "stackedarea": Visualization.AREA,
    "pie": Visualization.PIE,
    "bar": Visualization.BAR,
    "stackedcolumn": Visualization.BAR,
    "unstackedcolumn": Visualization.BAR,
}
MAX_PARTS = 150


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _kind(part: dict[str, Any]) -> str:
    """`MarkdownPart` from `Extension[azure]/HubsExtension/PartType/MarkdownPart`."""
    text = _str(_dict(part.get("metadata")).get("type"))
    return printable(text.rsplit("/", 1)[-1], 60) or "unknown"


def _input(part: dict[str, Any], name: str) -> Any:
    for item in _dict(part.get("metadata")).get("inputs") or []:
        if _dict(item).get("name") == name:
            return _dict(item).get("value")
    return None


def _layouts(parts: list[dict[str, Any]]) -> list[tuple[dict[str, Any], Layout]]:
    boxes = []
    for part in parts:
        pos = _dict(part.get("position"))
        x, y = max(_int(pos.get("x"), 0), 0), max(_int(pos.get("y"), 0), 0)
        boxes.append(
            (part, x, y, max(_int(pos.get("colSpan"), 4), 1), max(_int(pos.get("rowSpan"), 3), 1))
        )
    width = max((x + w for _, x, _, w, _ in boxes), default=12)
    scale = 12 / width if width > 12 else 1.0
    placed = []
    for part, x, y, w, h in boxes:
        left, right = round(x * scale), max(round((x + w) * scale), round(x * scale) + 1)
        top = y // 2
        height = max(2, min(12, -(-h // 2)))
        placed.append(
            (
                part,
                Layout(
                    column=left + 1, row=top + 1, width=min(right - left, 12 - left), height=height
                ),
            )
        )
    packed, _moved = pack(placed)
    return packed


def _scope(ids: list[str], notes_what: str) -> MetricScope:
    literal = [i for i in ids if type_of_resource_id(i) and not i.startswith("[")]
    if ids and len(literal) == len(ids):
        return MetricScope(resource_ids=sorted(set(literal)), several=len(set(literal)) > 1)
    return MetricScope(several=True, notes=[DR.N_RESOURCES.format(what=notes_what)])


def _chart_block(part: dict[str, Any]) -> dict[str, Any]:
    """The `{metrics, title, chartType}` block, wherever this part version keeps it."""
    meta = _dict(part.get("metadata"))
    options = _dict(_dict(_dict(meta.get("settings")).get("content")).get("options"))
    for block in (
        _dict(options.get("chart")),
        _dict(_dict(_input(part, "options")).get("chart")),
        _dict(_input(part, "queryInputs")),
    ):
        if block.get("metrics"):
            return block
    return {}


def _template_type(text: str) -> str:
    match = TEMPLATE_TYPE.search(text)
    return match[1].lower() if match else ""


def _metric_part(part: dict[str, Any], layout: Layout, page: str | None, names: Any) -> IRWidget:
    block = _chart_block(part)
    kind = _kind(part)
    title = _str(block.get("title")) or _str(_dict(block.get("titleKind")).get("title")) or kind
    specs: list[MetricSpec] = []
    ids: list[str] = []
    notes: list[str] = []
    try:
        for raw in block.get("metrics") or []:
            m = _dict(raw)
            rid = _str(m.get("resourceId")) or _str(_dict(m.get("resourceMetadata")).get("id"))
            rid = rid or _str(block.get("id"))
            ids.append(rid)
            namespace = (
                _str(m.get("namespace"))
                or type_of_resource_id(rid)
                or _template_type(rid)
                or _template_type(_str(block.get("id")))
            )
            name = _str(m.get("name"))
            if not name:
                raise Fail(f"{DR.PART_METRIC}: the metric has no name")
            number = m.get("aggregationType")
            if number is not None and number not in AGGREGATION_NUMBERS:
                raise Fail(f"{DR.PART_METRIC}: aggregation {DR.shown(number, 20)} has no match")
            if number is None:
                notes.append(DR.N_AGG_DEFAULT.format(name=DR.shown(name, 40)))
            label = _str(_dict(m.get("metricVisualization")).get("displayName")) or None
            specs.append(
                MetricSpec(
                    namespace,
                    name,
                    AGGREGATION_NUMBERS.get(number, "avg") if isinstance(number, int) else "avg",
                    None,
                    label,
                )
            )
        if not specs:
            raise Fail(f"{DR.PART_METRIC}: the part names no metric")
        chart_type = block.get("chartType", _dict(block.get("visualization")).get("chartType"))
        if chart_type not in (None, 0):
            notes.append(DR.N_CHART_TYPE.format(kind=DR.shown(chart_type, 10)))
        chart = metric_chart(
            specs, _scope(ids, "(an ARM template expression)"), names, Visualization.LINE
        )
    except Fail as fail:
        reason = fail.reason
        if reason.startswith(DR.METRIC):
            reason = reason.replace(DR.METRIC, DR.PART_METRIC, 1)
        return placeholder(title, layout, f"dashboard {kind}", [reason], page)
    return chart_widget(title, layout, f"dashboard {kind}", Visualization.LINE, chart, page, notes)


def _markdown_part(part: dict[str, Any], layout: Layout, page: str | None) -> IRWidget:
    meta = _dict(part.get("metadata"))
    settings = _dict(_dict(_dict(meta.get("settings")).get("content")).get("settings"))
    body = _str(settings.get("content")) or _str(
        _dict(_dict(meta.get("settings")).get("content")).get("content")
    )
    heading = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "Markdown")
    title = _str(settings.get("title")).strip() or heading
    return markdown(title, layout, "dashboard markdown", body, [], page)


def _logs_part(
    part: dict[str, Any], layout: Layout, page: str | None, variables: tuple[str, ...]
) -> IRWidget:
    title = _str(_input(part, "PartTitle")) or "Logs"
    query = _str(_input(part, "Query"))
    if not query.strip():
        return placeholder(title, layout, "dashboard logs", [DR.PART_LOGS], page)
    found = dashboard_kql.translate_kql(query, variables)
    if isinstance(found, str):
        return placeholder(title, layout, "dashboard logs", [found], page)
    if _str(_input(part, "ControlType")).lower() == "analyticsgrid":
        viz = Visualization.TABLE
    else:
        viz = LOG_CHARTS.get(_str(_input(part, "SpecificChart")).lower(), Visualization.TABLE)
    if viz in (Visualization.LINE, Visualization.AREA) and not found.timeseries:
        viz = Visualization.TABLE
    return result(
        title,
        layout,
        "dashboard logs",
        Confidence.APPROXIMATE,
        list(found.notes),
        visualization=viz,
        raw_series=[RawNrqlSeries(nrql=found.nrql)],
        page=page,
        data_path=LOG_FORWARDING,
    )


def _part(part: dict[str, Any], layout: Layout, page: str | None, dash: AzureDashboard) -> IRWidget:
    kind = _kind(part)
    if kind == "MarkdownPart":
        return _markdown_part(part, layout, page)
    if kind in ("MetricsChartPart", "MonitorChartPart"):
        return _metric_part(part, layout, page, dash.names)
    if kind == "LogsDashboardPart":
        return _logs_part(part, layout, page, ())
    title = _str(_dict(_input(part, "options")).get("title")) or kind
    return placeholder(title, layout, f"dashboard {kind}", [f"{DR.PART_TYPE}: {kind}"], page)


def _safe_part(
    part: dict[str, Any], layout: Layout, page: str | None, dash: AzureDashboard
) -> IRWidget:
    try:
        return _part(part, layout, page, dash)
    except (ValueError, TypeError, KeyError, AttributeError):
        return placeholder(
            _kind(part), layout, "dashboard part", [f"{DR.PART_TYPE}: unreadable"], page
        )


def translate_portal(dash: AzureDashboard) -> IRDashboard:
    name = printable(dash.name, MAX_TITLE) or "dashboard"
    base: dict[str, Any] = {
        "source_system": "azure",
        "source_key": printable(dash.key or dash.name, 10_000),
        "name": name,
    }
    if dash.content is None:
        layout = Layout(column=1, row=1, width=12, height=2)
        reason = f"{DR.UNREADABLE}: {DR.shown(dash.error, 160)}"
        return IRDashboard(
            **base,
            widgets=[placeholder(name, layout, "dashboard", [reason])],
            confidence=Confidence.MANUAL,
            reasons=[reason],
        )
    widgets: list[IRWidget] = []
    lenses = [_dict(lens) for lens in dash.content.get("lenses", [])][:MAX_PAGES]
    for index, lens in enumerate(lenses):
        page = None if index == 0 else f"Lens {index + 1}"
        parts = [p for p in lens.get("parts", []) if isinstance(p, dict)][:MAX_PARTS]
        widgets += [_safe_part(p, lay, page, dash) for p, lay in _layouts(parts)]
    reasons = [DR.N_LAYOUT]
    if _dict(_dict(dash.metadata.get("model")).get("timeRange")):
        reasons.append(DR.N_PORTAL_TIME)
    converted = [w for w in widgets if w.kind is not WidgetKind.UNSUPPORTED]
    if not widgets:
        confidence, reasons = Confidence.UNSUPPORTED, [DR.NO_ITEMS.replace("workbook", "dashboard")]
    elif not converted:
        confidence = Confidence.MANUAL
        reasons.append("no part could be converted")
    else:
        confidence = Confidence.APPROXIMATE
        reasons.append(f"{len(widgets) - len(converted)} of {len(widgets)} parts not converted")
    return IRDashboard(**base, widgets=widgets, confidence=confidence, reasons=reasons)
