"""Grafana's 24-column grid to New Relic's 12 columns, with a heading for each row.

New Relic has no collapsible group, so a row (or tab) becomes a full-width markdown heading and
its panels follow it; a collapsed row is shown open. Panels are laid out one section at a time:
the section's panels keep their relative position, are moved down when they would overlap after
the grid is halved, and the next section starts under the last one. Edges are halved, not sizes,
so neighbours that touched still touch. A widget taller than the provider accepts is clamped by
the IR, which records it.
"""

from dataclasses import dataclass
from itertools import groupby

from importer.core.dashboard_ir import MAX_WIDGETS, IRWidget, Layout, Visualization, WidgetKind
from importer.core.ir import Confidence
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert.panels import Draft

GRID = 24
MAX_HEIGHT = 100
HEADING_ROWS = 1


@dataclass
class Placed:
    """A draft with its Grafana grid position and the section (row or tab) it sits in."""

    draft: Draft
    x: int
    y: int
    w: int
    h: int
    section: str


def _fit(item: Placed, top: int) -> Layout:
    """The New Relic cell for a Grafana box; `top` is the section's first Grafana row."""
    left, right = (item.x + 1) // 2, (item.x + item.w + 1) // 2
    first, last = (item.y - top) // 2, -(-(item.y - top + item.h) // 2)
    column = min(max(left + 1, 1), 12)
    return Layout(
        column=column,
        row=first + 1,
        width=max(1, min(right - left, 12 - column + 1)),
        height=max(1, min(last - first, MAX_HEIGHT)),
    )


def _overlaps(a: Layout, b: Layout) -> bool:
    return (
        a.column < b.column + b.width
        and b.column < a.column + a.width
        and a.row < b.row + b.height
        and b.row < a.row + a.height
    )


def pack(placed: list[tuple[Draft, Layout]]) -> list[tuple[Draft, Layout]]:
    """Reading order, with any widget that overlaps an earlier one moved below it."""
    taken: list[Layout] = []
    out: list[tuple[Draft, Layout]] = []
    for draft, layout in sorted(placed, key=lambda p: (p[1].row, p[1].column)):
        row = layout.row
        for _ in range(len(taken) + 1):
            hit = [t for t in taken if _overlaps(t, layout.model_copy(update={"row": row}))]
            if not hit:
                break
            row = max(t.row + t.height for t in hit)
        layout = layout.model_copy(update={"row": row})
        taken.append(layout)
        out.append((draft, layout))
    return out


def heading(section: str, row: int) -> IRWidget:
    title = printable(section, 200)
    return IRWidget(
        kind=WidgetKind.MARKDOWN,
        source_type="row",
        title=title,
        layout=Layout(column=1, row=row, width=12, height=HEADING_ROWS),
        visualization=Visualization.MARKDOWN,
        text="## " + " ".join(md_cell(section, 100).split()),
        confidence=Confidence.EXACT,
    )


def lay_out(items: list[Placed]) -> tuple[list[IRWidget], int]:
    """Widgets in page order and how many did not fit under the widget limit."""
    widgets: list[IRWidget] = []
    cursor = 1
    for section, group in groupby(items, key=lambda i: i.section):
        members = list(group)
        if section:
            widgets.append(heading(section, cursor))
            cursor += HEADING_ROWS
        top = min(m.y for m in members)
        packed = pack([(m.draft, _fit(m, top)) for m in members])
        for draft, layout in packed:
            shifted = layout.model_copy(update={"row": layout.row + cursor - 1})
            widgets.append(draft.widget(shifted))
        cursor += max(layout.row + layout.height - 1 for _, layout in packed)
    return widgets[:MAX_WIDGETS], max(0, len(widgets) - MAX_WIDGETS)
