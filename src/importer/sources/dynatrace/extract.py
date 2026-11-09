"""Read workflows and SLOs from a Dynatrace Platform tenant, read-only.

Only the GET endpoints below can run, against a host that matched ENV_URL, so no user text
becomes a host or an operation. The platform token comes from the environment
(DYNATRACE_API_TOKEN) and is sent as a bearer token; this code never stores or prints it. The
export holds the same JSON `convert --source dynatrace` reads. Workflow tasks can hold email
addresses and channel names: review an export before sharing it.

The paging parameters and response keys follow the Dynatrace documentation and have not been
tried against a tenant that has workflows or SLOs (the tenant used so far had none).
"""

import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.text import printable
from importer.sources.dynatrace.models import MAX_INPUT_BYTES

ENV_URL = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
WORKFLOW_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
WORKFLOWS_PATH = "/platform/automation/v1/workflows"
SLOS_PATH = "/platform/slo/v1/slos"
PAGE_SIZE = 100
MAX_PAGES = 100
MAX_ITEMS = MAX_PAGES * PAGE_SIZE

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class DynatraceError(Exception):
    """Reading from Dynatrace failed; the message is safe to show."""


def api_token() -> str:
    token = os.environ.get("DYNATRACE_API_TOKEN", "").strip()
    if not token:
        raise DynatraceError(
            "DYNATRACE_API_TOKEN is not set. Create a platform token with the scopes "
            "automation:workflows:read and slo:slos:read and put it in .env"
        )
    return token


def environment_url() -> str:
    return os.environ.get("DYNATRACE_ENV_URL", "").strip()


def _message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        return str(error.get("message", "")) if isinstance(error, dict) else ""
    except ValueError:
        return ""


@contextmanager
def http_getter(token: str) -> Iterator[Getter]:
    """A Getter that reuses one connection pool and closes it when the block ends.

    httpx reads SSL_CERT_FILE, so a corporate root certificate can be supplied that way.
    Redirects are not followed, so the token cannot be sent to another host.
    """
    with httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"}) as client:

        def get(url: str, params: dict[str, str]) -> dict[str, Any]:
            try:
                response = client.get(url, params=params)
            except httpx.HTTPError as exc:
                raise DynatraceError(f"could not reach Dynatrace: {type(exc).__name__}") from exc
            if response.status_code >= 400:
                raise DynatraceError(
                    f"Dynatrace returned {response.status_code}: "
                    f"{printable(_message(response), 300)}"
                )
            try:
                body = response.json()
            except ValueError as exc:
                raise DynatraceError("Dynatrace returned a body that is not JSON") from exc
            return body if isinstance(body, dict) else {}

        yield get


def _dicts(value: object) -> list[dict[str, Any]]:
    return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []


def _list_workflows(get: Getter, host: str) -> list[dict[str, Any]]:
    """Page with limit and offset until `count` is reached or a short page ends the list."""
    items: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        page = get(host + WORKFLOWS_PATH, {"limit": str(PAGE_SIZE), "offset": str(len(items))})
        found = _dicts(page.get("results"))
        items += found
        count = page.get("count")
        done = len(items) >= count if isinstance(count, int) else len(found) < PAGE_SIZE
        if done or not found:
            return items
    raise DynatraceError(f"more than {MAX_PAGES} pages of workflows; stopped")


def _list_slos(get: Getter, host: str) -> list[dict[str, Any]]:
    """Page with page-size, then page-key (a follow-up request carries only the key)."""
    items: list[dict[str, Any]] = []
    params = {"page-size": str(PAGE_SIZE)}
    for _ in range(MAX_PAGES):
        page = get(host + SLOS_PATH, params)
        items += _dicts(page.get("slos"))
        key = page.get("nextPageKey")
        if not isinstance(key, str) or not key:
            return items
        params = {"page-key": key}
    raise DynatraceError(f"more than {MAX_PAGES} pages of SLOs; stopped")


def _complete(get: Getter, host: str, workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch the full workflow when a list entry has no tasks (a summary)."""
    full: list[dict[str, Any]] = []
    for item in workflows[:MAX_ITEMS]:
        identifier = item.get("id")
        if (
            "tasks" in item
            or not isinstance(identifier, str)
            or not WORKFLOW_ID.fullmatch(identifier)
        ):
            full.append(item)
        else:
            full.append(get(f"{host}{WORKFLOWS_PATH}/{identifier}", {}))
    return full


@dataclass
class ExtractResult:
    workflows: int = 0
    slos: int = 0
    warnings: list[str] = field(default_factory=list)


def _write(folder: Path, name: str, data: list[dict[str, Any]]) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise DynatraceError(f"{name} is larger than the converter accepts")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)


def extract(get: Getter, env_url: str, out: Path) -> ExtractResult:
    """Write workflows/ and slos/ under `out` (a new or empty folder)."""
    host = env_url.removesuffix("/")
    if not ENV_URL.fullmatch(host):
        raise ValueError("DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    workflows = _complete(get, host, _list_workflows(get, host))
    result = ExtractResult(workflows=len(workflows))
    try:
        slos = _list_slos(get, host)
    except DynatraceError as exc:
        result.warnings.append(f"SLOs: {exc}")
        slos = []
    result.slos = len(slos)
    _write(out / "workflows", "workflows.json", workflows)
    _write(out / "slos", "slos.json", slos)
    return result
