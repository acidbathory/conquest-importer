"""Extractor round 2: secrets never reach disk, file names, atomic writes and paging."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.sources.grafana.dashboards import extract as dx
from importer.sources.grafana.dashboards import models
from importer.sources.grafana.extract import GrafanaError
from test_grafana_dashboards_extract import read, serve

PLANTED = [
    "hunter2PLANT1",
    "PLANT2password",
    "PLANT3secure",
    "PLANT4tokenvalue",
    "PLANT5bearerabcdef",
    "PLANT6annotationtoken",
    "PLANT7inputsecret",
    "glsa_PLANT8serviceaccounttoken",
]


def secret_dashboard(uid: str = "sec") -> dict[str, Any]:
    return {
        "dashboard": {
            "uid": uid,
            "title": "Keep this title",
            "schemaVersion": 39,
            "__inputs": [
                {
                    "name": "DS",
                    "type": "datasource",
                    "value": "https://u:PLANT7inputsecret@h.example/",
                }
            ],
            "annotations": {
                "list": [{"name": "a", "token": "PLANT6annotationtoken", "expr": "up"}]
            },
            "panels": [
                {
                    "id": 1,
                    "type": "stat",
                    "title": "p",
                    "links": [{"url": "https://x.example/d?a=1&token=PLANT4tokenvalue&b=2"}],
                    "datasource": {
                        "type": "prometheus",
                        "uid": "p",
                        "url": "https://admin:hunter2PLANT1@prom.example.com/api",
                        "password": "PLANT2password",
                        "secureJsonData": {"basicAuthPassword": "PLANT3secure"},
                    },
                    "targets": [
                        {
                            "refId": "A",
                            "expr": 'up{job="keep"}',
                            "headers": [{"Authorization": "Bearer PLANT5bearerabcdef"}],
                            "note": "Bearer PLANT5bearerabcdef and glsa_PLANT8serviceaccounttoken",
                        }
                    ],
                }
            ],
        },
        "meta": {"folderUid": "f1"},
    }


LIBRARY = {
    "result": {
        "totalCount": 1,
        "elements": [
            {
                "uid": "lib1",
                "name": "n",
                "type": "stat",
                "model": {
                    "datasource": {"jsonData": {"httpHeaderValue1": "PLANT3secure"}},
                    "list": [[{"apiKey": "PLANT2password"}]],
                },
            }
        ],
    }
}


def test_secrets_in_dashboards_and_library_panels_are_not_written(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/dashboards/uid/"):
            return httpx.Response(200, json=secret_dashboard())
        if request.url.path == "/api/library-elements":
            return httpx.Response(200, json=LIBRARY)
        return serve(uids=["sec"])(request)

    result = read(handler, tmp_path)
    out = tmp_path / "out"
    files = [p for p in out.rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = path.read_text()
        for planted in PLANTED:
            assert planted not in text, (path.name, planted)
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(out.stat().st_mode & 0o777) == "0o700"
    kept = json.loads(next((out / "dashboards").glob("*.json")).read_text())
    assert kept["dashboard"]["title"] == "Keep this title"
    assert kept["dashboard"]["panels"][0]["targets"][0]["expr"] == 'up{job="keep"}'
    assert result.redactions >= 8
    assert any(str(result.redactions) in w and "redacted" in w for w in result.warnings)


def test_the_file_names_differ_even_on_a_case_insensitive_file_system(tmp_path: Path) -> None:
    result = read(serve(uids=["Abc", "abc"]), tmp_path)
    folder = tmp_path / "out" / "dashboards"
    names = [p.name for p in folder.iterdir()]
    assert result.dashboards == 2 and len(names) == 2
    assert len({n.casefold() for n in names}) == 2
    recorded = {json.loads((folder / n).read_text())["meta"]["uid"] for n in names}
    assert recorded == {"Abc", "abc"}


def test_json_nested_a_hundred_thousand_deep_is_an_error_not_a_traceback(tmp_path: Path) -> None:
    deep = httpx.Response(200, content=b"[" * 100_000)
    with pytest.raises(GrafanaError):
        read(lambda _r: deep, tmp_path)


def test_a_fatal_error_halfway_leaves_no_export_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_REQUESTS", 3)
    with pytest.raises(GrafanaError):
        read(serve(), tmp_path)
    assert list(tmp_path.iterdir()) == []  # neither out/ nor a temporary folder


def test_a_server_that_caps_pages_below_the_limit_is_read_to_the_empty_page(
    tmp_path: Path,
) -> None:
    uids = [f"u{i}" for i in range(5)]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            page = int(request.url.params["page"])
            return httpx.Response(200, json=[{"uid": u} for u in uids[(page - 1) * 2 : page * 2]])
        return serve(uids=uids)(request)

    assert read(handler, tmp_path).dashboards == 5


def test_a_server_that_ignores_the_page_number_does_not_loop_or_duplicate(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            return httpx.Response(200, json=[{"uid": "a"}, {"uid": "b"}])
        return serve(uids=["a", "b"])(request)

    result = read(handler, tmp_path)
    assert result.dashboards == 2 and any("repeated" in w for w in result.warnings)


def test_folders_that_fill_every_page_are_reported_like_dashboards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_PAGES", 2)
    monkeypatch.setattr(dx, "PAGE_SIZE", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/folders":
            page = request.url.params["page"]
            return httpx.Response(200, json=[{"uid": f"f{page}", "title": "t"}])
        if request.url.path == "/api/search":
            return httpx.Response(200, json=[])
        return serve()(request)

    result = read(handler, tmp_path)
    assert any("folders were NOT read" in w and "more than 2 pages" in w for w in result.warnings)


def test_the_real_page_and_size_limits_are_what_the_docs_say() -> None:
    assert (dx.MAX_PAGES, dx.PAGE_SIZE, dx.MAX_REQUESTS) == (20, 1000, 6000)
    assert (models.MAX_DASHBOARDS, models.MAX_LIBRARY) == (5000, 5000)


def test_a_host_that_differs_only_after_normalising_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(GrafanaError, match="exactly as validated"):
        dx.http_getter("token", "https://MyStack.grafana.net/").__enter__()


def test_a_failure_of_one_dashboard_is_skipped_but_a_fatal_one_is_not(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/beta"):
            raise httpx.ConnectError("down")
        return serve()(request)

    with pytest.raises(GrafanaError, match="ConnectError"):
        read(handler, tmp_path)


def test_a_dashboard_larger_than_the_write_cap_is_counted_as_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_INPUT_BYTES", 200)
    result = read(serve(), tmp_path)
    assert result.dashboards == 0 and any(
        "3 dashboards were NOT read" in w for w in result.warnings
    )


def test_a_library_page_listing_stops_at_the_total_and_keeps_the_raw_shape(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/library-elements":
            seen.append(request.url.params["page"])
        return serve()(request)

    result = read(handler, tmp_path)
    assert seen == ["1"] and result.library_panels == 1
    saved = json.loads((tmp_path / "out" / "library-panels.json").read_text())
    assert saved["libraryPanels"][0]["uid"] == "lib1"
