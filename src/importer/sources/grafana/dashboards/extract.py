"""Read dashboards, folders and library panels from a Grafana stack, read-only.

Only GET calls on a fixed set of paths run, against one host that `validate_host` of the alert
extractor accepted. The token (GRAFANA_SA_TOKEN) is sent as a bearer token and is never stored
or printed. The safeguards are those of `core/safe_http.py`: redirects are not followed, a
status of 300 or more is an error, server text is scrubbed of the token, each response and the
whole run have byte caps, the run has a deadline, and every loop is bounded. The only files
written are `dashboards/dashboard-<hash>.json`, `folders.json` and `library-panels.json`, in a
new folder (mode 0700, files 0600) that appears only when the run finished. Credentials in the
dashboard JSON are removed before anything is written (see redact.py).

Paths follow the Grafana HTTP API documentation (`/api/search`, `/api/dashboards/uid/{uid}`,
`/api/folders`, `/api/library-elements`). None was read from a live stack in this repository yet.
The `/apis/dashboard.grafana.app` list is documented but not used: which version a stack serves
is still an open question (docs/research/grafana-dashboards.md, section 10.1).
"""

import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from importer.core.ids import MAX_INPUT_BYTES, source_id
from importer.core.safe_http import ReadBudget, error_message, read_body, scrub
from importer.sources.grafana.dashboards.common import mapping, sequence, text
from importer.sources.grafana.dashboards.models import MAX_DASHBOARDS, MAX_LIBRARY
from importer.sources.grafana.dashboards.redact import Count, redact
from importer.sources.grafana.extract import GrafanaError, validate_host

SERVICE = "Grafana"
SEARCH = "/api/search"
FOLDERS = "/api/folders"
LIBRARY = "/api/library-elements"
DASHBOARD = re.compile(r"/api/dashboards/uid/([A-Za-z0-9_-]{1,40})")
UID = re.compile(r"[A-Za-z0-9_-]{1,40}")
FIXED_PATHS = frozenset({SEARCH, FOLDERS, LIBRARY})
# parameter name -> the only values it may take
PARAMS = {
    "type": re.compile(r"dash-db"),
    "kind": re.compile(r"1"),
    "limit": re.compile(r"[0-9]{1,5}"),
    "page": re.compile(r"[0-9]{1,5}"),
    "perPage": re.compile(r"[0-9]{1,4}"),
}
PAGE_SIZE = 1000
LIBRARY_PAGE = 100
MAX_PAGES = 20
MAX_REQUESTS = 6000
KEPT_META = ("folderUid", "folderTitle", "provisioned")
Getter = Callable[[str, dict[str, str]], Any]


class _Fatal(GrafanaError):
    """The run must stop (network failure, size, time or request budget), not skip one item."""


def _allowed(path: str, params: dict[str, str]) -> bool:
    if path not in FIXED_PATHS and not DASHBOARD.fullmatch(path):
        return False
    return all(k in PARAMS and PARAMS[k].fullmatch(v) for k, v in params.items())


def _decode(response: httpx.Response, token: str, budget: ReadBudget) -> Any:
    body = read_body(response, _Fatal, SERVICE, budget)
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):  # also JSON nested deeper than the parser allows
        parsed = None
    status = response.status_code
    if status >= 300:  # redirects are not followed, so 3xx is a failure too
        detail = scrub(error_message(parsed), token)
        raise GrafanaError(f"{SERVICE} returned {status}" + (f": {detail}" if detail else ""))
    if not isinstance(parsed, dict | list):
        raise GrafanaError(f"{SERVICE} returned a body that is not a JSON object or list")
    return parsed


@contextmanager
def http_getter(
    token: str,
    base: str,
    transport: httpx.BaseTransport | None = None,
    allow_host: str | None = None,
) -> Iterator[Getter]:
    """A Getter for the allow-listed paths that closes its connection pool when the block ends."""
    try:  # checked here too, so no caller can send the token to a host that was not validated
        checked = validate_host(base, allow_host)
    except ValueError as exc:
        raise GrafanaError(str(exc)) from None
    if checked != base:
        raise GrafanaError("the base URL must be https://<host> exactly as validated")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    budget = ReadBudget()
    requests = 0
    with httpx.Client(
        timeout=60, headers=headers, transport=transport, follow_redirects=False
    ) as client:

        def get(path: str, params: dict[str, str]) -> Any:
            nonlocal requests
            if not _allowed(path, params):
                raise GrafanaError("this path is not on the allow-list of read-only calls")
            requests += 1
            if requests > MAX_REQUESTS:
                raise _Fatal(f"more than {MAX_REQUESTS} requests; stopped")
            budget.check_time(_Fatal, SERVICE)
            try:
                with client.stream("GET", base + path, params=params) as response:
                    return _decode(response, token, budget)
            except httpx.HTTPError as exc:
                raise _Fatal(f"could not reach {SERVICE}: {type(exc).__name__}") from None

        yield get


@dataclass
class DashboardExtract:
    dashboards: int = 0
    folders: int = 0
    library_panels: int = 0
    redactions: int = 0
    warnings: list[str] = field(default_factory=list)


def _paged(
    get: Getter,
    path: str,
    params: Callable[[int], dict[str, str]],
    items_of: Callable[[Any], list[Any]],
    what: str,
    result: DashboardExtract,
    done: Callable[[Any, int], bool] = lambda _body, _seen: False,
) -> Iterator[list[Any]]:
    """Pages until an empty one, a repeated one, `done`, or the page cap (which is an error).

    A server may return fewer items per page than asked for, so a short page is not the end.
    """
    previous: list[Any] | None = None
    seen = 0
    for page in range(1, MAX_PAGES + 1):
        body = get(path, params(page))
        items = items_of(body)
        if not items:
            return
        if items == previous:
            result.warnings.append(f"the server repeated a page of {what}; stopped reading it")
            return
        previous = items
        seen += len(items)
        yield items
        if done(body, seen):
            return
    raise GrafanaError(f"more than {MAX_PAGES} pages of {what}; stopped")


