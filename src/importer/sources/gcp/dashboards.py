"""Translate Cloud Monitoring dashboards into the dashboard IR.

Like the alert translator this never raises on odd input: a widget it cannot convert is labelled
and kept, so the report shows what was lost. Metric filters and aggregations go through the same
helpers as alert conditions (`metric_scope`, `check_aligner`, `metric_series`), so metric and
label naming rules live in one place. Charts and scorecards over a time series filter convert;
text widgets become markdown; everything else is a failed widget with a reason.
"""

import math
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
    Fail,
    check_aligner,
    metric_scope,
    metric_series,
    ratio_series,
    seconds,
)

MAX_TITLE = 200
MAX_KEY = 10_000  # keys are not cut to the title length, so long names stay distinct
MAX_DATASETS = 20
MAX_SECONDS = 86400
DEFAULT_ROW_HEIGHT = 3
# Bar charts have no time axis in New Relic, so stacked bars are shown as an area chart.
PLOT_TYPES = {
    "LINE": Visualization.LINE,
    "STACKED_AREA": Visualization.AREA,
    "STACKED_BAR": Visualization.AREA,
}
BAR_NOTE = "stacked bars are shown as an area chart over time"
GROUP_NOTE = "a scorecard with a group-by shows one value per group, not a single number"
READ_FAILED = "widget could not be read"
QUERY_REASONS = {
    "timeSeriesQueryLanguage": "MQL queries in widgets are not converted",
    "prometheusQuery": "PromQL queries in widgets are not converted",
    "opsAnalyticsQuery": "SQL queries in widgets are not converted",
}
SECOND_STAGE = "second-stage aggregation is not supported"
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
    if (isinstance(value, str) and value.isascii() and value.isdecimal()) or (
        isinstance(value, float) and math.isfinite(value)
    ):
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
        left, right = round(x * scale), round((x + w) * scale)  # edges, so neighbours still touch
        top, bottom = y // 2, -(-(y + h) // 2)
        placed.append((widget, _layout(left + 1, top + 1, right - left, bottom - top)))
    return placed


def _overlaps(a: Layout, b: Layout) -> bool:
    return (
        a.column < b.column + b.width
        and b.column < a.column + a.width
        and a.row < b.row + b.height
        and b.row < a.row + a.height
    )


def pack(placed: list[Placed]) -> tuple[list[Placed], int]:
    """Reading order, with any widget that overlaps an earlier one moved down below it.

    Returns the widgets and how many were moved. Each pass moves a widget past at least one more
    box, so the loop ends after at most one pass per box already placed.
    """
    taken: list[Layout] = []
    out: list[Placed] = []
    moved = 0
    for widget, layout in sorted(placed, key=lambda p: (p[1].row, p[1].column)):
        row = layout.row
        for _ in range(len(taken) + 1):
            hit = [t for t in taken if _overlaps(t, layout.model_copy(update={"row": row}))]
            if not hit:
                break
            row = max(t.row + t.height for t in hit)
        if row != layout.row:
            moved += 1
            layout = layout.model_copy(update={"row": row})
        taken.append(layout)
        out.append((widget, layout))
    return out, moved


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


def _ratio_series(ratio: object, notes: set[str]) -> Series | str:
    """A numerator/denominator query as one series: sum(numerator) / sum(denominator)."""
    parts = ratio if isinstance(ratio, dict) else {}
    num, den = parts.get("numerator"), parts.get("denominator")
    if not isinstance(num, dict) or not isinstance(den, dict) or "secondaryAggregation" in parts:
        return UNKNOWN_QUERY if "secondaryAggregation" not in parts else SECOND_STAGE
    num_agg: dict[str, object] = {"alignmentPeriod": f"{POLL_SECONDS}s"}
    if isinstance(num.get("aggregation"), dict):
        num_agg = num["aggregation"]
    den_agg = den["aggregation"] if isinstance(den.get("aggregation"), dict) else num_agg
    body = {
        "filter": num.get("filter"),
        "aggregations": [num_agg],
        "denominatorFilter": den.get("filter"),
        "denominatorAggregations": [den_agg],
    }
    try:
        metric_type, scope, scope_notes = metric_scope(body)
        agg, aligner = check_aligner(num_agg)
        period = seconds(agg.get("alignmentPeriod")) or POLL_SECONDS
        if period > MAX_SECONDS:
            raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
        signal, period, _scale, signal_notes = metric_series(
            metric_type, scope, agg, aligner, period, chart=True
        )
        signal, _scale, signal_notes = ratio_series(body, signal, aligner, period, signal_notes)
    except Fail as fail:
        return fail.reason
    if period < POLL_SECONDS:
        notes.add(POLL_NOTE)
    notes.update(scope_notes, signal_notes)
    return Series(signal=signal, period_seconds=max(period, POLL_SECONDS))


