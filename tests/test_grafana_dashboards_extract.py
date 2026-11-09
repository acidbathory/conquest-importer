"""The dashboard extractor, driven through the real getter with httpx.MockTransport."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import safe_http
from importer.sources.grafana.dashboards import extract as dx
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.extract import GrafanaError

HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_SECRETSECRET"  # noqa: S105 - a made-up value for leak tests
Handler = Callable[[httpx.Request], httpx.Response]


def dash(uid: str, panels: int = 1) -> dict[str, Any]:
    return {
        "dashboard": {
            "uid": uid,
            "title": f"Dashboard {uid}",
            "schemaVersion": 39,
            "panels": [
                {"id": i, "type": "stat", "title": "p", "targets": []} for i in range(panels)
            ],
        },
        "meta": {
            "folderUid": "f1",
            "folderTitle": "Ops",
            "provisioned": False,
            "url": "/d/" + uid,
            "createdBy": "someone@example.com",
            "slug": "x",
        },
    }


UIDS = ["alpha", "beta", "gamma"]
LIBRARY = {
    "result": {
        "totalCount": 1,
        "page": 1,
        "perPage": 100,
        "elements": [
            {
                "uid": "lib1",
                "name": "CPU",
                "type": "timeseries",
                "model": {"type": "timeseries", "title": "CPU", "targets": []},
                "meta": {"createdBy": {"name": "someone"}},
            }
        ],
    }
}


def serve(
    uids: list[str] | None = None,
    fail: str = "",
    search_pages: int = 1,
    seen: list[httpx.Request] | None = None,
) -> Handler:
    found = UIDS if uids is None else uids

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        path = request.url.path
        if fail and fail in path:
            return httpx.Response(403, json={"message": "no permission for " + TOKEN})
        if path == "/api/search":
            page = int(request.url.params.get("page", "1"))
            items = [{"uid": u, "title": u, "type": "dash-db"} for u in found]
            if search_pages > 1:
                return httpx.Response(
                    200, json=items[page - 1 : page] if page <= len(items) else []
                )
            return httpx.Response(200, json=items if page == 1 else [])
        if path.startswith("/api/dashboards/uid/"):
            uid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=dash(uid)) if uid in found else httpx.Response(404)
        if path == "/api/folders":
            return httpx.Response(200, json=[{"uid": "f1", "title": "Ops", "id": 4}])
        if path == "/api/library-elements":
            return httpx.Response(200, json=LIBRARY)
        return httpx.Response(404, json={"message": "not on the list"})

    return handler


def getter(handler: Handler, token: str = TOKEN) -> Any:
    return dx.http_getter(token, HOST, transport=httpx.MockTransport(handler))


def read(handler: Handler, tmp_path: Path) -> dx.DashboardExtract:
    with getter(handler) as get:
        return dx.extract_dashboards(get, tmp_path / "out")


def test_the_real_getter_reads_dashboards_folders_and_library_panels(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    result = read(serve(seen=seen), tmp_path)
    assert (result.dashboards, result.folders, result.library_panels) == (3, 1, 1)
    assert all(r.method == "GET" for r in seen)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    paths = {r.url.path for r in seen}
    assert paths == {
        "/api/search",
        "/api/folders",
        "/api/library-elements",
        *(f"/api/dashboards/uid/{u}" for u in UIDS),
    }


def test_only_dashboard_folder_and_library_json_is_written(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    files = sorted(
        p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*")
    )
    assert files == [
        "dashboards",
        "dashboards/alpha.json",
        "dashboards/beta.json",
        "dashboards/gamma.json",
        "folders.json",
        "library-panels.json",
    ]


def test_the_meta_block_keeps_only_the_folder_and_provisioned_flag(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    written = json.loads((tmp_path / "out" / "dashboards" / "alpha.json").read_text())
    assert written["meta"] == {"folderUid": "f1", "folderTitle": "Ops", "provisioned": False}
    everything = "".join(p.read_text() for p in (tmp_path / "out").rglob("*.json"))
    assert "someone" not in everything and TOKEN not in everything


def test_the_output_loads_with_folders_and_library_panels(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    estate = load_estate(tmp_path / "out")
    assert len(estate.dashboards) == 3 and not estate.unreadable
    assert estate.library_panels == 1 and estate.dashboards[0].folder == "Ops"


def test_search_pages_are_followed_until_a_short_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "PAGE_SIZE", 1)
    seen: list[httpx.Request] = []
    result = read(serve(search_pages=3, seen=seen), tmp_path)
    assert result.dashboards == 3
    pages = [r.url.params["page"] for r in seen if r.url.path == "/api/search"]
    assert pages[:3] == ["1", "2", "3"]


def test_a_search_that_never_ends_stops_at_the_page_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_PAGES", 3)
    monkeypatch.setattr(dx, "PAGE_SIZE", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            page = request.url.params["page"]
            return httpx.Response(200, json=[{"uid": f"u{page}", "type": "dash-db"}])
        return serve()(request)

    with pytest.raises(GrafanaError, match="more than 3 pages"):
        read(handler, tmp_path)


def test_the_dashboard_cap_stops_a_runaway_listing_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_DASHBOARDS", 2)
    result = read(serve(), tmp_path)
    assert result.dashboards == 2
    assert any("more than 2 dashboards" in w for w in result.warnings)


def test_a_uid_that_is_not_a_plain_token_is_never_requested(tmp_path: Path) -> None:
    bad = ["../../etc/passwd", "a/b", "x" * 41, "ok uid", "", "a?b=c", "é"]
    seen: list[httpx.Request] = []
    result = read(serve(uids=["good", *bad], seen=seen), tmp_path)
    assert result.dashboards == 1
    fetched = [r.url.path for r in seen if r.url.path.startswith("/api/dashboards")]
    assert fetched == ["/api/dashboards/uid/good"]
    assert any("7 dashboards" in w for w in result.warnings)


@pytest.mark.parametrize(
    "path",
    [
        "/api/datasources",
        "/api/dashboards/db",
        "/api/dashboards/uid/",
        "/api/dashboards/uid/a/b",
        "/api/dashboards/uid/../../x",
        "/api/dashboards/uid/a?x=1",
        "/api/dashboards/uid/" + "a" * 41,
        "/api/library-elements/abc",
        "/api/v1/provisioning/alert-rules",
        "/apis/dashboard.grafana.app/v1/namespaces/default/dashboards",
        "/api/search/",
        "",
        "x",
    ],
)
def test_a_path_off_the_allow_list_is_refused_by_the_real_getter(path: str) -> None:
    with getter(serve()) as get, pytest.raises(GrafanaError, match="allow-list"):
        get(path, {})


def test_a_parameter_off_the_allow_list_is_refused() -> None:
    with getter(serve()) as get:
        with pytest.raises(GrafanaError, match="allow-list"):
            get("/api/search", {"query": "x"})
        with pytest.raises(GrafanaError, match="allow-list"):
            get("/api/search", {"limit": "1; DROP"})


def test_a_redirect_is_an_error_and_is_not_followed(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(GrafanaError, match="302"):
        read(handler, tmp_path)
    assert len(seen) == 1


def test_server_text_is_scrubbed_of_the_token_and_failures_name_the_status(
    tmp_path: Path,
) -> None:
    with pytest.raises(GrafanaError) as err:
        read(serve(fail="/api/search"), tmp_path)
    assert "403" in str(err.value) and TOKEN not in str(err.value)
    assert "SECRETSECRET" not in str(err.value)


def test_a_failing_dashboard_is_skipped_with_a_warning_and_the_rest_are_written(
    tmp_path: Path,
) -> None:
    result = read(serve(fail="/api/dashboards/uid/beta"), tmp_path)
    assert result.dashboards == 2
    assert any("1 dashboard" in w and "NOT read" in w for w in result.warnings)
    assert all("SECRET" not in w for w in result.warnings)
    assert not (tmp_path / "out" / "dashboards" / "beta.json").exists()


@pytest.mark.parametrize("fail", ["/api/folders", "/api/library-elements"])
def test_folders_and_library_panels_only_warn_when_they_cannot_be_read(
    tmp_path: Path, fail: str
) -> None:
    result = read(serve(fail=fail), tmp_path)
    assert result.dashboards == 3
    assert any("NOT read" in w for w in result.warnings)
    assert all("SECRET" not in w for w in result.warnings)


def test_a_response_that_is_not_a_dashboard_is_skipped(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/dashboards/uid/beta":
            return httpx.Response(200, json=["not", "an", "object"])
        if request.url.path == "/api/dashboards/uid/gamma":
            return httpx.Response(200, json={"dashboard": "text"})
        return serve()(request)

    result = read(handler, tmp_path)
    assert result.dashboards == 1


def test_an_oversize_response_is_refused_by_length_and_by_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def declared(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-length": str(safe_http.MAX_RESPONSE_BYTES + 1)}
        )

    with pytest.raises(GrafanaError, match="larger than"):
        read(declared, tmp_path / "a")
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 50)
    with pytest.raises(GrafanaError, match="larger than"):
        read(lambda _r: httpx.Response(200, content=b'["' + b"x" * 100 + b'"]'), tmp_path / "b")


def test_the_total_byte_cap_stops_the_run_and_is_not_skipped_as_one_bad_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 400)
    with pytest.raises(GrafanaError, match="in total"):
        read(serve(), tmp_path)


def test_the_deadline_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0] + [safe_http.DEADLINE_SECONDS + 1.0] * 1000)
    monkeypatch.setattr(safe_http, "_clock", lambda: next(ticks))
    with pytest.raises(GrafanaError, match="took longer"):
        read(serve(), tmp_path)


def test_the_request_cap_stops_a_run_that_asks_for_too_much(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_REQUESTS", 3)
    with pytest.raises(GrafanaError, match="more than 3 requests"):
        read(serve(), tmp_path)


def test_a_connection_failure_names_only_the_error_type(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret detail " + TOKEN)

    with pytest.raises(GrafanaError) as err:
        read(handler, tmp_path)
    assert "ConnectError" in str(err.value) and "secret" not in str(err.value)


def test_an_existing_folder_with_files_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with getter(serve()) as get, pytest.raises(ValueError, match="already has files"):
        dx.extract_dashboards(get, out)


def test_a_host_that_is_not_a_grafana_cloud_stack_is_refused_before_any_request() -> None:
    with pytest.raises(GrafanaError, match="GRAFANA_URL"):
        dx.http_getter(TOKEN, "https://evil.example.com").__enter__()


def test_the_client_is_really_closed_when_the_block_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[httpx.Client] = []

    class Recording(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(httpx, "Client", Recording)
    with dx.http_getter("token", HOST):
        assert not made[0].is_closed
    assert made[0].is_closed
    with pytest.raises(RuntimeError), dx.http_getter("token", HOST):
        raise RuntimeError("boom")
    assert made[1].is_closed


def test_the_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)
    with pytest.raises(GrafanaError, match="GRAFANA_SA_TOKEN is not set"):
        dx.dashboards_token()
    monkeypatch.setenv("GRAFANA_SA_TOKEN", f" {TOKEN} ")
    assert dx.dashboards_token() == TOKEN
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "glsa_" + chr(0x2013) + "dash")
    with pytest.raises(GrafanaError, match="plain ASCII") as err:
        dx.dashboards_token()
    assert "glsa" not in str(err.value)


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: Handler,
    token: str = TOKEN,
    url: str = HOST,
) -> Any:
    monkeypatch.setenv("GRAFANA_URL", url)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", token)
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    monkeypatch.setattr(
        "importer.commands.grafana_dashboards.http_getter",
        lambda t, b, allow_host=None: dx.http_getter(
            t, b, httpx.MockTransport(handler), allow_host=allow_host
        ),
    )
    return CliRunner().invoke(app, ["extract-grafana-dashboards", "--out", str(tmp_path / "o")])


def test_the_cli_reads_the_stack_and_prints_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve())
    assert out.exit_code == 0, out.output
    assert "3 dashboards" in out.output
    assert f"importer plan-grafana-dashboards --in {tmp_path / 'o'}" in out.output
    assert TOKEN not in out.output


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        ("https://grafana.example.com", TOKEN, "GRAFANA_URL"),
        (HOST, "glsa_" + chr(0x2013), "plain ASCII"),
        ("", TOKEN, "GRAFANA_URL"),
    ],
)
def test_the_cli_refuses_a_bad_host_or_token_without_echoing_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str, token: str, message: str
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(), token=token, url=url)
    assert out.exit_code == 1 and message in out.output
    assert "glsa" not in out.output and not (tmp_path / "o").exists()


def test_the_cli_shows_a_scrubbed_error_when_grafana_says_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(fail="/api/search"))
    assert out.exit_code == 1 and "403" in out.output
    assert TOKEN not in out.output and "SECRETSECRET" not in out.output
