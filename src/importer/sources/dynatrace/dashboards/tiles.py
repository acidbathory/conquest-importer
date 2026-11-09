"""Classify one Platform dashboard tile as exact, draft or manual (or convert, for markdown).

The DQL rules are in dql_rules.py. This module adds what is specific to a tile: the type, the
visualization, the layout and the data the tile needs. A tile that depends on Dynatrace-only
data (self-monitoring metrics, security events, Smartscape, Davis) is flagged: that does not
change its verdict, because the query may still translate, but the data does not exist in New
Relic.
"""

from importer.core.text import printable
from importer.sources.dynatrace.dashboards import reasons as r
from importer.sources.dynatrace.dashboards.dql_rules import Analysis, analyse
from importer.sources.dynatrace.dashboards.markdown_tiles import read_markdown
from importer.sources.dynatrace.dashboards.models import Layout, Tile, Unreadable
from importer.sources.dynatrace.dashboards.plan_models import LayoutPlan, TilePlan, Verdict

WIDGETS = {  # Dynatrace visualization -> New Relic visualization id (research note 2.6)
    "lineChart": "viz.line",
    "areaChart": "viz.area",
    "singleValue": "viz.billboard",
    "table": "viz.table",
    "donutChart": "viz.pie",
    "pieChart": "viz.pie",
    "categoricalBarChart": "viz.bar",
}
CLOSEST_WIDGETS = {  # shown with a different look; UNVERIFIED as equivalents
    "barChart": "viz.area",
    "gauge": "viz.bullet",
    "meterBar": "viz.bullet",
}
DISPLAY_SETTINGS = ("thresholds", "coloring")
OTHER_KINDS = {"code": r.TILE_CODE, "slo": r.TILE_SLO}


def layout_plan(layout: Layout | None) -> LayoutPlan | None:
    """24 columns to 12: edges move to round-half-up of x/2 so neighbours still touch.

    The row height is half the Dynatrace height, at least 1. The factor is a proposal and is
    UNVERIFIED (research note section 2.8).
    """
    if layout is None:
        return None
    left = (layout.x + 1) // 2
    right = (layout.x + layout.w + 1) // 2
    return LayoutPlan(
        column=left + 1,
        width=max(1, right - left),
        row=layout.y + 1,
        height=max(1, (layout.h + 1) // 2),
    )


def _verdict(blockers: list[str], notes: list[str]) -> Verdict:
    if blockers:
        return Verdict.MANUAL
    return Verdict.DRAFT if notes else Verdict.EXACT


def _widget(tile: Tile, a: Analysis) -> str:
    """The widget id; records a blocker for a visualization New Relic cannot show."""
    if tile.visualization in WIDGETS:
        return WIDGETS[tile.visualization]
    if tile.visualization in CLOSEST_WIDGETS:
        a.draft(r.D_WIDGET)
        return CLOSEST_WIDGETS[tile.visualization]
    a.block(r.TILE_VIZ)
    return ""


def _data_tile(tile: Tile) -> TilePlan:
    a = analyse(tile.query)
    widget = _widget(tile, a)
    if tile.prompt:
        a.block(r.TILE_PROMPT)
    if tile.tile_timeframe:
        a.draft(r.D_TILE_TIMEFRAME)
    if tile.segments:
        a.draft(r.D_SEGMENTS)
    notes = list(a.drafts)
    if any(tile.settings.get(key) for key in DISPLAY_SETTINGS):
        notes.append(r.N_SETTINGS)  # informational: display settings do not change the verdict
    if a.dynatrace_only:
        notes.append(r.NO_DATA)
    return TilePlan(
        id=printable(tile.id, 80),
        kind="data",
        title=printable(tile.title, 120),
        verdict=_verdict(a.blockers, a.drafts),
        widget=widget,
        reasons=a.blockers,
        notes=notes,
        no_data_source=a.dynatrace_only,
        variables=a.variables,
        limit_variables=a.limit_variables,
        metrics=a.metrics,
        layout=layout_plan(tile.layout),
    )


def _markdown_tile(tile: Tile) -> TilePlan:
    verdict, action, flags = read_markdown(tile.content)
    return TilePlan(
        id=printable(tile.id, 80),
        kind="markdown",
        title=printable(tile.title, 120),
        verdict=verdict,
        widget="viz.markdown",
        action=action,
        flags=flags,
        layout=layout_plan(tile.layout),
    )


def _manual_tile(tile: Tile, reason: str) -> TilePlan:
    return TilePlan(
        id=printable(tile.id, 80),
        kind=printable(tile.kind, 40),
        title=printable(tile.title, 120),
        verdict=Verdict.MANUAL,
        reasons=[reason],
        layout=layout_plan(tile.layout),
    )


def classify_tile(tile: Tile) -> TilePlan:
    if tile.kind == "markdown":
        return _markdown_tile(tile)
    if tile.kind == "data":
        return _data_tile(tile)
    return _manual_tile(tile, OTHER_KINDS.get(tile.kind, r.TILE_TYPE))


def unreadable_tile(item: Unreadable) -> TilePlan:
    return TilePlan(
        id=printable(item.ref, 80),
        kind="unreadable",
        verdict=Verdict.MANUAL,
        reasons=[r.TILE_UNREADABLE],
    )
