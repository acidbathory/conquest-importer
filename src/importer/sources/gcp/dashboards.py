"""Translate Cloud Monitoring dashboards into the dashboard IR.

Like the alert translator this never raises on odd input: a widget it cannot convert is labelled
and kept, so the report shows what was lost. Metric filters and aggregations go through the same
helpers as alert conditions (`metric_scope`, `check_aligner`, `metric_series`), so metric and
label naming rules live in one place. Charts and scorecards over a time series filter convert;
text widgets become markdown; everything else is a failed widget with a reason.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import (
    MAX_PAGED_WIDGETS,
    PAGE_WIDGETS,
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Series,
    VariableType,
    Visualization,
    WidgetKind,
)
from importer.core.dashboard_nrql import VARIABLE_NAME
from importer.core.emit.nrql import identifier, widget_nrql
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.gcp import datapath
from importer.sources.gcp.dashboard_extra import (
    NOT_SHOWN,
    PAGE_KINDS,
    failed,
    incident_list,
    kind_of,
    logs_panel,
    result,
    section_header,
    split_pages,
)
from importer.sources.gcp.dashboard_models import MAX_WIDGETS, GcpDashboard
from importer.sources.gcp.dashboard_options import options_of, thresholds_of, unit_of
from importer.sources.gcp.dashboard_promql import promql_nrql
from importer.sources.gcp.translate import (
    POLL_SECONDS,
    Fail,
    check_aligner,
    merge_two,
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
    "timeSeriesQueryLanguage": (
        "MQL queries in widgets are not converted (Google has ended MQL for new charts in the "
        "console; rewrite the query in NRQL)"
    ),
    "opsAnalyticsQuery": "SQL queries in widgets are not converted",
}
SECOND_STAGE_FAIL = "second-stage aggregation is not supported"
SECOND_STAGE_NOTE = "a second-stage aggregation that only smooths one series is not migrated"
CHART_KINDS = ("xyChart", "scorecard", "timeSeriesTable", "pieChart")
NO_QUERY = "widget has no query"
UNKNOWN_QUERY = "widget query is not recognised"
NO_CONTENT = "text widget has no content"
POLL_NOTE = (
    "the alignment period is raised to 300s because New Relic polls Google Cloud every 5 minutes"
)

Placed = tuple[dict[str, Any], Layout]
Query = Series | RawNrqlSeries


@dataclass
class Context:
    """Dashboard-level state: the template variables a query may use, and those it did use."""

    variables: dict[str, str] = field(default_factory=dict)  # variable name -> label key
    used: set[str] = field(default_factory=set)


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


def _second_stage(
    body: dict[str, Any], agg: dict[str, object]
) -> tuple[dict[str, object], list[str]]:
    """The aggregation with the dashboard's second stage merged in, and notes; Fail if it cannot
    be written as one query."""
    second = body.get("secondaryAggregation")
    if not isinstance(second, dict) or not second:
        return agg, []
    smooths = (
        not second.get("groupByFields")
        and second.get("crossSeriesReducer") in (None, "", "REDUCE_NONE")
        and second.get("perSeriesAligner") in (None, "", "ALIGN_NONE", "ALIGN_MEAN")
    )
    if smooths:
        return agg, [SECOND_STAGE_NOTE]
    fields = second.get("groupByFields")
    if isinstance(fields, list):
        second = {
            **second,
            "groupByFields": [
                QUOTED_LABEL.sub(r"\1", f) if isinstance(f, str) else f for f in fields
            ],
        }
    return merge_two(agg, second)


QUOTED_LABEL = re.compile(r'(?<=\.label\.)"([A-Za-z_][A-Za-z0-9_]*)"')


def _filter_series(body: dict[str, Any], notes: set[str]) -> Series | str:
    """Dashboards quote label keys (`resource.label."zone"`); the filter reader does not."""
    text = body.get("filter")
    if isinstance(text, str):
        body = {**body, "filter": QUOTED_LABEL.sub(r"\1", text)}
    agg = body.get("aggregation")
    if isinstance(agg, dict) and isinstance(agg.get("groupByFields"), list):
        fields = [
            QUOTED_LABEL.sub(r"\1", f) if isinstance(f, str) else f for f in agg["groupByFields"]
        ]
        body = {**body, "aggregation": {**agg, "groupByFields": fields}}
    try:
        metric_type, scope, scope_notes = metric_scope(body)
        raw = body.get("aggregation")
        agg, second_notes = _second_stage(body, raw if isinstance(raw, dict) else {})
        agg, aligner = check_aligner(agg)
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
    notes.update(scope_notes, signal_notes, second_notes)
    for key in ("pickTimeSeriesFilter", "statisticalTimeSeriesFilter"):
        if key in body:
            notes.add(f"{key} is not migrated; all series are shown")
    return Series(signal=signal, period_seconds=max(period, POLL_SECONDS))


def _ratio_series(ratio: object, notes: set[str]) -> Series | str:
    """A numerator/denominator query as one series: sum(numerator) / sum(denominator)."""
    parts = ratio if isinstance(ratio, dict) else {}
    num, den = parts.get("numerator"), parts.get("denominator")
    if not isinstance(num, dict) or not isinstance(den, dict) or "secondaryAggregation" in parts:
        return UNKNOWN_QUERY if "secondaryAggregation" not in parts else SECOND_STAGE_FAIL
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


def _series(query: object, notes: set[str], ctx: Context, *, timeseries: bool) -> Query | str:
    """One data set's query as a Series (or a pre-built query), or the reason it cannot convert."""
    tsq = query if isinstance(query, dict) else {}
    if "timeSeriesFilterRatio" in tsq:
        return _ratio_series(tsq["timeSeriesFilterRatio"], notes)
    for key, reason in QUERY_REASONS.items():
        if key in tsq:
            return reason
    if "prometheusQuery" in tsq:
        done = promql_nrql(
            tsq.get("prometheusQuery"),
            timeseries=timeseries,
            bucket_seconds=None,
            variables=ctx.variables,
        )
        if isinstance(done, str):
            return done
        nrql, query_notes, used = done
        ctx.used.update(used)
        notes.update(query_notes)
        try:
            return RawNrqlSeries(nrql=nrql)
        except ValueError:
            return "PromQL not converted: the converted query is not accepted by the query checks"
    body = tsq.get("timeSeriesFilter")
    if not isinstance(body, dict):
        return UNKNOWN_QUERY
    return _filter_series(body, notes)