def _series(query: object, notes: set[str]) -> Series | str:
    """One data set's time series filter as a Series, or the reason it cannot convert."""
    tsq = query if isinstance(query, dict) else {}
    if "timeSeriesFilterRatio" in tsq:
        return _ratio_series(tsq["timeSeriesFilterRatio"], notes)
    for key, reason in QUERY_REASONS.items():
        if key in tsq:
            return reason
    body = tsq.get("timeSeriesFilter")
    if not isinstance(body, dict):
        return UNKNOWN_QUERY
    try:
        metric_type, scope, scope_notes = metric_scope(body)
        if body.get("secondaryAggregation"):
            raise Fail(Confidence.MANUAL, SECOND_STAGE)
        raw = body.get("aggregation")
        agg, aligner = check_aligner(raw if isinstance(raw, dict) else {})
        period = seconds(agg.get("alignmentPeriod")) or POLL_SECONDS
        if period > MAX_SECONDS:
            raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
        signal, period, _scale, signal_notes = metric_series(
            metric_type, scope, agg, aligner, period, chart=True
        )
    except Fail as fail:
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
    if not scorecard and str(plot) == "STACKED_BAR":
        reasons.append(BAR_NOTE)
    elif not scorecard and plot and str(plot) not in PLOT_TYPES:
        reasons.append("this plot type is not supported; shown as a line")
    if scorecard and any(s.signal.group_by for s in series):
        reasons.append(GROUP_NOTE)
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
    return _failed(title, layout, kind, ["dashboard widget type is not supported"])


def _safe_widget(widget: dict[str, Any], layout: Layout) -> IRWidget:
    try:
        return translate_widget(widget, layout)
    except Exception:
        return _failed("widget", layout, "unknown", [READ_FAILED])


def translate_dashboard(dashboard: GcpDashboard) -> IRDashboard:
    key, layout = dashboard.layout
    laid = LAYOUTS[key](layout) if key else []
    skipped = max(len(laid) - MAX_WIDGETS, 0)
    placed, moved = pack(laid[:MAX_WIDGETS])
    widgets = [_safe_widget(w, lay) for w, lay in placed]
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
    if moved:
        reasons.append(
            f"{moved} widgets overlapped in the source layout or did not fit 12 columns and "
            "were moved down"
        )
    if skipped:
        reasons.append(f"{skipped} widgets over the limit of {MAX_WIDGETS} were not converted")
    fallback = dashboard.name.rsplit("/", 1)[-1] or "dashboard"
    return IRDashboard(
        source_system="gcp",
        source_key=printable(dashboard.name or dashboard.displayName or "dashboard", MAX_KEY),
        name=printable(dashboard.displayName or fallback, MAX_TITLE),
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
    )


def translate_dashboards(dashboards: list[GcpDashboard]) -> list[IRDashboard]:
    """Translate all; a repeated source key gets `#2`, `#3` so each dashboard has its own file."""
    out: list[IRDashboard] = []
    used: set[str] = set()
    for dashboard in dashboards:
        translated = translate_dashboard(dashboard)
        key, n = translated.source_key, 1
        while translated.source_key in used:
            n += 1
            translated = translated.model_copy(update={"source_key": f"{key}#{n}"})
        used.add(translated.source_key)
        out.append(translated)
    return out
