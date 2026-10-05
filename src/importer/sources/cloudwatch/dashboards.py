"""Translate CloudWatch dashboards into the dashboard IR.

Like the alert translator this never raises on odd input: a widget it cannot convert is labelled
and kept, so the report shows exactly what was lost. Metric queries reuse the alert path
(`_resolve_term`), so metric and dimension naming rules live in one place.
"""

from typing import Any

from pydantic import ValidationError

from importer.core.dashboard_ir import (
    IRDashboard,
    IRWidget,
    Layout,
    Series,
    Visualization,
    WidgetKind,
)
from importer.core.ir import Confidence, MetricSignal
from importer.core.text import printable
from importer.sources.cloudwatch.dashboard_models import CloudWatchDashboard
from importer.sources.cloudwatch.models import Dimension
from importer.sources.cloudwatch.translate import NAMING_NOTE, _Fail, _resolve_term

DEFAULT_PERIOD = 300
MAX_TITLE = 200
# CloudWatch chart settings that have no counterpart here yet; named in the report when present.
UNMIGRATED_SETTINGS = ("annotations", "yAxis", "legend")
UNSUPPORTED_TYPES = {
    "log": "log widget (CloudWatch Logs Insights query) is not supported yet",
    "alarm": "alarm status widget is not supported yet",
    "explorer": "metrics explorer widget is not supported yet",
}
VIEWS = {
    "singleValue": (Visualization.BILLBOARD, None),
    "bar": (Visualization.BAR, None),
    "pie": (Visualization.PIE, None),
    "gauge": (Visualization.BILLBOARD, "gauge view is shown as a single number"),
}


