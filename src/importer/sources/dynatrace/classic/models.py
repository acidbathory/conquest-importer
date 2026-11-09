"""Models for Dynatrace Settings 2.0 objects that hold classic and Davis alerting configuration.

The loader accepts a file (a list, one object, or a Settings API response with `items`) or a folder
of such files. Each object is validated on its own: an object that does not fit its schema is kept
with a `problem` text (field names only, never values) and becomes a manual item, and the rest
load. Unknown fields are kept (extra="allow"). Notification objects are reduced to a safe subset
here, so a secret, a webhook URL or a recipient address never survives past the loader.
"""

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, FiniteFloat, ValidationError

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.json_depth import TooDeepError, check_depth
from importer.core.text import printable

MAX_FILES = 10_000
MAX_ITEMS = 100_000  # objects in one run
MAX_DEPTH = 32  # nesting inside one object's value

METRIC_EVENTS = "builtin:anomaly-detection.metric-events"
DETECTORS = "builtin:davis.anomaly-detectors"
PROFILE = "builtin:alerting.profile"
NOTIFICATIONS = "builtin:problem.notifications"
MAINTENANCE = "builtin:alerting.maintenance-window"
MAINTENANCE_NEW = "builtin:maintenance-windows"
ZONES = "builtin:management-zones"
DETECTION_PREFIX = "builtin:anomaly-detection."

# schema id -> what an object of it is called in a report (inventory only)
INVENTORY: dict[str, str] = {
    PROFILE: "alerting profile",
    NOTIFICATIONS: "problem notification",
    MAINTENANCE: "maintenance window",
    MAINTENANCE_NEW: "maintenance window",
    ZONES: "management zone",
}
# built-in detection tuning the extractor reads (the allow-list; the loader accepts any
# `builtin:anomaly-detection.` id it is given)
DETECTION_SCHEMAS = (
    "builtin:anomaly-detection.services",
    "builtin:anomaly-detection.infrastructure-hosts",
    "builtin:anomaly-detection.infrastructure-disks",
    "builtin:anomaly-detection.infrastructure-disks.per-disk-override",
    "builtin:anomaly-detection.disk-rules",
    "builtin:anomaly-detection.infrastructure-vmware",
    "builtin:anomaly-detection.infrastructure-aws",
    "builtin:anomaly-detection.databases",
    "builtin:anomaly-detection.kubernetes-cluster",
    "builtin:anomaly-detection.kubernetes-node",
    "builtin:anomaly-detection.kubernetes-namespace",
    "builtin:anomaly-detection.kubernetes-workload",
    "builtin:anomaly-detection.kubernetes-pvc",
    "builtin:anomaly-detection.rum-web",
    "builtin:anomaly-detection.rum-mobile",
    "builtin:anomaly-detection.rum-custom",
)
# every schema id the extractor may ask for; no user text becomes a schema id
READ_SCHEMAS = (
    METRIC_EVENTS,
    DETECTORS,
    PROFILE,
    NOTIFICATIONS,
    MAINTENANCE,
    MAINTENANCE_NEW,
    ZONES,
    *DETECTION_SCHEMAS,
)
# the only parts of a problem notification that are kept
NOTIFICATION_KEEP = ("enabled", "type", "displayName", "alertingProfile")


def _not_a_bool(value: Any) -> Any:
    """A JSON true or false is not a count; pydantic would read it as 1 or 0."""
    if isinstance(value, bool):
        raise ValueError("a boolean is not a number")
    return value


Count = Annotated[int, BeforeValidator(_not_a_bool)]


