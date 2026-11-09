"""Translate Cloud Monitoring dashboards into the dashboard IR.

Like the alert translator this never raises on odd input: a widget it cannot convert is labelled
and kept, so the report shows what was lost. Metric filters and aggregations go through the same
helpers as alert conditions (`_metric_scope`, `check_aligner`, `metric_series`), so metric and
label naming rules live in one place. Charts and scorecards over a time series filter convert;
text widgets become markdown; everything else is a failed widget with a reason.
"""

from typing import Any

from importer.core.dashboard_ir import (
    IRDashboard,
    IRWidget,
    Layout,
    Series,
    Visualization,
    WidgetKind,
)
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.gcp.dashboard_models import MAX_WIDGETS, GcpDashboard
from importer.sources.gcp.translate import (
    POLL_SECONDS,
    _Fail,
    _metric_scope,
    _seconds,
    check_aligner,
    metric_series,
)

MAX_TITLE = 200
MAX_DATASETS = 20
MAX_SECONDS = 86400
DEFAULT_ROW_HEIGHT = 3
PLOT_TYPES = {
    "LINE": Visualization.LINE,
    "STACKED_AREA": Visualization.AREA,
    "STACKED_BAR": Visualization.BAR,
}
QUERY_REASONS = {
    "timeSeriesQueryLanguage": "MQL queries in widgets are not converted",
    "prometheusQuery": "PromQL queries in widgets are not converted",
    "timeSeriesFilterRatio": "ratio queries in widgets are not converted",
    "opsAnalyticsQuery": "SQL queries in widgets are not converted",
}
NO_QUERY = "widget has no query"
UNKNOWN_QUERY = "widget query is not recognised"
NO_CONTENT = "text widget has no content"
POLL_NOTE = (
    "the alignment period is raised to 300s because New Relic polls Google Cloud every 5 minutes"
)

Placed = tuple[dict[str, Any], Layout]


def _int(value: object, default: int, low: int = 0, high: int = 10_000) -> int:
    """An integer from a JSON number or a numeric string (the API sends int64 as text)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return min(max(value, low), high) if isinstance(value, int) else default


def _layout(column: int, row: int, width: int, height: int) -> Layout:
    column = min(max(column, 1), 12)
    return Layout(
        column=column,
        row=max(row, 1),
        width=max(1, min(width, 12 - column + 1)),
        height=max(1, min(height, 100)),
    )


def _dicts(value: object) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _mosaic(layout: dict[str, Any]) -> list[Placed]:
    columns = _int(layout.get("columns"), 48, 1, 48)
    placed: list[Placed] = []
    for tile in _dicts(layout.get("tiles")):
        widget = tile.get("widget")
        if not isinstance(widget, dict):
            continue
        scale = 12 / columns
        x, y = _int(tile.get("xPos"), 0), _int(tile.get("yPos"), 0)
        w, h = _int(tile.get("width"), columns // 2 or 1), _int(tile.get("height"), 4)
        placed.append(
            (
                widget,
                _layout(int(x * scale) + 1, y // 2 + 1, round(w * scale), max(2, round(h / 2))),
            )
        )
    return placed


def _grid(layout: dict[str, Any]) -> list[Placed]:
    across = _int(layout.get("columns"), 2, 1, 12)
    span = 12 // across
    return [
        (w, _layout((i % across) * span + 1, (i // across) * DEFAULT_ROW_HEIGHT + 1, span, 3))
        for i, w in enumerate(_dicts(layout.get("widgets")))
    ]


def _column_layout(layout: dict[str, Any]) -> list[Placed]:
    columns = _dicts(layout.get("columns"))
    weights = [_int(c.get("weight"), 1, 1, 12) for c in columns]
    total = sum(weights) or 1
    placed: list[Placed] = []
    left = 1
    for column, weight in zip(columns, weights, strict=True):
        width = max(1, round(12 * weight / total))
        for j, widget in enumerate(_dicts(column.get("widgets"))):
            placed.append(
                (widget, _layout(left, j * DEFAULT_ROW_HEIGHT + 1, width, DEFAULT_ROW_HEIGHT))
            )
        left += width
    return placed


def _row_layout(layout: dict[str, Any]) -> list[Placed]:
    placed: list[Placed] = []
    for i, row in enumerate(_dicts(layout.get("rows"))):
        widgets = _dicts(row.get("widgets"))
        span = 12 // max(len(widgets), 1) or 1
        placed += [
            (w, _layout(j * span + 1, i * DEFAULT_ROW_HEIGHT + 1, span, DEFAULT_ROW_HEIGHT))
            for j, w in enumerate(widgets)
        ]
    return placed


LAYOUTS = {
    "mosaicLayout": _mosaic,
    "gridLayout": _grid,
    "columnLayout": _column_layout,
    "rowLayout": _row_layout,
}


def _series(query: object, notes: set[str]) -> Series | str:
    """One data set's time series filter as a Series, or the reason it cannot convert."""
    tsq = query if isinstance(query, dict) else {}
    for key, reason in QUERY_REASONS.items():
        if key in tsq:
            return reason
    body = tsq.get("timeSeriesFilter")
    if not isinstance(body, dict):
        return UNKNOWN_QUERY
    try:
        metric_type, scope, scope_notes = _metric_scope(body)
        if body.get("secondaryAggregation"):
            raise _Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
        raw = body.get("aggregation")
        agg, aligner = check_aligner(raw if isinstance(raw, dict) else {})
        period = _seconds(agg.get("alignmentPeriod")) or POLL_SECONDS
        if period > MAX_SECONDS:
            raise _Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
        signal, period, _scale, signal_notes = metric_series(
            metric_type, scope, agg, aligner, period, chart=True
        )
    except _Fail as fail:
        return fail.reason
    if period < POLL_SECONDS:
        notes.add(POLL_NOTE)
    notes.update(scope_notes, signal_notes)
    return Series(signal=signal, period_seconds=max(period, POLL_SECONDS))


