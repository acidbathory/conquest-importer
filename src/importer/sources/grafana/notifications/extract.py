"""Read contact points, policies, mute timings, templates and ruler rules from Grafana, read-only.

Only GET, only the paths below, none with a query string, so a contact point is never read with
`decrypt` and Grafana only ever returns `[REDACTED]` for a stored secret. The host goes through
the same check as the alert rule extractor (`validate_host`) and the token comes from
GRAFANA_SA_TOKEN. Safeguards are those of `core/safe_http.py`: no redirects, a status of 300 or
more is an error, server text is scrubbed of the token, one response and the whole run have a
byte cap, and the run has a deadline. Nothing here is paged: every section is one response, and
the number of ruler calls is capped.

Each section is sanitised before anything is written: secrets are dropped at read time
(`contact_points.py`), templates keep a hash, data sources keep type and uid. A section that
cannot be read is a warning; the others are still written.
"""

import json
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core import safe_http
from importer.core.ids import MAX_INPUT_BYTES
from importer.sources.grafana.extract import (
    TOKEN_LIKE,
    Getter,
    GrafanaError,
    validate_host,
)
from importer.sources.grafana.notifications.loader import (
    FILE_CONTACT_POINTS,
    FILE_DATASOURCES,
    FILE_MUTE_TIMINGS,
    FILE_POLICIES,
    FILE_TEMPLATES,
    RULE_FILES,
    UID,
    read_contact_points,
    read_datasources,
    read_templates,
)
from importer.sources.grafana.notifications.models import DataSource
from importer.sources.grafana.notifications.payloads import (
    mute_timings_to_payload,
    policy_to_payload,
)
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.rulefiles import as_ruler_group, load_rule_groups
from importer.sources.grafana.notifications.timings import parse_mute_timings

SERVICE = "Grafana"
CONTACT_POINTS = "/api/v1/provisioning/contact-points"  # never with a query string
POLICIES = "/api/v1/provisioning/policies"
MUTE_TIMINGS = "/api/v1/provisioning/mute-timings"
TEMPLATES = "/api/v1/provisioning/templates"
DATASOURCES = "/api/datasources"
ALLOWED_PATHS = frozenset({CONTACT_POINTS, POLICIES, MUTE_TIMINGS, TEMPLATES, DATASOURCES})
RULER_PATH = re.compile(r"/api/ruler/([A-Za-z0-9_-]{1,40})/api/v1/rules")
RULER_TYPES = frozenset({"prometheus", "loki"})
MAX_RULER_SOURCES = 50


def ruler_path(uid: str) -> str:
    """The ruler path for a data source uid; refuses an uid that is not a plain identifier."""
    if not UID.fullmatch(uid):
        raise GrafanaError("this path is not on the allow-list of read-only calls")
    return f"/api/ruler/{uid}/api/v1/rules"


def _allowed(path: str) -> bool:
    return path in ALLOWED_PATHS or RULER_PATH.fullmatch(path) is not None


def _scrub(text: str, token: str) -> str:
    return TOKEN_LIKE.sub("***", safe_http.scrub(text, token))


def _decode(response: httpx.Response, token: str, budget: safe_http.ReadBudget) -> Any:
    body = safe_http.read_body(response, GrafanaError, SERVICE, budget)
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed, valid = None, False
    else:
        valid = True
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = _scrub(safe_http.error_message(parsed), token)
        raise GrafanaError(f"{SERVICE} returned {status}" + (f": {detail}" if detail else ""))
    if not valid:
        raise GrafanaError(f"{SERVICE} returned a body that is not JSON")
    return parsed


