"""Datadog dashboards to IR dashboards: widgets, groups as pages or sections, variables, layout.

A top-level `group` becomes a page when the dashboard has several groups that fit the page
limits; otherwise each group becomes a heading and the dashboard stays on one page. A group inside
a group is always a heading on its parent's page. Layout is Datadog's 12-column grid taken
as it is; a widget with no layout is placed left to right after the others, and boxes that
overlap are moved down (`core.dashboard_pack`).
"""

from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import (
    MAX_PAGES,
    MAX_WIDGETS,
    PAGE_WIDGETS,
    PROVIDER_MAX_HEIGHT,
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    WidgetKind,
)
from importer.core.dashboard_pack import pack
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.models import DatadogDashboard
from importer.sources.datadog.dashboards.variables import read_variables
from importer.sources.datadog.dashboards.widgets import (
    Context,
    heading,
    translate_widget,
)

MAX_TITLE = 200
MAX_DEPTH = 8  # groups inside groups; deeper ones are read as placeholders by the type check
DEFAULT_WIDTH = {"note": 12, "free_text": 12, "group": 12}
DEFAULT_HEIGHT = {"note": 2, "free_text": 1}
CHART_WIDTH = 4
CHART_HEIGHT = 3
MAX_COLUMNS = 12


def _int(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _box(item: dict[str, Any], kind: str, flow: "Flow") -> Layout:
    """The layout of a widget: Datadog's own x, y, width, height when present."""
    raw = item.get("layout")
    if isinstance(raw, dict) and all(k in raw for k in ("x", "y", "width", "height")):
        width = min(max(_int(raw["width"], CHART_WIDTH), 1), MAX_COLUMNS)
        column = min(max(_int(raw["x"], 0), 0), MAX_COLUMNS - width) + 1
        return Layout(
            column=column,
            row=max(_int(raw["y"], 0), 0) + 1,
            width=width,
            height=min(max(_int(raw["height"], CHART_HEIGHT), 1), PROVIDER_MAX_HEIGHT),
        )
    return flow.next(DEFAULT_WIDTH.get(kind, CHART_WIDTH), DEFAULT_HEIGHT.get(kind, CHART_HEIGHT))


@dataclass
class Flow:
    """Places boxes with no layout left to right and wraps to a new row when full."""

    row: int = 1
    column: int = 1
    height: int = 0

    def next(self, width: int, height: int) -> Layout:
        if self.column + width - 1 > MAX_COLUMNS:
            self.row += self.height
            self.column, self.height = 1, 0
        box = Layout(column=self.column, row=self.row, width=width, height=height)
        self.column += width
        self.height = max(self.height, height)
        return box


@dataclass
class Block:
    """Widgets that move together: one top-level group, or a run of loose widgets."""

    title: str | None  # the group's title; None for loose widgets
    items: list[tuple[IRWidget, Layout]] = field(default_factory=list)


def _definition(item: dict[str, Any]) -> dict[str, Any]:
    inner = item.get("definition")
    return inner if isinstance(inner, dict) else item


def _text(definition: dict[str, Any], key: str, default: str) -> str:
    value = definition.get(key)
    return value if isinstance(value, str) else default


def _place(
    item: dict[str, Any], flow: Flow, ctx: Context, depth: int
) -> list[tuple[IRWidget, Layout]]:
    """The widgets of one item, group headings included, with layouts relative to the block."""
    definition = _definition(item)
    kind = _text(definition, "type", "unknown")
    box = _box(item, kind, flow)
    if kind != "group" or depth >= MAX_DEPTH:
        return [(translate_widget(definition, box, None, ctx), box)]
    title = _text(definition, "title", "")
    inner = _children(definition, ctx, depth + 1)
    top = Layout(column=1, row=box.row, width=MAX_COLUMNS, height=1)
    shifted = [(w, lay.model_copy(update={"row": lay.row + box.row})) for w, lay in inner]
    return [(heading(title, top, None), top), *shifted]


def _children(
    definition: dict[str, Any], ctx: Context, depth: int
) -> list[tuple[IRWidget, Layout]]:
    flow = Flow()
    out: list[tuple[IRWidget, Layout]] = []
    kids = definition.get("widgets")
    for kid in kids if isinstance(kids, list) else []:
        if isinstance(kid, dict):
            out += _place(kid, flow, ctx, depth)
    return out


def _blocks(raw: list[dict[str, Any]], ctx: Context) -> list[Block]:
    blocks: list[Block] = []
    flow = Flow()
    for item in raw:
        definition = _definition(item)
        if definition.get("type") == "group":
            block = Block(_text(definition, "title", ""))
            block.items = _children(definition, ctx, 1)
            blocks.append(block)
            continue
        if not blocks or blocks[-1].title is not None:
            blocks.append(Block(None))
            flow = Flow()
        kind = _text(definition, "type", "unknown")
        box = _box(item, kind, flow)
        blocks[-1].items.append((translate_widget(definition, box, None, ctx), box))
    return blocks


def _unique(title: str, taken: set[str], fallback: str) -> str:
    base = printable(" ".join(title.split()), MAX_TITLE) or fallback
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base} ({n})"
    taken.add(name)
    return name


