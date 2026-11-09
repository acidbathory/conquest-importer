"""One Grafana panel to one widget draft (research note, section 4).

`convert_panel` follows the coverage planner: a panel it calls convertible becomes a chart,
billboard, pie, table or markdown widget; every other panel becomes a visible "Not migrated"
placeholder that carries the reason and the original query so a person can finish it. A widget
type the planner counts as "extra" (gauge, bar gauge, bar chart, logs) is converted here when the
queries converted, because the dashboard IR can express it.
"""

import html
import re
from dataclasses import dataclass, field

from importer.core.dashboard_ir import (
    IRWidget,
    Layout,
    RawNrqlSeries,
    Threshold,
    Visualization,
    WidgetKind,
    WidgetOptions,
)
from importer.core.ir import Confidence
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.display import (
    Display,
    new_relic_unit,
    thresholds_for,
)
from importer.sources.grafana.dashboard_convert.legend import read_legend
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import (
    Converted,
    QueryOptions,
    convert_log_lines,
    convert_query,
)
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboard_convert.variables import Variables
from importer.sources.grafana.dashboards.datasources import resolve_target
from importer.sources.grafana.dashboards.models import Dashboard, Panel, Target
from importer.sources.grafana.dashboards.panels import LOG_TYPES, PanelType, panel_type
from importer.sources.grafana.dashboards.plan import PanelPlan

V = Visualization
MAX_TITLE = 200
MAX_QUERY_TEXT = 100
MAX_RAW_SERIES = 50
EXTRA_CONVERTIBLE = frozenset({"gauge", "bargauge", "barchart", "logs", "logstable"})
FIXED_VIZ = {
    "stat": V.BILLBOARD,
    "gauge": V.BILLBOARD,
    "bargauge": V.BAR,
    "barchart": V.BAR,
    "piechart": V.PIE,
    "table": V.TABLE,
    "logs": V.TABLE,
    "logstable": V.TABLE,
}
INTERVAL = re.compile(r"(\d+)([smh])")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}
TAGS = re.compile(r"<[^>]*>")
MAX_BUCKET = 86_400
PROBE = Layout(column=1, row=1, width=12, height=1)


@dataclass
class Draft:
    """A widget without a place on the page."""

    kind: WidgetKind
    source_type: str
    title: str
    confidence: Confidence
    reasons: list[str]
    visualization: Visualization | None = None
    queries: list[str] = field(default_factory=list)
    text: str | None = None
    options: WidgetOptions = field(default_factory=WidgetOptions)
    thresholds: list[Threshold] = field(default_factory=list)

    def widget(self, layout: Layout) -> IRWidget:
        return IRWidget(
            kind=self.kind,
            source_type=self.source_type,
            title=self.title,
            layout=layout,
            visualization=self.visualization,
            text=self.text,
            confidence=self.confidence,
            reasons=self.reasons,
            raw_series=[RawNrqlSeries(nrql=q) for q in self.queries],
            options=self.options,
            thresholds=self.thresholds,
        )


@dataclass
class Context:
    """What panel conversion needs from the dashboard; `numeric` and `metrics` are collected."""

    dash: Dashboard
    name_map: NameMap
    variables: Variables
    by_id: dict[int, Panel] = field(default_factory=dict)
    numeric: set[str] = field(default_factory=set)
    metrics: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for panel in self.dash.panels:
            if panel.id is not None:
                self.by_id.setdefault(panel.id, panel)

    @property
    def names(self) -> set[str]:
        return {v.name for v in self.variables.variables}


def title_of(panel: Panel) -> str:
    if panel.title.strip():
        return printable(panel.title, MAX_TITLE)
    return f"Panel {panel.id}" if panel.id is not None else "Panel"


def placeholder(panel: Panel, why: list[str], label: str) -> Draft:
    """A visible "Not migrated" widget: the reasons first, then the original query."""
    shown = next((t.expr for t in panel.targets if not t.hidden and t.expr.strip()), "")
    reasons = list(dict.fromkeys(why))
    if shown:
        reasons.insert(min(1, len(reasons)), f"query: {md_cell(shown, MAX_QUERY_TEXT)}")
    return Draft(
        WidgetKind.UNSUPPORTED, printable(label, 30), title_of(panel), Confidence.MANUAL, reasons
    )


def _text(panel: Panel, display: Display) -> Draft:
    content, notes = display.content, []
    if display.mode == "html":
        content, notes = html.unescape(TAGS.sub("", content)), [r.N_HTML]
    return Draft(
        WidgetKind.MARKDOWN,
        "text",
        title_of(panel),
        Confidence.APPROXIMATE if notes else Confidence.EXACT,
        notes,
        V.MARKDOWN,
        text=content.strip() or title_of(panel),
    )


def _visualization(label: str, display: Display) -> tuple[Visualization, list[str]]:
    if label in FIXED_VIZ:
        return FIXED_VIZ[label], []
    notes = [r.N_BARS] if display.draw == "bars" else []
    if display.stacking in ("normal", "percent"):
        return V.AREA, notes + ([r.N_STACK] if display.stacking == "percent" else [])
    return V.LINE, notes


