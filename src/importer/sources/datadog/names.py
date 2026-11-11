"""How a Datadog metric or tag is named in New Relic: the name map.

No page read gives a table from Datadog metric names to New Relic names (docs/research/
datadog-verified.md section 13). What is documented is that the OpenTelemetry collector and the
StatsD integration keep the metric name and turn tags into attributes of the same name. So the
default is the same name on the `Metric` event, always reported as UNVERIFIED until a data check
has seen the data arrive. An owner who knows better writes `name-map.json` next to the export (or
names a file in IMPORTER_DATADOG_NAME_MAP):

    {"metrics": {"system.cpu.user": {"event": "Metric", "attribute": "cpu.user",
                                      "unit_scale": 1.0, "kind": "gauge"}},
     "attributes": {"env": "deployment.environment.name"}}

`unit_scale` multiplies the Datadog threshold (and the value in a formula) to give New Relic units;
`kind` (gauge, count or rate) tells the converter how to read `.as_count()`. The file is read as
strictly as the Dynatrace and Grafana maps: unknown keys are refused and every name must be an NRQL
identifier, so a typo fails at load time and not when a condition is emitted.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError, field_validator

from importer.core.emit.nrql import identifier
from importer.core.ids import MAX_INPUT_BYTES

MAP_ENV = "IMPORTER_DATADOG_NAME_MAP"
MAP_FILE = "name-map.json"
MAX_ENTRIES = 100_000

Kind = Literal["gauge", "count", "rate"]


class NameMapError(ValueError):
    """The map file is not usable; the message names the file and the row."""


class NameEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: str = "Metric"
    attribute: str
    unit_scale: FiniteFloat = 1.0
    kind: Kind | None = None

    @field_validator("event", "attribute")
    @classmethod
    def _identifier(cls, name: str) -> str:
        identifier(name)
        return name

    @field_validator("unit_scale")
    @classmethod
    def _scale(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("unit_scale must be above zero")
        return value


class NameMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: dict[str, NameEntry] = {}
    attributes: dict[str, str] = {}

    @field_validator("metrics", "attributes")
    @classmethod
    def _size(cls, found: dict[str, object]) -> dict[str, object]:
        if len(found) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} entries")
        return found

    @field_validator("attributes")
    @classmethod
    def _attributes(cls, found: dict[str, str]) -> dict[str, str]:
        for value in found.values():
            identifier(value)
        return found


def load_name_map(path: Path) -> NameMap:
    try:
        too_big = path.stat().st_size > MAX_INPUT_BYTES
    except OSError:
        raise NameMapError(f"the name map {path.name} cannot be read") from None
    if too_big:
        raise NameMapError(f"{path.name} is too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.pop("_comment", None)  # a note for people reading the file
        return NameMap.model_validate(data)
    except json.JSONDecodeError as exc:
        raise NameMapError(f"{path.name} is not valid JSON ({exc.msg})") from None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise NameMapError(f"{path.name}: {where}: {first['msg']}") from None


def find_name_map(source: Path) -> NameMap:
    """The map named by IMPORTER_DATADOG_NAME_MAP, else `name-map.json` in the input folder (or
    beside the input file). An absent map is an empty one: every name then stays as it is."""
    named = os.environ.get(MAP_ENV, "").strip()
    if named:
        return load_name_map(Path(named))
    folder = source if source.is_dir() else source.parent
    candidate = folder / MAP_FILE
    return load_name_map(candidate) if candidate.is_file() else NameMap()


@dataclass(frozen=True)
class Resolved:
    event: str
    attribute: str
    unit_scale: float
    kind: Kind | None
    from_map: bool


def resolve_metric(names: NameMap, metric: str) -> Resolved | None:
    """The New Relic event and attribute for a Datadog metric; None if the name is unusable."""
    entry = names.metrics.get(metric)
    if entry is not None:
        return Resolved(entry.event, entry.attribute, entry.unit_scale, entry.kind, True)
    try:
        identifier(metric)
    except ValueError:
        return None
    return Resolved("Metric", metric, 1.0, None, False)


def resolve_attribute(names: NameMap, key: str) -> str | None:
    """The New Relic attribute for a Datadog tag key or log attribute; None if unusable."""
    name = names.attributes.get(key, key)
    try:
        identifier(name)
    except ValueError:
        return None
    return name
