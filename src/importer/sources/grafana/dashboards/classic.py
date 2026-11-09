"""Read a classic dashboard (and the v1 resource, which holds the same object under `spec`).

Handles schemaVersion 0 to 42 as stored: `rows[]` with `span` (before 16), row panels with
members inside `panels[]` (collapsed) or after them (open), string data source names, and
variables with old `hide` spellings. Nothing is migrated in place; the fields are only read.
"""

from collections import ChainMap
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from importer.sources.grafana.dashboards.common import (
    Found,
    ds_ref,
    mapping,
    note,
    schema_version,
    sequence,
    target_hint,
    text,
    truthy,
    whole,
)
from importer.sources.grafana.dashboards.models import (
    GRID_COLUMNS,
    MAX_ANNOTATIONS,
    MAX_DEPTH,
    MAX_EXPR,
    MAX_PANELS,
    MAX_QUERY,
    MAX_TARGETS,
    MAX_VARIABLES,
    MAX_Y,
    W_ANNOTATIONS,
    W_DEEP,
    W_NOT_OBJECTS,
    W_PANELS,
    W_SCHEMA_NEWER,
    W_TARGETS,
    W_VARIABLES,
    Annotation,
    Dashboard,
    Panel,
    Target,
    Variable,
)

LATEST_SCHEMA = 42
MAX_REGEX = 300
MAX_TRANSFORMATIONS = 20
LEGACY_MAPPINGS = ("valueMaps", "rangeMaps")
# old table panel `transform` values that reshape the result; the default needs nothing
LEGACY_TABLE_TRANSFORMS = frozenset({"timeseries_to_rows", "timeseries_aggregations", "json"})
_END = object()  # the end of a panel list (a JSON null in the list is not the end)


def read_target(raw: dict[str, Any]) -> Target:
    expr = raw.get("expr")
    return Target(
        ref_id=text(raw.get("refId"), 20),
        expr=expr[:MAX_EXPR] if isinstance(expr, str) else "",
        ds=ds_ref(raw.get("datasource")),
        hidden=truthy(raw.get("hide")) or raw.get("hidden") is True,
        legend=text(raw.get("legendFormat") or raw.get("alias")),
        fmt=text(raw.get("format"), 40),
        instant=raw.get("instant") is True,
        interval=text(raw.get("interval"), 20),
        malformed="expr" in raw and raw["expr"] is not None and not isinstance(expr, str),
        panel_id=whole(raw.get("panelId"), 0, 2**31, -1) if "panelId" in raw else None,
        hint=target_hint(raw),
        expr_type=text(raw.get("type"), 40) if raw.get("expression") is not None else "",
    )


def read_targets(raw: object, warnings: list[str]) -> list[Target]:
    items = [t for t in sequence(raw) if isinstance(t, dict)]
    if len(items) > MAX_TARGETS and W_TARGETS not in warnings:
        warnings.append(W_TARGETS)
    return [read_target(t) for t in items[:MAX_TARGETS]]


def _transformations(body: dict[str, Any]) -> list[str]:
    """Transformation ids, plus the old table panel's `transform` when it reshapes the data."""
    ids = (
        text(mapping(t).get("id") or mapping(t).get("kind"), 60)
        for t in sequence(body.get("transformations"))
    )
    found = [i for i, _ in zip(ids, range(MAX_TRANSFORMATIONS), strict=False)]
    legacy = text(body.get("transform"), 40)
    if legacy in LEGACY_TABLE_TRANSFORMS:
        found.append(f"legacy-{legacy}")
    return found


def _counts(body: dict[str, Any]) -> tuple[int, int]:
    """Value mappings and field overrides, in the modern and the legacy spelling."""
    config = mapping(body.get("fieldConfig"))
    mappings = len(sequence(mapping(config.get("defaults")).get("mappings")))
    mappings += sum(len(sequence(body.get(key))) for key in LEGACY_MAPPINGS)
    overrides = len(sequence(config.get("overrides"))) + len(sequence(body.get("seriesOverrides")))
    return mappings, overrides


def _calcs(body: dict[str, Any], options: dict[str, Any]) -> list[str]:
    """How a stat reduces the series: reduceOptions.calcs, or the old singlestat valueName."""
    found = sequence(mapping(options.get("reduceOptions")).get("calcs"))
    names = [text(c, 40) for c in found[:5] if isinstance(c, str)]
    legacy = text(body.get("valueName"), 20)
    return names or ([legacy] if legacy else [])


