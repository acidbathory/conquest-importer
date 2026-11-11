"""Datadog dashboard JSON: the file loader and checked accessors.

Input is what the customer already has: the body `GET /api/v1/dashboard/{id}` returns, a list of
such bodies, or `{"dashboards": [...]}`. Nothing is fetched. Every field is read through a
checked accessor because exports in the wild carry nulls and wrong types. A name map found beside
the export (the same one the monitors use) is bound to each dashboard.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr

from importer.core.ids import source_id
from importer.sources.datadog.models import SKIP_FILES, _files, _read
from importer.sources.datadog.names import NameMap, find_name_map
from importer.sources.datadog.sanitize import shown

MAX_DASHBOARDS = 5_000


class DatadogDashboard(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: Any = None
    title: Any = None
    description: Any = None
    layout_type: Any = None
    template_variables: Any = None
    widgets: Any = None
    source_key: str = ""
    _names: NameMap = PrivateAttr(default_factory=NameMap)

    @property
    def names(self) -> NameMap:
        return self._names

    def bind(self, names: NameMap) -> "DatadogDashboard":
        bound = self.model_copy()
        bound._names = names
        return bound

    @property
    def title_text(self) -> str:
        return shown(self.title, 128) if isinstance(self.title, str) and self.title.strip() else ""

    @property
    def widget_list(self) -> list[dict[str, Any]]:
        if not isinstance(self.widgets, list):
            return []
        return [w for w in self.widgets if isinstance(w, dict)]

    @property
    def variable_list(self) -> list[dict[str, Any]]:
        if not isinstance(self.template_variables, list):
            return []
        return [v for v in self.template_variables if isinstance(v, dict)]


def _is_dashboard(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("widgets"), list)


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [i for i in data if _is_dashboard(i)]
    if isinstance(data, dict) and isinstance(data.get("dashboards"), list):
        return [i for i in data["dashboards"] if _is_dashboard(i)]
    return [data] if _is_dashboard(data) else []


def load_dashboards(path: Path) -> list[DatadogDashboard]:
    """Dashboards from a file or a folder (recursively); monitors in the folder are skipped.

    Raises ValueError for a file that is too big, too deeply nested or not JSON.
    """
    found: list[DatadogDashboard] = []
    keys: set[str] = set()
    names = find_name_map(path)
    for file in _files(path):
        if file.name in SKIP_FILES:
            continue
        data = _read(file)
        items = _items(data)
        listed = len(items) > 1 or isinstance(data, list)
        for index, raw in enumerate(items):
            ident = raw.get("id")
            base = ident if isinstance(ident, str) and ident else file.stem
            key = f"datadog-dashboard/{shown(base, 80)}" + (f"#{index}" if listed else "")
            if key in keys:
                key = f"{key}@{source_id(file.name + str(index))}"
            keys.add(key)
            found.append(
                DatadogDashboard.model_validate(raw)
                .model_copy(update={"source_key": key})
                .bind(names)
            )
            if len(found) > MAX_DASHBOARDS:
                raise ValueError(f"more than {MAX_DASHBOARDS} dashboards in {path}")
    return found