_result = result
_failed = failed


def _as_raw(s: Query, timeseries: bool) -> RawNrqlSeries:
    """A structured series as pre-built NRQL, so it can sit beside PromQL queries in one widget."""
    if isinstance(s, RawNrqlSeries):
        return s
    nrql = widget_nrql(s, timeseries)
    return RawNrqlSeries(nrql=nrql + (" TIMESERIES AUTO" if timeseries and s.auto_period else ""))


TABLE_NOTE = "table column settings and templates are not migrated"
PIE_NOTE = "slice name templates are not migrated"


def _view(kind: str, plot: object) -> tuple[Visualization, list[str]]:
    if kind == "scorecard":
        return Visualization.BILLBOARD, []
    if kind == "timeSeriesTable":
        return Visualization.TABLE, []
    if kind == "pieChart":
        return Visualization.PIE, []
    viz = PLOT_TYPES.get(str(plot), Visualization.LINE)
    if str(plot) == "STACKED_BAR":
        return viz, [BAR_NOTE]
    if plot and str(plot) not in PLOT_TYPES:
        return viz, ["this plot type is not supported; shown as a line"]
    return viz, []


def _chart(body: dict[str, Any], title: str, layout: Layout, kind: str, ctx: Context) -> IRWidget:
    scorecard = kind == "scorecard"
    queries = (
        [{"timeSeriesQuery": body.get("timeSeriesQuery")}]
        if scorecard
        else _dicts(body.get("dataSets"))[:MAX_DATASETS]
    )
    plot = next((q.get("plotType") for q in queries if q.get("plotType")), None)
    viz, reasons = _view(kind, plot)
    timeseries = viz in TIMESERIES_VISUALIZATIONS
    notes: set[str] = set()
    found = [_series(q.get("timeSeriesQuery"), notes, ctx, timeseries=timeseries) for q in queries]
    series = [s for s in found if not isinstance(s, str)]
    failures = [s for s in found if isinstance(s, str)]
    if not series:
        return _failed(title, layout, kind, failures or [NO_QUERY])
    reasons = sorted(notes) + reasons
    if any(isinstance(s, RawNrqlSeries) for s in series):
        raw = [_as_raw(s, timeseries) for s in series]
        extra: dict[str, Any] = {"raw_series": raw}
    else:
        extra = {"series": series}
    structured = [s for s in series if isinstance(s, Series)]
    if scorecard and any(s.signal.group_by for s in structured):
        reasons.append(GROUP_NOTE)
    if scorecard:
        reasons += [
            f"{printable(k, 20)} is not migrated"
            for k in ("gaugeView", "sparkChartView")
            if body.get(k)
        ]
    unit, unit_notes = unit_of(queries)
    thresholds, threshold_notes = thresholds_of(body.get("thresholds"), viz)
    reasons += unit_notes + threshold_notes
    if kind == "timeSeriesTable":
        reasons.append(TABLE_NOTE)
    if kind == "pieChart":
        reasons.append(PIE_NOTE)
        if str(body.get("chartType")) == "DONUT":
            reasons.append("a donut chart is shown as a pie chart")
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
        options=options_of(unit, viz),
        thresholds=thresholds,
        **extra,
    )


