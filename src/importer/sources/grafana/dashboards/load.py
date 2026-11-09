"""Load Grafana dashboards from a file, a list, an API response or a folder.

Every dashboard is checked on its own: one that cannot be read becomes an `Unreadable` item with
a fixed reason and the rest still load. Only the number of dashboards stops a load, because a
folder with that many files is not a dashboard export.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.json_depth import check_depth
from importer.sources.grafana.dashboards.classic import read_classic
from importer.sources.grafana.dashboards.common import Found, mapping, note, sequence, text
from importer.sources.grafana.dashboards.models import (
    MAX_DASHBOARDS,
    MAX_FILES,
    MAX_LIBRARY,
    PLAN_MARKER,
    U_BAD_JSON,
    U_ENCODING,
    U_INVALID,
    U_NOT_OBJECT,
    U_TOO_BIG,
    U_UNKNOWN_SHAPE,
    W_BOTH_SHAPES,
    W_PLAN_FILE,
    W_TOO_MANY_DASHBOARDS,
    W_TOO_MANY_FILES,
    Dashboard,
    Estate,
    Unreadable,
)
from importer.sources.grafana.dashboards.v2 import read_v2

LIBRARY_FILE = "library-panels.json"
FOLDERS_FILE = "folders.json"
FOLDER_ANNOTATION = "grafana.app/folder"
UNWRAP_LIMIT = 3  # envelope, then wrapper, then body


def _is_v2(node: dict[str, Any]) -> bool:
    layout = node.get("layout")
    return (
        isinstance(node.get("elements"), dict)
        and isinstance(layout, dict)
        and isinstance(layout.get("kind"), str)
    )


def _has_panels(node: dict[str, Any]) -> bool:
    return isinstance(node.get("panels"), list) or isinstance(node.get("rows"), list)


def _is_classic(node: dict[str, Any]) -> bool:
    if _has_panels(node):
        return True
    return isinstance(node.get("title"), str) and "schemaVersion" in node


def detect_shape(data: object) -> Found | None:
    """Locate the dashboard body and say which of the three shapes it is, or None.

    Tests in order: a resource envelope (`apiVersion`, `kind: Dashboard`, `spec`), a wrapper
    (`dashboard` with `meta`), then the body itself: v2 has `elements` and `layout`, classic has
    `panels` or `rows`.
    """
    if not isinstance(data, dict):
        return None
    node, enveloped = data, False
    api, name, folder_uid, folder_title = "", "", "", ""
    for _ in range(UNWRAP_LIMIT):
        spec = node.get("spec")
        if (
            isinstance(node.get("apiVersion"), str)
            and node.get("kind") == "Dashboard"
            and isinstance(spec, dict)
        ):
            meta = mapping(node.get("metadata"))
            api, name = text(node["apiVersion"], 60), text(meta.get("name"), 100)
            folder_uid = text(mapping(meta.get("annotations")).get(FOLDER_ANNOTATION), 100)
            node, enveloped = spec, True
        elif isinstance(node.get("dashboard"), dict):
            meta = mapping(node.get("meta"))
            folder_uid = text(meta.get("folderUid"), 100) or folder_uid
            folder_title = text(meta.get("folderTitle"))
            node = node["dashboard"]
        else:
            break
    both = _is_v2(node) and _has_panels(node)
    if _is_v2(node) and not both:
        return Found("v2", node, api, name, folder_uid, folder_title)
    if _is_classic(node):
        shape = "v1" if enveloped else "classic"
        return Found(shape, node, api, name, folder_uid, folder_title, both)
    return None


def _entries(data: Any) -> list[Any]:
    """The dashboards in a file: a list, a list response (`items`, `dashboards`) or one."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and detect_shape(data) is None:
        for key in ("items", "dashboards"):
            found = data.get(key)
            if isinstance(found, list):
                return found
    return [data]


def _read_one(entry: Any, library: dict[str, Any], folders: Mapping[str, str]) -> Dashboard | str:
    """The dashboard, or the fixed reason it could not be read."""
    if not isinstance(entry, dict):
        return U_NOT_OBJECT
    found = detect_shape(entry)
    if found is None:
        return U_UNKNOWN_SHAPE
    reader = read_v2 if found.shape == "v2" else read_classic
    try:
        dash = reader(found.root, found, library)
    except (ValueError, TypeError, KeyError, RecursionError):  # message may hold file text
        return U_INVALID
    dash.folder = found.folder_title or folders.get(found.folder_uid, found.folder_uid)
    if found.both:
        note(dash, W_BOTH_SHAPES)
    return dash


