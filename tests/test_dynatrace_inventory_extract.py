"""The inventory extractor, driven through the real getter and an in-memory HTTP transport."""

import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import safe_http
from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory import extract as ix

HOST = "https://abc12345.apps.dynatrace.com"
TOKEN = "dt0s16.AAAAAAAA.SECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests
DOC_IDS = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 4)]
MONITOR = "HTTP_CHECK-00000000000000AB"
BOARD = {"version": 21, "variables": [], "tiles": {"1": {"type": "markdown", "content": "hi"}}}
SCRIPT = {
    "version": "1.0",
    "requests": [
        {
            "url": "https://user:pw123@shop.example.invalid/a/b?token=SECRETQ#frag",
            "method": "POST",
            "requestBody": "password=hunter2",
            "configuration": {
                "requestHeaders": [{"name": "Authorization", "value": "Bearer HUNTER"}]
            },
            "preProcessingScript": "api.setValue('k', 'SCRIPTSECRET');",
        }
    ],
}
Handler = Callable[[httpx.Request], httpx.Response]


def getter(handler: Handler, token: str = TOKEN) -> AbstractContextManager[ix.Getter]:
    return ix.http_getter(token, transport=httpx.MockTransport(handler))


def serve(fail: set[str] | None = None, seen: list[httpx.Request] | None = None) -> Handler:
    """A tenant with 3 dashboards over two pages, 1 classic dashboard, 1 monitor and 3 entities."""
    fail = fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        params = request.url.params
        area = next((a for a, p in AREAS.items() if re.fullmatch(p, path)), "")
        if area in fail:
            return httpx.Response(403, json={"error": {"message": f"no scope {TOKEN}"}})
        if area == "documents":
            second = params.get("page-key") == "p2"
            docs = DOC_IDS[2:] if second else DOC_IDS[:2]
            body: dict[str, Any] = {
                "documents": [
                    {"id": i, "name": f"board {i[-1]}", "type": "dashboard"} for i in docs
                ]
            }
            if not second:
                body["nextPageKey"] = "p2"
            return httpx.Response(200, json=body)
        if area == "document_content":
            return httpx.Response(200, json=BOARD)
        if area == "classic_list":
            return httpx.Response(200, json={"dashboards": [{"id": DOC_IDS[0], "name": "c"}]})
        if area == "classic_one":
            return httpx.Response(
                200, json={"id": DOC_IDS[0], "dashboardMetadata": {"name": "c"}, "tiles": []}
            )
        if area == "monitors":
            return httpx.Response(200, json={"monitors": [{"entityId": MONITOR, "name": "m"}]})
        if area == "monitor":
            detail = {"entityId": MONITOR, "name": "m", "type": "HTTP", "script": SCRIPT}
            return httpx.Response(200, json=detail)
        if area == "locations":
            return httpx.Response(
                200, json={"locations": [{"entityId": "GEOLOCATION-0000000000000001", "name": "x"}]}
            )
        if area == "entities":
            kind = params.get("entitySelector", "")
            if '"HOST"' in kind:
                page = {"totalCount": 2, "entities": [host(1), host(2)]}
            elif '"SERVICE"' in kind:
                page = {"totalCount": 1, "entities": [service()]}
            else:
                page = {"totalCount": 0, "entities": []}
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"error": {"message": "unexpected path"}})

    return handler


def host(n: int) -> dict[str, Any]:
    return {
        "entityId": f"HOST-000000000000000{n}",
        "type": "HOST",
        "displayName": f"secret-host-{n}.corp.invalid",
        "properties": {"osType": "LINUX", "ipAddress": ["10.1.2.3"]},
    }


def service() -> dict[str, Any]:
    return {
        "entityId": "SERVICE-0000000000000001",
        "type": "SERVICE",
        "displayName": "svc-name-secret",
        "properties": {"serviceTechnologyTypes": ["Java"]},
    }


AREAS = {
    "documents": r"/platform/document/v1/documents",
    "document_content": r"/platform/document/v1/documents/[0-9a-f-]{36}/content",
    "classic_list": r"/api/config/v1/dashboards",
    "classic_one": r"/api/config/v1/dashboards/[0-9a-f-]{36}",
    "monitors": r"/api/v1/synthetic/monitors",
    "monitor": r"/api/v1/synthetic/monitors/[A-Z_]+-[0-9A-F]{16}",
    "locations": r"/api/v1/synthetic/locations",
    "entities": r"/api/v2/entities",
}