def panel_from_body(body: dict[str, Any], library: Mapping[str, Any], warnings: list[str]) -> Panel:
    """One panel; a library panel stub is completed from its model when the model is known."""
    lib_uid, found = "", False
    stub = mapping(body.get("libraryPanel"))
    if isinstance(stub.get("uid"), str):
        lib_uid = text(stub["uid"], 100)
        model = library.get(lib_uid)
        if isinstance(model, dict):
            found = True
            kept: dict[str, Any] = {k: body[k] for k in ("id", "gridPos", "span") if k in body}
            if isinstance(body.get("title"), str) and body["title"]:
                kept["title"] = body["title"]
            body = {**model, **kept}
    mappings, overrides = _counts(body)
    options = mapping(body.get("options"))
    defaults = mapping(mapping(body.get("fieldConfig")).get("defaults"))
    content = options.get("content", body.get("content"))
    return Panel(
        id=whole(body.get("id"), -(2**31), 2**31, 0) if "id" in body else None,
        type=text(body.get("type"), 100),
        title=text(body.get("title")),
        ds=ds_ref(body.get("datasource")),
        targets=read_targets(body.get("targets"), warnings),
        transformations=_transformations(body),
        library_uid=lib_uid,
        library_found=found,
        repeat=text(body.get("repeat"), 100),
        mappings=mappings,
        overrides=overrides,
        links=len(sequence(body.get("links"))),
        text_mode=text(options.get("mode", body.get("mode")), 20),
        text_chars=len(content) if isinstance(content, str) else 0,
        time_shift=text(body.get("timeShift"), 40),
        time_from=text(body.get("timeFrom"), 40),
        calcs=_calcs(body, options),
        unit=text(defaults.get("unit") or body.get("format"), 40),
    )


def _place(panel: Panel, body: dict[str, Any], cursor: int) -> int:
    """Set the grid position; returns where the next legacy `span` panel in a row starts."""
    grid = mapping(body.get("gridPos"))
    span = body.get("span")
    if grid or not isinstance(span, int | float) or isinstance(span, bool):
        panel.x = whole(grid.get("x"), 0, GRID_COLUMNS, 0)
        panel.y = whole(grid.get("y"), 0, MAX_Y, 0)
        panel.w = whole(grid.get("w"), 1, GRID_COLUMNS, GRID_COLUMNS)
        panel.h = whole(grid.get("h"), 1, MAX_Y, 1)
        return cursor
    panel.x = min(cursor, GRID_COLUMNS)
    panel.w = whole(span * 2, 1, GRID_COLUMNS, GRID_COLUMNS)  # 12 legacy columns to 24
    return cursor + panel.w


@dataclass
class _Frame:
    items: Iterator[Any]
    section: str
    depth: int
    cursor: int = 0


@dataclass
class _Walk:
    dash: Dashboard
    library: Mapping[str, Any]
    skipped: int = 0


def _legacy_rows(root: dict[str, Any]) -> list[Any]:
    """Old `rows[]` as row panels that hold their members, so one walk reads both layouts."""
    rows = []
    for row in sequence(root.get("rows")):
        if not isinstance(row, dict):
            rows.append(row)
            continue
        rows.append(
            {
                "type": "row",
                "title": row.get("title"),
                "collapsed": row.get("collapse") is True,
                "repeat": row.get("repeat"),
                "panels": row.get("panels"),
            }
        )
    return rows


def _row(frame: _Frame, body: dict[str, Any], walk: _Walk, stack: list[_Frame]) -> None:
    dash = walk.dash
    dash.rows += 1
    dash.collapsed_rows += body.get("collapsed") is True
    dash.row_repeats += bool(text(body.get("repeat")))
    title = text(body.get("title"))
    members = sequence(body.get("panels"))
    if not members:
        frame.section = title  # an open row: the panels that follow in the list are its members
        return
    if frame.depth + 1 > MAX_DEPTH:
        note(dash, W_DEEP)
        return
    stack.append(_Frame(iter(members), title, frame.depth + 1))


