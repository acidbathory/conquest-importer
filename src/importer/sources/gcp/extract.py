"""Read alert policies and inventory from a Google Cloud project, read-only.

Only the GET endpoints in READ_PATHS can run, with a project id that matched PROJECT_ID, so no
user text becomes a URL path segment or an operation name. The access token comes from the
environment (GOOGLE_OAUTH_ACCESS_TOKEN) or from `gcloud auth print-access-token`; this code never
stores it. The export holds the same JSON `convert --source gcp` reads. Notification channels are
written as type and name only, because their labels hold email addresses and webhook URLs.

Exports can contain project ids and resource names: review them before sharing.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.safe_http import get_json, is_plain_token

PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
API = "https://monitoring.googleapis.com"
# the dashboards API is v1; everything else here is v3
API_VERSIONS = {"dashboards": "v1"}
DEFAULT_VERSION = "v3"
# key in the response -> path below projects/<id>
READ_PATHS = {
    "alertPolicies": "alertPolicies",
    "notificationChannels": "notificationChannels",
    "uptimeCheckConfigs": "uptimeCheckConfigs",
    "dashboards": "dashboards",
}
MAX_PAGES = 100
MAX_TOTAL_PAGES = 200  # across every list in one run
SERVICE = "Google Cloud"

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class GcpError(Exception):
    """Reading from Google Cloud failed; the message is safe to show."""


def access_token() -> str:
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip() or _gcloud_token()
    if not is_plain_token(token):
        raise GcpError(
            "the Google access token must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token


def _gcloud_token() -> str:
    exe = shutil.which("gcloud")
    if exe is None:
        raise GcpError(
            "no Google credentials found. Set GOOGLE_OAUTH_ACCESS_TOKEN or install gcloud and "
            "run `gcloud auth login`"
        )
    try:
        done = subprocess.run(  # noqa: S603 - fixed arguments, no user text
            [exe, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise GcpError("`gcloud auth print-access-token` failed; run `gcloud auth login`") from exc
    return done.stdout.strip()


@contextmanager
def http_getter(token: str, transport: httpx.BaseTransport | None = None) -> Iterator[Getter]:
    """A Getter that reuses one connection pool and closes it when the block ends.

    Redirects are not followed, a status of 300 or more is an error, and server text is scrubbed
    (see core/safe_http.py). `transport` is for tests.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=60, headers=headers, transport=transport) as client:

        def get(url: str, params: dict[str, str]) -> dict[str, Any]:
            return get_json(client, url, params, token, GcpError, SERVICE)

        yield get


class PageBudget:
    """Pages left for the whole run, shared by every list so the total is bounded."""

    def __init__(self, pages: int) -> None:
        self.left = pages

    def spend(self) -> None:
        if self.left <= 0:
            raise GcpError(f"more than {MAX_TOTAL_PAGES} pages in total; stopped")
        self.left -= 1


def _list_all(get: Getter, project: str, key: str, budget: PageBudget) -> list[dict[str, Any]]:
    version = API_VERSIONS.get(key, DEFAULT_VERSION)
    url = f"{API}/{version}/projects/{project}/{READ_PATHS[key]}"
    items: list[dict[str, Any]] = []
    params: dict[str, str] = {"pageSize": "100"}
    for _ in range(MAX_PAGES):
        budget.spend()
        page = get(url, params)
        found = page.get(key, [])
        if isinstance(found, list):
            items += [i for i in found if isinstance(i, dict)]
        token = page.get("nextPageToken")
        if not isinstance(token, str) or not token:
            return items
        params = {"pageSize": "100", "pageToken": token}
    raise GcpError(f"more than {MAX_PAGES} pages of {key}; stopped")


def _channel_summary(channel: dict[str, Any]) -> dict[str, Any]:
    """Keep what is needed to plan destinations; drop labels (addresses, URLs) and secrets."""
    return {
        "name": channel.get("name", ""),
        "type": channel.get("type", ""),
        "displayName": channel.get("displayName", ""),
        "enabled": channel.get("enabled", True),
    }


def _uptime_summary(check: dict[str, Any]) -> dict[str, Any]:
    http = check.get("httpCheck") if isinstance(check.get("httpCheck"), dict) else {}
    return {
        "name": check.get("name", ""),
        "displayName": check.get("displayName", ""),
        "period": check.get("period", ""),
        "kind": "http" if http else ("tcp" if "tcpCheck" in check else "other"),
        "path": http.get("path", "") if http else "",
        "monitoredResource": check.get("monitoredResource", {}).get("type", "")
        if isinstance(check.get("monitoredResource"), dict)
        else "",
    }


@dataclass
class ExtractResult:
    project: str
    policies: int = 0
    conditions: int = 0
    channels: int = 0
    uptime_checks: int = 0
    dashboards: int = 0
    warnings: list[str] = field(default_factory=list)


def extract(get: Getter, project: str, out: Path) -> ExtractResult:
    """Write policies/, channels/, uptime-checks/ and dashboards/ under a new or empty `out`."""
    if not PROJECT_ID.match(project):
        raise ValueError("--project must be a Google Cloud project id such as my-project-123")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult(project)
    budget = PageBudget(MAX_TOTAL_PAGES)
    policies = _list_all(get, project, "alertPolicies", budget)
    result.policies = len(policies)
    result.conditions = sum(len(p.get("conditions", [])) for p in policies)
    optional: dict[str, list[dict[str, Any]]] = {}
    for key, summarise in (
        ("notificationChannels", _channel_summary),
        ("uptimeCheckConfigs", _uptime_summary),
        ("dashboards", dict),  # layout and queries only, no secrets: kept whole
    ):
        try:
            optional[key] = [summarise(i) for i in _list_all(get, project, key, budget)]
        except GcpError as exc:
            result.warnings.append(f"{key}: {exc}")
            optional[key] = []
    result.channels = len(optional["notificationChannels"])
    result.uptime_checks = len(optional["uptimeCheckConfigs"])
    result.dashboards = len(optional["dashboards"])
    for folder, name, data in (
        ("policies", "policies.json", policies),
        ("channels", "channels.json", optional["notificationChannels"]),
        ("uptime-checks", "uptime-checks.json", optional["uptimeCheckConfigs"]),
        ("dashboards", "dashboards.json", optional["dashboards"]),
    ):
        text = json.dumps(data, indent=2) + "\n"
        if len(text) > MAX_INPUT_BYTES:
            raise GcpError(f"{name} is larger than the converter accepts")
        (out / folder).mkdir(parents=True, exist_ok=True)
        (out / folder / name).write_text(text)
    return result
