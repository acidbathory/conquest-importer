"""The saved export is an allow-list: hostile fields never reach disk, and limits hold."""

import json
import re
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory import extract as ix
from importer.sources.dynatrace.inventory import models as im
from importer.sources.dynatrace.inventory import sanitize

HOST = "https://abc12345.apps.dynatrace.com"
TOKEN = "dt0s16.AAAAAAAA.SECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests
UUID = "00000000-0000-0000-0000-00000000000{}"
MONITOR = "HTTP_CHECK-00000000000000AB"
CANARIES = [
    "CANARY_HEADER",
    "CANARY_USER",
    "CANARY_PASS",
    "CANARY_APIKEY",
    "CANARY_TYPED",
    "CANARY_DESC",
    "CANARY_PATTERN",
    "CANARY_CSS",
    "CANARY_JUNK",
    "CANARY_OWNER",
    "CANARY_URLCRED",
    "CANARY_QUERY",
    "CANARY_CERT",
]
Handler = Callable[[httpx.Request], httpx.Response]


def hostile_monitor() -> dict[str, Any]:
    return {
        "entityId": MONITOR,
        "name": "https://CANARY_JUNK@host/x?token=CANARY_JUNK",
        "type": "HTTP",
        "enabled": True,
        "frequencyMin": 5,
        "locations": ["GEOLOCATION-0000000000000001", "CANARY_JUNK", 5, {"CANARY_JUNK": 1}],
        "tags": ["CANARY_JUNK"],
        "managementZones": ["CANARY_JUNK"],
        "script": {
            "version": "1.0",
            "secretField": "CANARY_JUNK",
            "requests": [
                {
                    "url": "https://CANARY_URLCRED:pw@shop.example.invalid/p?q=CANARY_QUERY#f",
                    "method": "post",
                    "requestBody": "CANARY_JUNK",
                    "authentication": {
                        "type": "BASIC",
                        "userName": "CANARY_USER",
                        "Password": "CANARY_PASS",
                    },
                    "clientCertificate": "CANARY_CERT",
                    "apiKey": "CANARY_APIKEY",
                    "description": "CANARY_DESC",
                    "configuration": {
                        "requestHeaders": [{"name": "X", "value": "CANARY_HEADER"}],
                        "userAgent": "CANARY_JUNK",
                        "followRedirects": False,
                        "extra": "CANARY_JUNK",
                    },
                    "validation": {
                        "rules": [
                            {
                                "type": "patternConstraint",
                                "passIfFound": True,
                                "value": "CANARY_PATTERN",
                                "regex": True,
                            },
                            {"type": "httpStatusesList", "passIfFound": False, "value": ">=400"},
                            {"type": "CANARY_JUNK"},
                        ]
                    },
                }
            ],
        },
        "anomalyDetection": {
            "outageHandling": {
                "globalOutage": True,
                "globalOutagePolicy": {"consecutiveRuns": 2, "x": "CANARY_JUNK"},
            },
            "loadingTimeThresholds": {
                "enabled": True,
                "thresholds": [{"valueMs": 3000, "x": "CANARY_JUNK"}],
            },
            "other": "CANARY_JUNK",
        },
    }


def hostile_browser() -> dict[str, Any]:
    return {
        "entityId": "SYNTHETIC_TEST-00000000000000AB",
        "name": "b",
        "type": "BROWSER",
        "frequencyMin": True,
        "script": {
            "type": "clickpath",
            "version": "1.0",
            "cookies": [{"name": "a", "value": "CANARY_JUNK"}],
            "configuration": {
                "device": {"deviceName": "CANARY_JUNK; DROP", "orientation": "portrait"}
            },
            "events": [
                {"type": "navigate", "url": "https://u:CANARY_PASS@x.invalid/a?b=CANARY_QUERY"},
                {
                    "type": "keystrokes",
                    "textValue": "CANARY_TYPED",
                    "value": "CANARY_TYPED",
                    "description": "CANARY_DESC",
                    "target": {"locators": [{"type": "css", "value": "CANARY_CSS"}]},
                },
                {"type": "javascript", "javaScript": "CANARY_JUNK"},
            ],
        },
    }


