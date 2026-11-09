"""The metric map: which New Relic signal stands for a Dynatrace metric key.

Dynatrace metrics do not exist in New Relic unless the customer sends equivalent data, so every
conversion needs a hand-built map, as the Google source has `gcp_verified.json`. Each row has a
`status` that the converter repeats in its reasons: `documented`, `verified`, `unverified` or
`none`. The map file is trusted input; its names are still checked as NRQL identifiers when it is
loaded, so a typo fails at load time and not when a condition is emitted.
"""

import json
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, FiniteFloat, ValidationError, field_validator

from importer.core.emit.nrql import identifier
from importer.core.ids import MAX_INPUT_BYTES

MAP_PATH = Path(__file__).with_name("metric_map.json")
MAP_ENV = "IMPORTER_DYNATRACE_METRIC_MAP"
SELECT = re.compile(r"[A-Za-z0-9_.,()*= ]{1,200}")

Status = Literal["documented", "verified", "unverified", "none"]


class MapError(ValueError):
    """The metric map file is not usable; the message names the file and the row."""


class MapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[str]
    event: str
    attribute: str | None = None
    select: str | None = None
    unit_factor: FiniteFloat = 1.0
    unit: str = ""
    dimensions: dict[str, str] = {}
    default_aggregation: str = ""
    status: Status
    evidence: str = ""

    @field_validator("keys")
    @classmethod
    def _keys(cls, keys: list[str]) -> list[str]:
        if not keys or any(not k.strip() for k in keys):
            raise ValueError("keys must be a non-empty list of non-empty names")
        return keys

    @field_validator("event", "attribute")
    @classmethod
    def _identifier(cls, name: str | None) -> str | None:
        if name is not None:
            identifier(name)
        return name

    @field_validator("dimensions")
    @classmethod
    def _dimensions(cls, found: dict[str, str]) -> dict[str, str]:
        for attribute in found.values():
            identifier(attribute)
        return found

    @field_validator("select")
    @classmethod
    def _select(cls, text: str | None) -> str | None:
        if text is not None and not SELECT.fullmatch(text):
            raise ValueError("select has characters outside the allowed set")
        return text

    @field_validator("unit_factor")
    @classmethod
    def _factor(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("unit_factor must be above zero")
        return value

    def model_post_init(self, _context: object) -> None:
        if (self.attribute is None) == (self.select is None):
            raise ValueError("exactly one of attribute and select must be set")


class Prerequisite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prefix: str
    text: str


class MetricMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prerequisites: list[Prerequisite] = []
    default_prerequisite: str = ""
    metrics: list[MapEntry]

    def find(self, key: str) -> MapEntry | None:
        for row in self.metrics:
            if key in row.keys:
                return row
        return None

    def prerequisite(self, key: str) -> str:
        for item in self.prerequisites:
            if key.startswith(item.prefix):
                return item.text
        return self.default_prerequisite


def _check_unique(rows: Iterable[MapEntry]) -> None:
    seen: set[str] = set()
    for row in rows:
        for key in row.keys:
            if key in seen:
                raise MapError(f"a metric key is in more than one row of the metric map ({key!r})")
            seen.add(key)


def load_metric_map(path: Path | None = None) -> MetricMap:
    """Read and check a metric map file (the shipped one by default)."""
    file = path or MAP_PATH
    try:
        too_big = file.stat().st_size > MAX_INPUT_BYTES
    except OSError:
        raise MapError(f"the metric map {file.name} cannot be read") from None
    if too_big:
        raise MapError(f"{file.name} is too large")
    try:
        data = json.loads(file.read_text())
        if isinstance(data, dict):
            data.pop("_comment", None)  # a note for people reading the file
        loaded = MetricMap.model_validate(data)
    except json.JSONDecodeError as exc:
        raise MapError(f"{file.name} is not valid JSON ({exc.msg})") from None
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise MapError(f"{file.name}: {where}: {first['msg']}") from None
    _check_unique(loaded.metrics)
    loaded.prerequisites.sort(key=lambda p: len(p.prefix), reverse=True)
    return loaded


@lru_cache(maxsize=1)
def shipped_map() -> MetricMap:
    return load_metric_map()


@lru_cache(maxsize=8)
def _user_map(path: str) -> MetricMap:
    return load_metric_map(Path(path))


def active_map() -> MetricMap:
    """The map named by IMPORTER_DYNATRACE_METRIC_MAP (checked as strictly as the shipped one),
    otherwise the shipped map. The variable is read at each call."""
    named = os.environ.get(MAP_ENV, "").strip()
    return _user_map(named) if named else shipped_map()


def lookup(key: str) -> MapEntry | None:
    """The active map's row for a Dynatrace metric key, or None."""
    return active_map().find(key)


def prerequisite(key: str) -> str:
    """What must be installed or sent so the metric exists in New Relic."""
    return active_map().prerequisite(key)