def read(handler: Handler, tmp_path: Path, token: str = TOKEN) -> ix.ExtractResult:
    with getter(handler, token) as get:
        return ix.extract(get, HOST, tmp_path / "out")


def test_the_real_getter_reads_every_area_and_writes_the_folders(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    result = read(serve(seen=seen), tmp_path)
    assert (result.dashboards, result.classic_dashboards, result.monitors) == (3, 1, 1)
    assert (result.locations, result.entities) == (1, 3) and not result.warnings
    out = tmp_path / "out"
    assert len(list((out / "dashboards").glob("*.json"))) == 3
    envelope = json.loads(next((out / "dashboards").glob("*.json")).read_text())
    assert envelope["type"] == "dashboard" and envelope["content"]["tiles"]["1"]["content"] == "hi"
    assert (out / "classic-dashboards").is_dir() and (
        out / "synthetics" / "monitors.json"
    ).is_file()
    assert (out / "synthetics" / "locations.json").is_file() and (out / "entities").is_dir()
    assert all(r.method == "GET" for r in seen)
    assert {r.url.host for r in seen} == {"abc12345.apps.dynatrace.com"}
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)
    allowed = [re.compile(p) for p in AREAS.values()]
    assert all(any(p.fullmatch(r.url.path) for p in allowed) for r in seen)


def test_the_second_page_of_documents_is_requested_with_only_the_page_key(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    read(serve(seen=seen), tmp_path)
    pages = [r for r in seen if r.url.path == "/platform/document/v1/documents"]
    assert len(pages) == 2 and "filter" in pages[0].url.params
    assert dict(pages[1].url.params) == {"page-key": "p2"}


@pytest.mark.parametrize(
    "env",
    [
        "http://abc12345.apps.dynatrace.com",
        "https://abc12345.apps.dynatrace.com.evil.example",
        "https://evil.example/abc12345.apps.dynatrace.com",
        "https://abc12345.live.dynatrace.com",
        "https://ABC.apps.dynatrace.com",
        "https://abc12345.apps.dynatrace.com:8443",
        "https://abc12345.apps.dynatrace.com/path",
        "https://user@abc12345.apps.dynatrace.com",
        "",
    ],
)
def test_only_a_platform_host_is_accepted(env: str, tmp_path: Path) -> None:
    with getter(serve()) as get, pytest.raises(ValueError, match="DYNATRACE_ENV_URL"):
        ix.extract(get, env, tmp_path / "out")


def test_a_trailing_slash_on_the_host_is_fine(tmp_path: Path) -> None:
    with getter(serve()) as get:
        assert ix.extract(get, HOST + "/", tmp_path / "out").dashboards == 3


def test_a_folder_with_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("1")
    with getter(serve()) as get, pytest.raises(ValueError, match="already has files"):
        ix.extract(get, HOST, tmp_path / "out")


def test_one_area_that_fails_is_a_warning_and_the_rest_are_still_read(tmp_path: Path) -> None:
    result = read(serve(fail={"documents"}), tmp_path)
    assert result.dashboards == 0 and result.monitors == 1 and result.entities == 3
    assert [w for w in result.warnings if "dashboards" in w and "NOT read" in w]
    assert all(TOKEN not in w and "SECRETSECRET" not in w for w in result.warnings)
    assert not result.read_nothing


def test_when_nothing_can_be_read_that_is_reported(tmp_path: Path) -> None:
    result = read(serve(fail=set(AREAS)), tmp_path)
    assert result.read_nothing and len(result.warnings) >= 4


def test_a_document_id_that_is_not_a_uuid_is_never_requested(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/platform/document/v1/documents":
            return httpx.Response(200, json={"documents": [{"id": "../../admin", "name": "x"}]})
        return httpx.Response(200, json={})

    result = read(handler, tmp_path)
    assert result.dashboards == 0 and any("not read" in w for w in result.warnings)
    assert not any("admin" in str(r.url) for r in seen)


def test_a_redirect_is_an_error_and_is_not_followed(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    result = read(handler, tmp_path)
    assert result.read_nothing and {r.url.host for r in seen} == {"abc12345.apps.dynatrace.com"}
    assert any("returned 302" in w for w in result.warnings)


def test_the_token_never_reaches_a_warning_or_a_file(tmp_path: Path) -> None:
    result = read(serve(fail={"monitors"}), tmp_path, token=TOKEN)
    text = "".join(result.warnings) + "".join(
        p.read_text() for p in (tmp_path / "out").rglob("*") if p.is_file()
    )
    assert TOKEN not in text and "SECRETSECRET" not in text


def test_a_response_over_the_size_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 150)
    result = read(serve(), tmp_path)
    assert any("larger than the importer accepts" in w for w in result.warnings)


def test_the_whole_run_has_a_byte_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 600)
    result = read(serve(), tmp_path)
    assert any("bytes in total" in w for w in result.warnings)


def test_the_whole_run_has_a_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter(range(0, 10_000, 400))
    monkeypatch.setattr(safe_http, "_clock", lambda: next(ticks))
    result = read(serve(), tmp_path)
    assert any("took longer than" in w for w in result.warnings)


def test_endless_paging_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ix, "MAX_PAGES", 3)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/document/v1/documents":
            return httpx.Response(200, json={"documents": [], "nextPageKey": "again"})
        return serve()(request)

    result = read(handler, tmp_path)
    assert any("more than 3 pages" in w for w in result.warnings)