def serve(**overrides: Any) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/platform/document/v1/documents":
            docs = overrides.get("documents", [{"id": UUID.format(1), "name": "Board"}])
            return httpx.Response(200, json={"documents": docs})
        if path.endswith("/content"):
            return httpx.Response(200, json=overrides.get("content", {"tiles": {}}))
        if path == "/api/config/v1/dashboards":
            return httpx.Response(200, json={"dashboards": [{"id": UUID.format(1)}]})
        if path.startswith("/api/config/v1/dashboards/"):
            return httpx.Response(200, json=overrides.get("classic", {"tiles": []}))
        if path == "/api/v1/synthetic/monitors":
            ids = [{"entityId": MONITOR}, {"entityId": "SYNTHETIC_TEST-00000000000000AB"}]
            return httpx.Response(200, json={"monitors": ids})
        if path.endswith(MONITOR):
            return httpx.Response(200, json=hostile_monitor())
        if path.startswith("/api/v1/synthetic/monitors/"):
            return httpx.Response(200, json=hostile_browser())
        if path == "/api/v1/synthetic/locations":
            return httpx.Response(200, json={"locations": overrides.get("locations", [])})
        if path == "/api/v2/entities":
            return httpx.Response(
                200, json=overrides.get("entities", {"totalCount": 0, "entities": []})
            )
        return httpx.Response(404, json={})

    return handler


def read(handler: Handler, tmp_path: Path) -> ix.ExtractResult:
    with ix.http_getter(TOKEN, transport=httpx.MockTransport(handler)) as get:
        return ix.extract(get, HOST, tmp_path / "out")


def everything(tmp_path: Path) -> str:
    return "".join(p.read_text() for p in (tmp_path / "out").rglob("*") if p.is_file())