def _as_pages(blocks: list[Block]) -> bool:
    groups = [b for b in blocks if b.title is not None]
    loose = sum(len(b.items) for b in blocks if b.title is None)
    return (
        len(groups) >= 2
        and len(groups) + (1 if loose else 0) <= MAX_PAGES
        and all(len(b.items) <= PAGE_WIDGETS for b in groups)
        and loose <= PAGE_WIDGETS
    )


def _offset(placed: list[tuple[IRWidget, Layout]], base: int) -> list[tuple[IRWidget, Layout]]:
    return [(w, lay.model_copy(update={"row": lay.row + base})) for w, lay in placed]


def _bottom(placed: list[tuple[IRWidget, Layout]]) -> int:
    return max((lay.row + lay.height for _, lay in placed), default=1)


def _arrange(blocks: list[Block], name: str) -> tuple[list[IRWidget], int]:
    """Widgets with their final page and layout, and how many overlapped and were moved."""
    paged = _as_pages(blocks)
    taken = {name}
    pages: dict[str | None, list[tuple[IRWidget, Layout]]] = {}
    for block in blocks:
        if block.title is None:
            page: str | None = None
            items = block.items
        elif paged:
            page = _unique(block.title, taken, "Group")
            items = block.items
        else:
            page = None
            top = Layout(column=1, row=1, width=MAX_COLUMNS, height=1)
            items = [(heading(block.title, top, None), top), *_offset(block.items, 1)]
        have = pages.setdefault(page, [])
        have += _offset(items, _bottom(have) - 1 if have else 0)
    out: list[IRWidget] = []
    moved = 0
    for page, placed in pages.items():
        packed, count = pack(placed)
        moved += count
        out += [w.model_copy(update={"layout": lay, "page": page}) for w, lay in packed]
    return out, moved


def _cap(widgets: list[IRWidget]) -> tuple[list[IRWidget], int]:
    """Keep within the limits the IR enforces; the rest is counted, never dropped silently."""
    if any(w.page for w in widgets):
        kept: list[IRWidget] = []
        seen: dict[str | None, int] = {}
        for w in widgets:
            if seen.get(w.page, 0) < PAGE_WIDGETS:
                seen[w.page] = seen.get(w.page, 0) + 1
                kept.append(w)
        return kept, len(widgets) - len(kept)
    return widgets[:MAX_WIDGETS], max(len(widgets) - MAX_WIDGETS, 0)


def _confidence(widgets: list[IRWidget]) -> Confidence:
    if any(w.kind is not WidgetKind.UNSUPPORTED for w in widgets):
        return Confidence.APPROXIMATE
    return Confidence.MANUAL


def translate_dashboard(dashboard: DatadogDashboard) -> IRDashboard:
    ctx = Context(dashboard.names, read_variables(dashboard.variable_list))
    name = printable(dashboard.title_text, MAX_TITLE) or "Datadog dashboard"
    arranged, moved = _arrange(_blocks(dashboard.widget_list, ctx), name)
    widgets, dropped = _cap(arranged)
    reasons = []
    if moved:
        reasons.append(DR.N_MOVED.format(count=moved))
    if dropped:
        reasons.append(DR.N_CAPPED.format(count=dropped))
    variables: list[IRVariable] = [u.variable for u in ctx.used.values()]
    return IRDashboard(
        source_system="datadog",
        source_key=dashboard.source_key,
        name=name,
        widgets=widgets,
        confidence=_confidence(widgets),
        reasons=reasons,
        variables=variables,
    )


def translate_dashboards(dashboards: list[DatadogDashboard]) -> list[IRDashboard]:
    """All dashboards; a repeated source key gets an index so each has its own file."""
    out: list[IRDashboard] = []
    used: set[str] = set()
    for dash in dashboards:
        found = translate_dashboard(dash)
        key, n = found.source_key, 1
        while found.source_key in used:
            n += 1
            found = found.model_copy(update={"source_key": f"{key}#{n}"})
        used.add(found.source_key)
        out.append(found)
    return out
