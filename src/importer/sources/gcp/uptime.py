"""Google uptime checks as an inventory with the steps to recreate each one in New Relic.

No new IR kind: a check is a manual item. The extractor keeps the check's kind, period and URL
path but not the host (it sits in the monitored resource labels), so a monitor cannot be created
automatically. One report row and one plan entry per check.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.remediation import steps_for
from importer.core.report import md_cell
from importer.core.text import printable

CHECKS_FILE = "uptime-checks.json"
MAX_CHECKS = 10000
# New Relic run frequencies in minutes, as in the monitor editor (not confirmed against the API)
FREQUENCIES = (1, 5, 10, 15, 30, 60)

REASONS = {
    "http": "uptime check inventory (http): the host is not exported, create a ping monitor",
    "tcp": "uptime check inventory (tcp): New Relic has no TCP monitor, choose another check",
    "other": "uptime check inventory (other): this kind of check has no direct counterpart",
}


class UptimePlan(BaseModel):
    name: str
    kind: str
    path: str
    period: str
    frequency: str
    reason: str


def frequency_for(period: str) -> str:
    """The shortest New Relic run frequency that is not faster than the Google period."""
    seconds = int(period[:-1]) if period.endswith("s") and period[:-1].isdigit() else 300
    minutes = max(1, -(-seconds // 60))
    chosen = next((f for f in FREQUENCIES if f >= minutes), FREQUENCIES[-1])
    text = "every minute" if chosen == 1 else f"every {chosen} minutes"
    note = "" if chosen * 60 == seconds else f" (Google ran it every {printable(period, 12)})"
    return text + note


def plan_check(check: dict[str, Any]) -> UptimePlan:
    kind = str(check.get("kind", ""))
    kind = kind if kind in REASONS else "other"
    return UptimePlan(
        name=printable(str(check.get("displayName") or check.get("name") or "unnamed"), 100),
        kind=kind,
        path=printable(str(check.get("path", "")), 200),
        period=printable(str(check.get("period", "")), 12),
        frequency=frequency_for(str(check.get("period", ""))),
        reason=REASONS[kind],
    )


def load_checks(path: Path) -> list[dict[str, Any]]:
    """Checks from `uptime-checks.json` (a file, or a folder holding it)."""
    file = path / CHECKS_FILE if path.is_dir() else path
    if not file.is_file():
        raise ValueError(f"no {CHECKS_FILE} found at {path}")
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
    data = json.loads(file.read_text())
    if not isinstance(data, list) or len(data) > MAX_CHECKS:
        raise ValueError(f"{file.name}: expected a list of at most {MAX_CHECKS} checks")
    return [c for c in data if isinstance(c, dict)]


def write_outputs(checks: list[dict[str, Any]], out: Path) -> list[UptimePlan]:
    """Write uptime-plan.json and report.md under `out`."""
    plans = [plan_check(c) for c in checks]
    out.mkdir(parents=True, exist_ok=True)
    (out / "uptime-plan.json").write_text(
        json.dumps([p.model_dump() for p in plans], indent=2) + "\n"
    )
    lines = [
        "# Google uptime check report",
        "",
        "The host of each check is not exported. Take it from the Google check, then create the "
        "monitor named under Create in New Relic (Synthetic monitoring, Create monitor).",
        "",
        "| Check | Kind | URL path | Google period | Create | New Relic run frequency |",
        "|---|---|---|---|---|---|",
    ]
    for p in plans:
        create = {
            "http": "ping monitor, or a scripted API monitor for assertions",
            "tcp": "TCP check: see the steps below",
            "other": "group check: see the steps below",
        }[p.kind]
        lines.append(
            f"| {md_cell(p.name, 60)} | {p.kind} | {md_cell(p.path or '/', 60)} | "
            f"{md_cell(p.period or 'unknown', 12)} | {create} | {md_cell(p.frequency, 60)} |"
        )
    kinds = sorted({p.kind for p in plans})
    lines += ["", "## Steps", ""]
    for kind in kinds:
        lines += [f"- {kind}: {md_cell(step, 400)}" for step in steps_for([REASONS[kind]])]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return plans