class EntityCondition(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = ""
    operator: str = ""
    value: str = ""


class EntityFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    dimensionKey: str | None = None
    conditions: list[EntityCondition] = []


class DimensionFilter(BaseModel):
    model_config = ConfigDict(extra="allow")

    dimensionKey: str = ""
    operator: str = ""
    dimensionValue: str = ""


class QueryDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    metricKey: str = ""
    aggregation: str = ""
    metricSelector: str = ""
    managementZone: str | None = None
    queryOffset: Count | None = None
    entityFilter: EntityFilter = EntityFilter()
    dimensionFilter: list[DimensionFilter] = []


class ModelProperties(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    threshold: FiniteFloat | None = None
    signalFluctuation: FiniteFloat | None = None
    tolerance: FiniteFloat | None = None
    alertCondition: str = ""
    alertOnNoData: bool = False
    violatingSamples: Count
    samples: Count
    dealertingSamples: Count | None = None


class EventTemplate(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str = ""
    description: str = ""
    eventType: str = ""
    davisMerge: bool | None = None


class MetricEventValue(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    summary: str = ""
    queryDefinition: QueryDefinition
    modelProperties: ModelProperties
    eventTemplate: EventTemplate = EventTemplate()


class Pair(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    value: str | int | float | bool | None = None


class Analyzer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    input: list[Pair] = []


class ExecutionSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    queryOffset: Count | None = None


class DetectorValue(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    title: str = ""
    analyzer: Analyzer
    executionSettings: ExecutionSettings = ExecutionSettings()


# schema id -> the model that checks its value; schemas not listed are inventory and unchecked
VALUE_MODELS: dict[str, type[BaseModel]] = {
    METRIC_EVENTS: MetricEventValue,
    DETECTORS: DetectorValue,
}


class SettingsObject(BaseModel):
    """One Settings 2.0 object. `problem` is set when the object could not be read."""

    model_config = ConfigDict(extra="allow")

    objectId: str = ""
    schemaId: str = ""
    scope: str = ""
    value: dict[str, Any] = {}
    problem: str = ""


def depth(value: Any) -> int:
    """Nesting depth of a JSON value, without recursion (so a hostile file cannot overflow)."""
    deepest = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict | list):
            deepest = max(deepest, level)
            if deepest > MAX_DEPTH:
                return deepest
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, level + 1) for child in children)
    return deepest


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error["loc"])


_MEANING = {
    "finite_number": "is not a finite number",
    "missing": "is missing",
}


def describe(exc: ValidationError) -> str:
    """The first problem as field names only, so no value from the export is repeated."""
    first = exc.errors()[0]
    text = _MEANING.get(first["type"], "has the wrong type")
    return printable(f"{_location(first)} {text}", 120)


def reduce_notification(value: dict[str, Any]) -> dict[str, Any]:
    """Keep type, name, enabled flag and profile id; drop every secret, URL and recipient."""
    kept: dict[str, Any] = {}
    for key in NOTIFICATION_KEEP:
        found = value.get(key)
        # the flag is kept only as a real boolean: the text "false" is not read as enabled
        if isinstance(found, bool) if key == "enabled" else isinstance(found, str):
            kept[key] = found
    return kept


def _text(raw: dict[str, Any], key: str) -> str:
    found = raw.get(key)
    return found if isinstance(found, str) else ""


def _checked(schema: str, value: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The value to keep and a problem text ("" when it is fine)."""
    if depth(value) > MAX_DEPTH:
        return {}, f"the object is nested more than {MAX_DEPTH} levels deep"
    model = VALUE_MODELS.get(schema)
    if model is not None:
        try:
            model.model_validate(value)
        except ValidationError as exc:
            return {}, describe(exc)
    if schema == NOTIFICATIONS:
        return reduce_notification(value), ""
    return value, ""


def parse_object(raw: object) -> SettingsObject:
    """One item of a Settings response; never raises."""
    if not isinstance(raw, dict):
        return SettingsObject(problem="an item is not a JSON object")
    base = {k: _text(raw, k) for k in ("objectId", "schemaId", "scope")}
    value = raw.get("value")
    if not base["schemaId"]:
        return SettingsObject(
            **base, problem="the item has no schemaId (not a Settings 2.0 object)"
        )
    if not isinstance(value, dict):
        return SettingsObject(**base, problem="the item has no value object")
    kept, problem = _checked(base["schemaId"], value)
    return SettingsObject(**base, value=kept, problem=problem)


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    found = sorted(f for folder in (path, path / "settings") for f in folder.glob("*.json"))
    if len(found) > MAX_FILES:
        raise ValueError(f"{path} has more than {MAX_FILES} JSON files; pass a smaller folder")
    return found


def _raw_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return list(data["items"])
        return [data]
    raise ValueError("expected a JSON object, a list, or a Settings response with `items`")


def _read(file: Path) -> Any:
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    try:
        text = file.read_text()
        check_depth(text)
        return json.loads(text)
    except (TooDeepError, RecursionError):
        raise ValueError(f"{file.name} is nested too deeply to read") from None
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{file.name} is not valid JSON ({exc.msg} at line {exc.lineno})"
        ) from None


def load_objects(path: Path) -> list[SettingsObject]:
    """Read Settings objects from a file, a Settings API response, or a folder of them."""
    objects: list[SettingsObject] = []
    for file in _files(path):
        for raw in _raw_items(_read(file)):
            objects.append(parse_object(raw))
            if len(objects) > MAX_ITEMS:
                raise ValueError(f"more than {MAX_ITEMS} objects; pass a smaller export")
    return objects
