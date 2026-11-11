"""Azure Monitor export models and the loader `convert --source azure` uses.

The loader accepts what `extract-azure` writes (list pages `{"value": [...]}`), a single resource,
a plain list, and the official example files from the Azure REST API specs (a request plus
`responses`; the resource is read from the response body, never from the request). Real captured
responses added later as extra fixtures need no change: any of those shapes works.

Unknown fields are kept and wrong types never raise, because exports come from a live tenant and
the swagger says undocumented values may appear. The translator decides what a field means and
labels what it cannot read. Only alert rules are returned; action groups and alert processing
rules are read by the notification code, and other resource types are ignored.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.json_depth import TooDeepError, check_depth

# lower-cased resource type -> the key extract-azure uses for it
FAMILIES = {
    "microsoft.insights/metricalerts": "metricAlerts",
    "microsoft.insights/scheduledqueryrules": "scheduledQueryRules",
    "microsoft.insights/activitylogalerts": "activityLogAlerts",
    "microsoft.alertsmanagement/prometheusrulegroups": "prometheusRuleGroups",
    "microsoft.alertsmanagement/smartdetectoralertrules": "smartDetectorAlertRules",
}
NAME_MAP_FILE = "name-map.json"
MAX_FILES = 5000
_FOLDER_WORDS = {
    "metricalerts": "metricAlerts",
    "scheduledqueryrules": "scheduledQueryRules",
    "activitylogalerts": "activityLogAlerts",
    "prometheusrulegroups": "prometheusRuleGroups",
    "smartdetectoralertrules": "smartDetectorAlertRules",
}
_NAME_VALUE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,200}")
_DURATION = re.compile(
    r"P(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?", re.IGNORECASE
)


def iso_seconds(value: object) -> int | None:
    """Seconds in an ISO 8601 duration such as PT5M or P1DT12H; None for anything else.

    Months and years are refused (a month has no fixed length) and so are fractions.
    """
    if not isinstance(value, str) or len(value) > 40:
        return None
    match = _DURATION.fullmatch(value.strip())
    if match is None or not any(match.groupdict().values()):
        return None
    d, h, m, s = (int(match[k] or 0) for k in ("d", "h", "m", "s"))
    return d * 86400 + h * 3600 + m * 60 + s


class NameMap(BaseModel):
    """Hand-made names that override the derived ones; the file is `name-map.json`.

    `metrics`: Azure metric (`Percentage CPU` or `Microsoft.Compute/virtualMachines/Percentage
    CPU`) to the New Relic metric name seen arriving. `attributes`: a dimension or log column
    (`ShardId`, `AzureDiagnostics.Category`, or the bare name) to the New Relic attribute.
    """

    metrics: dict[str, str] = {}
    attributes: dict[str, str] = {}


def _name_map(folder: Path) -> NameMap:
    path = folder / NAME_MAP_FILE
    if not path.is_file():
        return NameMap()
    try:
        names = NameMap.model_validate(_read_json(path))
    except ValueError as exc:
        raise ValueError(f"{NAME_MAP_FILE} is not usable: {exc}") from None
    for value in (*names.metrics.values(), *names.attributes.values()):
        if not _NAME_VALUE.fullmatch(value):
            raise ValueError(
                f"{NAME_MAP_FILE}: {value[:40]!r} is not a plain New Relic name "
                "(letters, digits, dots and underscores)"
            )
    return names


class AzureAlert(BaseModel):
    """One alert rule resource as Azure returns it; everything else is kept in `extra`."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    name: str = ""
    type: str = ""
    kind: str = ""
    location: str = ""
    tags: dict[str, Any] = {}
    properties: dict[str, Any] = {}
    family: str = ""  # metricAlerts, scheduledQueryRules, ... (set by the loader)
    names: NameMap = Field(default_factory=NameMap, exclude=True)

    @field_validator("id", "name", "type", "kind", "location", mode="before")
    @classmethod
    def _text(cls, value: object) -> str:
        return value if isinstance(value, str) else ""

    @field_validator("tags", "properties", mode="before")
    @classmethod
    def _object(cls, value: object) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def model_post_init(self, _context: object) -> None:
        if not self.name and self.id:
            self.name = self.id.rstrip("/").rsplit("/", 1)[-1]
        if not self.family:
            self.family = FAMILIES.get(self.type.lower(), "")


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    raw = path.read_bytes()
    try:
        check_depth(raw)
        return json.loads(raw)
    except TooDeepError:
        raise ValueError(f"{path.name} is nested too deeply to read") from None
    except (ValueError, RecursionError):
        raise ValueError(f"{path.name} is not valid JSON") from None


def _example_bodies(data: dict[str, Any]) -> list[Any]:
    """The response bodies of an official example file (request echoes are not read)."""
    responses = data.get("responses")
    if not isinstance(responses, dict):
        return []
    bodies: list[Any] = []
    for code in sorted(responses, key=str):
        response = responses[code]
        if isinstance(response, dict) and isinstance(response.get("body"), dict | list):
            bodies.append(response["body"])
    return bodies


def _resources(data: Any) -> list[dict[str, Any]]:
    """Resource objects in any accepted shape: list, page, single resource, example wrapper."""
    if isinstance(data, list):
        return [r for item in data for r in _resources(item)]
    if not isinstance(data, dict):
        return []
    if "responses" in data and "parameters" in data:
        return [r for body in _example_bodies(data) for r in _resources(body)]
    if isinstance(data.get("value"), list):
        return [r for r in data["value"] if isinstance(r, dict)]
    return [data] if "properties" in data or "type" in data else []


def _family_from_path(path: Path) -> str:
    for part in (path.stem, path.parent.name):
        key = re.sub(r"[^a-z]", "", part.lower())
        for word, family in _FOLDER_WORDS.items():
            if word in key or word.rstrip("s") in key:
                return family
    return ""


def _family_from_shape(props: dict[str, Any]) -> str:
    criteria = props.get("criteria")
    if isinstance(criteria, dict) and "odata.type" in criteria:
        return "metricAlerts"
    if isinstance(criteria, dict) and "allOf" in criteria:
        return "scheduledQueryRules"
    if "condition" in props and "scopes" in props:
        return "activityLogAlerts"
    if isinstance(props.get("rules"), list):
        return "prometheusRuleGroups"
    if "detector" in props:
        return "smartDetectorAlertRules"
    return ""


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    found = sorted(p for p in path.rglob("*.json") if p.is_file() and p.name != NAME_MAP_FILE)
    if len(found) > MAX_FILES:
        raise ValueError(f"more than {MAX_FILES} JSON files in {path.name}")
    return found


def load_alerts(path: Path) -> list[AzureAlert]:
    """Alert rules from a file or folder; identical copies are read once."""
    folder = path if path.is_dir() else path.parent
    names = _name_map(folder)
    alerts: list[AzureAlert] = []
    seen: set[str] = set()
    for file in _files(path):
        hint = _family_from_path(file)
        for resource in _resources(_read_json(file)):
            digest = hashlib.sha256(
                json.dumps(resource, sort_keys=True, default=str).encode()
            ).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            alert = AzureAlert.model_validate(resource)
            if not alert.family and not alert.type:  # no type: the folder or the shape says
                alert.family = hint or _family_from_shape(alert.properties)
            if alert.family:
                alert.names = names
                alerts.append(alert)
    return alerts
