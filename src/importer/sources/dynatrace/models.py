"""Dynatrace models: workflows and SLOs, in the shape the Platform APIs return them.

Unknown fields are kept (extra="allow") so nothing is silently dropped. A workflow has `tasks`
and a `trigger`; an SLO has `criteria` and a `customSli`. That is how the loader tells them apart.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.json_depth import check_depth

MAX_FILES = 10_000
WORKFLOW_KEYS = ("tasks", "trigger")
SLO_KEYS = ("criteria", "customSli")
RESPONSE_KEYS = ("results", "slos")  # workflow list response, SLO list response


class DtTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    action: str = ""
    name: str = ""
    description: str = ""
    input: dict[str, Any] = {}
    conditions: dict[str, Any] = {}
    predecessors: list[str] = []


class DtWorkflow(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    title: str
    description: str = ""
    tasks: dict[str, DtTask] = {}
    trigger: dict[str, Any] = {}
    triggerType: str = ""
    isDeployed: bool = True


class DtSlo(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    name: str
    description: str = ""
    tags: list[str] = []
    criteria: list[dict[str, Any]] = []
    customSli: dict[str, Any] = {}


DtItem = DtWorkflow | DtSlo


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    # the files here and in the folders `extract-dynatrace` writes; not alerts/, which holds IR
    found = sorted(
        f for folder in (path, path / "workflows", path / "slos") for f in folder.glob("*.json")
    )
    if len(found) > MAX_FILES:
        raise ValueError(f"{path} has more than {MAX_FILES} JSON files; pass a smaller folder")
    return found


def _items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in RESPONSE_KEYS:
            if isinstance(data.get(key), list):
                return list(data[key])
        return [data]
    raise ValueError("expected a JSON object or list of workflows or SLOs")


def _item(raw: Any) -> DtItem:
    if isinstance(raw, dict):
        if any(k in raw for k in WORKFLOW_KEYS):
            return DtWorkflow.model_validate(raw)
        if any(k in raw for k in SLO_KEYS):
            return DtSlo.model_validate(raw)
    raise ValueError(
        "an item is neither a workflow nor an SLO (expected tasks or trigger, "
        "or criteria or customSli)"
    )


def load_exports(path: Path) -> list[DtItem]:
    """Read workflows and SLOs from a JSON file, an API response, or a folder of either."""
    items: list[DtItem] = []
    for file in _files(path):
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        text = file.read_text()
        check_depth(text)
        items += [_item(raw) for raw in _items(json.loads(text))]
    return items


def load_workflows(path: Path) -> list[DtWorkflow]:
    return [i for i in load_exports(path) if isinstance(i, DtWorkflow)]


def load_slos(path: Path) -> list[DtSlo]:
    return [i for i in load_exports(path) if isinstance(i, DtSlo)]
