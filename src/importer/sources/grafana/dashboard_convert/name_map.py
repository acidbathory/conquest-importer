"""An optional map from a Prometheus metric name to the New Relic event and attribute that hold it.

Converted queries use the Prometheus name as it is, because remote write keeps it (research note
grafana-notifications-datapath.md, section 6). When the data arrived under another name, or on
another event, the owner writes a map; it is read as strictly as the Dynatrace metric map:
unknown keys are refused, names must be NRQL identifiers, and a bad file fails at load time.

    {"metrics": {"node_load1": {"event": "Metric", "attribute": "node.load.1m"}}}
"""

import json
import os
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from importer.core.emit.nrql import identifier
from importer.core.ids import MAX_INPUT_BYTES

MAP_ENV = "IMPORTER_GRAFANA_NAME_MAP"
MAX_ENTRIES = 100_000
METRIC_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")


class NameMapError(ValueError):
    """The map file is not usable; the message names the file and the row."""


class NameEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str
    attribute: str

    @field_validator("event", "attribute")
    @classmethod
    def _identifier(cls, name: str) -> str:
        identifier(name)
        return name


class NameMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, NameEntry] = {}

    @field_validator("metrics")
    @classmethod
    def _names(cls, found: dict[str, NameEntry]) -> dict[str, NameEntry]:
        if len(found) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} metrics")
        for key in found:
            if not METRIC_NAME.fullmatch(key):
                raise ValueError("a key is not a Prometheus metric name")
        return found


def load_name_map(path: Path) -> NameMap:
    """Read and check a name map file."""
    try:
        too_big = path.stat().st_size > MAX_INPUT_BYTES
    except OSError:
        raise NameMapError(f"the name map {path.name} cannot be read") from None
    if too_big:
        raise NameMapError(f"{path.name} is too large")
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data.pop("_comment", None)  # a note for people reading the file
        return NameMap.model_validate(data)
    except (json.JSONDecodeError, RecursionError) as exc:
        detail = exc.msg if isinstance(exc, json.JSONDecodeError) else "nested too deeply"
        raise NameMapError(f"{path.name} is not valid JSON ({detail})") from None
    except (UnicodeDecodeError, OSError):
        raise NameMapError(f"the name map {path.name} cannot be read") from None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise NameMapError(f"{path.name}: {where}: {first['msg']}") from None


def active_name_map() -> NameMap:
    """The map named by IMPORTER_GRAFANA_NAME_MAP, or an empty one. Read at each call."""
    named = os.environ.get(MAP_ENV, "").strip()
    return load_name_map(Path(named)) if named else NameMap()