def _walk_panels(root: dict[str, Any], walk: _Walk) -> None:
    dash = walk.dash
    top = sequence(root.get("panels")) or _legacy_rows(root)
    stack = [_Frame(iter(top), "", 0)]
    while stack:
        frame = stack[-1]
        body = next(frame.items, _END)
        if body is _END:
            stack.pop()
        elif not isinstance(body, dict):
            walk.skipped += 1
        elif body.get("type") == "row":
            _row(frame, body, walk, stack)
        elif len(dash.panels) >= MAX_PANELS:
            note(dash, W_PANELS)
            return
        else:
            panel = panel_from_body(body, walk.library, dash.warnings)
            frame.cursor = _place(panel, body, frame.cursor)
            panel.section, panel.depth = frame.section, frame.depth
            dash.panels.append(panel)


def read_variable(raw: dict[str, Any]) -> Variable:
    query = raw.get("query")
    if isinstance(query, dict):
        query = query.get("query")
    return Variable(
        name=text(raw.get("name")),
        kind=text(raw.get("type"), 40),
        query=text(query if isinstance(query, str) else raw.get("definition"), MAX_QUERY),
        ds=ds_ref(raw.get("datasource")),
        multi=raw.get("multi") is True,
        include_all=raw.get("includeAll") is True,
        regex=text(raw.get("regex"), MAX_REGEX),
        hidden=raw.get("hide") == 2 or raw.get("hideVariable") is True,
    )


def _variables(root: dict[str, Any], dash: Dashboard) -> None:
    listing = root.get("templating")
    items = sequence(mapping(listing).get("list") if isinstance(listing, dict) else listing)
    found = [v for v in items if isinstance(v, dict)]
    if len(found) > MAX_VARIABLES:
        dash.warnings.append(W_VARIABLES)
    dash.variables = [read_variable(v) for v in found[:MAX_VARIABLES]]


def _annotations(root: dict[str, Any], dash: Dashboard) -> None:
    listing = root.get("annotations")
    items = sequence(mapping(listing).get("list") if isinstance(listing, dict) else listing)
    found = [a for a in items if isinstance(a, dict)]
    if len(found) > MAX_ANNOTATIONS:
        dash.warnings.append(W_ANNOTATIONS)
    for raw in found[:MAX_ANNOTATIONS]:
        ref = ds_ref(raw.get("datasource"))
        dash.annotations.append(
            Annotation(
                name=text(raw.get("name")),
                ds_type=ref.type if ref else "",
                enabled=raw.get("enable") is not False,
                builtin=truthy(raw.get("builtIn")),
            )
        )


def _inputs(root: dict[str, Any]) -> dict[str, str]:
    """`${DS_X}` placeholders of an external export and the plugin each stands for."""
    found: dict[str, str] = {}
    for item in sequence(root.get("__inputs"))[:100]:
        entry = mapping(item)
        name, plugin = text(entry.get("name"), 100), text(entry.get("pluginId"), 100)
        if name and plugin and entry.get("type") == "datasource":
            found[name] = plugin
    return found


def read_header(root: dict[str, Any], found: Found) -> Dashboard:
    version = schema_version(root.get("schemaVersion"))
    dash = Dashboard(
        uid=text(root.get("uid"), 100) or found.name,
        title=text(root.get("title")),
        shape=found.shape,
        api_version=found.api_version,
        schema_version=version,
        tags=len(sequence(root.get("tags"))),
        links=len(sequence(root.get("links"))),
        inputs=_inputs(root),
    )
    if version is not None and version > LATEST_SCHEMA:
        dash.warnings.append(W_SCHEMA_NEWER.format(version=version))
    return dash


def _element_models(elements: dict[str, Any]) -> dict[str, Any]:
    """Library panel models from `__elements`: each entry is a wrapper {uid, name, kind, model}.

    The wrapper shape is read from the Grafana code comment, not from a real export (unverified).
    """
    found: dict[str, Any] = {}
    for key, value in elements.items():
        if isinstance(value, dict):
            found[key] = value["model"] if isinstance(value.get("model"), dict) else value
    return found


def read_classic(root: dict[str, Any], found: Found, library: dict[str, Any]) -> Dashboard:
    dash = read_header(root, found)
    elements = mapping(root.get("__elements"))
    walk = _Walk(dash, ChainMap(_element_models(elements), library))
    _walk_panels(root, walk)
    if walk.skipped:
        dash.warnings.append(W_NOT_OBJECTS.format(count=walk.skipped))
    _variables(root, dash)
    _annotations(root, dash)
    return dash
