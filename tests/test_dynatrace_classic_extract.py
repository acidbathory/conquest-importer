"""The classic extractor on the real getter, driven by an in-memory HTTP transport."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.core import safe_http
from importer.sources.dynatrace.classic import extract as cx
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.extract import (
    ClassicError,
    api_token,
    extract,
    http_getter,
    is_env_url,
)
from importer.sources.dynatrace.classic.models import load_objects

TOKEN = "dt0s16.ABCDEFGH.SECRETSECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests
HOST = "https://abc12345.apps.dynatrace.com"
CLASSIC = "https://abc12345.live.dynatrace.com"
PRIMARY = "/platform/classic/environment-api/v2/settings/objects"
FALLBACK = "/platform/settings/v1/objects"
Handler = Callable[[httpx.Request], httpx.Response]


def event(n: int = 1) -> dict[str, Any]:
    return {
        "objectId": f"id-{n}",
        "schemaId": models.METRIC_EVENTS,
        "scope": "environment",
        "value": {
            "summary": "s",
            "queryDefinition": {"type": "METRIC_KEY", "metricKey": "k", "aggregation": "AVG"},
            "modelProperties": {
                "type": "STATIC_THRESHOLD",
                "threshold": 1,
                "alertCondition": "ABOVE",
                "violatingSamples": 1,
                "samples": 1,
            },
        },
    }


def getter(
    handler: Handler, host: str = HOST, token: str = TOKEN
) -> AbstractContextManager[cx.Getter]:
    return http_getter(token, host, transport=httpx.MockTransport(handler))


def schema_of(request: httpx.Request) -> str:
    params = request.url.params
    return params.get("schemaIds") or params.get("schema-id") or ""


def serve(by_schema: dict[str, list[dict[str, Any]]], pages: int = 1) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        schema = schema_of(request)
        number = 0
        if "nextPageKey" in request.url.params:
            assert len(request.url.params) == 1, "a follow-up page sends only the key"
            number = int(request.url.params["nextPageKey"])
            schema = models.METRIC_EVENTS  # only this schema pages in these tests
        items = by_schema.get(schema, [])
        body: dict[str, Any] = {"items": items if number == 0 else [event(100 + number)]}
        if number + 1 < pages and schema == models.METRIC_EVENTS:
            body["nextPageKey"] = str(number + 1)
        return httpx.Response(200, json=body)

    return handler


def read(handler: Handler, tmp_path: Path, host: str = HOST) -> cx.ExtractResult:
    with getter(handler, host) as get:
        return extract(get, host, tmp_path / "out")


def test_it_reads_every_allowed_schema_with_get_only_and_writes_a_loadable_folder(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve({models.METRIC_EVENTS: [event()]})(request)

    result = read(handler, tmp_path)
    assert {r.method for r in seen} == {"GET"}
    assert {r.url.host for r in seen} == {"abc12345.apps.dynatrace.com"}
    assert {r.url.path for r in seen} == {PRIMARY}
    assert {schema_of(r) for r in seen} == set(models.READ_SCHEMAS)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert result.counts[models.METRIC_EVENTS] == 1
    loaded = load_objects(tmp_path / "out")
    assert [o.objectId for o in loaded] == ["id-1"] and not loaded[0].problem


def test_follow_up_pages_send_only_the_page_key(tmp_path: Path) -> None:
    result = read(serve({models.METRIC_EVENTS: [event()]}, pages=3), tmp_path)
    assert result.counts[models.METRIC_EVENTS] == 3


def test_a_classic_host_uses_the_classic_path_and_an_api_token_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"items": []})

    read(handler, tmp_path, CLASSIC)
    assert {r.url.path for r in seen} == {"/api/v2/settings/objects"}
    assert seen[0].headers["authorization"] == f"Api-Token {TOKEN}"


def test_a_404_on_the_primary_path_falls_back_and_other_dialect_is_used(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == PRIMARY:
            return httpx.Response(404, json={"error": {"message": "not found"}})
        assert request.url.params["add-fields"] == "value"
        assert request.url.params["scope"] == "environment"
        items = [event()] if schema_of(request) == models.METRIC_EVENTS else []
        return httpx.Response(200, json={"items": items})

    result = read(handler, tmp_path)
    assert result.counts[models.METRIC_EVENTS] == 1
    assert FALLBACK in {r.url.path for r in seen}
    assert any("entity-scoped" in w for w in result.warnings)


def test_a_schema_that_404s_on_both_paths_is_a_warning_naming_it(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if schema_of(request) == models.DETECTORS:
            return httpx.Response(404, json={"error": {"message": "unknown schema"}})
        return httpx.Response(200, json={"items": []})

    result = read(handler, tmp_path)
    warned = [w for w in result.warnings if models.DETECTORS in w]
    assert len(warned) == 1 and "404" in warned[0]
    assert models.DETECTORS not in result.counts


def test_a_401_stops_the_run_and_does_not_echo_the_token(tmp_path: Path) -> None:
    echo = f"bad {TOKEN}; Authorization: Bearer {TOKEN}"
    with pytest.raises(ClassicError) as err:
        read(lambda _r: httpx.Response(401, json={"error": {"message": echo}}), tmp_path)
    text = str(err.value)
    assert "returned 401" in text and "SECRETSECRET" not in text and TOKEN not in text


def test_a_redirect_is_an_error_and_is_not_followed(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    with pytest.raises(ClassicError, match="returned 302"):
        read(handler, tmp_path)
    assert {r.url.host for r in seen} == {"abc12345.apps.dynatrace.com"}


def test_the_getter_refuses_another_host() -> None:
    with (
        getter(lambda _r: httpx.Response(200, json={})) as get,
        pytest.raises(ClassicError, match="not the configured"),
    ):
        get("https://evil.example/x", {})


@pytest.mark.parametrize(
    "url",
    [
        "http://abc.apps.dynatrace.com",
        "https://abc.apps.dynatrace.com.evil.example",
        "https://evil.example/https://abc.apps.dynatrace.com",
        "https://abc.apps.dynatrace.com:8443",
        "https://user@abc.apps.dynatrace.com",
        "https://abc.sprint.dynatracelabs.com",
        "https://ABC.apps.dynatrace.com",
        "https://.apps.dynatrace.com",
        "https://abc.apps.dynatrace.com/path",
        "",
    ],
)
def test_only_dynatrace_tenant_hosts_are_accepted(url: str, tmp_path: Path) -> None:
    assert not is_env_url(url)
    with (
        getter(lambda _r: httpx.Response(200, json={})) as get,
        pytest.raises(ValueError, match="DYNATRACE_ENV_URL"),
    ):
        extract(get, url, tmp_path / "out")


def test_both_tenant_kinds_are_accepted() -> None:
    assert is_env_url(HOST) and is_env_url(HOST + "/") and is_env_url(CLASSIC)


def test_the_page_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cx, "MAX_PAGES", 3)
    with pytest.raises(ClassicError, match="more than 3 pages"):
        read(serve({models.METRIC_EVENTS: [event()]}, pages=50), tmp_path)


def test_the_total_page_count_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cx, "MAX_TOTAL_PAGES", 4)
    with pytest.raises(ClassicError, match="more than 4 pages in total"):
        read(serve({}), tmp_path)


def test_the_object_count_per_schema_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cx, "MAX_OBJECTS", 2)
    with pytest.raises(ClassicError, match="more than 2 objects"):
        read(serve({models.METRIC_EVENTS: [event(1), event(2), event(3)]}), tmp_path)


def test_a_huge_response_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 100)
    big = {"items": [{"objectId": "x" * 500}]}
    with pytest.raises(ClassicError, match="larger than the importer accepts"):
        read(lambda _r: httpx.Response(200, json=big), tmp_path)


def test_total_bytes_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 300)
    with pytest.raises(ClassicError, match="bytes in total"):
        read(lambda _r: httpx.Response(200, json={"items": [event(n) for n in range(5)]}), tmp_path)


def test_a_slow_run_hits_the_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 400
        return now[0]

    monkeypatch.setattr(safe_http, "_clock", clock)
    with pytest.raises(ClassicError, match="took longer"):
        read(lambda _r: httpx.Response(200, json={"items": []}), tmp_path)


def test_secrets_never_reach_the_export_folder(tmp_path: Path) -> None:
    note = {
        "objectId": "n1",
        "schemaId": models.NOTIFICATIONS,
        "scope": "environment",
        "value": {
            "enabled": True,
            "type": "WEBHOOK",
            "displayName": "Bridge",
            "alertingProfile": "p",
            "webHookNotification": {"url": "https://h/?token=PLANTED-TOKEN"},
            "emailNotification": {"toRecipients": ["who@example.invalid"]},
        },
    }
    detector = {
        "objectId": "d1",
        "schemaId": models.DETECTORS,
        "scope": "environment",
        "value": {
            "title": "t",
            "analyzer": {"name": "x", "input": []},
            "executionSettings": {"actor": "00000000-PLANTED-ACTOR", "queryOffset": None},
        },
    }
    read(serve({models.NOTIFICATIONS: [note], models.DETECTORS: [detector]}), tmp_path)
    text = "".join(f.read_text() for f in (tmp_path / "out").rglob("*.json"))
    assert "PLANTED" not in text and "example.invalid" not in text and "Bridge" in text


def test_an_existing_output_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    with pytest.raises(ValueError, match="already has files"):
        read(serve({}), tmp_path)


def test_a_200_that_is_not_an_object_with_items_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ClassicError, match="not a JSON object"):
        read(lambda _r: httpx.Response(200, json=["x"]), tmp_path)
    with pytest.raises(ClassicError, match="no `items` list"):
        read(lambda _r: httpx.Response(200, json={"totalCount": 0}), tmp_path / "second")


@pytest.mark.parametrize(
    ("env", "message"),
    [("", "not set"), ("dt0s16.a b", "plain ASCII"), ("tok“en", "plain ASCII")],
)
def test_the_token_is_read_from_the_environment_and_checked(
    env: str, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_API_TOKEN", env)
    with pytest.raises(ClassicError, match=message):
        api_token()
    monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
    assert api_token() == TOKEN


def test_the_export_json_is_what_the_loader_reads(tmp_path: Path) -> None:
    read(serve({models.PROFILE: [{**event(), "schemaId": models.PROFILE}]}), tmp_path)
    files = sorted(p.name for p in (tmp_path / "out" / "settings").glob("*.json"))
    assert files and all(f.endswith(".json") for f in files)
    data = json.loads((tmp_path / "out" / "settings" / files[0]).read_text())
    assert "items" in data and "schemaId" in data


def test_the_command_reads_the_environment_and_never_prints_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from importer.cli import app
    from importer.commands import dynatrace_classic as command

    def with_transport(token: str, env_url: str) -> AbstractContextManager[cx.Getter]:
        return http_getter(
            token,
            env_url,
            transport=httpx.MockTransport(serve({models.METRIC_EVENTS: [event()]})),
        )

    monkeypatch.setattr(command, "http_getter", with_transport)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    out = tmp_path / "export"
    result = CliRunner().invoke(app, ["extract-dynatrace-classic", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Read 1 objects" in result.output and TOKEN not in result.output
    assert (out / "settings").is_dir()


def test_the_command_fails_clearly_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    monkeypatch.delenv("DYNATRACE_API_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["extract-dynatrace-classic", "--out", str(tmp_path / "o")])
    assert result.exit_code == 1 and "DYNATRACE_API_TOKEN is not set" in result.output
