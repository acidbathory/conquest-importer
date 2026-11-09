"""Dynatrace dashboards, read tolerantly.

Two shapes are read: Platform dashboard documents (version, variables, tiles, layouts; research
note section 2) and classic dashboards from the Configuration API (`dashboardMetadata` and a
list of tiles). A file can be a bare dashboard, an extract envelope (`{"id", "name", "content"}`
written by `extract-dynatrace-inventory`) or a list of either.

Every object is validated on its own. A tile that cannot be read becomes an `Unreadable` entry
that the planner reports as manual; one bad tile, variable or file never stops the others. The
JSON is untrusted: sizes and nesting depth are capped, and no text from it is ever put into a
reason.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from importer.core.ids import MAX_INPUT_BYTES

MAX_FILES = 5000
MAX_DOCUMENTS = 5000
MAX_TILES = 500  # per dashboard; the rest are reported as unread
MAX_VARIABLES = 100
MAX_TEXT = 100_000  # one query, markdown body or script
MAX_DEPTH = 64  # nesting of the parsed JSON; real dashboards are far below this
FOLDERS = ("dashboards", "classic-dashboards")  # where the extractor writes

# Why something could not be read. The planner turns each code into a fixed reason.
NOT_AN_OBJECT = "not-an-object"
TOO_LARGE = "too-large"
TOO_DEEP = "too-deep"
NOT_JSON = "not-json"
NOT_A_DASHBOARD = "not-a-dashboard"
BAD_FIELD = "bad-field"
TOO_MANY = "too-many"


class Unreadable(BaseModel):
    """An object that failed validation. `ref` is untrusted; show it only through md_cell."""

    model_config = ConfigDict(frozen=True)

    ref: str
    code: str


class Layout(BaseModel):
    """A grid position. Values outside a sane range make the whole layout None."""

    model_config = ConfigDict(frozen=True)

    x: int = Field(default=0, ge=0, le=10_000)
    y: int = Field(default=0, ge=0, le=100_000)
    w: int = Field(default=1, ge=1, le=10_000)
    h: int = Field(default=1, ge=1, le=100_000)


class Tile(BaseModel):
    """A Platform dashboard tile. `kind` is the raw `type`; the planner decides what it means."""

    model_config = ConfigDict(frozen=True)

    id: str
    kind: str
    title: str = ""
    content: str = ""  # markdown body
    query: str = ""  # DQL of a data tile
    input: str = ""  # script of a code tile, SLO id of an slo tile
    visualization: str = ""
    settings: dict[str, Any] = {}
    tile_timeframe: bool = False  # a tile timeframe that is switched on
    segments: bool = False
    prompt: bool = False  # a Davis prompt tile
    layout: Layout | None = None


class Variable(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    type: str
    input: str = ""
    multiple: bool = False
    default: str = ""


class PlatformDashboard(BaseModel):
    version: int | None = None
    variables: list[Variable] = []
    tiles: list[Tile] = []
    unreadable: list[Unreadable] = []
    default_timeframe: bool = False


class ClassicTile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = ""
    tile_type: str
    markdown: str = ""
    metrics: list[str] = []  # metric keys of the tile's queries
    management_zone: bool = False  # the tile overrides the management zone


class ClassicDashboard(BaseModel):
    tiles: list[ClassicTile] = []
    unreadable: list[Unreadable] = []
    management_zone: bool = False  # the dashboard filter names a management zone
    filters: int = 0  # dynamic filters and tag filters


class Document(BaseModel):
    """One dashboard read from a file, or the reason it could not be read."""

    ref: str  # file name (and index in a list file)
    kind: Literal["platform", "classic", "unreadable"]
    name: str = ""
    owner: str = ""
    private: bool | None = None
    app: str = ""  # originAppId of a document
    error: str = ""  # an Unreadable code when kind is "unreadable"
    platform: PlatformDashboard | None = None
    classic: ClassicDashboard | None = None


def _str(value: object, limit: int = MAX_TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _depth_exceeds(value: Any, limit: int) -> bool:
    """True when the parsed JSON nests deeper than `limit` (checked without recursion)."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return True
        children = (
            item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        )
        stack.extend((child, depth + 1) for child in children)
    return False


def _layout(raw: object) -> Layout | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Layout.model_validate(
            {k: raw[k] for k in ("x", "y", "w", "h") if isinstance(raw.get(k), int)}
        )
    except ValidationError:
        return None


def _on(value: object, key: str) -> bool:
    return isinstance(value, dict) and value.get(key) is True


