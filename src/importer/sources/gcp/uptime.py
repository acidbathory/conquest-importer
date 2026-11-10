"""Google uptime checks as an inventory with the steps to recreate each one in New Relic.

No new IR kind: a check is a manual item. The extractor keeps the check's kind, period and URL
path but not the host (it sits in the monitored resource labels), so a monitor cannot be created
automatically. One report row and one plan entry per check.
"""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.redact import safe_name, safe_path
from importer.core.remediation import steps_for
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable

CHECKS_FILE = "uptime-checks.json"
MAX_CHECKS = 10000
# New Relic run frequencies in minutes, as in the monitor editor (not confirmed against the API);
# the slowest is the last
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


UNKNOWN_PERIOD = "period unknown: choose the frequency yourself"
PERIOD = re.compile(r"[0-9]{1,9}s")


def frequency_for(period: object) -> str:
    """The shortest New Relic run frequency that does not run more often than Google did.

    A period that is not a whole number of seconds with an `s` (missing, `2h`, `1.5s`, ...) gives
    no frequency: guessing one would pass for the real value.
    """
    text = period if isinstance(period, str) else ""
    if not PERIOD.fullmatch(text) or int(text[:-1]) == 0:
        return UNKNOWN_PERIOD
    seconds = int(text[:-1])
    minutes = -(-seconds // 60)
    chosen = next((f for f in FREQUENCIES if f >= minutes), FREQUENCIES[-1])
    shown = "every minute" if chosen == 1 else f"every {chosen} minutes"
    if minutes > FREQUENCIES[-1]:
        return (
            f"{shown} (Google ran it every {seconds}s; the slowest New Relic frequency is "
            f"{FREQUENCIES[-1]} minutes)"
        )
    return shown if chosen * 60 == seconds else f"{shown} (Google ran it every {seconds}s)"


def _tail(check: dict[str, Any]) -> str:
    return str(check.get("name", "")).rsplit("/", 1)[-1] or "unnamed"


def plan_check(check: dict[str, Any]) -> UptimePlan:
    kind = str(check.get("kind", ""))
    kind = kind if kind in REASONS else "other"
    return UptimePlan(
        name=safe_name(str(check.get("displayName", "")), _tail(check), 100),
        kind=kind,
        path=safe_path(str(check.get("path", ""))),
        period=printable(raw if isinstance(raw := check.get("period"), str) else "", 12),
        frequency=frequency_for(check.get("period")),
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
        *header_lines("Google uptime check report", "gcp"),
        "",
        "The host of each check is not exported, and the path has no query string or fragment. "
        "Names and paths that looked like an address, URL or token are masked. "
        "Take the host from the Google check, then create the "
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
            f"{md_cell(p.period or 'unknown', 12)} | {create} | {md_cell(p.frequency, 160)} |"
        )
    kinds = sorted({p.kind for p in plans})
    lines += ["", "## Steps", ""]
    for kind in kinds:
        lines += [f"- {kind}: {md_cell(step, 400)}" for step in steps_for([REASONS[kind]])]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return plans