def _options(display: Display, viz: Visualization) -> tuple[WidgetOptions, list[str]]:
    unit, notes = new_relic_unit(display.unit)
    axis = viz in (V.LINE, V.AREA, V.BAR)
    low, high = display.low, display.high
    if low is not None and high is not None and low > high:
        low = high = None
    options = WidgetOptions(
        unit=unit,
        decimals=display.decimals,
        legend=display.legend if viz in (V.LINE, V.AREA, V.BAR, V.PIE) else None,
        stacked=True
        if viz in (V.AREA, V.BAR) and display.stacking in ("normal", "percent")
        else None,
        y_min=low if axis else None,
        y_max=high if axis else None,
    )
    return options, notes


def _bucket(panel: Panel, display: Display) -> int | None:
    """A fixed bucket asked for by a query or the panel, in seconds; None means automatic."""
    for text in [*(t.interval for t in panel.targets if not t.hidden), display.interval]:
        found = INTERVAL.fullmatch(text.strip())
        if found:
            seconds = int(found[1]) * UNIT_SECONDS[found[2]]
            return seconds if 0 < seconds <= MAX_BUCKET else None
    return None


def _targets(panel: Panel, ctx: Context) -> list[tuple[Target, Panel]]:
    """Visible queries with the panel that owns each; a reused result is the source's queries."""
    found: list[tuple[Target, Panel]] = []
    for target in panel.targets:
        if target.hidden:
            continue
        if target.panel_id is None:
            found.append((target, panel))
            continue
        source = ctx.by_id.get(target.panel_id)
        if source is None or any(t.panel_id is not None for t in source.targets):
            raise Unconvertible(r.C_REUSE)
        found += [(t, source) for t in source.targets if not t.hidden]
    if not found or len(found) > MAX_RAW_SERIES:
        raise Unconvertible(r.C_SHAPE)
    return found


def _convert_target(
    target: Target, owner: Panel, panel: Panel, label: str, opts: QueryOptions, ctx: Context
) -> Converted:
    family = resolve_target(target, owner, ctx.dash)
    if label in LOG_TYPES and family == "loki":
        return convert_log_lines(target.expr, opts)
    if family not in ("prometheus", "loki"):
        raise Unconvertible(r.C_SHAPE)
    repeat = panel.repeat or None
    return convert_query(target.expr, family, opts, read_legend(target.legend), repeat)


def _charts(
    panel: Panel, plan: PanelPlan, display: Display, ctx: Context, kind: PanelType
) -> Draft:
    viz, notes = _visualization(kind.label, display)
    bucket = _bucket(panel, display)
    opts = QueryOptions(
        variables=ctx.variables.options,
        name_map=ctx.name_map,
        timeseries=viz in (V.LINE, V.AREA),
        bucket_seconds=bucket,
        intervals=ctx.variables.intervals,
        unavailable=ctx.variables.unavailable,
    )
    queries: list[str] = []
    for target, owner in _targets(panel, ctx):
        done = _convert_target(target, owner, panel, kind.label, opts, ctx)
        if not set(done.variables) <= ctx.names:
            raise Unconvertible(r.C_VARIABLE_UNDEFINED)
        queries.append(done.nrql)
        notes += done.notes
        ctx.numeric.update(done.numeric_variables)
        ctx.metrics.extend(done.metrics)
    if bucket and opts.timeseries:
        notes.append(r.N_FIXED_INTERVAL)
    if viz == V.BILLBOARD and len(queries) > 1:
        notes.append(r.N_MULTI_QUERY)
    options, option_notes = _options(display, viz)
    thresholds, threshold_notes = thresholds_for(display, viz)
    return Draft(
        WidgetKind.CHART,
        kind.label,
        title_of(panel),
        Confidence.APPROXIMATE,
        list(dict.fromkeys([*notes, *plan.notes, *option_notes, *threshold_notes])),
        viz,
        queries,
        options=options,
        thresholds=thresholds,
    )


def converts(plan: PanelPlan, kind: PanelType) -> bool:
    """Whether the planner's verdict lets the panel become a widget."""
    if plan.tier is not None:
        return True
    return kind.label in EXTRA_CONVERTIBLE and plan.extra_tier is not None


def convert_panel(panel: Panel, plan: PanelPlan, display: Display | None, ctx: Context) -> Draft:
    """The widget draft for one panel, or a placeholder; it never raises."""
    kind = panel_type(panel.type)
    if not converts(plan, kind):
        return placeholder(panel, plan.reasons, kind.label)
    shown = display if display is not None else Display()
    try:
        is_text = kind.label == "text"
        draft = _text(panel, shown) if is_text else _charts(panel, plan, shown, ctx, kind)
        if display is None and not is_text:
            draft.reasons.append(r.N_DISPLAY)
        draft.widget(PROBE)  # the IR checks the options against the widget type here
        return draft
    except Unconvertible as stop:
        return placeholder(panel, [stop.reason], kind.label)
    except ValueError:  # a model refused a value the readers let through
        return placeholder(panel, [r.C_UNEXPECTED], kind.label)