def test_a_hostile_monitor_is_saved_as_an_allow_list(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    text = everything(tmp_path)
    for canary in CANARIES:
        assert canary not in text, canary
    monitors = json.loads((tmp_path / "out/synthetics/monitors.json").read_text())["monitors"]
    http, browser = monitors
    assert http["locations"] == ["GEOLOCATION-0000000000000001"]
    request = http["script"]["requests"][0]
    assert request["url"] == "https://shop.example.invalid/p" and request["method"] == "POST"
    assert (
        request["authentication"] == sanitize.OMITTED
        and request["clientCertificate"] == sanitize.OMITTED
    )
    assert request["configuration"] == {
        "acceptAnyCertificate": False,
        "followRedirects": False,
        "userAgent": sanitize.OMITTED,
    }
    rules = request["validation"]["rules"]
    assert rules[0] == {"type": "patternConstraint", "passIfFound": True, "regex": True}
    assert rules[1] == {
        "type": "httpStatusesList",
        "passIfFound": False,
        "regex": False,
        "value": ">=400",
    }
    assert rules[2]["type"] == "other"
    assert http["anomalyDetection"]["outageHandling"]["globalOutagePolicy"] == {
        "consecutiveRuns": 2
    }
    assert set(http["anomalyDetection"]) == {"outageHandling", "loadingTimeThresholds"}
    assert http["anomalyDetection"]["loadingTimeThresholds"]["thresholds"] == [{"valueMs": 3000}]
    assert http["name"] == "monitor-1"  # the name looked like a URL with a token
    assert browser["frequencyMin"] is None  # JSON true is not 1
    script = browser["script"]
    assert (
        script["cookies"] == [sanitize.OMITTED]
        and script["configuration"]["device"]["deviceName"] == ""
    )
    assert [e["type"] for e in script["events"]] == ["navigate", "keystrokes", "javascript"]
    assert script["events"][0]["url"] == "https://x.invalid/a"
    assert script["events"][1]["target"] == {"locators": [{"type": "css"}]}


def test_the_allow_listed_monitor_still_plans_the_same_way(tmp_path: Path) -> None:
    from importer.sources.dynatrace.inventory.synthetics import plan_synthetics

    read(serve(), tmp_path)
    monitors = im.load_monitors(tmp_path / "out")
    assert monitors.skipped == 0 and len(monitors.items) == 2
    plan = plan_synthetics(monitors.items, [])
    assert [m.target for m in plan.monitors] == ["SCRIPT_API", "SCRIPT_BROWSER"]


def test_entity_values_must_look_like_product_names(tmp_path: Path) -> None:
    page = {
        "totalCount": 1,
        "entities": [
            {
                "type": "SERVICE",
                "properties": {
                    "serviceTechnologyTypes": [
                        "Java",
                        "Node.js",
                        "a|b",
                        "x" * 80,
                        "<img>",
                        "C++",
                        "CANARY_JUNK; rm",
                    ],
                    "osType": "LINUX",
                },
            }
        ],
    }
    read(serve(entities=page), tmp_path)
    saved = json.loads((tmp_path / "out/entities/entities-SERVICE.json").read_text())
    assert saved["entities"][0]["technologies"] == ["Java", "Node.js", "C++"]
    assert "CANARY" not in everything(tmp_path)


def test_dashboard_content_keeps_only_what_the_planner_reads(tmp_path: Path) -> None:
    content = {
        "version": 21,
        "owner": "CANARY_OWNER",
        "modificationInfo": {"lastModifiedBy": "CANARY_OWNER"},
        "variables": [
            {
                "key": "v",
                "type": "query",
                "input": "x",
                "multiple": True,
                "defaultValue": ["a"],
                "secret": "CANARY_JUNK",
            }
        ],
        "tiles": {
            "1": {
                "type": "data",
                "title": "t",
                "query": "timeseries avg(m)",
                "visualization": "lineChart",
                "customLinkSettings": {"url": "CANARY_JUNK"},
                "queryConfig": {"x": "CANARY_JUNK"},
                "timeframe": {"tileTimeframeEnabled": True, "from": "CANARY_JUNK"},
                "segments": {"tileSegmentsEnabled": False, "tileSegments": ["CANARY_JUNK"]},
                "visualizationSettings": {
                    "thresholds": [{"id": 1}],
                    "chartSettings": {"x": "CANARY_JUNK"},
                },
            },
            "2": {"type": "code", "title": "c", "input": "CANARY_JUNK"},
            "3": "not a tile",
        },
        "layouts": {"1": {"x": 0, "y": 0, "w": 24, "h": 6, "z": "CANARY_JUNK"}},
        "settings": {"defaultTimeframe": {"value": "x"}},
    }
    envelope_body = {"content": content, "owner": "CANARY_OWNER"}
    read(
        serve(
            content=envelope_body,
            documents=[{"id": UUID.format(1), "name": "n", "owner": "CANARY_OWNER"}],
        ),
        tmp_path,
    )
    assert "CANARY" not in everything(tmp_path)
    saved = json.loads(next((tmp_path / "out/dashboards").glob("*.json")).read_text())
    inner = saved["content"]
    assert set(inner) == {"version", "variables", "tiles", "layouts", "settings"}
    assert set(inner["tiles"]) == {"1", "2"} and inner["tiles"]["2"]["input"] == sanitize.OMITTED
    assert inner["tiles"]["1"]["visualizationSettings"] == {"thresholds": [{"id": 1}]}
    assert inner["tiles"]["1"]["timeframe"] == {"tileTimeframeEnabled": True}
    assert inner["layouts"]["1"] == {"x": 0, "y": 0, "w": 24, "h": 6}
    assert inner["settings"] == {"defaultTimeframe": True}
    assert inner["variables"][0]["multiple"] is True


def test_a_response_without_tiles_is_not_saved_with_its_metadata(tmp_path: Path) -> None:
    body = {"owner": "CANARY_OWNER", "note": "no tiles here"}
    result = read(serve(content=body), tmp_path)
    assert result.dashboards == 0 and any("dashboards were not read" in w for w in result.warnings)
    assert "CANARY" not in everything(tmp_path)
    assert not list((tmp_path / "out").glob("dashboards/*"))


def test_a_classic_dashboard_loses_owner_tags_and_unknown_fields(tmp_path: Path) -> None:
    classic = {
        "id": UUID.format(1),
        "dashboardMetadata": {
            "name": "Ops",
            "owner": "CANARY_OWNER",
            "tags": ["CANARY_JUNK"],
            "dashboardFilter": {
                "managementZone": {"id": "1", "name": "CANARY_JUNK"},
                "timeframe": "-2h",
            },
            "dynamicFilters": {"filters": ["OS_TYPE", "HOST_TAG_KEY:env", "x y; z"]},
        },
        "tiles": [
            {
                "name": "m",
                "tileType": "MARKDOWN",
                "markdown": "hello",
                "bounds": {"top": 1},
                "extra": "CANARY_JUNK",
            },
            {
                "name": "c",
                "tileType": "DATA_EXPLORER",
                "queries": [
                    {"metric": "builtin:host.cpu.usage", "filterBy": "CANARY_JUNK"},
                    {"metric": "bad metric!"},
                ],
                "tileFilter": {"managementZone": {"name": "CANARY_JUNK"}},
            },
        ],
    }
    read(serve(classic=classic), tmp_path)
    assert "CANARY" not in everything(tmp_path)
    saved = json.loads(next((tmp_path / "out/classic-dashboards").glob("*.json")).read_text())
    meta = saved["dashboardMetadata"]
    assert meta["owner"] == "" and meta["dashboardFilter"] == {"managementZone": True}
    assert meta["dynamicFilters"]["filters"] == ["OS_TYPE", "HOST_TAG_KEY:env"]
    assert saved["tiles"][1]["queries"] == [{"metric": "builtin:host.cpu.usage"}]
    assert saved["tiles"][1]["tileFilter"] == {"managementZone": True}


@pytest.mark.skipif(not hasattr(stat, "S_IMODE"), reason="POSIX permissions")
def test_files_are_private_to_the_owner(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    out = tmp_path / "out"
    folders = [out, *[p for p in out.rglob("*") if p.is_dir()]]
    files = [p for p in out.rglob("*") if p.is_file()]
    assert files and all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in files)
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o700 for p in folders)


