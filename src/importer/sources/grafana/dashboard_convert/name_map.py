"""An optional map from a Prometheus metric name to the New Relic event and attribute that hold it.

Converted queries use the Prometheus name as it is, because remote write keeps it (research note
grafana-notifications-datapath.md, section 6). When the data arrived under another name, or on
another event, the owner writes a map; it is read as strictly as the Dynatrace metric map:
unknown keys are refused, names must be NRQL identifiers, and a bad file fails at load time.

    {"metrics": {"node_load1": {"event": "Metric", "attribute": "node.load.1m"}}}

The optional "series" key says which labels name exactly one series of a gauge, for example
`{"series": {"node_load1": ["instance"], "grafanacloud_org_info": []}}`. sum() of a gauge adds
every sample, so it is converted (to latest() or average()) only when the query groups by or pins
those labels; the converter cannot see the series count in New Relic and never guesses it.

The optional "types" key gives a metric's exact type: "counter", "gauge" or "histogram_bucket"
(the `_bucket` series of a histogram). `grafana-prom-hints` writes it from Prometheus metadata
and the series it observed. With a type the converter stops guessing from the name suffix
(`_total`, `_count`, `_sum`, `_bucket`): a gauge that happens to end in `_total` converts, and a
counter without a suffix is refused when read without rate(). A metric with no type keeps the
suffix guess. A hints file is an ordinary name map that has only "types" and "series".

The optional "variables" key gives a default for each `${NAME}` placeholder in a PromQL query
that has no value of its own (a Google sample policy, an Azure rule template):
`{"variables": {"CLUSTER_NAME": "prod-1"}}`. The conversion writes the default into the query
and says so in a note; a placeholder with no default stays a refusal. A value may hold letters,
digits and `_ . : / * + | ( ) - ` and spaces only, so it can never close a string or a selector.

The optional "labels" key lists every label of a metric (what `keyset()` returns), so that a
`without(...)` grouping can be written as the labels that remain.
"""

import json
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from importer.core.emit.nrql import identifier
from importer.core.ids import MAX_INPUT_BYTES

MAP_ENV = "IMPORTER_GRAFANA_NAME_MAP"
MAX_ENTRIES = 100_000
METRIC_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")
VARIABLE_NAME = re.compile(r"\w{1,64}")
VARIABLE_VALUE = re.compile(r"[A-Za-z0-9_.:/*+|() -]{0,200}")


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
    # gauge metric -> the labels that together name exactly one series (an empty list: the metric
    # has one series). Only with this hint is sum() of a gauge converted.
    series: dict[str, list[str]] = {}
    # metric -> its exact type; absent means the converter guesses from the name suffix
    types: dict[str, Literal["counter", "gauge", "histogram_bucket"]] = {}

    # default text for `${NAME}` placeholders in PromQL queries
    variables: dict[str, str] = {}
    # metric -> every label it carries (from keyset()); lets without() become a by() list
    labels: dict[str, list[str]] = {}

    def is_gauge(self, name: str) -> bool:
        """True when the hints file types this metric as a gauge (never a guess from the name)."""
        return self.types.get(name) == "gauge"

    @field_validator("variables")
    @classmethod
    def _variables(cls, found: dict[str, str]) -> dict[str, str]:
        if len(found) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} variables")
        for key, value in found.items():
            if not VARIABLE_NAME.fullmatch(key):
                raise ValueError("a key is not a variable name")
            if not VARIABLE_VALUE.fullmatch(value):
                raise ValueError("a variable default holds a character that is not allowed")
        return found

    @field_validator("labels")
    @classmethod
    def _labels(cls, found: dict[str, list[str]]) -> dict[str, list[str]]:
        for key, names in found.items():
            if not METRIC_NAME.fullmatch(key):
                raise ValueError("a key is not a Prometheus metric name")
            for label in names:
                identifier(label)
        return found

    @field_validator("types")
    @classmethod
    def _types(cls, found: dict[str, str]) -> dict[str, str]:
        if len(found) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} metrics")
        for key in found:
            if not METRIC_NAME.fullmatch(key):
                raise ValueError("a key is not a Prometheus metric name")
        return found

    @field_validator("series")
    @classmethod
    def _series(cls, found: dict[str, list[str]]) -> dict[str, list[str]]:
        for key, labels in found.items():
            if not METRIC_NAME.fullmatch(key):
                raise ValueError("a key is not a Prometheus metric name")
            for label in labels:
                identifier(label)
        return found

    @field_validator("metrics")
    @classmethod
    def _names(cls, found: dict[str, NameEntry]) -> dict[str, NameEntry]:
        if len(found) > MAX_ENTRIES:
            raise ValueError(f"more than {MAX_ENTRIES} metrics")
        for key in found:
            if not METRIC_NAME.fullmatch(key):
                raise ValueError("a key is not a Prometheus metric name")
        return found


def merge_hints(base: NameMap, hints: NameMap) -> NameMap:
    """`base` with the types and series of `hints` added; what the owner wrote in `base` wins."""
    return base.model_copy(
        update={
            "types": {**hints.types, **base.types},
            "series": {**hints.series, **base.series},
            "labels": {**hints.labels, **base.labels},
        }
    )


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


def hinted_gauge(name: str) -> bool:
    """is_gauge on the active name map; an unreadable map hints nothing."""
    try:
        return active_name_map().is_gauge(name)
    except NameMapError:
        return False
