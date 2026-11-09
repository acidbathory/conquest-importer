"""Read classic and Davis alerting objects from a Dynatrace tenant, read-only.

Only GET requests for the schema ids in `models.READ_SCHEMAS` can run, against a host that matched
`ENV_URL`, so no user text becomes a host, a path or a schema id. The token comes from the
environment (DYNATRACE_API_TOKEN) and is sent as a bearer token to a Platform host
(`*.apps.dynatrace.com`) or as an API token to a classic SaaS host (`*.live.dynatrace.com`); this
code never stores or prints it. Redirects are not followed, a status of 300 or more is an error,
responses and the whole run are size and time capped, and every loop is bounded (see
core/safe_http.py).

On a Platform host the objects are read from the classic Environment API proxy first and from the
Platform settings service when the proxy answers 404 for that schema. The fallback has not been
tried against a tenant. Notification objects are reduced and detector actor ids are dropped before
anything is written, so the export holds no secret, URL or address.
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

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.safe_http import ReadBudget, get_json, is_plain_token
from importer.sources.dynatrace.classic import models

PLATFORM_HOST = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
# Classic SaaS tenants (and hybrid ones) keep the same Settings objects behind the Environment
# API on their own host, with an API token. The pattern is as strict as the Platform one.
CLASSIC_HOST = re.compile(r"https://[a-z0-9]+\.live\.dynatrace\.com")
PRIMARY_PATH = "/platform/classic/environment-api/v2/settings/objects"
FALLBACK_PATH = "/platform/settings/v1/objects"
CLASSIC_PATH = "/api/v2/settings/objects"
PAGE_SIZE = "500"
FIELDS = "objectId,schemaId,scope,value"
MAX_PAGES = 100  # per schema
MAX_TOTAL_PAGES = 400  # across every schema in one run
MAX_OBJECTS = 20_000  # per schema
SERVICE = "Dynatrace"
NOT_FOUND = re.compile(r"returned 404")
FALLBACK_WARNING = (
    "some schemas were read through /platform/settings/v1, which was asked for scope "
    "environment only; entity-scoped overrides (one host or service) are not in the export"
)

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class ClassicError(Exception):
    """Reading from Dynatrace failed; the message is safe to show."""


def is_env_url(url: str) -> bool:
    """True for https://<tenant id>.apps.dynatrace.com or .live.dynatrace.com, no path."""
    host = url.removesuffix("/")
    return PLATFORM_HOST.fullmatch(host) is not None or CLASSIC_HOST.fullmatch(host) is not None