def test_a_page_key_of_100000_characters_is_a_warning_not_a_traceback(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/document/v1/documents":
            return httpx.Response(200, json={"documents": [], "nextPageKey": "k" * 100_000})
        return serve()(request)

    result = read(handler, tmp_path)
    assert any("page key" in w and "too long" in w for w in result.warnings)


def test_a_key_just_inside_the_limit_is_followed(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if (
            request.url.path == "/platform/document/v1/documents"
            and "page-key" not in request.url.params
        ):
            return httpx.Response(200, json={"documents": [], "nextPageKey": "k" * ix.MAX_PAGE_KEY})
        return serve()(request)

    read(handler, tmp_path)
    assert any(len(r.url.params.get("page-key", "")) == ix.MAX_PAGE_KEY for r in seen)


def test_the_getter_turns_an_invalid_url_into_a_clean_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    with (
        ix.http_getter(TOKEN, transport=httpx.MockTransport(handler)) as get,
        pytest.raises(DynatraceError, match="InvalidURL"),
    ):
        get(HOST + "/x", {"k": "a" * 100_000})


def test_a_boolean_total_count_is_not_a_number(tmp_path: Path) -> None:
    page = {"totalCount": True, "entities": []}
    result = read(serve(entities=page), tmp_path)
    assert not any("partial" in w for w in result.warnings)
    assert not list((tmp_path / "out").glob("entities/*"))
    assert im.is_count(0) and not im.is_count(True) and not im.is_count(-1) and not im.is_count("1")


def test_a_partial_entity_list_is_reported(tmp_path: Path) -> None:
    page = {"totalCount": 5, "entities": [{"type": "HOST", "properties": {"osType": "LINUX"}}]}
    result = read(serve(entities=page), tmp_path)
    assert any(w.startswith("entity lists are partial for HOST") for w in result.warnings)


def test_duplicate_ids_in_a_listing_are_fetched_and_written_once(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    inner = serve(
        documents=[{"id": UUID.format(1)}, {"id": UUID.format(1)}, {"id": UUID.format(2)}]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return inner(request)

    result = read(handler, tmp_path)
    assert result.dashboards == 2 and not any(
        "dashboards were not read" in w for w in result.warnings
    )
    assert sum(r.url.path.endswith("/content") for r in seen) == 2


def test_the_fetch_cap_is_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ix, "MAX_DETAIL_FETCHES", 2)
    seen: list[httpx.Request] = []
    inner = serve(documents=[{"id": UUID.format(i)} for i in range(1, 6)])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return inner(request)

    result = read(handler, tmp_path)
    assert sum(r.url.path.endswith("/content") for r in seen) == 2 and result.dashboards == 2
    assert any("3 dashboards were not read" in w for w in result.warnings)


def test_the_page_cap_allows_exactly_that_many_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ix, "MAX_PAGES", 3)

    def pages(more_after: int) -> Handler:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/platform/document/v1/documents":
                n = int(request.url.params.get("page-key", "1"))
                body: dict[str, Any] = {"documents": []}
                if n < more_after:
                    body["nextPageKey"] = str(n + 1)
                return httpx.Response(200, json=body)
            return serve()(request)

        return handler

    ok = read(pages(3), tmp_path)  # three pages, the last has no key
    assert not any("more than 3 pages" in w for w in ok.warnings)
    (tmp_path / "out").rename(tmp_path / "out1")
    bad = read(pages(4), tmp_path)  # a fourth page would be needed
    assert any("more than 3 pages of documents" in w for w in bad.warnings)


def test_a_script_list_is_cut_at_the_item_cap() -> None:
    raw = {"type": "clickpath", "events": [{"type": "navigate", "url": "https://x.invalid/"}] * 300}
    out = sanitize.script("BROWSER", raw)
    assert len(out["events"]) == sanitize.MAX_LIST_ITEMS == 200


def test_a_save_over_the_size_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ix, "MAX_INPUT_BYTES", 50)
    result = read(serve(locations=[{"entityId": "GEOLOCATION-01", "name": "x" * 200}]), tmp_path)
    assert any("larger than the planner accepts" in w for w in result.warnings)


@pytest.mark.parametrize("value", [True, False, 0, -3, 10**7, "5", 5.0, None])
def test_whole_number_rejects_bools_and_out_of_range_values(value: object) -> None:
    assert im.whole_number(value) is None
    assert im.whole_number(7) == 7


@pytest.mark.parametrize(
    "env",
    [
        "https://abc12345.apps.dynatrace.com//",
        "https://abc12345.apps.dynatrace.com/ ",
        "https://abc12345.apps.dynatrace.com/x/",
    ],
)
def test_only_one_trailing_slash_is_removed(env: str, tmp_path: Path) -> None:
    with (
        ix.http_getter(TOKEN, transport=httpx.MockTransport(serve())) as get,
        pytest.raises(ValueError, match="DYNATRACE_ENV_URL"),
    ):
        ix.extract(get, env, tmp_path / "out")


def test_the_canaries_cover_every_kind_of_field() -> None:
    assert len(set(CANARIES)) == len(CANARIES) and all(
        re.fullmatch(r"CANARY_[A-Z]+", c) for c in CANARIES
    )


def test_a_dashboard_name_that_looks_like_a_token_is_replaced(tmp_path: Path) -> None:
    docs = [{"id": UUID.format(1), "name": "https://user@host/?token=CANARY_JUNK"}]
    read(serve(documents=docs), tmp_path)
    saved = json.loads(next((tmp_path / "out/dashboards").glob("*.json")).read_text())
    assert saved["name"] == "dashboard-1" and "CANARY" not in everything(tmp_path)
