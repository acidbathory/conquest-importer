"""Read dashboards, synthetic monitors and an entity inventory from a Dynatrace tenant, read-only.

Only GET requests to the paths in READ_PATHS can run, against a host that matched
`https://<tenant id>.apps.dynatrace.com`. Ids that go into a path are checked against a pattern
first, so no text from a response becomes a host or an operation. The token comes from
DYNATRACE_API_TOKEN, is sent as a bearer token and is never stored or printed. The shared
safeguards in core/safe_http.py apply: no redirects, a status of 300 or more is an error, server
text is scrubbed of the token, each response and the whole run have a byte cap, and the run has
a deadline. Every list has a page cap, a page key longer than MAX_PAGE_KEY is refused, and the run
has a total page cap.

What is written is built from an allow-list (see sanitize.py), in files readable by the owner
only (0600 in folders 0700):
- dashboards/: one envelope per Platform dashboard (name, app id, and the tile fields the
  planner reads). Titles, queries and markdown are stored as written: they can name internal
  systems. Review before sharing.
- classic-dashboards/: classic dashboards without owner and tags.
- synthetics/: monitors reduced to structure (see sanitize.py) and public location names.
- entities/: for each entity only its type, product names of its technologies and operating
  system. No name, id or address.

Endpoints and how sure we are (research note docs/research/dynatrace-dashboards-inventory-
datapath.md):
- documents list (`type in ('dashboard')`, `page-key`): DOCUMENTED (D4); the response keys
  `documents` and `nextPageKey` are UNVERIFIED.
- document content, `.../documents/<id>/content`: UNVERIFIED path and response shape (the note
  lists it as an open question). Its `content` object, or the body when that has `tiles`.
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

import httpx

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.redact import safe_name
from importer.core.safe_http import ReadBudget, get_json, is_plain_token
from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory import sanitize
from importer.sources.dynatrace.inventory.models import dicts, is_count, label

ENV_URL = re.compile(r"https://[a-z0-9]+\.apps\.dynatrace\.com")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MONITOR_ID = re.compile(r"[A-Z_]+-[0-9A-F]{16}")
SERVICE = "Dynatrace"
OMITTED = sanitize.OMITTED

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
    "CLOUD_APPLICATION": "",
    "APPLICATION": "",
    "MOBILE_APPLICATION": "",
    "AWS_LAMBDA_FUNCTION": "",
}
PAGE_SIZE = 100
ENTITY_PAGE_SIZE = 500
MAX_PAGES = 100
MAX_TOTAL_PAGES = 400  # list pages across every area of one run
MAX_DETAIL_FETCHES = 1000  # single-object reads per area
MAX_PAGE_KEY = 2000  # a longer page key is hostile, and would not fit in a URL anyway

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
    """True for exactly https://<tenant id>.apps.dynatrace.com (no path, port or slash)."""
    return ENV_URL.fullmatch(url) is not None


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
            try:
                return get_json(client, url, params, token, DynatraceError, SERVICE, budget)
            except httpx.InvalidURL:  # not an HTTPError: a hostile page key can build one
                raise DynatraceError("could not reach Dynatrace: InvalidURL") from None

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
        if len(nxt) > MAX_PAGE_KEY:
            raise DynatraceError(f"a page key of {label} was too long; stopped")
        params = {cursor: nxt}
    raise DynatraceError(f"more than {MAX_PAGES} pages of {label}; stopped")


class _Partial(DynatraceError):
    """Some of an area was read; `count` says how much. The message goes to the warnings."""

    def __init__(self, count: int, message: str) -> None:
        super().__init__(message)
        self.count = count


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)  # mkdir's mode is cut by the umask


