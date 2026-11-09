"""Read a v2 dashboard resource: `elements` (panels by name) and a `layout` that places them.

Layouts are Grid, Rows, Tabs and AutoGrid, and rows and tabs can hold each other; the walk is
iterative and stops at MAX_DEPTH. Field names follow the v2beta1 schema; v2alpha1 spellings
(`kind` holding the plugin id) are read too. How a live export stores a Prometheus query is not
confirmed (see docs/research/grafana-dashboards.md section 5.1), so a query's `spec` is read for
the usual keys and the rest ignored.
"""

from collections.abc import Mapping
from typing import Any

from importer.sources.grafana.dashboards.classic import (
    panel_from_body,
    read_header,
    read_target,
)
from importer.sources.grafana.dashboards.common import (
    Found,
    ds_ref,
    mapping,
    sequence,
    text,
    truthy,
    whole,
)
from importer.sources.grafana.dashboards.models import (
    GRID_COLUMNS,
    MAX_ANNOTATIONS,
    MAX_DEPTH,
    MAX_PANELS,
    MAX_QUERY,
    MAX_TARGETS,
    MAX_VARIABLES,
    MAX_Y,
    W_ANNOTATIONS,
    W_DEEP,
    W_PANELS,
    W_TARGETS,
    W_UNPLACED,
    W_VARIABLES,
    Annotation,
    Dashboard,
    DsRef,
    Panel,
    Target,
    Variable,
)

VARIABLE_KINDS = {
    "QueryVariable": "query",
    "TextVariable": "textbox",
    "ConstantVariable": "constant",
    "DatasourceVariable": "datasource",
    "IntervalVariable": "interval",
    "CustomVariable": "custom",
    "SwitchVariable": "switch",
    "GroupByVariable": "adhoc",
    "AdhocVariable": "adhoc",
}
GENERIC_KINDS = frozenset({"", "DataQuery", "VizConfig"})  # v2beta1 puts the plugin in `group`
AUTO_ROW_HEIGHT = 8


def _query_ds(query: dict[str, Any]) -> DsRef:
    """Plugin id from `group` (v2beta1) or `kind` (v2alpha1); uid from the datasource name."""
    kind = text(query.get("kind"))
    ref = ds_ref(query.get("datasource"))
    plugin = text(query.get("group")) or ("" if kind in GENERIC_KINDS else kind)
    return DsRef(type=plugin or (ref.type if ref else ""), uid=ref.uid if ref else "")


def _target(raw: dict[str, Any]) -> Target:
    spec = mapping(raw.get("spec"))
    query = mapping(spec.get("query"))
    body = {
        **mapping(query.get("spec")),
        "refId": spec.get("refId"),
        "hide": spec.get("hidden"),
        "datasource": None,
    }
    target = read_target(body)
    target.ds = _query_ds(query)
    return target


def _panel_body(spec: dict[str, Any]) -> tuple[dict[str, Any], list[Target]]:
    """A classic-shaped body for the shared panel reader, plus the targets read apart."""
    group = mapping(mapping(spec.get("data")).get("spec"))
    viz = mapping(spec.get("vizConfig"))
    viz_spec = mapping(viz.get("spec"))
    kind = text(viz.get("kind"))
    body: dict[str, Any] = {
        "id": spec.get("id"),
        "title": spec.get("title"),
        "type": text(viz.get("group")) or ("" if kind in GENERIC_KINDS else kind),
        "links": spec.get("links"),
        "options": viz_spec.get("options"),
        "fieldConfig": viz_spec.get("fieldConfig"),
        "transformations": group.get("transformations"),
    }
    queries = [mapping(q) for q in sequence(group.get("queries")) if isinstance(q, dict)]
    return body, [_target(q) for q in queries[:MAX_TARGETS]]


def _element(
    element: dict[str, Any], library: Mapping[str, Any], warnings: list[str]
) -> Panel | None:
    spec = mapping(element.get("spec"))
    if element.get("kind") == "LibraryPanel":
        stub = {
            "id": spec.get("id"),
            "title": spec.get("title"),
            "libraryPanel": spec.get("libraryPanel"),
        }
        return panel_from_body(stub, library, warnings)
    if element.get("kind") != "Panel":
        return None
    body, targets = _panel_body(spec)
    panel = panel_from_body(body, library, warnings)
    panel.targets = targets
    queries = sequence(mapping(mapping(spec.get("data")).get("spec")).get("queries"))
    if len(queries) > MAX_TARGETS and W_TARGETS not in warnings:
        warnings.append(W_TARGETS)
    return panel


