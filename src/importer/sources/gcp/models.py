"""GCP source models, in the shape of `gcloud alpha monitoring policies list --format=json`.

Unknown fields are kept (extra="allow") so nothing is silently dropped.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from importer.core.ids import MAX_INPUT_BYTES

CONDITION_KINDS = (
    "conditionThreshold",
    "conditionAbsent",
    "conditionMatchedLog",
    "conditionMonitoringQueryLanguage",
    "conditionPrometheusQueryLanguage",
    "conditionSql",
)


class GcpCondition(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    displayName: str = ""

    @property
    def kind(self) -> str:
        extra = self.model_extra or {}
        return next((k for k in CONDITION_KINDS if k in extra), "")

    @property
    def body(self) -> dict[str, Any]:
        value = (self.model_extra or {}).get(self.kind)
        return value if isinstance(value, dict) else {}


class GcpAlertPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str = ""
    displayName: str
    enabled: bool = True
    combiner: str = "OR"
    severity: str = ""
    conditions: list[GcpCondition] = []
    notificationChannels: list[str] = []
    documentation: dict[str, Any] = {}
    alertStrategy: dict[str, Any] = {}


def _files(path: Path) -> list[Path]:
    return sorted(path.glob("*.json")) if path.is_dir() else [path]


def load_policies(path: Path) -> list[GcpAlertPolicy]:
    """Read alert policies from a JSON list, an API response, or a folder of either."""
    policies: list[GcpAlertPolicy] = []
    for file in _files(path):
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        data = json.loads(file.read_text())
        items = data.get("alertPolicies", []) if isinstance(data, dict) else data
        policies += [GcpAlertPolicy.model_validate(i) for i in items]
    return policies