def load_data(
    data: Any,
    source: str,
    library: dict[str, Any] | None = None,
    folders: Mapping[str, str] | None = None,
) -> Estate:
    """Dashboards from parsed JSON (one dashboard, a list, or an API response)."""
    estate = Estate(library_panels=len(library or {}), folders=dict(folders or {}))
    add_entries(estate, _entries(data), source, library or {})
    estate.finish()
    return estate


def add_entries(estate: Estate, entries: list[Any], source: str, library: dict[str, Any]) -> None:
    """Read each entry; past the dashboard cap the rest are left out with one note."""
    for position, entry in enumerate(entries):
        if len(estate.dashboards) >= MAX_DASHBOARDS:
            limit_note = W_TOO_MANY_DASHBOARDS.format(limit=MAX_DASHBOARDS)
            if limit_note not in estate.warnings:
                estate.warnings.append(limit_note)
            return
        result = _read_one(entry, library, estate.folders)
        if isinstance(result, str):
            estate.unreadable.append(Unreadable(source=source, position=position, reason=result))
        else:
            estate.add(result)


def _read_json(path: Path) -> tuple[Any, str]:
    """The parsed file and "", or None and the fixed reason it could not be read."""
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            return None, U_TOO_BIG
        # utf-8-sig drops a byte order mark that editors on Windows add
        text = path.read_bytes().decode("utf-8-sig")
        check_depth(text)
        return json.loads(text), ""
    except UnicodeDecodeError:
        return None, U_ENCODING
    except (ValueError, RecursionError, OSError):  # bad JSON or nesting deeper than the parser
        return None, U_BAD_JSON


def library_from(data: Any) -> dict[str, dict[str, Any]]:
    """Library panel models by uid, from our export or the raw `/api/library-elements` page."""
    body = mapping(data)
    elements = body.get("libraryPanels") or mapping(body.get("result")).get("elements")
    found: dict[str, dict[str, Any]] = {}
    for element in sequence(elements)[:MAX_LIBRARY]:
        item = mapping(element)
        if isinstance(item.get("uid"), str) and isinstance(item.get("model"), dict):
            found[item["uid"][:100]] = item["model"]
    return found


def folders_from(data: Any) -> dict[str, str]:
    body = data if isinstance(data, list) else mapping(data).get("folders")
    found: dict[str, str] = {}
    for folder in sequence(body)[:MAX_LIBRARY]:
        item = mapping(folder)
        if isinstance(item.get("uid"), str):
            found[item["uid"][:100]] = text(item.get("title"))
    return found


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ValueError(f"{path} is not a file or a folder")
    return sorted(p for p in path.rglob("*.json") if p.is_file() and not p.is_symlink())


def _is_plan(data: Any) -> bool:
    return isinstance(data, dict) and data.get("kind") == PLAN_MARKER


def load_estate(path: Path) -> Estate:
    """Dashboards from a file or a folder of JSON files (searched recursively)."""
    files = _files(path)
    root = path if path.is_dir() else path.parent
    cut = len(files) > MAX_FILES
    files = files[:MAX_FILES]
    library: dict[str, dict[str, Any]] = {}
    folders: dict[str, str] = {}
    for file in files:
        if file.name in (LIBRARY_FILE, FOLDERS_FILE):
            data, _ = _read_json(file)
            if file.name == LIBRARY_FILE:
                library.update(library_from(data))
            else:
                folders.update(folders_from(data))
    estate = Estate(library_panels=len(library), folders=folders)
    if cut:
        estate.warnings.append(W_TOO_MANY_FILES.format(limit=MAX_FILES))
    plans = 0
    for file in files:
        if file.name in (LIBRARY_FILE, FOLDERS_FILE):
            continue
        name = text(file.relative_to(root).as_posix(), 100)
        data, problem = _read_json(file)
        if problem:
            estate.unreadable.append(Unreadable(source=name, position=0, reason=problem))
        elif _is_plan(data):
            plans += 1
        else:
            add_entries(estate, _entries(data), name, library)
    if plans:
        estate.warnings.append(W_PLAN_FILE.format(count=plans))
    estate.finish()
    return estate
