"""The parts of a dashboard file the coverage planner's models leave out.

The planner reads a dashboard into small models and drops the rest. Converting needs the display
options of each panel, the saved values of each variable and the dashboard time range, so this
module reads them from the same file in the same order and keeps only small typed values. Panels
are matched to the planner's panels by position (classic) or by title (v2); when the two lists
do not agree the display options are not used and the dashboard says so.
"""

import json
from collections import ChainMap, deque
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from importer.core.ids import MAX_INPUT_BYTES
from importer.sources.grafana.dashboard_convert.display import Display, read_display
from importer.sources.grafana.dashboard_convert.variables import RawVar
from importer.sources.grafana.dashboards.common import mapping, sequence, text, whole
from importer.sources.grafana.dashboards.load import detect_shape, library_from
from importer.sources.grafana.dashboards.models import (
    MAX_DEPTH,
    MAX_FILES,
    MAX_PANELS,
    MAX_TEXT,
    MAX_VARIABLES,
    PLAN_MARKER,
    Dashboard,
    Estate,
)

LIBRARY_FILE = "library-panels.json"
FOLDERS_FILE = "folders.json"
_END = object()


class RawDashboard(BaseModel):
    displays: list[Display | None] = Field(default_factory=list)  # one per planner panel
    variables: list[RawVar] = Field(default_factory=list)
    time_range: str = ""
    matched: bool = True  # False: the display options could not be matched to the panels


# --- classic panels ---


def _legacy_rows(root: dict[str, Any]) -> list[Any]:
    rows: list[Any] = []
    for row in sequence(root.get("rows")):
        if isinstance(row, dict):
            rows.append({"type": "row", "panels": row.get("panels")})
        else:
            rows.append(row)
    return rows


def _with_library(body: dict[str, Any], library: Mapping[str, Any]) -> dict[str, Any]:
    stub = mapping(body.get("libraryPanel"))
    model = library.get(stub["uid"][:100]) if isinstance(stub.get("uid"), str) else None
    if not isinstance(model, dict):
        return body
    kept: dict[str, Any] = {k: body[k] for k in ("id", "gridPos", "span") if k in body}
    if isinstance(body.get("title"), str) and body["title"]:
        kept["title"] = body["title"]
    return {**model, **kept}


