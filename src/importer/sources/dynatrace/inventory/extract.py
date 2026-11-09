"""Read dashboards, synthetic monitors and an entity inventory from a Dynatrace tenant, read-only.

Only GET requests to the paths in READ_PATHS can run, against a host that matched
`https://<tenant id>.apps.dynatrace.com`. Ids that go into a path are checked against a pattern
first, so no text from a response becomes a host or an operation. The token comes from
DYNATRACE_API_TOKEN, is sent as a bearer token and is never stored or printed. The shared
safeguards in core/safe_http.py apply: no redirects, a status of 300 or more is an error, server
text is scrubbed of the token, each response and the whole run have a byte cap, and the run has
a deadline. Every list has a page cap and the run has a total page cap.

What is written (review before sharing; free text is reduced, not proven clean):
- dashboards/: one envelope per Platform dashboard document (name, app, content). The owner
  (a user id) is dropped.
- classic-dashboards/: classic dashboards, with the owner address dropped.
- synthetics/: monitors with the script reduced to its structure (URLs lose user info, query
  and fragment; request bodies, header and cookie values, scripts and typed text become
  "<omitted>") and the locations.
- entities/: for each entity only its type, technologies and operating system. No name, id or
  address is kept.

Endpoints and how sure we are (research note docs/research/dynatrace-dashboards-inventory-
datapath.md):
- documents list (`type in ('dashboard')`, `page-key`): DOCUMENTED (D4); the response keys
  `documents` and `nextPageKey` are UNVERIFIED.
- document content, `.../documents/<id>/content`: UNVERIFIED path and response shape (the note
  lists it as an open question). The response is stored as is, or its `content` object.
- classic dashboards, `/api/config/v1/dashboards`: DOCUMENTED on the classic API (D5), but
  whether a platform token can call it on the `apps` host is UNVERIFIED.
- synthetic monitors and locations, `/api/v1/synthetic/...`: DOCUMENTED on the classic API
  (D12, D14, D16); the same host question applies. UNVERIFIED on the `apps` host.
- entities, `/api/v2/entities`: DOCUMENTED (D19, D20), but the page carries a deprecation
  banner and the token needs `entities.read`; UNVERIFIED on the `apps` host.
- Smartscape counts: NOT read. They need `POST /platform/storage/query/v1/query:execute`, and
  this extractor makes GET requests only. An owner decision.

An area that cannot be read is a warning, never a silent zero, and the other areas still run.
"""

import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.redact import safe_name, safe_path
from importer.core.safe_http import ReadBudget, get_json, is_plain_token
from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory.models import dicts

ENV_URL = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MONITOR_ID = re.compile(r"[A-Z_]+-[0-9A-F]{16}")
SERVICE = "Dynatrace"
OMITTED = "<omitted>"

# name -> path below the host; "{id}" is replaced by an id that matched ID_PATTERN
READ_PATHS = {
    "documents": "/platform/document/v1/documents",
    "document_content": "/platform/document/v1/documents/{id}/content",
    "classic_dashboards": "/api/config/v1/dashboards",
    "classic_dashboard": "/api/config/v1/dashboards/{id}",
    "monitors": "/api/v1/synthetic/monitors",
    "monitor": "/api/v1/synthetic/monitors/{id}",
    "locations": "/api/v1/synthetic/locations",
    "entities": "/api/v2/entities",
}
ID_PATTERN = {
    "document_content": UUID,
    "classic_dashboard": UUID,
    "monitor": MONITOR_ID,
}
ENTITY_TYPES = {  # type -> extra field to ask for
    "HOST": "+properties.osType",
    "SERVICE": "+properties.serviceTechnologyTypes",
    "PROCESS_GROUP": "",
    "KUBERNETES_CLUSTER": "",
    "APPLICATION": "",
    "MOBILE_APPLICATION": "",
    "AWS_LAMBDA_FUNCTION": "",
}
PAGE_SIZE = 100
ENTITY_PAGE_SIZE = 500
MAX_PAGES = 100
MAX_TOTAL_PAGES = 400  # list pages across every area of one run
MAX_DETAIL_FETCHES = 1000  # single-object reads per area
MAX_LIST_ITEMS = 200  # items kept from a list inside a script
MAX_SCRIPT_DEPTH = 20

