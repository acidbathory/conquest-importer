"""Cloud Monitoring dashboards, in the shape of the v1 `projects.dashboards` API.

Unknown fields are kept (extra="allow"); layouts and widgets are read as plain dicts because the
JSON is untrusted and loosely typed, and the translator validates what it reads.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from importer.core.ids import MAX_INPUT_BYTES

MAX_DASHBOARDS = 5000
MAX_WIDGETS = 200  # per dashboard; the rest are reported as not converted
LAYOUT_KEYS = ("mosaicLayout", "gridLayout", "columnLayout", "rowLayout")


class GcpDashboard(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    displayName: str = ""

    @field_validator("name", "displayName", mode="before")
    @classmethod
    def _null_is_empty(cls, value: object) -> object:
        return "" if value is None else value

    @property
    def layout(self) -> tuple[str, dict[str, Any]]:
        extra = self.model_extra or {}
        for key in LAYOUT_KEYS:
            value = extra.get(key)
            if isinstance(value, dict):
                return key, value
        return "", {}


def _items(data: Any, file: str) -> list[Any]:
    if isinstance(data, dict) and "dashboards" in data:
        data = data["dashboards"]
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list) or not all(isinstance(d, dict) for d in data):
        raise ValueError(f"{file}: expected a dashboard, a list of dashboards or an API response")
    return data


def load_dashboards(path: Path) -> list[GcpDashboard]:
    """Read dashboards from a file or folder of JSON (one dashboard, a list, or an API page)."""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    found: list[GcpDashboard] = []
    for file in files:
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        found += [
            GcpDashboard.model_validate(i) for i in _items(json.loads(file.read_text()), file.name)
        ]
        if len(found) > MAX_DASHBOARDS:
            raise ValueError(f"more than {MAX_DASHBOARDS} dashboards; split the export")
    return found
