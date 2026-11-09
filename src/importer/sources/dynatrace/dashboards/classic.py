"""Classic dashboards (Configuration API): a coarse plan by tile type (research note build item 9).

Markdown and header tiles convert. A chart of metrics (`DATA_EXPLORER`) needs the metric name
map, which does not exist yet, so it is manual with that reason. Every other tile type (custom
charts, USQL, entity lists, problem lists, map tiles, synthetic tiles) has no New Relic widget.
A management zone filter on the dashboard or a tile is reported: New Relic has no management
zones.
"""

from importer.core.text import printable
from importer.sources.dynatrace.dashboards import reasons as r
from importer.sources.dynatrace.dashboards.markdown_tiles import read_markdown
from importer.sources.dynatrace.dashboards.models import ClassicDashboard, ClassicTile
from importer.sources.dynatrace.dashboards.plan_models import TilePlan, Verdict

TEXT_TILES = {"MARKDOWN": "viz.markdown", "HEADER": "viz.markdown"}


def classify_tile(index: int, tile: ClassicTile) -> TilePlan:
    base = {"id": f"tile {index}", "kind": printable(tile.tile_type, 40)}
    title = printable(tile.name, 120)
    zone = [r.C_ZONE] if tile.management_zone else []
    if tile.tile_type in TEXT_TILES:
        _, action, flags = read_markdown(tile.markdown)
        if tile.tile_type == "HEADER":
            action = "keep"  # a header has no body; it becomes a heading
        return TilePlan(
            **base,
            title=title,
            verdict=Verdict.CONVERT,
            widget=TEXT_TILES[tile.tile_type],
            action=action,
            flags=flags,
            notes=zone,
        )
    reason = r.C_METRICS if tile.tile_type == "DATA_EXPLORER" else r.C_TILE
    return TilePlan(
        **base,
        title=title,
        verdict=Verdict.MANUAL,
        reasons=[reason],
        notes=zone,
        metrics=[printable(m, 200) for m in tile.metrics],
    )


def classify_tiles(dashboard: ClassicDashboard) -> list[TilePlan]:
    return [classify_tile(i, t) for i, t in enumerate(dashboard.tiles)]
