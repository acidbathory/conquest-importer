"""The synthetic monitor plan: each monitor to a New Relic monitor type (research note section 5).

A plan, not a conversion: nothing is created. For each monitor it says which New Relic monitor
type fits, whether that monitor could be created from the JSON alone or needs a script that a
person writes, how its locations map, and what the availability thresholds would become. The
location table is a data file (data/synthetic_locations.json) whose rows are marked DOCS or
UNVERIFIED.

Decision table (HTTP monitors):
- one GET or HEAD request, no body, no pre or post script, at most one `patternConstraint` that
  must be found: a simple monitor (SIMPLE);
- only a certificate-expiry rule: a certificate check monitor;
- anything else (POST, body, several requests, status-list or regex rules, scripts): a scripted
  API monitor that a person writes.
Browser monitors: `availability` (one navigate) is a simple browser monitor; a clickpath of
navigate, click, keystrokes and select events is a step monitor (the step locator format is not
confirmed, so it is not created from JSON); a clickpath with JavaScript or cookie events is a
scripted browser monitor. Network availability monitors have no New Relic monitor.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.dynatrace.dashboards.report_util import table
from importer.sources.dynatrace.inventory import reasons as r
from importer.sources.dynatrace.inventory.models import Location, Monitor, dicts

DATA = Path(__file__).parent / "data"
PERIODS = {
    1: "EVERY_MINUTE",
    5: "EVERY_5_MINUTES",
    10: "EVERY_10_MINUTES",
    15: "EVERY_15_MINUTES",
    30: "EVERY_30_MINUTES",
    60: "EVERY_HOUR",
    360: "EVERY_6_HOURS",
    720: "EVERY_12_HOURS",
    1440: "EVERY_DAY",
}
STEP_EVENTS = frozenset({"navigate", "click", "tap", "keystrokes", "selectoption", "select"})
TYPES = Literal[
    "SIMPLE", "BROWSER", "CERT_CHECK", "STEP_MONITOR", "SCRIPT_API", "SCRIPT_BROWSER", "NONE"
]
MAX_REQUESTS = 50
MAX_EVENTS = 500


class LocationPlan(BaseModel):
    new_relic: str  # a public location name, or "" when there is none
    status: str  # DOCS, UNVERIFIED, PRIVATE or UNMAPPED
    reasons: list[str] = []


class MonitorPlan(BaseModel):
    ref: str
    name: str
    dynatrace_type: str
    target: TYPES
    creatable_from_json: bool
    period: str = ""
    devices: list[str] = []
    locations: list[LocationPlan] = []
    reasons: list[str] = []  # why a script or a person is needed
    notes: list[str] = []
    alert_consecutive_runs: int | None = None  # availability threshold, for the alert note
    alert_loading_ms: int | None = None


class SyntheticsPlan(BaseModel):
    monitors: list[MonitorPlan] = []
    skipped: int = 0


@lru_cache(maxsize=1)
def _locations() -> tuple[dict[tuple[str, str], tuple[str, str]], dict[str, tuple[str, str]]]:
    raw = json.loads((DATA / "synthetic_locations.json").read_text())
    cities = {
        (row["city"].lower(), row["country"]): (row["new_relic"], row["status"])
        for row in raw["cities"]
    }
    countries = {row["country"]: (row["new_relic"], row["status"]) for row in raw["countries"]}
    return cities, countries


def _http_requests(script: dict[str, Any]) -> list[dict[str, Any]]:
    return dicts(script.get("requests"), MAX_REQUESTS)


def _rules(request: dict[str, Any]) -> list[dict[str, Any]]:
    validation = request.get("validation")
    return dicts(validation.get("rules"), 50) if isinstance(validation, dict) else []


def _has_script(request: dict[str, Any]) -> bool:
    return bool(request.get("preProcessingScript") or request.get("postProcessingScript"))


def _simple_rules(rules: list[dict[str, Any]]) -> bool:
    """At most one pattern rule that must be found."""
    if len(rules) > 1:
        return False
    return all(x.get("type") == "patternConstraint" and x.get("passIfFound") is True for x in rules)


def _classify_http(monitor: Monitor) -> tuple[TYPES, list[str], list[str]]:
    requests = _http_requests(monitor.script)
    if not requests:
        return "NONE", [r.S_NO_SCRIPT], []
    if len(requests) > 1:
        return "SCRIPT_API", [r.S_API_SCRIPT], []
    request = requests[0]
    rules = _rules(request)
    if _has_script(request):
        return "SCRIPT_API", [r.S_API_SCRIPT, r.S_PRE_POST], []
    if str(request.get("method", "GET")).upper() not in ("GET", "HEAD") or request.get(
        "requestBody"
    ):
        return "SCRIPT_API", [r.S_API_SCRIPT], []
    if rules and all(x.get("type") == "certificateExpiryDateConstraint" for x in rules):
        return "CERT_CHECK", [], []
    if not _simple_rules(rules):
        return "SCRIPT_API", [r.S_API_SCRIPT], []
    return "SIMPLE", [], _simple_notes(request)


def _simple_notes(request: dict[str, Any]) -> list[str]:
    config = request.get("configuration")
    config = config if isinstance(config, dict) else {}
    notes = []
    if "followRedirects" in config:
        notes.append(r.S_REDIRECT)
    if config.get("userAgent"):
        notes.append(r.S_USER_AGENT)
    return notes


def _classify_browser(monitor: Monitor) -> tuple[TYPES, list[str], list[str]]:
    script = monitor.script
    events = dicts(script.get("events"), MAX_EVENTS)
    kinds = {str(e.get("type", "")).lower() for e in events}
    if not events:
        return "NONE", [r.S_NO_SCRIPT], []
    if script.get("type") == "availability" and kinds == {"navigate"} and len(events) == 1:
        return "BROWSER", [], []
    if script.get("cookies") or not kinds <= STEP_EVENTS:
        return "SCRIPT_BROWSER", [r.S_BROWSER_SCRIPT], []
    return "STEP_MONITOR", [r.S_STEP], []


def _classify(monitor: Monitor) -> tuple[TYPES, list[str], list[str]]:
    if monitor.type == "HTTP":
        return _classify_http(monitor)
    if monitor.type == "BROWSER":
        return _classify_browser(monitor)
    if monitor.type == "NETWORK_AVAILABILITY":
        return "SCRIPT_API", [r.S_NETWORK], []
    return "NONE", [r.S_KIND], []


def device_for(script: dict[str, Any]) -> list[str]:
    """New Relic device names from the browser configuration (a name table is not needed for
    the three families Dynatrace offers; anything else is treated as desktop)."""
    config = script.get("configuration")
    device = config.get("device") if isinstance(config, dict) else None
    if not isinstance(device, dict):
        return ["DESKTOP"]
    name = str(device.get("deviceName", "")).lower()
    landscape = str(device.get("orientation", "")).lower() == "landscape"
    if any(word in name for word in ("ipad", "tablet", "galaxy tab")):
        return ["TABLET_LANDSCAPE" if landscape else "TABLET_PORTRAIT"]
    if any(word in name for word in ("iphone", "pixel", "galaxy", "phone", "mobile")):
        return ["MOBILE_LANDSCAPE" if landscape else "MOBILE_PORTRAIT"]
    return ["DESKTOP"]


def map_location(location: Location | None) -> LocationPlan:
    """A Dynatrace location to a New Relic one; private and unknown ones say what is missing."""
    if location is None:
        return LocationPlan(new_relic="", status="UNMAPPED", reasons=[r.S_LOCATION])
    if location.kind == "PRIVATE" or location.id.startswith("SYNTHETIC_LOCATION"):
        return LocationPlan(new_relic="", status="PRIVATE", reasons=[r.S_PRIVATE])
    cities, countries = _locations()
    found = cities.get((location.city.lower(), location.country)) or countries.get(location.country)
    if found is None:
        return LocationPlan(new_relic="", status="UNMAPPED", reasons=[r.S_LOCATION])
    return LocationPlan(new_relic=found[0], status=found[1])


def _period(minutes: int | None) -> tuple[str, list[str]]:
    if minutes is None:
        return "", []
    if minutes in PERIODS:
        return PERIODS[minutes], []
    larger = [m for m in PERIODS if m > minutes]
    return (PERIODS[min(larger)] if larger else PERIODS[1440]), [r.S_FREQUENCY]


def plan_monitor(monitor: Monitor, locations: dict[str, Location]) -> MonitorPlan:
    target, reasons, notes = _classify(monitor)
    period, period_notes = _period(monitor.frequency_min)
    mapped = [map_location(locations.get(i)) for i in monitor.locations]
    if not monitor.locations:
        notes.append(r.S_NO_LOCATION)
    notes += period_notes
    if not monitor.enabled:
        notes.append(r.S_DISABLED)
    if monitor.outage_handling or monitor.loading_thresholds:
        notes.append(r.S_ALERT)
    return MonitorPlan(
        ref=printable(monitor.ref, 120),
        name=printable(monitor.name, 120),
        dynatrace_type=printable(monitor.type, 40),
        target=target,
        creatable_from_json=target in ("SIMPLE", "BROWSER", "CERT_CHECK"),
        period=period,
        devices=device_for(monitor.script) if monitor.type == "BROWSER" else [],
        locations=mapped,
        reasons=reasons,
        notes=notes,
        alert_consecutive_runs=monitor.outage_runs,
        alert_loading_ms=monitor.loading_ms,
    )


def plan_synthetics(
    monitors: list[Monitor], locations: list[Location], skipped: int = 0
) -> SyntheticsPlan:
    by_id = {x.id: x for x in locations}
    return SyntheticsPlan(monitors=[plan_monitor(m, by_id) for m in monitors], skipped=skipped)


def render_report(plan: SyntheticsPlan) -> str:
    by_target: dict[str, int] = {}
    for m in plan.monitors:
        by_target[m.target] = by_target.get(m.target, 0) + 1
    creatable = sum(m.creatable_from_json for m in plan.monitors)
    lines = [
        "# Dynatrace synthetic monitor plan",
        "",
        f"Monitors read: **{len(plan.monitors)}** (entries not read: {plan.skipped}). "
        f"Creatable from the JSON alone: **{creatable}**. No monitor is created by this plan.",
        "",
        *table(["New Relic monitor", "Count"], [[k, str(v)] for k, v in sorted(by_target.items())]),
        "",
        "## Monitors",
        "",
        *table(
            [
                "Monitor",
                "Dynatrace type",
                "New Relic",
                "From JSON",
                "Period",
                "Locations",
                "Reasons and notes",
            ],
            [
                [
                    md_cell(m.name, 60),
                    md_cell(m.dynatrace_type, 40),
                    m.target,
                    "yes" if m.creatable_from_json else "no",
                    m.period,
                    md_cell(", ".join(x.new_relic or x.status for x in m.locations), 120),
                    md_cell("; ".join([*m.reasons, *m.notes]), 400),
                ]
                for m in plan.monitors
            ],
        ),
    ]
    reasons = [
        *dict.fromkeys(
            x
            for m in plan.monitors
            for x in [*m.reasons, *m.notes, *[y for loc in m.locations for y in loc.reasons]]
        )
    ]
    if reasons:
        lines += ["", "## What to do", ""]
        for remedy in remedies_for(reasons):
            lines.append(f"### {md_cell(remedy.title)}")
            lines += [f"- {md_cell(step, 600)}" for step in remedy.steps]
            lines.append("")
    return "\n".join(lines) + "\n"


def write_plan(plan: SyntheticsPlan, out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(plan.model_dump_json(indent=2, exclude_defaults=True) + "\n")
    (out / "report.md").write_text(render_report(plan))
