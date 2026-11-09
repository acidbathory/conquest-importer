"""Read workflows and SLOs from a Dynatrace Platform tenant, read-only.

Only the GET endpoints below can run, against a host that matched ENV_URL, so no user text
becomes a host or an operation. The platform token comes from the environment
(DYNATRACE_API_TOKEN) and is sent as a bearer token; this code never stores or prints it, and
text that Dynatrace sends back is scrubbed of it before it reaches a message. Redirects are not
followed, responses are size-capped, and every loop and the whole run have a bound. The export
holds the same JSON `convert --source dynatrace` reads. Workflow tasks can hold email addresses
and channel names: review an export before sharing it.

The paging parameters and response keys follow the Dynatrace documentation and have not been
tried against a tenant that has workflows or SLOs (the tenant used so far had none).
"""

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable

ENV_URL = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
WORKFLOW_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
TOKEN_LIKE = re.compile(r"dt0[a-z][0-9]{2}\.[A-Za-z0-9._-]+")
WORKFLOWS_PATH = "/platform/automation/v1/workflows"
SLOS_PATH = "/platform/slo/v1/slos"
PAGE_SIZE = 100
MAX_PAGES = 100
MAX_WORKFLOWS = 2000  # a tenant with more is read in part only after the limit is raised
MAX_DETAIL_FETCHES = 500  # single-workflow reads made to complete list entries
DEADLINE_SECONDS = 300  # for completing list entries, all together
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_MESSAGE = 300

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class DynatraceError(Exception):
    """Reading from Dynatrace failed; the message is safe to show."""


def is_plain_token(token: str) -> bool:
    """True for non-empty ASCII text with no spaces or control characters."""
    return bool(token) and token.isascii() and token.isprintable() and " " not in token


def is_env_url(url: str) -> bool:
    """True for https://<tenant id>.apps.dynatrace.com, with or without a trailing slash."""
    return ENV_URL.fullmatch(url.removesuffix("/")) is not None


def api_token() -> str:
    token = os.environ.get("DYNATRACE_API_TOKEN", "").strip()
    if not token:
        raise DynatraceError(
            "DYNATRACE_API_TOKEN is not set. Create a platform token with the scopes "
            "automation:workflows:read and slo:slos:read and put it in .env"
        )
    if not is_plain_token(token):
        raise DynatraceError(
            "DYNATRACE_API_TOKEN must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token


def environment_url() -> str:
    return os.environ.get("DYNATRACE_ENV_URL", "").strip()


def scrub(text: str, token: str) -> str:
    """Server text that is safe to show: no token, no bearer value, printable, capped."""
    if token:
        text = text.replace(token, "***")
    return printable(TOKEN_LIKE.sub("***", BEARER.sub("Bearer ***", text)), MAX_MESSAGE)


def _message(body: object) -> str:
    """The error message in a response body, or "" when the body has another shape."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    found = error.get("message") if isinstance(error, dict) else error or body.get("message")
    return found if isinstance(found, str) else ""


def _read(response: httpx.Response) -> bytes:
    """The body, refusing one larger than MAX_RESPONSE_BYTES (header or actual size)."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
        raise DynatraceError("Dynatrace sent a response larger than the importer accepts")
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise DynatraceError("Dynatrace sent a response larger than the importer accepts")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(response: httpx.Response, token: str) -> dict[str, Any]:
    body = _read(response)
    valid = True
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed, valid = None, False
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = scrub(_message(parsed), token) if status >= 400 else ""
        raise DynatraceError(f"Dynatrace returned {status}" + (f": {detail}" if detail else ""))
    if not valid:
        raise DynatraceError("Dynatrace returned a body that is not JSON")
    return parsed if isinstance(parsed, dict) else {}


@contextmanager
def http_getter(token: str, transport: httpx.BaseTransport | None = None) -> Iterator[Getter]:
    """A Getter that reuses one connection pool and closes it when the block ends.

    httpx reads SSL_CERT_FILE, so a corporate root certificate can be supplied that way.
    Redirects are not followed, so the token cannot be sent to another host. `transport` is for
    tests.
    """
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=60, headers=headers, transport=transport) as client:

        def get(url: str, params: dict[str, str]) -> dict[str, Any]:
            try:
                with client.stream("GET", url, params=params) as response:
                    return _decode(response, token)
            except httpx.HTTPError as exc:
                raise DynatraceError(f"could not reach Dynatrace: {type(exc).__name__}") from None

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
        if len(items) > MAX_WORKFLOWS:
            raise DynatraceError(f"more than {MAX_WORKFLOWS} workflows; stopped")
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


def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop list entries whose id was already seen (a page shifted while paging)."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for item in items:
        identifier = item.get("id")
        if isinstance(identifier, str):
            if identifier in seen:
                continue
            seen.add(identifier)
        kept.append(item)
    return kept


def _complete(
    get: Getter, host: str, workflows: list[dict[str, Any]], warnings: list[str]
) -> list[dict[str, Any]]:
    """Fetch the full workflow when a list entry has no tasks (a summary); drop what is unusable."""
    full: list[dict[str, Any]] = []
    fetches = dropped = 0
    started = time.monotonic()
    for item in workflows:
        if "tasks" in item:
            full.append(item)
            continue
        identifier = item.get("id")
        out_of_budget = (
            fetches >= MAX_DETAIL_FETCHES or time.monotonic() - started > DEADLINE_SECONDS
        )
        if (
            not isinstance(identifier, str)
            or not WORKFLOW_ID.fullmatch(identifier)
            or out_of_budget
        ):
            dropped += 1
        else:
            fetches += 1
            detail = get(f"{host}{WORKFLOWS_PATH}/{identifier}", {})
            if "tasks" in detail:
                full.append(detail)
            else:
                dropped += 1
    if dropped:
        warnings.append(
            f"{dropped} workflows were left out because their details could not be read "
            "(no usable id, an unexpected response, or the read limit was reached)"
        )
    return full


@dataclass
class ExtractResult:
    workflows: int = 0
    slos: int = 0
    slos_read: bool = True
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
    if not is_env_url(host):
        raise ValueError("DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult()
    listed = _unique(_list_workflows(get, host))
    workflows = _complete(get, host, listed, result.warnings)
    result.workflows = len(workflows)
    try:
        slos = _unique(_list_slos(get, host))
    except DynatraceError as exc:
        result.slos_read = False
        result.warnings.append(f"SLOs were NOT read: {exc}")
        slos = []
    result.slos = len(slos)
    _write(out / "workflows", "workflows.json", workflows)
    if result.slos_read:
        _write(out / "slos", "slos.json", slos)
    return result