def _result(
    title: str,
    layout: Layout,
    kind: str,
    confidence: Confidence,
    reasons: list[str],
    *,
    widget_kind: WidgetKind = WidgetKind.CHART,
    **extra: Any,
) -> IRWidget:
    return IRWidget(
        kind=widget_kind,
        source_type=printable(kind, 30),
        title=title,
        layout=layout,
        confidence=confidence,
        reasons=reasons,
        **extra,
    )


def _failed(title: str, layout: Layout, kind: str, reasons: list[str]) -> IRWidget:
    return _result(
        title,
        layout,
        kind,
        Confidence.MANUAL,
        sorted(set(reasons)),
        widget_kind=WidgetKind.UNSUPPORTED,
    )


def _chart(
    body: dict[str, Any], title: str, layout: Layout, kind: str, *, scorecard: bool
) -> IRWidget:
    queries = (
        [{"timeSeriesQuery": body.get("timeSeriesQuery")}]
        if scorecard
        else _dicts(body.get("dataSets"))[:MAX_DATASETS]
    )
    notes: set[str] = set()
    found = [(_series(q.get("timeSeriesQuery"), notes), q.get("plotType")) for q in queries]
    series = [s for s, _ in found if isinstance(s, Series)]
    failures = [s for s, _ in found if isinstance(s, str)]
    if not series:
        return _failed(title, layout, kind, failures or [NO_QUERY])
    plot = next((p for s, p in found if isinstance(s, Series)), None)
    viz = Visualization.BILLBOARD if scorecard else PLOT_TYPES.get(str(plot), Visualization.LINE)
    reasons = sorted(notes)
    if not scorecard and plot and str(plot) not in PLOT_TYPES:
        reasons.append(f"plot type {printable(str(plot), 30)!r} is not supported; shown as a line")
    if scorecard:
        reasons += [
            f"{printable(k, 20)} is not migrated"
            for k in ("gaugeView", "sparkChartView", "thresholds")
            if k in body
        ]
    if failures:
        reasons.append(
            f"{len(series)} of {len(found)} data sets converted; not converted: "
            + "; ".join(sorted(set(failures)))
        )
    if any(d.get("legendTemplate") for d in queries):
        reasons.append("legend templates are not migrated")
    return _result(
        title,
        layout,
        kind,
        Confidence.APPROXIMATE,
        reasons,
        visualization=viz,
        series=series,
    )


def _text(body: dict[str, Any], title: str, layout: Layout) -> IRWidget:
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return _failed(title, layout, "text", [NO_CONTENT])
    raw = str(body.get("format", "MARKDOWN")) == "RAW"
    return _result(
        title,
        layout,
        "text",
        Confidence.APPROXIMATE if raw else Confidence.EXACT,
        ["raw text is shown as markdown"] if raw else [],
        widget_kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=content,
    )


def translate_widget(widget: dict[str, Any], layout: Layout) -> IRWidget:
    kind = next((str(k) for k in widget if k not in ("title", "id")), "unknown")
    raw_title = widget.get("title")
    title = printable(
        raw_title if isinstance(raw_title, str) and raw_title else f"{kind} widget", MAX_TITLE
    )
    body = widget.get(kind)
    body = body if isinstance(body, dict) else {}
    if kind in ("xyChart", "scorecard"):
        return _chart(body, title, layout, kind, scorecard=kind == "scorecard")
    if kind == "text":
        return _text(body, title, layout)
    shown = printable(kind, 30)
    return _failed(title, layout, kind, [f"dashboard widget type {shown!r} is not supported"])


def translate_dashboard(dashboard: GcpDashboard) -> IRDashboard:
    key, layout = dashboard.layout
    placed = LAYOUTS[key](layout) if key else []
    skipped = max(len(placed) - MAX_WIDGETS, 0)
    widgets = [translate_widget(w, lay) for w, lay in placed[:MAX_WIDGETS]]
    failed = sum(w.kind == WidgetKind.UNSUPPORTED for w in widgets)
    reasons = ["layout is approximated from Cloud Monitoring's grid"]
    if not widgets:
        confidence, reasons = Confidence.UNSUPPORTED, ["dashboard has no widgets"]
    elif failed == len(widgets):
        confidence = Confidence.MANUAL
        reasons.append("no widget could be converted")
    else:
        confidence = Confidence.APPROXIMATE
        if failed:
            reasons.append(f"{failed} of {len(widgets)} widgets not converted")
    if skipped:
        reasons.append(f"{skipped} widgets over the limit of {MAX_WIDGETS} were not converted")
    fallback = dashboard.name.rsplit("/", 1)[-1] or "dashboard"
    return IRDashboard(
        source_system="gcp",
        source_key=printable(dashboard.name or dashboard.displayName or fallback, MAX_TITLE),
        name=printable(dashboard.displayName or fallback, MAX_TITLE),
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
    )


def translate_dashboards(dashboards: list[GcpDashboard]) -> list[IRDashboard]:
    return [translate_dashboard(d) for d in dashboards]