# (url, params) -> response body as plain JSON
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


def inventory_token() -> str:
    token = os.environ.get("DYNATRACE_API_TOKEN", "").strip()
    if not token:
        raise DynatraceError(
            "DYNATRACE_API_TOKEN is not set. Create a token that can read documents "
            "(document:documents:read), synthetic monitors, classic dashboards (ReadConfig) "
            "and entities (entities.read), and put it in .env"
        )
    if not is_plain_token(token):
        raise DynatraceError(
            "DYNATRACE_API_TOKEN must be plain ASCII text with no spaces; check that it was "
            "pasted without a smart quote, a dash or a line break"
        )
    return token


def environment_url() -> str:
    return os.environ.get("DYNATRACE_ENV_URL", "").strip()


def is_platform_host(url: str) -> bool:
    return ENV_URL.fullmatch(url.removesuffix("/")) is not None


@contextmanager
def http_getter(token: str, transport: httpx.BaseTransport | None = None) -> Iterator[Getter]:
    """A Getter that reuses one connection pool and closes it when the block ends.

    Redirects are not followed, so the token cannot be sent to another host. `transport` is for
    tests.
    """
    budget = ReadBudget()
    with httpx.Client(
        timeout=60,
        headers={"Authorization": f"Bearer {token}"},
        transport=transport,
        follow_redirects=False,
    ) as client:

        def get(url: str, params: dict[str, str]) -> dict[str, Any]:
            return get_json(client, url, params, token, DynatraceError, SERVICE, budget)

        yield get


class PageBudget:
    """List pages left for the whole run, shared by every list."""

    def __init__(self) -> None:
        self.left = MAX_TOTAL_PAGES

    def spend(self) -> None:
        if self.left <= 0:
            raise DynatraceError(f"more than {MAX_TOTAL_PAGES} pages in total; stopped")
        self.left -= 1


@dataclass
class ExtractResult:
    dashboards: int = 0
    classic_dashboards: int = 0
    monitors: int = 0
    locations: int = 0
    entities: int = 0
    areas_read: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def read_nothing(self) -> bool:
        return self.areas_read == 0


def _url(host: str, name: str, ident: str = "") -> str:
    """The URL of an allow-listed path; an id must match its pattern or this raises."""
    pattern = ID_PATTERN.get(name)
    if pattern is not None and not pattern.fullmatch(ident):
        raise DynatraceError("an id from Dynatrace did not have the expected form")
    return host + READ_PATHS[name].replace("{id}", ident)


def _list_pages(
    get: Getter, url: str, first: dict[str, str], label: str, budget: PageBudget, cursor: str
) -> Iterator[dict[str, Any]]:
    """Yield each page. `nextPageKey` in a response comes back as request parameter `cursor`,
    and a follow-up request carries only that parameter."""
    params = first
    for _ in range(MAX_PAGES):
        budget.spend()
        page = get(url, params)
        yield page
        nxt = page.get("nextPageKey")
        if not isinstance(nxt, str) or not nxt:
            return
        params = {cursor: nxt}
    raise DynatraceError(f"more than {MAX_PAGES} pages of {label}; stopped")


class _Partial(DynatraceError):
    """Some of an area was read; `count` says how much. The message goes to the warnings."""

    def __init__(self, count: int, message: str) -> None:
        super().__init__(message)
        self.count = count


