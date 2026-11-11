"""Datadog monitor models and the file loader.

The input is JSON the customer already has: the list `GET /api/v1/monitor` returns, one monitor,
a `{"monitors": [...]}` wrapper, or an integration template (`{version, title, definition}`, the
API object sits under `definition`). Nothing is fetched. Unknown fields and options are kept
(extra="allow"): Datadog ships options its own documentation does not list, so a reader must never
reject on them. Every field is typed `Any` and read through a checked accessor, because exports
in the wild carry nulls and wrong types.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from importer.core.ids import MAX_INPUT_BYTES, source_id
from importer.core.json_depth import check_depth

MAX_MONITORS = 50_000
NAME_MAP_FILE = "name-map.json"
SKIP_FILES = frozenset({NAME_MAP_FILE})


class DatadogMonitor(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Any = None
    name: Any = None
    type: Any = None
    query: Any = None
    message: Any = None
    tags: Any = None
    priority: Any = None
    options: Any = None
    # where the monitor came from (set by the loader): a stable, unique key
    source_key: str = ""
    template_title: str = ""

    @property
    def name_text(self) -> str:
        if isinstance(self.name, str) and self.name.strip():
            return self.name
        return self.template_title

    @property
    def type_text(self) -> str:
        return self.type if isinstance(self.type, str) else ""

    @property
    def query_text(self) -> str:
        return self.query if isinstance(self.query, str) else ""

    @property
    def message_text(self) -> str:
        return self.message if isinstance(self.message, str) else ""

    @property
    def tag_list(self) -> list[str]:
        if not isinstance(self.tags, list):
            return []
        return [t for t in self.tags if isinstance(t, str)]

    @property
    def options_dict(self) -> dict[str, Any]:
        return self.options if isinstance(self.options, dict) else {}

    @property
    def priority_level(self) -> int | None:
        """1 (highest) to 5, or None when absent or not a plain integer in that range."""
        p = self.priority
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 5:
            return p
        return None


def _files(path: Path) -> list[Path]:
    if path.is_dir():
        found = sorted(p for p in path.rglob("*.json") if p.is_file() and not p.is_symlink())
    else:
        found = [path]
    return [p for p in found if p.name not in SKIP_FILES]


def _is_dashboard(data: dict[str, Any]) -> bool:
    return "widgets" in data and "query" not in data and "definition" not in data


def _unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """The API monitor inside an integration template; the title is kept as a fallback name."""
    inner = data.get("definition")
    if not isinstance(inner, dict):
        return data
    title = data.get("title")
    out = dict(inner)
    out["template_title"] = title if isinstance(title, str) else ""
    return out


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("monitors"), list):
        return [i for i in data["monitors"] if isinstance(i, dict)]
    if _is_dashboard(data):
        return []
    if "type" in data or "query" in data or isinstance(data.get("definition"), dict):
        return [data]
    return []


def _read(file: Path) -> Any:
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    text = file.read_text(encoding="utf-8")
    check_depth(text)  # before json.loads: the limit is ours, not the interpreter's
    return json.loads(text)


def _key(raw: dict[str, Any], stem: str, index: int, listed: bool) -> str:
    ident = raw.get("id")
    if isinstance(ident, int) and not isinstance(ident, bool):
        return f"datadog/{ident}"
    return f"datadog/{stem}#{index}" if listed else f"datadog/{stem}"


def load_monitors(path: Path) -> list[DatadogMonitor]:
    """Read monitors from a file or a folder (recursively). Dashboards in the same folder are
    skipped. Raises ValueError for a file that is too big, too deeply nested or not JSON."""
    monitors: list[DatadogMonitor] = []
    keys: set[str] = set()
    for file in _files(path):
        data = _read(file)
        items = _items(data)
        listed = isinstance(data, list) or (isinstance(data, dict) and "monitors" in data)
        for index, raw in enumerate(items):
            fields = _unwrap(raw)
            monitor = DatadogMonitor.model_validate(fields)
            key = _key(fields, file.stem, index, listed)
            if key in keys:  # same id or name twice: keep both, with distinct stable keys
                key = f"{key}@{source_id(file.name + str(index))}"
            keys.add(key)
            monitors.append(monitor.model_copy(update={"source_key": key}))
            if len(monitors) > MAX_MONITORS:
                raise ValueError(f"more than {MAX_MONITORS} monitors in {path}")
    return monitors