def parse_tile(tile_id: str, raw: object, layout: object) -> Tile | Unreadable:
    if not isinstance(raw, dict):
        return Unreadable(ref=tile_id[:80], code=NOT_AN_OBJECT)
    kind = raw.get("type")
    if not isinstance(kind, str):
        return Unreadable(ref=tile_id[:80], code=BAD_FIELD)
    for key in ("content", "query", "input"):
        value = raw.get(key)
        if isinstance(value, str) and len(value) > MAX_TEXT:
            return Unreadable(ref=tile_id[:80], code=TOO_LARGE)
    settings = raw.get("visualizationSettings")
    timeframe = raw.get("timeframe")
    segments = raw.get("segments")
    return Tile(
        id=tile_id[:80],
        kind=kind[:40],  # shown in reports; the cap is applied here, once
        title=_str(raw.get("title"), 300),
        content=_str(raw.get("content")),
        query=_str(raw.get("query")),
        input=_str(raw.get("input")),
        visualization=_str(raw.get("visualization"), 80),
        settings=settings if isinstance(settings, dict) else {},
        tile_timeframe=_on(timeframe, "tileTimeframeEnabled"),
        segments=_on(segments, "tileSegmentsEnabled"),
        prompt="davisCopilot" in raw,
        layout=_layout(layout),
    )


def _default(raw: object) -> str:
    if isinstance(raw, list):
        return ",".join(str(i) for i in raw[:20])[:200]
    return str(raw)[:200] if isinstance(raw, str | int | float) else ""


def parse_variable(index: int, raw: object) -> Variable | Unreadable:
    ref = f"variable {index}"
    if not isinstance(raw, dict):
        return Unreadable(ref=ref, code=NOT_AN_OBJECT)
    key, kind = raw.get("key"), raw.get("type")
    if not isinstance(key, str) or not key or not isinstance(kind, str):
        return Unreadable(ref=ref, code=BAD_FIELD)
    text = raw.get("input")
    if isinstance(text, str) and len(text) > MAX_TEXT:
        return Unreadable(ref=key[:80], code=TOO_LARGE)
    return Variable(
        key=key[:80],
        type=kind[:40],
        input=text if isinstance(text, str) else "",
        multiple=raw.get("multiple") is True,
        default=_default(raw.get("defaultValue")),
    )


def parse_platform(raw: dict[str, Any]) -> PlatformDashboard:
    """Read a Platform dashboard; raises ValueError when `tiles` is not an object."""
    tiles_raw = raw.get("tiles")
    if not isinstance(tiles_raw, dict):
        raise ValueError(NOT_A_DASHBOARD)
    layouts_raw = raw.get("layouts")
    layouts: dict[str, Any] = layouts_raw if isinstance(layouts_raw, dict) else {}
    dashboard = PlatformDashboard(
        version=raw["version"] if isinstance(raw.get("version"), int) else None
    )
    settings = raw.get("settings")
    dashboard.default_timeframe = isinstance(settings, dict) and "defaultTimeframe" in settings
    for number, (tile_id, tile) in enumerate(tiles_raw.items()):
        if number >= MAX_TILES:
            dashboard.unreadable.append(Unreadable(ref=f"{len(tiles_raw)} tiles", code=TOO_MANY))
            break
        parsed = parse_tile(str(tile_id), tile, layouts.get(tile_id))
        if isinstance(parsed, Tile):
            dashboard.tiles.append(parsed)
        else:
            dashboard.unreadable.append(parsed)
    variables = raw.get("variables")
    for index, item in enumerate(variables[:MAX_VARIABLES] if isinstance(variables, list) else []):
        parsed_var = parse_variable(index, item)
        if isinstance(parsed_var, Variable):
            dashboard.variables.append(parsed_var)
        else:
            dashboard.unreadable.append(parsed_var)
    return dashboard


def _classic_metrics(raw: object) -> list[str]:
    queries = raw if isinstance(raw, list) else []
    found = [q.get("metric") for q in queries[:50] if isinstance(q, dict)]
    return [m[:200] for m in found if isinstance(m, str)]


def parse_classic_tile(index: int, raw: object) -> ClassicTile | Unreadable:
    ref = f"tile {index}"
    if not isinstance(raw, dict) or not isinstance(raw.get("tileType"), str):
        return Unreadable(ref=ref, code=NOT_AN_OBJECT if not isinstance(raw, dict) else BAD_FIELD)
    markdown = raw.get("markdown")
    if isinstance(markdown, str) and len(markdown) > MAX_TEXT:
        return Unreadable(ref=ref, code=TOO_LARGE)
    filt = raw.get("tileFilter")
    return ClassicTile(
        name=_str(raw.get("name"), 300),
        tile_type=raw["tileType"][:80],
        markdown=_str(markdown),
        metrics=_classic_metrics(raw.get("queries")),
        management_zone=isinstance(filt, dict) and bool(filt.get("managementZone")),
    )


