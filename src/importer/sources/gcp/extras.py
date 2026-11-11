"""The definitions an alert policy refers to but does not carry, read from the same export.

`extract-gcp` writes `log-metrics/`, `slos/` and `uptime-checks/` beside `policies/`. When
`convert --source gcp --in <export>/policies` finds them there, the translator can convert
conditions on log-based metrics, SLO selectors and uptime checks. Each part is None when its file
is missing (an older export, or a read that failed), and then the old behaviour is unchanged.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from importer.core.ids import MAX_INPUT_BYTES

SLO_TAIL = (
    4  # services/<service>/serviceLevelObjectives/<slo>: the project may be an id or a number
)


class GcpExtras(BaseModel):
    log_metrics: dict[str, dict[str, Any]] | None = None  # by metric name
    slos: dict[str, dict[str, Any]] | None = None  # by "services/<s>/serviceLevelObjectives/<id>"
    uptime: dict[str, dict[str, Any]] | None = None  # by check id

    def slo(self, full_name: str) -> dict[str, Any] | None:
        if self.slos is None:
            return None
        return self.slos.get(slo_key(full_name))


def slo_key(full_name: str) -> str:
    return "/".join(full_name.strip("/").split("/")[-SLO_TAIL:])


def _read(file: Path) -> list[dict[str, Any]] | None:
    if not file.is_file():
        return None
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{file} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    data = json.loads(file.read_text())
    return [i for i in data if isinstance(i, dict)] if isinstance(data, list) else None


def load_extras(export: Path) -> GcpExtras | None:
    """The extras beside `export/policies`, or None when the export has none of them."""
    metrics = _read(export / "log-metrics" / "log-metrics.json")
    slos = _read(export / "slos" / "slos.json")
    checks = _read(export / "uptime-checks" / "uptime-checks.json")
    if metrics is None and slos is None and checks is None:
        return None
    return GcpExtras(
        log_metrics=None if metrics is None else {str(m.get("name", "")): m for m in metrics},
        slos=None if slos is None else {slo_key(str(s.get("name", ""))): s for s in slos},
        uptime=None
        if checks is None
        else {str(c.get("name", "")).rsplit("/", 1)[-1]: c for c in checks},
    )
