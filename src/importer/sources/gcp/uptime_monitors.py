"""A plan for the New Relic Synthetics monitor behind each Google uptime check. A plan only.

Nothing is created: `convert-gcp-uptime` writes `synthetic-monitor-plan.json` and a person (or a
later, guarded step) creates the monitors. A check becomes:

- a simple (ping) monitor when it is a plain GET or HEAD with at most one "contains" matcher,
- a scripted API monitor when it needs a header, authentication, a body, another method or other
  matchers (the script is written by a person; the secrets are named, never stored),
- no monitor for a TCP check (New Relic Synthetics has no TCP monitor).

Secrets: the password of a basic-auth check, header values and a request body are never in the
export; the plan names a `SecretRef` (a logical name) for each one so the person creates it as a
New Relic secure credential.

UNVERIFIED (from memory of the New Relic documentation, not run against an account): the public
location names for the Google regions, and that a simple monitor has no timeout setting.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel

from importer.core.notification_ir import SecretRef
from importer.core.redact import safe_name
from importer.core.text import printable
from importer.sources.gcp.uptime import FREQUENCIES

PERIODS = {
    1: "EVERY_MINUTE",
    5: "EVERY_5_MINUTES",
    10: "EVERY_10_MINUTES",
    15: "EVERY_15_MINUTES",
    30: "EVERY_30_MINUTES",
    60: "EVERY_HOUR",
}
# Google checker region -> New Relic public locations (cloud regions); UNVERIFIED
REGIONS = {
    "USA": ["AWS_US_EAST_1", "AWS_US_WEST_1"],
    "EUROPE": ["AWS_EU_WEST_1", "AWS_EU_CENTRAL_1"],
    "SOUTH_AMERICA": ["AWS_SA_EAST_1"],
    "ASIA_PACIFIC": ["AWS_AP_SOUTHEAST_1", "AWS_AP_SOUTHEAST_2"],
}
ALL_REGIONS = list(REGIONS)
DEFAULT_PORTS = {True: 443, False: 80}
SECRET_PREFIX = "GCP_UPTIME_"  # noqa: S105 - a name prefix, not a secret


class SecretNeed(BaseModel):
    purpose: str
    secret_ref: str  # the logical name of the New Relic secure credential to create


class MonitorSpec(BaseModel):
    name: str
    check_id: str
    target: Literal["SIMPLE", "SCRIPT_API", "NONE"]
    uri: str = ""
    frequency: str = ""
    locations: list[str] = []
    google_timeout: str = ""
    verify_ssl: bool | None = None
    response_validation_text: str = ""
    secrets: list[SecretNeed] = []
    notes: list[str] = []
    created: bool = False  # a plan only: always False


def _minutes(period: str) -> int | None:
    digits = period[:-1]
    if not (period.endswith("s") and digits.isascii() and digits.isdecimal()):
        return None
    seconds = int(digits)
    return -(-seconds // 60) if seconds else None


def _frequency(period: str, notes: list[str]) -> str:
    minutes = _minutes(period)
    if minutes is None:
        notes.append("the Google period is unknown: choose the run frequency yourself")
        return ""
    chosen = next((f for f in FREQUENCIES if f >= minutes), FREQUENCIES[-1])
    if chosen * 60 != int(period[:-1]):
        notes.append(f"Google ran it every {period}; the nearest New Relic frequency is used")
    return PERIODS[chosen]


def _locations(regions: list[str], internal: bool, notes: list[str]) -> list[str]:
    if internal:
        notes.append(
            "the Google check runs from inside a VPC network (internal checkers): create a "
            "New Relic private location there and add it; public locations cannot reach it"
        )
    chosen: list[str] = []
    for region in regions or ALL_REGIONS:
        found = REGIONS.get(region)
        if found is None:
            notes.append(f"the Google region {printable(region, 30)!r} has no mapped location")
            continue
        chosen += [f for f in found if f not in chosen]
    if not regions:
        notes.append("the Google check used all regions; all four groups of locations are listed")
    return chosen


def _uri(details: dict[str, Any], notes: list[str]) -> str:
    host = str(details.get("host", ""))
    if not host:
        notes.append("the host is not in the export (it was missing or not a host name)")
        return ""
    ssl = bool(details.get("useSsl"))
    port = details.get("port")
    shown = "" if port in (None, DEFAULT_PORTS[ssl]) else f":{port}"
    path = str(details.get("pathSafe", "")) or "/"
    path = path if path.startswith("/") else "/" + path
    return f"{'https' if ssl else 'http'}://{host}{shown}{path}"


def _secret(check_id: str, suffix: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Z0-9]", "_", check_id.upper())[:14].strip("_") or "CHECK"
    name = f"{SECRET_PREFIX}{base}_{suffix}"
    n = 1
    while name in used:
        n += 1
        name = f"{SECRET_PREFIX}{base}_{suffix}_{n}"
    used.add(name)
    return SecretRef(name=name).name


def plan_monitor(check: dict[str, Any], used: set[str]) -> MonitorSpec | None:
    """The plan for one extracted check; None for a check from an older export (no details)."""
    details = check.get("details")
    if not isinstance(details, dict):
        return None
    check_id = printable(str(details.get("id", "")), 100) or "unnamed"
    notes: list[str] = []
    name = safe_name(str(check.get("displayName", "")), check_id, 100)
    kind = str(check.get("kind", ""))
    spec = MonitorSpec(
        name=name,
        check_id=check_id,
        target="NONE",
        google_timeout=printable(str(details.get("timeout", "")), 20),
    )
    if kind != "http":
        spec.notes = [
            "New Relic Synthetics has no TCP monitor; use a scripted API monitor that opens a "
            "connection from Node, or keep the Google check"
            if kind == "tcp"
            else "this kind of check (group) has no direct counterpart"
        ]
        return spec
    method = str(details.get("method", "GET")).upper()
    matchers = [m for m in details.get("contentMatchers", []) if isinstance(m, dict)]
    secrets: list[SecretNeed] = []
    reasons: list[str] = []
    if details.get("auth"):
        reasons.append("basic authentication")
        secrets.append(
            SecretNeed(
                purpose="basic-auth password", secret_ref=_secret(check_id, "PASSWORD", used)
            )
        )
    for header in details.get("headerNames", []):
        reasons.append(f"the header {printable(str(header), 60)}")
        label = re.sub(r"[^A-Z0-9]", "_", str(header).upper())[:12].strip("_") or "H"
        secrets.append(
            SecretNeed(
                purpose=f"value of the {printable(str(header), 60)} header",
                secret_ref=_secret(check_id, f"HDR_{label}", used),
            )
        )
    if details.get("hasBody"):
        reasons.append("a request body")
        secrets.append(
            SecretNeed(purpose="request body", secret_ref=_secret(check_id, "BODY", used))
        )
    if method not in ("GET", "HEAD"):
        reasons.append(f"the {printable(method, 10)} method")
    simple_matcher = len(matchers) == 1 and matchers[0].get("matcher") == "CONTAINS_STRING"
    if matchers and not simple_matcher:
        reasons.append("content matchers other than one 'contains'")
    spec.target = "SCRIPT_API" if reasons else "SIMPLE"
    if reasons:
        notes.append(
            "a scripted API monitor, written by a person, is needed for: " + ", ".join(reasons)
        )
    elif simple_matcher:
        spec.response_validation_text = printable(str(matchers[0].get("content", "")), 500)
    spec.verify_ssl = bool(details.get("validateSsl")) if details.get("useSsl") else None
    spec.uri = _uri(details, notes)
    spec.frequency = _frequency(str(check.get("period", "")), notes)
    spec.locations = _locations(
        [str(r) for r in details.get("regions", [])], bool(details.get("internal")), notes
    )
    if spec.google_timeout:
        notes.append(
            f"Google timed out after {spec.google_timeout}; a simple monitor's timeout is not "
            "set in the monitor, and a scripted monitor sets it in the request options"
        )
    spec.secrets, spec.notes = secrets, notes
    return spec


def plan_all(checks: list[dict[str, Any]]) -> list[MonitorSpec]:
    used: set[str] = set()
    plans = (plan_monitor(c, used) for c in checks)
    return [p for p in plans if p is not None]