def test_the_total_page_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ix, "MAX_TOTAL_PAGES", 4)
    result = read(serve(), tmp_path)
    assert any("pages in total" in w for w in result.warnings)


def test_secrets_in_monitor_scripts_are_not_written(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    written = (tmp_path / "out" / "synthetics" / "monitors.json").read_text()
    for secret in ("hunter2", "HUNTER", "SCRIPTSECRET", "pw123", "SECRETQ", "frag"):
        assert secret not in written
    request = json.loads(written)["monitors"][0]["script"]["requests"][0]
    assert request["url"] == "https://shop.example.invalid/a/b"
    assert request["method"] == "POST" and request["requestBody"] == ix.OMITTED
    assert request["preProcessingScript"] == ix.OMITTED
    assert "requestHeaders" not in request["configuration"] and request["hasHeaders"] is True


def test_entities_are_written_without_names_or_addresses(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    files = sorted((tmp_path / "out" / "entities").glob("*.json"))
    text = "".join(f.read_text() for f in files)
    for secret in ("secret-host", "svc-name-secret", "10.1.2.3", "HOST-0000", "SERVICE-0000"):
        assert secret not in text
    assert '"Java"' in text and '"LINUX"' in text
    assert sum(json.loads(f.read_text())["totalCount"] for f in files) == 3


@pytest.mark.parametrize("token", ["", "has space", "smart”quote", "line\nbreak"])
def test_an_odd_token_is_refused_before_any_request(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_API_TOKEN", token)
    with pytest.raises(DynatraceError):
        ix.inventory_token()


def run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Handler) -> Any:
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
    monkeypatch.setattr(
        "importer.commands.dynatrace_inventory.http_getter",
        lambda t: ix.http_getter(t, httpx.MockTransport(handler)),
    )
    return CliRunner().invoke(app, ["extract-dynatrace-inventory", "--out", str(tmp_path / "o")])


def test_the_cli_reads_and_names_the_next_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve())
    assert out.exit_code == 0, out.output
    assert "Read 3 dashboards" in out.output and "plan-dynatrace-dashboards" in out.output
    assert TOKEN not in out.output


def test_the_cli_fails_when_nothing_can_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(fail=set(AREAS)))
    assert out.exit_code == 1 and "SECRETSECRET" not in out.output


def test_the_cli_without_a_token_says_what_to_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DYNATRACE_API_TOKEN", raising=False)
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    out = CliRunner().invoke(app, ["extract-dynatrace-inventory", "--out", str(tmp_path / "o")])
    assert out.exit_code == 1 and "DYNATRACE_API_TOKEN" in out.output


@pytest.mark.parametrize(
    ("name", "ident"),
    [
        ("document_content", "../admin"),
        ("document_content", DOC_IDS[0] + "/x"),
        ("classic_dashboard", ""),
        ("monitor", "HTTP_CHECK-123"),
    ],
)
def test_an_id_that_does_not_match_its_pattern_never_becomes_a_url(name: str, ident: str) -> None:
    with pytest.raises(DynatraceError, match="expected form"):
        ix._url(HOST, name, ident)
