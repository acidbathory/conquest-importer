"""Azure workbooks and portal dashboards, read from what `extract-azure --dashboards` writes.

Accepted, in any mix in one folder: list pages `{"value": [...]}`, single resources, ARM templates
(`resources` holding a workbook or a dashboard), the official example files of the REST API specs
(the resource is read from the response, never from the request), raw workbook templates
(`*.workbook`, the gallery format `{"version": "Notebook/1.0", "items": [...]}`) and a dashboard
downloaded from the portal (`lenses` at the top or under `properties`). Alert rules and other
resources are ignored; the alert loader ignores these in turn.

The JSON is untrusted. A workbook's `serializedData` is a string holding JSON; when it cannot be
parsed (or was not listed, which needs `canFetchContent`) the dashboard is still returned, with
`error` saying why, so the report can show it instead of dropping it.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from importer.core.text import printable
from importer.sources.azure.models import NAME_MAP_FILE, NameMap, _name_map, _read_json

MAX_FILES = 5000
MAX_DASHBOARDS = 5000
MAX_TITLE = 200
WORKBOOK_TYPE = "microsoft.insights/workbooks"
DASHBOARD_TYPE = "microsoft.portal/dashboards"
SUFFIXES = (".json", ".workbook")
NOT_JSON = "serializedData is not valid JSON, so the workbook cannot be read"
NO_CONTENT = (
    "the workbook was listed without its content; read it again with canFetchContent=true "
    "(extract-azure --dashboards does)"
)
NO_LENSES = "the dashboard has no lenses (no parts to read)"


class AzureDashboard(BaseModel):
    """One workbook or portal dashboard; `content` is the parsed workbook or its lenses."""

    kind: Literal["workbook", "portal"]
    key: str  # the resource id, or the file name for a template
    name: str
    content: dict[str, Any] | None = None
    error: str = ""
    metadata: dict[str, Any] = {}  # portal dashboards: properties.metadata
    names: NameMap = Field(default_factory=NameMap, exclude=True)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _example_bodies(data: dict[str, Any]) -> list[Any]:
    """Response bodies of an official example (200 before 201); the request echo is not read."""
    responses = _dict(data.get("responses"))
    bodies = []
    for code in sorted(responses, key=str):
        body = _dict(responses[code]).get("body")
        if isinstance(body, dict | list):
            bodies.append(body)
    return bodies[:1]


def _is_raw_workbook(data: dict[str, Any]) -> bool:
    return _text(data.get("version")).startswith("Notebook/") and isinstance(
        data.get("items"), list
    )


def _resources(data: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Workbook and dashboard resources inside any accepted shape."""
    if depth > 4:
        return []
    if isinstance(data, list):
        return [r for item in data for r in _resources(item, depth + 1)]
    if not isinstance(data, dict):
        return []
    if "responses" in data and "parameters" in data:
        return [r for body in _example_bodies(data) for r in _resources(body, depth + 1)]
    if isinstance(data.get("value"), list):
        return _resources(data["value"], depth + 1)
    if isinstance(data.get("resources"), list) and "type" not in data:  # an ARM template
        return _resources(data["resources"], depth + 1)
    kind = _text(data.get("type")).lower()
    if kind in (WORKBOOK_TYPE, DASHBOARD_TYPE) or _is_raw_workbook(data):
        return [data]
    props = _dict(data.get("properties"))
    if "lenses" in data or "lenses" in props:
        return [data]
    return []


def _lenses(raw: object) -> list[dict[str, Any]]:
    """Lenses as a list; ARM exports use a list, the portal's own download an object by index."""
    items = list(raw.values()) if isinstance(raw, dict) else raw
    out = []
    for lens in items if isinstance(items, list) else []:
        parts = _dict(lens).get("parts")
        parts = list(parts.values()) if isinstance(parts, dict) else parts
        out.append(
            {"parts": [p for p in parts if isinstance(p, dict)]}
            if isinstance(parts, list)
            else {"parts": []}
        )
    return out


def _workbook(resource: dict[str, Any], fallback: str, names: NameMap) -> AzureDashboard:
    if _is_raw_workbook(resource):
        return AzureDashboard(
            kind="workbook",
            key=fallback,
            name=printable(fallback, MAX_TITLE),
            content=resource,
            names=names,
        )
    props = _dict(resource.get("properties"))
    name = _text(props.get("displayName")) or _text(resource.get("name")) or fallback
    base = {
        "kind": "workbook",
        "key": _text(resource.get("id")) or _text(resource.get("name")) or fallback,
        "name": printable(name, MAX_TITLE),
        "names": names,
    }
    data = props.get("serializedData")
    if isinstance(data, dict):
        return AzureDashboard(**base, content=data)
    if not isinstance(data, str) or not data.strip():
        return AzureDashboard(**base, error=NO_CONTENT)
    try:
        parsed = json.loads(data)
    except (ValueError, RecursionError):
        return AzureDashboard(**base, error=NOT_JSON)
    if not isinstance(parsed, dict):
        return AzureDashboard(**base, error=NOT_JSON)
    return AzureDashboard(**base, content=parsed)


def _portal(resource: dict[str, Any], fallback: str, names: NameMap) -> AzureDashboard:
    props = _dict(resource.get("properties")) or resource
    tags = _dict(resource.get("tags"))
    name = _text(tags.get("hidden-title")) or _text(resource.get("name")) or fallback
    lenses = _lenses(props.get("lenses", resource.get("lenses")))
    return AzureDashboard(
        kind="portal",
        key=_text(resource.get("id")) or _text(resource.get("name")) or fallback,
        name=printable(name, MAX_TITLE),
        content={"lenses": lenses} if lenses else None,
        error="" if lenses else NO_LENSES,
        metadata=_dict(props.get("metadata", resource.get("metadata"))),
        names=names,
    )


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    found = sorted(
        p
        for p in path.rglob("*")
        if p.is_file() and p.suffix in SUFFIXES and p.name != NAME_MAP_FILE
    )
    if len(found) > MAX_FILES:
        raise ValueError(f"more than {MAX_FILES} JSON files in {path.name}")
    return found


def load_dashboards(path: Path) -> list[AzureDashboard]:
    """Workbooks and portal dashboards from a file or folder; identical copies are read once."""
    names = _name_map(path if path.is_dir() else path.parent)
    found: list[AzureDashboard] = []
    seen: set[str] = set()
    for file in _files(path):
        fallback = re.sub(r"\.(workbook|json)$", "", file.name)
        for resource in _resources(_read_json(file)):
            digest = hashlib.sha256(
                json.dumps(resource, sort_keys=True, default=str).encode()
            ).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            is_portal = _text(resource.get("type")).lower() == DASHBOARD_TYPE or (
                not _is_raw_workbook(resource)
                and _text(resource.get("type")).lower() != WORKBOOK_TYPE
            )
            found.append((_portal if is_portal else _workbook)(resource, fallback, names))
        if len(found) > MAX_DASHBOARDS:
            raise ValueError(f"more than {MAX_DASHBOARDS} dashboards; split the export")
    return found
