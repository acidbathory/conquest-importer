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

from importer.core.dashboard_ir import (
    MAX_PAGES,
    PAGE_WIDGETS,
    IRWidget,
    Layout,
    Visualization,
    WidgetKind,
)
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


def _section_widgets(section: str, members: list[Placed]) -> list[IRWidget]:
    """One section's widgets, heading first, with rows counted from 1."""
    widgets = [heading(section, 1)] if section else []
    cursor = 1 + len(widgets) * HEADING_ROWS
    top = min(m.y for m in members)
    for draft, layout in pack([(m.draft, _fit(m, top)) for m in members]):
        widgets.append(draft.widget(layout.model_copy(update={"row": layout.row + cursor - 1})))
    return widgets


def _height(widgets: list[IRWidget]) -> int:
    return max(w.layout.row + w.layout.height - 1 for w in widgets)


def _rebase(widgets: list[IRWidget], first: int, page: str | None) -> list[IRWidget]:
    """The widgets moved so the first row is 1 (offset by `first - 1` for a stacked section)."""
    top = min(w.layout.row for w in widgets)
    return [
        w.model_copy(
            update={
                "layout": w.layout.model_copy(update={"row": w.layout.row - top + first}),
                "page": page,
            }
        )
        for w in widgets
    ]


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 1
    while candidate in taken:
        n += 1
        candidate = f"{name} ({n})"
    taken.add(candidate)
    return candidate


def _units(items: list[Placed], fallback: str) -> list[tuple[str, list[IRWidget]]]:
    """Sections as (page name, widgets); a section over one page is cut at widget boundaries."""
    units: list[tuple[str, list[IRWidget]]] = []
    for section, group in groupby(items, key=lambda i: i.section):
        widgets = _section_widgets(section, list(group))
        name = printable(" ".join(section.split()), 150) or fallback
        for n, start in enumerate(range(0, len(widgets), PAGE_WIDGETS)):
            units.append(
                (name if n == 0 else f"{name} part {n + 1}", widgets[start:][:PAGE_WIDGETS])
            )
    return units


def _merge(units: list[tuple[str, list[IRWidget]]]) -> list[tuple[str, list[IRWidget]]]:
    """Consecutive units stacked into pages of at most PAGE_WIDGETS, named after the first."""
    pages: list[tuple[str, list[list[IRWidget]]]] = []
    for name, widgets in units:
        if pages and sum(len(w) for w in pages[-1][1]) + len(widgets) <= PAGE_WIDGETS:
            pages[-1][1].append(widgets)
        else:
            pages.append((name, [widgets]))
    out: list[tuple[str, list[IRWidget]]] = []
    for name, parts in pages:
        stacked: list[IRWidget] = []
        cursor = 1
        for part in parts:
            stacked += _rebase(part, cursor, None)
            cursor = _height(stacked) + 1
        label = name if len(parts) == 1 else f"{name} and {len(parts) - 1} more"
        out.append((label, stacked))
    return out


def lay_out(items: list[Placed], fallback: str = "Dashboard") -> tuple[list[IRWidget], int]:
    """Widgets in page order and how many did not fit under 25 pages of 150 widgets.

    A dashboard of up to 150 widgets is one page with no page name (as it always was). A larger
    one gets a page for each section (Grafana row), named after it; a section over 150 widgets is
    cut at widget boundaries into `name`, `name part 2`, ...; with more than 25 pages, neighbouring
    sections share a page. Every page is re-based to row 1. `fallback` names a page whose section
    has no name.
    """
    units = _units(items, fallback)
    if sum(len(w) for _, w in units) <= PAGE_WIDGETS:
        flat: list[IRWidget] = []
        for _, widgets in units:
            flat += _rebase(widgets, _height(flat) + 1 if flat else 1, None)
        return flat, 0
    if len(units) > MAX_PAGES:
        units = _merge(units)
    taken: set[str] = set()
    out: list[IRWidget] = []
    for name, widgets in units[:MAX_PAGES]:
        out += _rebase(widgets, 1, _unique(name, taken))
    return out, sum(len(w) for _, w in units[MAX_PAGES:])