def parse_classic(raw: dict[str, Any]) -> ClassicDashboard:
    """Read a classic dashboard; raises ValueError when `tiles` is not a list."""
    tiles = raw.get("tiles")
    if not isinstance(tiles, list):
        raise ValueError(NOT_A_DASHBOARD)
    dashboard = ClassicDashboard()
    for index, item in enumerate(tiles):
        if index >= MAX_TILES:
            dashboard.unreadable.append(Unreadable(ref=f"{len(tiles)} tiles", code=TOO_MANY))
            break
        parsed = parse_classic_tile(index, item)
        if isinstance(parsed, ClassicTile):
            dashboard.tiles.append(parsed)
        else:
            dashboard.unreadable.append(parsed)
    meta = raw.get("dashboardMetadata")
    meta = meta if isinstance(meta, dict) else {}
    filt = meta.get("dashboardFilter")
    filt = filt if isinstance(filt, dict) else {}
    dashboard.management_zone = bool(filt.get("managementZone"))
    dynamic = meta.get("dynamicFilters")
    found = dynamic.get("filters") if isinstance(dynamic, dict) else None
    dashboard.filters = len(found) if isinstance(found, list) else 0
    return dashboard


def _fail(ref: str, code: str) -> Document:
    return Document(ref=ref, kind="unreadable", error=code)


def _meta(raw: dict[str, Any]) -> dict[str, Any]:
    private = raw.get("isPrivate")
    return {
        "owner": _str(raw.get("owner"), 120),
        "private": private if isinstance(private, bool) else None,
        "app": _str(raw.get("originAppId"), 120),
    }


def parse_document(ref: str, raw: object, stem: str = "") -> Document:
    """One dashboard from parsed JSON: a bare Platform or classic dashboard, or an envelope."""
    if not isinstance(raw, dict):
        return _fail(ref, NOT_AN_OBJECT)
    inner = raw.get("content")
    content: dict[str, Any] = inner if isinstance(inner, dict) else raw
    name = _str(raw.get("name"), 200) or stem
    meta = _meta(raw)
    classic_meta = content.get("dashboardMetadata")
    try:
        if isinstance(classic_meta, dict):
            name = _str(raw.get("name"), 200) or _str(classic_meta.get("name"), 200) or stem
            meta["owner"] = meta["owner"] or _str(classic_meta.get("owner"), 120)
            return Document(
                ref=ref, kind="classic", name=name, classic=parse_classic(content), **meta
            )
        return Document(
            ref=ref, kind="platform", name=name, platform=parse_platform(content), **meta
        )
    except ValueError:
        return _fail(ref, NOT_A_DASHBOARD)


def read_file(path: Path) -> list[Document]:
    """Every dashboard in one file; an unreadable file gives one `unreadable` document."""
    ref = path.name
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            return [_fail(ref, TOO_LARGE)]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError, OSError):
        return [_fail(ref, NOT_JSON)]
    if _depth_exceeds(data, MAX_DEPTH):
        return [_fail(ref, TOO_DEEP)]
    items = data if isinstance(data, list) else [data]
    if len(items) > MAX_DOCUMENTS:
        return [_fail(ref, TOO_MANY)]
    many = len(items) > 1 or isinstance(data, list)
    return [
        parse_document(f"{ref}[{n}]" if many else ref, item, path.stem)
        for n, item in enumerate(items)
    ]


def dashboard_files(path: Path) -> list[Path]:
    """The JSON files to read: the file itself, or the folder and the extractor's sub-folders."""
    if path.is_file():
        return [path]
    folders = [path, *(path / name for name in FOLDERS)]
    files = [f for folder in folders if folder.is_dir() for f in sorted(folder.glob("*.json"))]
    if len(files) > MAX_FILES:
        raise ValueError(f"more than {MAX_FILES} dashboard files; split the export")
    return files


def load_documents(path: Path) -> tuple[list[Document], int]:
    """All dashboards under `path`, and how many exact copies were skipped."""
    documents: list[Document] = []
    seen: set[str] = set()
    skipped = 0
    for file in dashboard_files(path):
        for doc in read_file(file):
            key = doc.model_dump_json()  # a unique ref makes unreadable files never collide
            if doc.kind != "unreadable":
                key = _document_key(doc)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            documents.append(doc)
    return documents, skipped


def _document_key(doc: Document) -> str:
    """Identity of the content only: a copy under another name is still the same dashboard."""
    body = doc.platform if doc.platform is not None else doc.classic
    payload = body.model_dump_json() if body is not None else ""
    return hashlib.sha256(payload.encode()).hexdigest()