@contextmanager
def notifications_getter(
    token: str,
    base: str,
    transport: httpx.BaseTransport | None = None,
    allow_host: str | None = None,
) -> Iterator[Getter]:
    """A getter for the allow-listed paths; the connection pool closes when the block ends."""
    try:  # checked again here so no caller can send the token to a host that was not validated
        checked = validate_host(base, allow_host)
    except ValueError as exc:
        raise GrafanaError(str(exc)) from None
    if checked != base:
        raise GrafanaError("the base URL must be https://<host> exactly as validated")
    if not safe_http.is_plain_token(token):
        raise GrafanaError("the Grafana token must be plain ASCII text with no spaces")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    budget = safe_http.ReadBudget()
    with httpx.Client(
        timeout=60, headers=headers, transport=transport, follow_redirects=False
    ) as client:

        def get(path: str) -> Any:
            if not _allowed(path):
                raise GrafanaError("this path is not on the allow-list of read-only calls")
            budget.check_time(GrafanaError, SERVICE)
            try:
                with client.stream("GET", base + path) as response:
                    return _decode(response, token, budget)
            except httpx.HTTPError as exc:
                raise GrafanaError(f"could not reach Grafana: {type(exc).__name__}") from None

        yield get


@dataclass
class NotificationsResult:
    contact_points: int = 0
    mute_timings: int = 0
    templates: int = 0
    datasources: int = 0
    rule_groups: int = 0
    warnings: list[str] = field(default_factory=list)


def _write(folder: Path, name: str, data: object) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise GrafanaError(f"{name} is larger than the importer accepts")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)


def _contact_points(get: Getter, out: Path, result: NotificationsResult) -> None:
    found = read_contact_points(get(CONTACT_POINTS))
    result.contact_points = len(found)
    _write(out, FILE_CONTACT_POINTS, [c.model_dump(mode="json") for c in found])


def _policies(get: Getter, out: Path, _result: NotificationsResult) -> None:
    tree = parse_policy_tree(get(POLICIES))
    _write(out, FILE_POLICIES, policy_to_payload(tree.root))


def _mute_timings(get: Getter, out: Path, result: NotificationsResult) -> None:
    found = parse_mute_timings(get(MUTE_TIMINGS))
    result.mute_timings = len(found)
    _write(out, FILE_MUTE_TIMINGS, mute_timings_to_payload(found))


def _templates(get: Getter, out: Path, result: NotificationsResult) -> None:
    found = read_templates(get(TEMPLATES))
    result.templates = len(found)
    _write(out, FILE_TEMPLATES, [t.model_dump(mode="json", exclude={"text"}) for t in found])


SECTIONS: list[tuple[str, Callable[[Getter, Path, NotificationsResult], None]]] = [
    ("contact points", _contact_points),
    ("notification policies", _policies),
    ("mute timings", _mute_timings),
    ("templates", _templates),
]


def _rule_files(
    get: Getter, out: Path, sources: list[DataSource], result: NotificationsResult
) -> None:
    ruled = [d for d in sources if d.type in RULER_TYPES]
    if len(ruled) > MAX_RULER_SOURCES:
        result.warnings.append(
            f"rule files were read for {MAX_RULER_SOURCES} of {len(ruled)} data sources"
        )
    for source in ruled[:MAX_RULER_SOURCES]:
        try:
            groups = load_rule_groups(get(ruler_path(source.uid)), source.type)
        except (GrafanaError, ValueError):
            result.warnings.append(f"rule files of a {source.type} data source were NOT read")
            continue
        if skipped := sum(g.skipped for g in groups):
            result.warnings.append(
                f"{skipped} rules in rule files could not be read and were left out"
            )
        by_namespace: dict[str, list[dict[str, Any]]] = {}
        for group in groups:
            by_namespace.setdefault(group.namespace, []).append(as_ruler_group(group))
        result.rule_groups += len(groups)
        _write(out / RULE_FILES, f"{source.type}-{source.uid}.json", by_namespace)


def extract_notifications(get: Getter, out: Path) -> NotificationsResult:
    """Write the sanitised notification objects under `out` (a new or empty folder)."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = NotificationsResult()
    for label, section in SECTIONS:
        try:
            section(get, out, result)
        except (GrafanaError, ValueError) as exc:
            result.warnings.append(f"{label} were NOT read: {_scrub(str(exc), '')}")
    try:
        sources = read_datasources(get(DATASOURCES))
        result.datasources = len(sources)
        _write(out, FILE_DATASOURCES, [s.model_dump() for s in sources])
    except (GrafanaError, ValueError) as exc:
        sources = []
        result.warnings.append(f"data sources were NOT read: {_scrub(str(exc), '')}")
    _rule_files(get, out, sources, result)
    return result
