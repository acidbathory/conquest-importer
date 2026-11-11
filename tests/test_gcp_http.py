"""The real Google getter and extractor, driven by an in-memory HTTP transport."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import safe_http
from importer.sources.gcp import extract as gx
from importer.sources.gcp.extract import GcpError, access_token, extract

TOKEN = "ya29.a0AfH6SMBSECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests
POLICY = {
    "name": "projects/my-project/alertPolicies/1",
    "displayName": "cpu",
    "conditions": [{"displayName": "c", "conditionThreshold": {}}],
}

Handler = Callable[[httpx.Request], httpx.Response]


def getter(handler: Handler, token: str = TOKEN) -> AbstractContextManager[gx.Getter]:
    return gx.http_getter(token, transport=httpx.MockTransport(handler))


def serve(policy_pages: int = 1, other_status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("alertPolicies"):
            token = request.url.params.get("pageToken", "0")
            nxt = int(token) + 1
            body: dict[str, Any] = {"alertPolicies": [POLICY]}
            if nxt < policy_pages:
                body["nextPageToken"] = str(nxt)
            return httpx.Response(200, json=body)
        if other_status != 200:
            return httpx.Response(other_status, json={"error": {"message": "nope"}})
        return httpx.Response(200, json={})

    return handler


def read(handler: Handler, tmp_path: Path, token: str = TOKEN) -> gx.ExtractResult:
    with getter(handler, token) as get:
        return extract(get, "my-project", tmp_path / "out")


def test_the_real_getter_reads_pages_and_sends_a_bearer_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve(policy_pages=2)(request)

    result = read(handler, tmp_path)
    assert result.policies == 2
    assert all(r.method == "GET" for r in seen)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert {r.url.host for r in seen} == {"monitoring.googleapis.com", "logging.googleapis.com"}


@pytest.mark.parametrize("body", [["a", "list"], "text", 5, None, {"error": ["x"]}])
def test_an_error_body_of_any_shape_gives_a_clean_error(body: Any, tmp_path: Path) -> None:
    with pytest.raises(GcpError, match="returned 500"):
        read(lambda _r: httpx.Response(500, json=body), tmp_path)


def test_a_401_that_echoes_the_token_does_not_leak_it(tmp_path: Path) -> None:
    echo = f"bad {TOKEN}; header was Authorization: Bearer {TOKEN} and Bearer ya29.OTHER.LEAKED"
    with pytest.raises(GcpError) as err:
        read(lambda _r: httpx.Response(401, json={"error": {"message": echo}}), tmp_path)
    text = str(err.value)
    assert "returned 401" in text
    for part in ("SECRETSECRET", "LEAKED", TOKEN):
        assert part not in text


def test_a_long_server_message_is_capped(tmp_path: Path) -> None:
    body = {"error": {"message": "x" * 5000}}
    with pytest.raises(GcpError) as err:
        read(lambda _r: httpx.Response(400, json=body), tmp_path)
    assert len(str(err.value)) < 400


def test_control_characters_in_a_server_message_are_removed(tmp_path: Path) -> None:
    body = {"error": {"message": "bad\x1b[31m\nred"}}
    with pytest.raises(GcpError) as err:
        read(lambda _r: httpx.Response(400, json=body), tmp_path)
    assert "\x1b" not in str(err.value) and "\n" not in str(err.value)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_an_error_and_is_not_followed(status: int, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": "https://evil.example/x"})

    with pytest.raises(GcpError, match=f"returned {status}"):
        read(handler, tmp_path)
    assert len(calls) == 1 and "evil" not in calls[0]


def test_a_body_that_is_not_json_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GcpError, match="not JSON"):
        read(lambda _r: httpx.Response(200, content=b"<html>"), tmp_path)


def test_a_declared_oversize_response_is_refused(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-length": str(safe_http.MAX_RESPONSE_BYTES + 1)}
        )

    with pytest.raises(GcpError, match="larger than"):
        read(handler, tmp_path)


def test_an_oversize_body_without_a_length_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 50)
    with pytest.raises(GcpError, match="larger than"):
        read(lambda _r: httpx.Response(200, json={"alertPolicies": ["x" * 100]}), tmp_path)


def test_a_connection_failure_names_only_the_error_type(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {TOKEN}")

    with pytest.raises(GcpError) as err:
        read(handler, tmp_path)
    assert str(err.value) == "could not reach Google Cloud: ConnectError"


def test_the_total_page_count_across_lists_is_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gx, "MAX_TOTAL_PAGES", 3)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return serve(policy_pages=50)(request)

    with pytest.raises(GcpError, match="pages in total"):
        read(handler, tmp_path)
    assert len(calls) == 3


def test_optional_lists_count_toward_the_total_page_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gx, "MAX_TOTAL_PAGES", 2)
    result = read(serve(policy_pages=1), tmp_path)
    assert any("pages in total" in w for w in result.warnings)


@pytest.mark.parametrize(
    "token", ["ya29.éé.SECRET", "ya29.ab cd", "ya29.a" + chr(0x2013) + "b", "a\nb"]
)
def test_a_token_with_odd_characters_is_refused_without_echoing_it(
    token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", token)
    with pytest.raises(GcpError) as err:
        access_token()
    text = str(err.value)
    assert "plain ASCII" in text
    for part in ("ya29", "é", "SECRET", chr(0x2013), "cd"):
        assert part not in text


def test_the_cli_hides_the_token_and_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", TOKEN)

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"invalid {TOKEN}"}})

    monkeypatch.setattr(
        "importer.cli.http_getter", lambda t: gx.http_getter(t, httpx.MockTransport(handler))
    )
    out = CliRunner().invoke(
        app, ["extract-gcp", "--project", "my-project", "--out", str(tmp_path / "o")]
    )
    assert out.exit_code == 1 and "returned 401" in out.output
    assert "SECRETSECRET" not in out.output and TOKEN not in out.output


def test_the_cli_runs_end_to_end_on_the_real_getter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(
        "importer.cli.http_getter", lambda t: gx.http_getter(t, httpx.MockTransport(serve()))
    )
    out = CliRunner().invoke(
        app, ["extract-gcp", "--project", "my-project", "--out", str(tmp_path / "o")]
    )
    assert out.exit_code == 0, out.output
    assert (
        json.loads((tmp_path / "o/policies/policies.json").read_text())[0]["displayName"] == "cpu"
    )