def _list_of(body: Any) -> list[Any]:
    if not isinstance(body, list):
        raise GrafanaError("unexpected response shape")
    return body


def _list_uids(get: Getter, result: DashboardExtract) -> list[str]:
    uids: dict[str, None] = {}
    rejected = 0
    pages = _paged(
        get,
        SEARCH,
        lambda page: {"type": "dash-db", "limit": str(PAGE_SIZE), "page": str(page)},
        _list_of,
        "dashboards",
        result,
    )
    for items in pages:
        for item in items:
            uid = mapping(item).get("uid")
            if isinstance(uid, str) and UID.fullmatch(uid):
                uids[uid] = None
            else:
                rejected += 1
        if len(uids) > MAX_DASHBOARDS:
            result.warnings.append(f"more than {MAX_DASHBOARDS} dashboards; the rest were left out")
            break
    if rejected:
        result.warnings.append(
            f"{rejected} dashboards had a uid that is not a plain token and were not requested"
        )
    return list(uids)[:MAX_DASHBOARDS]


def _write(path: Path, data: object, result: DashboardExtract) -> bool:
    """Write redacted JSON, new file, mode 0600. False when nothing was written."""
    count = Count()
    try:
        body = json.dumps(redact(data, count), indent=2) + "\n"
    except (RecursionError, ValueError):
        return False
    if len(body) > MAX_INPUT_BYTES:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as handle:
        handle.write(body)
    result.redactions += count.value
    return True


def _dashboard_file(uid: str) -> str:
    """A name that differs for every exact uid, also on a case-insensitive file system."""
    return f"dashboard-{source_id(uid):012x}.json"


def _dashboards(get: Getter, uids: list[str], out: Path, result: DashboardExtract) -> None:
    failed = 0
    for uid in uids:
        try:
            body = get(f"/api/dashboards/uid/{uid}", {})
        except _Fatal:
            raise
        except GrafanaError:
            failed += 1
            continue
        dashboard = mapping(body).get("dashboard")
        meta = mapping(mapping(body).get("meta"))
        kept = {k: meta[k] for k in KEPT_META if k in meta} | {"uid": uid}
        if isinstance(dashboard, dict) and _write(
            out / "dashboards" / _dashboard_file(uid),
            {"dashboard": dashboard, "meta": kept},
            result,
        ):
            result.dashboards += 1
        else:
            failed += 1
    if failed:
        result.warnings.append(
            f"{failed} dashboards were NOT read (no access, not found or too large)"
        )


def _folders(get: Getter, out: Path, result: DashboardExtract) -> None:
    found: list[dict[str, str]] = []
    pages = _paged(
        get,
        FOLDERS,
        lambda page: {"limit": str(PAGE_SIZE), "page": str(page)},
        _list_of,
        "folders",
        result,
    )
    for items in pages:
        for item in items:
            uid = mapping(item).get("uid")
            if isinstance(uid, str) and UID.fullmatch(uid):
                found.append({"uid": uid, "title": text(mapping(item).get("title"))})
    result.folders = len(found)
    _write(out / "folders.json", {"folders": found}, result)


def _library_elements(body: Any) -> list[Any]:
    return sequence(mapping(mapping(body).get("result")).get("elements"))


def _library_done(body: Any, seen: int) -> bool:
    total = mapping(mapping(body).get("result")).get("totalCount")
    return seen >= MAX_LIBRARY or (isinstance(total, int) and seen >= total)


def _library(get: Getter, out: Path, result: DashboardExtract) -> None:
    found: list[dict[str, Any]] = []
    pages = _paged(
        get,
        LIBRARY,
        lambda page: {"kind": "1", "perPage": str(LIBRARY_PAGE), "page": str(page)},
        _library_elements,
        "library panels",
        result,
        _library_done,
    )
    for elements in pages:
        for element in elements:
            item = mapping(element)
            if isinstance(item.get("uid"), str) and isinstance(item.get("model"), dict):
                found.append({k: item.get(k) for k in ("uid", "name", "type", "model")})
    result.library_panels = len(found)
    _write(out / "library-panels.json", {"libraryPanels": found}, result)


def _run(get: Getter, work: Path) -> DashboardExtract:
    result = DashboardExtract()
    uids = _list_uids(get, result)
    _dashboards(get, uids, work, result)
    for label, reader in (("folders", _folders), ("library panels", _library)):
        try:
            reader(get, work, result)
        except _Fatal:
            raise
        except GrafanaError as exc:
            result.warnings.append(f"{label} were NOT read: {exc}")
    if result.redactions:
        result.warnings.append(
            f"{result.redactions} values were redacted (passwords, tokens, headers, URL user "
            "info) before writing; the files are still dashboard JSON, so review them before "
            "sharing"
        )
    return result


def extract_dashboards(get: Getter, out: Path) -> DashboardExtract:
    """Write dashboards/, folders.json and library-panels.json under `out` (new or empty).

    Everything is written to a temporary folder next to `out` and moved into place only when the
    run finished, so a stopped run leaves no export that could be mistaken for a complete one.
    """
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".grafana-dashboards-", dir=out.parent))
    try:
        result = _run(get, work)
        if out.exists():
            out.rmdir()
        work.rename(out)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return result