def api_token() -> str:
    token = os.environ.get("DYNATRACE_API_TOKEN", "").strip()
    if not token:
        raise ClassicError(
            "DYNATRACE_API_TOKEN is not set. Create a platform token with the scopes "
            "settings:objects:read and settings:schemas:read (or an API token with settings.read "
            "for a classic host) and put it in .env"
        )
    if not is_plain_token(token):
        raise ClassicError(
            "DYNATRACE_API_TOKEN must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token


def environment_url() -> str:
    return os.environ.get("DYNATRACE_ENV_URL", "").strip()


def _check_url(env_url: str) -> str:
    host = env_url.removesuffix("/")
    if not is_env_url(host):
        raise ValueError(
            "DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com "
            "(or https://<tenant id>.live.dynatrace.com for a classic tenant)"
        )
    return host


@contextmanager
def http_getter(
    token: str, env_url: str, transport: httpx.BaseTransport | None = None
) -> Iterator[Getter]:
    """A Getter for one tenant that reuses a connection pool and closes it when the block ends.

    It refuses any URL that does not start with the tenant host. `transport` is for tests.
    """
    host = _check_url(env_url)
    scheme = "Api-Token" if CLASSIC_HOST.fullmatch(host) else "Bearer"
    budget = ReadBudget()
    with httpx.Client(
        timeout=60,
        headers={"Authorization": f"{scheme} {token}"},
        transport=transport,
        follow_redirects=False,
    ) as client:

        def get(url: str, params: dict[str, str]) -> dict[str, Any]:
            if not url.startswith(host + "/"):
                raise ClassicError("refused a request to a host that is not the configured tenant")
            return get_json(client, url, params, token, ClassicError, SERVICE, budget)

        yield get


class PageBudget:
    """Pages left for the whole run, shared by every schema so the total is bounded."""

    def __init__(self, pages: int) -> None:
        self.left = pages

    def spend(self) -> None:
        if self.left <= 0:
            raise ClassicError(f"more than {MAX_TOTAL_PAGES} pages in total; stopped")
        self.left -= 1


@dataclass
class ExtractResult:
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _first_params(schema: str, *, platform_style: bool) -> dict[str, str]:
    if platform_style:
        return {
            "schema-id": schema,
            "scope": "environment",
            "add-fields": "value",
            "page-size": PAGE_SIZE,
        }
    return {"schemaIds": schema, "fields": FIELDS, "pageSize": PAGE_SIZE}


def _list_schema(
    get: Getter, url: str, schema: str, pages: PageBudget, *, platform_style: bool
) -> list[dict[str, Any]]:
    """Every object of one schema. A follow-up request carries only the page key."""
    items: list[dict[str, Any]] = []
    params = _first_params(schema, platform_style=platform_style)
    key_name = "page-key" if platform_style else "nextPageKey"
    for _ in range(MAX_PAGES):
        pages.spend()
        page = get(url, params)
        found = page.get("items")
        if not isinstance(found, list):
            raise ClassicError(f"{SERVICE} returned a response with no `items` list")
        items += [i for i in found if isinstance(i, dict)]
        if len(items) > MAX_OBJECTS:
            raise ClassicError(f"more than {MAX_OBJECTS} objects of one schema; stopped")
        key = page.get("nextPageKey")
        if not isinstance(key, str) or not key:
            return items
        params = {key_name: key}
    raise ClassicError(f"more than {MAX_PAGES} pages of one schema; stopped")


def _minimise(item: dict[str, Any]) -> dict[str, Any]:
    """The object as it is written: wrapper keys only, with secrets and actor ids dropped."""
    obj = models.parse_object(item)
    raw = item.get("value")
    value: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    if obj.schemaId == models.NOTIFICATIONS:
        value = models.reduce_notification(value)
    if obj.schemaId == models.DETECTORS and isinstance(value.get("executionSettings"), dict):
        value["executionSettings"] = {
            k: v for k, v in value["executionSettings"].items() if k != "actor"
        }
    return {"objectId": obj.objectId, "schemaId": obj.schemaId, "scope": obj.scope, "value": value}


def _read_schema(
    get: Getter, host: str, schema: str, pages: PageBudget, result: ExtractResult
) -> list[dict[str, Any]] | None:
    """The objects of one schema, or None when no path knows it (a 404 on every path)."""
    if CLASSIC_HOST.fullmatch(host):
        paths = [(CLASSIC_PATH, False)]
    else:
        paths = [(PRIMARY_PATH, False), (FALLBACK_PATH, True)]
    for path, platform_style in paths:
        try:
            items = _list_schema(get, host + path, schema, pages, platform_style=platform_style)
        except ClassicError as exc:
            if NOT_FOUND.search(str(exc)):
                continue
            raise
        if platform_style and FALLBACK_WARNING not in result.warnings:
            result.warnings.append(FALLBACK_WARNING)
        return items
    return None


def _write(out: Path, schema: str, items: list[dict[str, Any]]) -> None:
    text = json.dumps({"schemaId": schema, "items": items}, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise ClassicError(f"the objects of {schema} are larger than the converter accepts")
    folder = out / "settings"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (schema.replace(":", "_") + ".json")).write_text(text)


def extract(get: Getter, env_url: str, out: Path) -> ExtractResult:
    """Write settings/<schema>.json under `out` (a new or empty folder)."""
    host = _check_url(env_url)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult()
    pages = PageBudget(MAX_TOTAL_PAGES)
    for schema in models.READ_SCHEMAS:
        items = _read_schema(get, host, schema, pages, result)
        if items is None:
            result.warnings.append(
                f"{schema}: returned 404 on every settings path; the tenant has none of these "
                "objects or the token cannot see them"
            )
            continue
        result.counts[schema] = len(items)
        _write(out, schema, [_minimise(i) for i in items])
    return result