def _layout(widget: dict[str, Any]) -> Layout:
    """CloudWatch uses a 24-column grid, New Relic 12; halve and clamp."""

    def num(key: str, default: int) -> int:
        v = widget.get(key, default)
        return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default

    column = min(num("x", 0) // 2 + 1, 12)
    width = max(1, min(round(num("width", 6) / 2), 12 - column + 1))
    return Layout(
        column=column,
        row=num("y", 0) // 2 + 1,
        width=width,
        height=max(1, min(num("height", 6) // 2, 100)),
    )


def _row_to_series(
    row: Any,
    previous: list[Any],
    props: dict[str, Any],
    prefixes: dict[str, str],
    notes: set[str],
) -> tuple[Series | None, list[Any], str | None]:
    """Convert one `metrics` row. Returns (series, tokens for '.' lookups, failure reason).

    Things that changed but did not stop the conversion are added to `notes`.
    """
    if not isinstance(row, list) or not row:
        return None, previous, "metric row is not a list"
    if isinstance(row[0], dict):
        return None, previous, "metric math expressions in widgets are not supported yet"
    options = row[-1] if isinstance(row[-1], dict) else {}
    tokens = row[:-1] if isinstance(row[-1], dict) else list(row)
    # "." repeats the value at the same position in the previous row.
    tokens = [previous[i] if t == "." and i < len(previous) else t for i, t in enumerate(tokens)]
    if tokens and tokens[0] == "...":
        # "..." repeats the previous row's namespace, metric and dimension names; the values
        # that follow replace its dimension values, one each.
        values = tokens[1:]
        names = previous[2::2]
        if len(previous) < 4 or len(values) != len(names):
            return None, previous, "'...' shorthand does not match the previous metric row"
        tokens = [*previous[:2]]
        for name, value in zip(names, values, strict=True):
            tokens += [name, value]
        notes.add(
            "'...' rows were expanded from the previous row (unverified against a real export)"
        )
    elif "..." in tokens:
        return None, tokens, "'...' shorthand in a metric row is not supported yet"
    if len(tokens) < 2 or len(tokens) % 2 != 0 or not all(isinstance(t, str) for t in tokens):
        return None, tokens, "metric row does not have the form namespace, metric, dimension pairs"
    if options.get("visible") is False:
        return None, tokens, None  # a hidden helper series; nothing to show
    dims = [Dimension(Name=tokens[i], Value=tokens[i + 1]) for i in range(2, len(tokens), 2)]
    stat = str(options.get("stat") or props.get("stat") or "Average")
    period = options.get("period") or props.get("period") or DEFAULT_PERIOD
    if not isinstance(period, int) or isinstance(period, bool) or not 1 <= period <= 86400:
        return None, tokens, "metric period is outside 1s-24h"
    if period < 60:
        notes.add("periods below 60s were raised to 60s (Metric Streams data is per minute)")
    try:
        term, scope = _resolve_term(tokens[0], tokens[1], dims, stat, prefixes)
        signal = MetricSignal(
            terms={"m1": term}, aggregation=term.space_aggregation, scope=scope, group_by=[]
        )
        label = options.get("label")
        series = Series(
            label=label if isinstance(label, str) and label else None,
            signal=signal,
            period_seconds=max(period, 60),  # Metric Streams data arrives per minute
        )
    except _Fail as exc:
        return None, tokens, exc.reason
    except ValidationError:
        return None, tokens, "metric row has values that are not supported"
    return series, tokens, None


def _metric_widget(
    widget: dict[str, Any], title: str, layout: Layout, prefixes: dict[str, str]
) -> IRWidget:
    props = widget.get("properties")
    props = props if isinstance(props, dict) else {}
    rows = props.get("metrics")
    series: list[Series] = []
    failures: list[str] = []
    attempted = 0
    previous: list[Any] = []
    notes: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        parsed, previous, failure = _row_to_series(row, previous, props, prefixes, notes)
        if parsed is not None:
            series.append(parsed)
            attempted += 1
        elif failure is not None:
            failures.append(failure)
            attempted += 1

    def result(conf: Confidence, kind: WidgetKind, reasons: list[str], **extra: Any) -> IRWidget:
        return IRWidget(
            kind=kind,
            source_type="metric",
            title=title,
            layout=layout,
            confidence=conf,
            reasons=reasons,
            **extra,
        )

    if not series:
        why = sorted(set(failures)) or ["widget has no metrics"]
        return result(Confidence.MANUAL, WidgetKind.UNSUPPORTED, why)

    view = props.get("view", "timeSeries")
    viz, view_note = VIEWS.get(str(view), (Visualization.LINE, None))
    if view not in VIEWS and view != "timeSeries":
        view_note = f"view {str(view)[:30]!r} is not supported; shown as a line chart"
    if viz == Visualization.LINE and props.get("stacked") is True:
        viz = Visualization.AREA
    reasons = [NAMING_NOTE]
    if view_note:
        reasons.append(view_note)
    if failures:
        reasons.append(
            f"{len(series)} of {attempted} metrics converted; not converted: "
            + "; ".join(sorted(set(failures)))
        )
    reasons += sorted(notes)
    unmigrated = [k for k in UNMIGRATED_SETTINGS if k in props]
    if unmigrated:
        reasons.append(f"chart settings not migrated: {', '.join(unmigrated)}")
    return result(
        Confidence.APPROXIMATE, WidgetKind.CHART, reasons, visualization=viz, series=series
    )


def translate_widget(widget: dict[str, Any], prefixes: dict[str, str]) -> IRWidget:
    wtype = widget.get("type")
    wtype = wtype if isinstance(wtype, str) else "unknown"
    props = widget.get("properties")
    props = props if isinstance(props, dict) else {}
    raw_title = props.get("title")
    title = printable(
        raw_title if isinstance(raw_title, str) and raw_title else f"{wtype} widget", MAX_TITLE
    )
    layout = _layout(widget)
    if wtype == "metric":
        return _metric_widget(widget, title, layout, prefixes)
    if wtype == "text":
        text = props.get("markdown")
        if isinstance(text, str):
            return IRWidget(
                kind=WidgetKind.MARKDOWN,
                source_type="text",
                title=title,
                layout=layout,
                visualization=Visualization.MARKDOWN,
                text=text,
                confidence=Confidence.EXACT,
            )
        reason = "text widget has no markdown"
    else:
        reason = UNSUPPORTED_TYPES.get(wtype, f"widget type {wtype[:30]!r} is not supported")
    return IRWidget(
        kind=WidgetKind.UNSUPPORTED,
        source_type=wtype[:30],
        title=title,
        layout=layout,
        confidence=Confidence.UNSUPPORTED,
        reasons=[reason],
    )


def translate_dashboard(dashboard: CloudWatchDashboard, prefixes: dict[str, str]) -> IRDashboard:
    widgets = [translate_widget(w, prefixes) for w in dashboard.widgets]
    failed = sum(w.kind == WidgetKind.UNSUPPORTED for w in widgets)
    reasons = ["layout is approximated from CloudWatch's 24-column grid"]
    if not widgets:
        confidence, reasons = Confidence.UNSUPPORTED, ["dashboard has no widgets"]
    elif failed == len(widgets):
        confidence = Confidence.MANUAL
        reasons.append("no widget could be converted")
    else:
        confidence = Confidence.APPROXIMATE
        if failed:
            reasons.append(f"{failed} of {len(widgets)} widgets not converted")
    return IRDashboard(
        source_system="cloudwatch",
        source_key=dashboard.name,
        name=printable(dashboard.name, MAX_TITLE),
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
    )
