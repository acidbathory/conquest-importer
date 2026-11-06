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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.text import printable
from importer.sources.gcp.models import MAX_INPUT_BYTES

PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
API = "https://monitoring.googleapis.com/v3"
# key in the response -> path below projects/<id>
READ_PATHS = {
    "alertPolicies": "alertPolicies",
    "notificationChannels": "notificationChannels",
    "uptimeCheckConfigs": "uptimeCheckConfigs",
}
MAX_PAGES = 100

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class GcpError(Exception):
    """Reading from Google Cloud failed; the message is safe to show."""


def access_token() -> str:
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN", "").strip()
    return token or _gcloud_token()


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


def http_getter(token: str) -> Getter:
    client = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"})

    def get(url: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise GcpError(f"could not reach Google Cloud: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            try:
                message = str(response.json().get("error", {}).get("message", ""))
            except ValueError:
                message = ""
            raise GcpError(
                f"Google Cloud returned {response.status_code}: {printable(message, 300)}"
            )
        body = response.json()
        return body if isinstance(body, dict) else {}

    return get


def _list_all(get: Getter, project: str, key: str) -> list[dict[str, Any]]:
    url = f"{API}/projects/{project}/{READ_PATHS[key]}"
    items: list[dict[str, Any]] = []
    params: dict[str, str] = {"pageSize": "100"}
    for _ in range(MAX_PAGES):
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
    warnings: list[str] = field(default_factory=list)


def extract(get: Getter, project: str, out: Path) -> ExtractResult:
    """Write policies/, channels/ and uptime-checks/ under `out` (a new or empty folder)."""
    if not PROJECT_ID.match(project):
        raise ValueError("--project must be a Google Cloud project id such as my-project-123")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult(project)
    policies = _list_all(get, project, "alertPolicies")
    result.policies = len(policies)
    result.conditions = sum(len(p.get("conditions", [])) for p in policies)
    optional: dict[str, list[dict[str, Any]]] = {}
    for key, summarise in (
        ("notificationChannels", _channel_summary),
        ("uptimeCheckConfigs", _uptime_summary),
    ):
        try:
            optional[key] = [summarise(i) for i in _list_all(get, project, key)]
        except GcpError as exc:
            result.warnings.append(f"{key}: {exc}")
            optional[key] = []
    result.channels = len(optional["notificationChannels"])
    result.uptime_checks = len(optional["uptimeCheckConfigs"])
    for folder, name, data in (
        ("policies", "policies.json", policies),
        ("channels", "channels.json", optional["notificationChannels"]),
        ("uptime-checks", "uptime-checks.json", optional["uptimeCheckConfigs"]),
    ):
        text = json.dumps(data, indent=2) + "\n"
        if len(text) > MAX_INPUT_BYTES:
            raise GcpError(f"{name} is larger than the converter accepts")
        (out / folder).mkdir(parents=True, exist_ok=True)
        (out / folder / name).write_text(text)
    return result