def _clean_url(url: str) -> str:
    """scheme://host[:port]/path without user info, query or fragment."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return OMITTED
    if not parts.scheme or not host:
        return OMITTED
    return f"{parts.scheme}://{host}{port}{safe_path(parts.path)}"


OMIT_KEYS = frozenset(
    {
        "requestBody",
        "preProcessingScript",
        "postProcessingScript",
        "javaScript",
        "textValue",
        "password",
        "credentials",
    }
)
MASKED_LISTS = frozenset({"requestHeaders", "cookies"})
URL_KEYS = frozenset({"url", "uri"})


def clean_script(key: str, value: Any, depth: int = 0) -> Any:
    """The script's structure without bodies, header and cookie values, code or typed text."""
    if depth > MAX_SCRIPT_DEPTH:
        return OMITTED
    if key in OMIT_KEYS and value not in (None, "", [], {}):
        return OMITTED
    if key in URL_KEYS and isinstance(value, str):
        return _clean_url(value)
    if key in MASKED_LISTS and isinstance(value, list):
        return [
            {**item, "value": OMITTED} if isinstance(item, dict) else OMITTED
            for item in value[:MAX_LIST_ITEMS]
        ]
    if isinstance(value, dict):
        return {str(k): clean_script(str(k), v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_script("", v, depth + 1) for v in value[:MAX_LIST_ITEMS]]
    return value


def _write(path: Path, data: object) -> None:
    text = json.dumps(data, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise DynatraceError(f"{path.name} is larger than the planner accepts")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _ids(items: list[dict[str, Any]], key: str, pattern: re.Pattern[str]) -> tuple[list[str], int]:
    """The ids that match `pattern` (up to the fetch cap), and how many entries were unusable."""
    found = [i[key] for i in items if isinstance(i.get(key), str) and pattern.fullmatch(i[key])]
    return found[:MAX_DETAIL_FETCHES], len(items) - len(found[:MAX_DETAIL_FETCHES])


def _shown(item: dict[str, Any], fallback: str) -> str:
    name = item.get("name")
    return safe_name(name if isinstance(name, str) else "", fallback, 100)


def _document_envelope(listed: dict[str, Any], body: dict[str, Any], number: int) -> dict[str, Any]:
    inner = body.get("content")
    return {
        "id": listed.get("id", ""),
        "name": _shown(listed, f"dashboard-{number}"),
        "type": "dashboard",
        "isPrivate": listed.get("isPrivate"),
        "originAppId": listed.get("originAppId", ""),
        "content": inner if isinstance(inner, dict) else body,
    }


def read_dashboards(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    first = {"filter": "type in ('dashboard')", "page-size": str(PAGE_SIZE)}
    for page in _list_pages(
        get, host + READ_PATHS["documents"], first, "documents", budget, "page-key"
    ):
        listed += dicts(page.get("documents"))
    ids, unusable = _ids(listed, "id", UUID)
    count = 0
    for number, ident in enumerate(ids, 1):
        entry = next(i for i in listed if i.get("id") == ident)
        body = get(_url(host, "document_content", ident), {})
        _write(
            out / "dashboards" / f"dashboard-{number:04d}.json",
            _document_envelope(entry, body, number),
        )
        count += 1
    if unusable:
        raise _Partial(
            count, f"{unusable} dashboards were not read: no usable id or over the limit"
        )
    return count


def read_classic_dashboards(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    for page in _list_pages(
        get, host + READ_PATHS["classic_dashboards"], {}, "dashboards", budget, ""
    ):
        listed += dicts(page.get("dashboards"))
    ids, unusable = _ids(listed, "id", UUID)
    for number, ident in enumerate(ids, 1):
        body = get(_url(host, "classic_dashboard", ident), {})
        meta = body.get("dashboardMetadata")
        if isinstance(meta, dict):
            body = {**body, "dashboardMetadata": {**meta, "owner": ""}}
        _write(out / "classic-dashboards" / f"classic-{number:04d}.json", body)
    if unusable:
        raise _Partial(len(ids), f"{unusable} classic dashboards were not read: no usable id")
    return len(ids)


def read_monitors(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    for page in _list_pages(get, host + READ_PATHS["monitors"], {}, "monitors", budget, ""):
        listed += dicts(page.get("monitors"))
    ids, unusable = _ids(listed, "entityId", MONITOR_ID)
    monitors: list[dict[str, Any]] = []
    for number, ident in enumerate(ids, 1):
        detail = get(_url(host, "monitor", ident), {})
        script = detail.get("script")
        monitors.append(
            {
                "entityId": ident,
                "name": _shown(detail, f"monitor-{number}"),
                "type": detail.get("type", ""),
                "enabled": detail.get("enabled", True),
                "frequencyMin": detail.get("frequencyMin"),
                "locations": detail.get("locations", []),
                "script": clean_script("script", script) if isinstance(script, dict) else {},
                "anomalyDetection": detail.get("anomalyDetection", {}),
            }
        )
    _write(out / "synthetics" / "monitors.json", {"monitors": monitors})
    if unusable:
        raise _Partial(len(ids), f"{unusable} monitors were not read: no usable id")
    return len(ids)


def read_locations(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    keep = ("entityId", "name", "type", "cloudPlatform", "countryCode", "city")
    found: list[dict[str, Any]] = []
    for page in _list_pages(get, host + READ_PATHS["locations"], {}, "locations", budget, ""):
        found += [{k: item[k] for k in keep if k in item} for item in dicts(page.get("locations"))]
    _write(out / "synthetics" / "locations.json", {"locations": found})
    return len(found)


def _entity(kind: str, raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties")
    props = props if isinstance(props, dict) else {}
    tech = props.get("serviceTechnologyTypes")
    return {
        "type": kind,
        "technologies": [t for t in tech if isinstance(t, str)] if isinstance(tech, list) else [],
        "os": props.get("osType") if isinstance(props.get("osType"), str) else "",
    }


def read_entities(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    total = 0
    shortfalls: list[str] = []
    for kind, extra in ENTITY_TYPES.items():
        first = {"entitySelector": f'type("{kind}")', "pageSize": str(ENTITY_PAGE_SIZE)}
        if extra:
            first["fields"] = extra
        entities: list[dict[str, Any]] = []
        reported = 0
        for page in _list_pages(
            get, host + READ_PATHS["entities"], first, kind, budget, "nextPageKey"
        ):
            entities += [_entity(kind, e) for e in dicts(page.get("entities"))]
            count = page.get("totalCount")
            reported = count if isinstance(count, int) else reported
        if reported or entities:
            _write(
                out / "entities" / f"entities-{kind}.json",
                {"totalCount": reported, "entities": entities},
            )
        if reported > len(entities):
            shortfalls.append(kind)
        total += len(entities)
    if shortfalls:
        raise _Partial(total, "entity lists are partial for " + ", ".join(shortfalls))
    return total


AREAS: dict[str, Callable[[Getter, str, Path, PageBudget], int]] = {
    "dashboards": read_dashboards,
    "classic_dashboards": read_classic_dashboards,
    "monitors": read_monitors,
    "locations": read_locations,
    "entities": read_entities,
}


def extract(get: Getter, env_url: str, out: Path) -> ExtractResult:
    """Write dashboards/, classic-dashboards/, synthetics/ and entities/ under a new `out`."""
    host = env_url.removesuffix("/")
    if not is_platform_host(host):
        raise ValueError("DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    result = ExtractResult()
    budget = PageBudget()
    for area, read in AREAS.items():
        label = area.replace("_", " ")
        try:
            count = read(get, host, out, budget)
        except _Partial as exc:
            count = exc.count
            result.warnings.append(str(exc))
        except DynatraceError as exc:
            result.warnings.append(f"{label} were NOT read: {exc}")
            continue
        result.areas_read += 1
        setattr(result, area, count)
    return result