def _text(body: dict[str, Any], title: str, layout: Layout, *, titled: bool) -> IRWidget:
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        if not titled:
            return _failed(title, layout, "text", [NO_CONTENT])
        return _result(
            title,
            layout,
            "text",
            Confidence.APPROXIMATE,
            ["an empty text box with a title is shown as a heading"],
            widget_kind=WidgetKind.MARKDOWN,
            visualization=Visualization.MARKDOWN,
            text=f"## {title}",
        )
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


def translate_widget(
    widget: dict[str, Any], layout: Layout, ctx: Context | None = None
) -> IRWidget:
    kind = kind_of(widget)
    raw_title = widget.get("title")
    title = printable(
        raw_title if isinstance(raw_title, str) and raw_title else f"{kind} widget", MAX_TITLE
    )
    body = widget.get(kind)
    body = body if isinstance(body, dict) else {}
    if kind in CHART_KINDS:
        return _chart(body, title, layout, kind, ctx or Context())
    if kind == "text":
        return _text(
            body, title, layout, titled=isinstance(raw_title, str) and bool(raw_title.strip())
        )
    if kind == "sectionHeader":
        return section_header(body, title, layout)
    if kind == "logsPanel":
        return logs_panel(body, title, layout)
    if kind == "incidentList":
        return incident_list(title, layout)
    if kind in NOT_SHOWN:
        return _failed(title, layout, kind, [NOT_SHOWN[kind]])
    return _failed(title, layout, kind, ["dashboard widget type is not supported"])


def _safe_widget(widget: dict[str, Any], layout: Layout, ctx: Context) -> IRWidget:
    try:
        return datapath.tag_widget(translate_widget(widget, layout, ctx))
    except Exception:
        return _failed("widget", layout, "unknown", [READ_FAILED])


def _context(dashboard: GcpDashboard) -> Context:
    """Template variables from `dashboardFilters` that a query can name."""
    ctx = Context()
    extra = dashboard.model_extra or {}
    for item in _dicts(extra.get("dashboardFilters")):
        name, label = item.get("templateVariable"), item.get("labelKey")
        if (
            isinstance(name, str)
            and isinstance(label, str)
            and re.fullmatch(VARIABLE_NAME, name)
            and name not in ctx.variables
        ):
            try:
                identifier(label)
            except ValueError:
                continue
            ctx.variables[name] = label
    return ctx


def _variables(ctx: Context) -> list[IRVariable]:
    return [
        IRVariable(
            name=name,
            title=name,
            type=VariableType.NRQL,
            nrql=f"SELECT uniques({identifier(ctx.variables[name])}, 200) FROM Metric",  # noqa: S608
        )
        for name in sorted(ctx.used)
    ]


def translate_dashboard(dashboard: GcpDashboard) -> IRDashboard:
    key, layout = dashboard.layout
    laid = LAYOUTS[key](layout) if key else []
    paged = any(kind_of(w) in PAGE_KINDS for w, _ in laid)
    cap = MAX_PAGED_WIDGETS if paged else MAX_WIDGETS
    skipped = max(len(laid) - cap, 0)
    placed, moved = pack(laid[:cap])
    fallback = dashboard.name.rsplit("/", 1)[-1] or "dashboard"
    name = printable(dashboard.displayName or fallback, MAX_TITLE)
    ctx = _context(dashboard)
    tiles, page_notes = split_pages(placed, name, PAGE_WIDGETS if paged else MAX_WIDGETS)
    widgets = [
        _safe_widget(w, lay, ctx).model_copy(update={"page": page}) for w, lay, page in tiles
    ]
    failed_count = sum(w.kind == WidgetKind.UNSUPPORTED for w in widgets)
    reasons = ["layout is approximated from Cloud Monitoring's grid", *page_notes]
    if not widgets:
        confidence, reasons = Confidence.UNSUPPORTED, ["dashboard has no widgets"]
    elif failed_count == len(widgets):
        confidence = Confidence.MANUAL
        reasons.append("no widget could be converted")
    else:
        confidence = Confidence.APPROXIMATE
        if failed_count:
            reasons.append(f"{failed_count} of {len(widgets)} widgets not converted")
    if moved:
        reasons.append(
            f"{moved} widgets overlapped in the source layout or did not fit 12 columns and "
            "were moved down"
        )
    if skipped:
        reasons.append(f"{skipped} widgets over the limit of {cap} were not converted")
    return IRDashboard(
        source_system="gcp",
        source_key=printable(dashboard.name or dashboard.displayName or "dashboard", MAX_KEY),
        name=name,
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
        variables=_variables(ctx),
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