def _write(path: Path, data: object) -> None:
    """Write JSON readable by the owner only (0600) in a folder only the owner can open."""
    text = json.dumps(data, indent=2) + "\n"
    if len(text) > MAX_INPUT_BYTES:
        raise DynatraceError(f"{path.name} is larger than the planner accepts")
    _private_dir(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(text)


def _ids(
    items: list[dict[str, Any]], key: str, pattern: re.Pattern[str]
) -> tuple[list[dict[str, Any]], int]:
    """Entries with a valid, not yet seen id (up to the fetch cap), and how many were left out
    because the id was unusable or the cap was reached. A repeated id is read once, silently."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    valid = 0
    for item in items:
        ident = item.get(key)
        if not isinstance(ident, str) or not pattern.fullmatch(ident):
            continue
        valid += 1
        if ident not in seen:
            seen.add(ident)
            kept.append(item)
    capped = kept[:MAX_DETAIL_FETCHES]
    return capped, (len(items) - valid) + (len(kept) - len(capped))


def _shown(item: dict[str, Any], fallback: str) -> str:
    name = item.get("name")
    return safe_name(name if isinstance(name, str) else "", fallback, 100)


def _content(body: dict[str, Any]) -> dict[str, Any] | None:
    """The dashboard in a content response: its `content` object, or the body if it has tiles."""
    inner = body.get("content")
    candidate = inner if isinstance(inner, dict) else body
    return sanitize.dashboard_content(candidate)


def read_dashboards(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    first = {"filter": "type in ('dashboard')", "page-size": str(PAGE_SIZE)}
    for page in _list_pages(
        get, host + READ_PATHS["documents"], first, "documents", budget, "page-key"
    ):
        listed += dicts(page.get("documents"))
    entries, unusable = _ids(listed, "id", UUID)
    count = 0
    for number, entry in enumerate(entries, 1):
        content = _content(get(_url(host, "document_content", entry["id"]), {}))
        if content is None:
            unusable += 1
            continue
        count += 1
        private = entry.get("isPrivate")
        envelope = {
            "id": entry["id"],
            "name": _shown(entry, f"dashboard-{number}"),
            "type": "dashboard",
            "isPrivate": private if isinstance(private, bool) else None,
            "originAppId": label(entry.get("originAppId")),
            "content": content,
        }
        _write(out / "dashboards" / f"dashboard-{count:04d}.json", envelope)
    if unusable:
        raise _Partial(
            count, f"{unusable} dashboards were not read: no usable id, no tiles or over the limit"
        )
    return count


def read_classic_dashboards(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    for page in _list_pages(
        get, host + READ_PATHS["classic_dashboards"], {}, "dashboards", budget, ""
    ):
        listed += dicts(page.get("dashboards"))
    entries, unusable = _ids(listed, "id", UUID)
    count = 0
    for entry in entries:
        body = get(_url(host, "classic_dashboard", entry["id"]), {})
        classic = sanitize.classic_dashboard(body)
        if classic is None:
            unusable += 1
            continue
        count += 1
        _write(out / "classic-dashboards" / f"classic-{count:04d}.json", classic)
    if unusable:
        raise _Partial(count, f"{unusable} classic dashboards were not read: no usable id or tiles")
    return count


def read_monitors(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    listed: list[dict[str, Any]] = []
    for page in _list_pages(get, host + READ_PATHS["monitors"], {}, "monitors", budget, ""):
        listed += dicts(page.get("monitors"))
    entries, unusable = _ids(listed, "entityId", MONITOR_ID)
    monitors = [
        sanitize.monitor(entry["entityId"], get(_url(host, "monitor", entry["entityId"]), {}), n)
        for n, entry in enumerate(entries, 1)
    ]
    _write(out / "synthetics" / "monitors.json", {"monitors": monitors})
    if unusable:
        raise _Partial(len(monitors), f"{unusable} monitors were not read: no usable id")
    return len(monitors)


def read_locations(get: Getter, host: str, out: Path, budget: PageBudget) -> int:
    found: list[dict[str, Any]] = []
    for page in _list_pages(get, host + READ_PATHS["locations"], {}, "locations", budget, ""):
        found += [sanitize.location(item) for item in dicts(page.get("locations"))]
    _write(out / "synthetics" / "locations.json", {"locations": found})
    return len(found)


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
            entities += [sanitize.entity(kind, e) for e in dicts(page.get("entities"))]
            count = page.get("totalCount")
            if isinstance(count, int) and is_count(count):
                reported = count
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
    host = env_url.removesuffix("/")  # one trailing slash is fine; the host is then checked whole
    if not is_platform_host(host):
        raise ValueError("DYNATRACE_ENV_URL must look like https://<tenant id>.apps.dynatrace.com")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    _private_dir(out)
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