class _Placer:
    """Puts element panels on the page in layout order, once the walk reaches them."""

    def __init__(
        self, dash: Dashboard, elements: dict[str, Any], library: Mapping[str, Any]
    ) -> None:
        self.dash = dash
        self.elements = elements
        self.library = library
        self.placed: set[str] = set()

    def place(self, ref: object, x: int, y: int, w: int, h: int, section: str, depth: int) -> None:
        name = text(mapping(ref).get("name"), 200)
        element = self.elements.get(name)
        if not isinstance(element, dict):
            return
        if len(self.dash.panels) >= MAX_PANELS:
            _note(self.dash, W_PANELS)
            return
        panel = _element(element, self.library, self.dash.warnings)
        if panel is None:
            return
        self.placed.add(name)
        panel.x, panel.y, panel.w, panel.h = x, y, w, h
        panel.section, panel.depth = section, depth
        self.dash.panels.append(panel)

    def grid(self, spec: dict[str, Any], section: str, depth: int) -> None:
        for item in sequence(spec.get("items")):
            s = mapping(mapping(item).get("spec"))
            self.place(
                s.get("element"),
                whole(s.get("x"), 0, GRID_COLUMNS, 0),
                whole(s.get("y"), 0, MAX_Y, 0),
                whole(s.get("width"), 1, GRID_COLUMNS, GRID_COLUMNS),
                whole(s.get("height"), 1, MAX_Y, 1),
                section,
                depth,
            )
            repeat = text(mapping(s.get("repeat")).get("value"), 100)
            if repeat and self.dash.panels and self.dash.panels[-1].section == section:
                self.dash.panels[-1].repeat = repeat

    def auto_grid(self, spec: dict[str, Any], section: str, depth: int) -> None:
        columns = whole(spec.get("maxColumnCount"), 1, GRID_COLUMNS, 3)
        width = GRID_COLUMNS // columns
        for index, item in enumerate(sequence(spec.get("items"))):
            s = mapping(mapping(item).get("spec"))
            row, col = divmod(index, columns)
            self.place(
                s.get("element"),
                col * width,
                row * AUTO_ROW_HEIGHT,
                width,
                AUTO_ROW_HEIGHT,
                section,
                depth,
            )


def _note(dash: Dashboard, message: str) -> None:
    if message not in dash.warnings:
        dash.warnings.append(message)


def _join(section: str, title: str) -> str:
    return f"{section} / {title}"[:300] if section and title else (title or section)


def _walk_layout(root: dict[str, Any], dash: Dashboard, placer: _Placer) -> None:
    stack: list[tuple[dict[str, Any], int, str]] = [(mapping(root.get("layout")), 0, "")]
    while stack:
        node, depth, section = stack.pop()
        if depth > MAX_DEPTH:
            _note(dash, W_DEEP)
            continue
        kind, spec = node.get("kind"), mapping(node.get("spec"))
        if kind == "GridLayout":
            placer.grid(spec, section, depth)
        elif kind == "AutoGridLayout":
            placer.auto_grid(spec, section, depth)
        elif kind in ("RowsLayout", "TabsLayout"):
            rows = kind == "RowsLayout"
            children = [mapping(c) for c in sequence(spec.get("rows" if rows else "tabs"))]
            for child in reversed(children):
                cspec = mapping(child.get("spec"))
                stack.append(
                    (
                        mapping(cspec.get("layout")),
                        depth + 1,
                        _join(section, text(cspec.get("title"))),
                    )
                )
            _count_children(dash, children, rows=rows)


def _count_children(dash: Dashboard, children: list[dict[str, Any]], *, rows: bool) -> None:
    if not rows:
        dash.tabs += len(children)
        return
    dash.rows += len(children)
    dash.collapsed_rows += sum(mapping(c.get("spec")).get("collapse") is True for c in children)
    dash.row_repeats += sum(bool(mapping(mapping(c.get("spec")).get("repeat"))) for c in children)


def _variables(root: dict[str, Any], dash: Dashboard) -> None:
    found = [mapping(v) for v in sequence(root.get("variables")) if isinstance(v, dict)]
    if len(found) > MAX_VARIABLES:
        dash.warnings.append(W_VARIABLES)
    for raw in found[:MAX_VARIABLES]:
        spec = mapping(raw.get("spec"))
        query = spec.get("query")
        ref: DsRef | None = None
        if isinstance(query, dict):
            ref = _query_ds(query)
            inner = mapping(query.get("spec"))
            query = inner.get("expr") or inner.get("query") or inner.get("definition")
        dash.variables.append(
            Variable(
                name=text(spec.get("name")),
                kind=VARIABLE_KINDS.get(text(raw.get("kind"), 40), text(raw.get("kind"), 40)),
                query=text(query, MAX_QUERY) or text(spec.get("pluginId"), 100),
                ds=ref,
                multi=spec.get("multi") is True,
                include_all=spec.get("includeAll") is True,
                regex=text(spec.get("regex")),
                hidden=spec.get("hide") == "hideVariable",
            )
        )


def _annotations(root: dict[str, Any], dash: Dashboard) -> None:
    found = [mapping(a) for a in sequence(root.get("annotations")) if isinstance(a, dict)]
    if len(found) > MAX_ANNOTATIONS:
        dash.warnings.append(W_ANNOTATIONS)
    for raw in found[:MAX_ANNOTATIONS]:
        spec = mapping(raw.get("spec"))
        dash.annotations.append(
            Annotation(
                name=text(spec.get("name")),
                ds_type=_query_ds(mapping(spec.get("query"))).type,
                enabled=spec.get("enable") is not False,
                builtin=truthy(spec.get("builtIn")),
            )
        )


def read_v2(root: dict[str, Any], found: Found, library: dict[str, Any]) -> Dashboard:
    dash = read_header(root, found)
    elements = mapping(root.get("elements"))
    placer = _Placer(dash, elements, library)
    _walk_layout(root, dash, placer)
    left = [name for name in elements if name not in placer.placed]
    if left:
        dash.warnings.append(W_UNPLACED.format(count=len(left)))
        for name in left[: max(0, MAX_PANELS - len(dash.panels))]:
            placer.place({"name": name}, 0, MAX_Y, GRID_COLUMNS, 1, "", 0)
    _variables(root, dash)
    _annotations(root, dash)
    return dash
