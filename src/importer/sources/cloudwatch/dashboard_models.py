"""CloudWatch dashboards, in the shape of `aws cloudwatch get-dashboard` output.

The dashboard body is a JSON document inside a JSON string. Widgets are kept as plain dicts
because the body is untrusted and loosely typed; the translator validates what it reads.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from importer.sources.cloudwatch.models import MAX_INPUT_BYTES


class CloudWatchDashboard(BaseModel):
    name: str
    widgets: list[dict[str, Any]]


def _parse(data: Any, fallback_name: str) -> CloudWatchDashboard:
    name = fallback_name
    if isinstance(data, dict) and "DashboardBody" in data:
        name = str(data.get("DashboardName") or fallback_name)
        data = data["DashboardBody"]
        if isinstance(data, str):
            data = json.loads(data)
    if not isinstance(data, dict) or not isinstance(data.get("widgets", []), list):
        raise ValueError(f"{name}: expected a dashboard body with a 'widgets' list")
    widgets = data.get("widgets", [])
    if not all(isinstance(w, dict) for w in widgets):
        raise ValueError(f"{name}: every widget must be a JSON object")
    return CloudWatchDashboard(name=name, widgets=widgets)


def load_dashboards(path: Path) -> list[CloudWatchDashboard]:
    """Read one get-dashboard JSON file (or a bare dashboard body), or every *.json in a folder."""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    out: list[CloudWatchDashboard] = []
    for file in files:
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // 1024 // 1024} MB")
        out.append(_parse(json.loads(file.read_text()), file.stem))
    return out