def classic_bodies(root: dict[str, Any], library: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Panel bodies in the order the planner's reader meets them (rows entered where it enters)."""
    found: list[dict[str, Any]] = []
    stack: list[Iterator[Any]] = [iter(sequence(root.get("panels")) or _legacy_rows(root))]
    depths = [0]
    while stack:
        body = next(stack[-1], _END)
        if body is _END:
            stack.pop()
            depths.pop()
        elif not isinstance(body, dict):
            continue
        elif body.get("type") == "row":
            members = sequence(body.get("panels"))
            if members and depths[-1] + 1 <= MAX_DEPTH:
                stack.append(iter(members))
                depths.append(depths[-1] + 1)
        elif len(found) >= MAX_PANELS:
            break
        else:
            found.append(_with_library(body, library))
    return found


def _models(elements: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value["model"] if isinstance(value.get("model"), dict) else value
        for key, value in elements.items()
        if isinstance(value, dict)
    }


# --- v2 panels ---


def v2_displays(root: dict[str, Any], dash: Dashboard) -> list[Display | None]:
    """Display options of a v2 dashboard's panels, matched to the planner's by title."""
    waiting: dict[str, deque[Display]] = {}
    for element in mapping(root.get("elements")).values():
        spec = mapping(mapping(element).get("spec"))
        if mapping(element).get("kind") != "Panel":
            continue
        viz = mapping(mapping(spec.get("vizConfig")).get("spec"))
        body = {"fieldConfig": viz.get("fieldConfig"), "options": viz.get("options")}
        waiting.setdefault(text(spec.get("title")), deque()).append(read_display(body))
    return [
        queue.popleft() if (queue := waiting.get(panel.title)) else None for panel in dash.panels
    ]


# --- variables and time ---


def _values(value: object) -> list[str]:
    items = value if isinstance(value, list) else [value]
    return [i[:200] for i in items[:50] if isinstance(i, str)]


def _refresh(value: object) -> int | None:
    names = {"onDashboardLoad": 1, "onTimeRangeChanged": 2}
    return names.get(value) if isinstance(value, str) else whole(value, 0, 2, 0) or None


def _raw_variable(item: dict[str, Any]) -> RawVar:
    spec = mapping(item.get("spec")) if "spec" in item else item
    options = [
        (text(mapping(o).get("text"), 200), text(mapping(o).get("value"), 200))
        for o in sequence(spec.get("options"))[:500]
        if isinstance(o, dict)
    ]
    return RawVar(
        label=text(spec.get("label"), MAX_TEXT),
        current=_values(mapping(spec.get("current")).get("value")),
        options=[(t or v, v) for t, v in options if v],
        refresh=_refresh(spec.get("refresh")),
    )


def raw_variables(root: dict[str, Any], shape: str) -> list[RawVar]:
    """Saved state of each variable, in the planner's order (objects only, capped)."""
    if shape == "v2":
        listing = sequence(root.get("variables"))
    else:
        node = root.get("templating")
        listing = sequence(mapping(node).get("list") if isinstance(node, dict) else node)
    return [_raw_variable(v) for v in [x for x in listing if isinstance(x, dict)][:MAX_VARIABLES]]


def _time_range(root: dict[str, Any], shape: str) -> str:
    node = mapping(root.get("timeSettings" if shape == "v2" else "time"))
    start, end = text(node.get("from"), 40), text(node.get("to"), 40)
    return f"{start} to {end}" if start and end else ""


# --- one dashboard ---


def read_raw(
    root: dict[str, Any], shape: str, dash: Dashboard, library: Mapping[str, Any]
) -> RawDashboard:
    """Everything the converter needs from one dashboard body besides the planner's models."""
    raw = RawDashboard(variables=raw_variables(root, shape), time_range=_time_range(root, shape))
    if shape == "v2":
        raw.displays = v2_displays(root, dash)
        return raw
    models = ChainMap(_models(mapping(root.get("__elements"))), dict(library))
    bodies = classic_bodies(root, models)
    agree = len(bodies) == len(dash.panels) and all(
        text(b.get("type"), 100) == p.type and text(b.get("title")) == p.title
        for b, p in zip(bodies, dash.panels, strict=True)
    )
    raw.matched = agree
    raw.displays = [read_display(b) for b in bodies] if agree else [None] * len(dash.panels)
    return raw


# --- files ---


def read_json_file(path: Path) -> Any:
    """The parsed file, or None when it is too large, not UTF-8 or not JSON."""
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            return None
        return json.loads(path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError, OSError):
        return None


def _entries(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and detect_shape(data) is None:
        for key in ("items", "dashboards"):
            found = data.get(key)
            if isinstance(found, list):
                return found
    return [data]


def _json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    found = sorted(p for p in path.rglob("*.json") if p.is_file() and not p.is_symlink())
    return found[:MAX_FILES]


def raw_for_estate(path: Path, estate: Estate) -> dict[int, RawDashboard]:
    """Raw extras for each dashboard of the estate, keyed by its position in `estate.dashboards`.

    The files are walked again in the order the planner's loader walks them; an entry is matched
    to the dashboard with the same uid and title that has not been matched yet. A dashboard is
    read as soon as it is matched and its body is dropped.
    """
    waiting: dict[tuple[str, str], deque[int]] = {}
    for index, dash in enumerate(estate.dashboards):
        waiting.setdefault((dash.uid, dash.title), deque()).append(index)
    files = _json_files(path)
    library: dict[str, dict[str, Any]] = {}
    for file in files:
        if file.name == LIBRARY_FILE:
            library.update(library_from(read_json_file(file)))
    found: dict[int, RawDashboard] = {}
    for file in files:
        if file.name in (LIBRARY_FILE, FOLDERS_FILE):
            continue
        data = read_json_file(file)
        if isinstance(data, dict) and data.get("kind") == PLAN_MARKER:
            continue
        for entry in _entries(data) if data is not None else []:
            located = detect_shape(entry)
            if located is None:
                continue
            root = located.root
            key = (text(root.get("uid"), 100) or located.name, text(root.get("title")))
            if (queue := waiting.get(key)) and queue:
                index = queue.popleft()
                found[index] = read_raw(root, located.shape, estate.dashboards[index], library)
    return found
