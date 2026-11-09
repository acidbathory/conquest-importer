"""Encoded tokens, caps across pages, the overall deadline, redirects and empty tokens."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest

from importer.core import safe_http
from importer.sources.gcp import extract as gx
from importer.sources.gcp.extract import GcpError, access_token, extract

SLASHY = "ya29.a0Af/H6+SMB=SECRETSECRET"
POLICY = {
    "name": "projects/my-project/alertPolicies/1",
    "displayName": "cpu",
    "conditions": [{"displayName": "c", "conditionThreshold": {}}],
}

Handler = Callable[[httpx.Request], httpx.Response]


def pages(count: int) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("alertPolicies"):
            nxt = int(request.url.params.get("pageToken", "0")) + 1
            body: dict[str, Any] = {"alertPolicies": [POLICY]}
            if nxt < count:
                body["nextPageToken"] = str(nxt)
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={})

    return handler


def read(handler: Handler, tmp_path: Path, token: str = "ya29.plain") -> gx.ExtractResult:  # noqa: S107
    with gx.http_getter(token, transport=httpx.MockTransport(handler)) as get:
        return extract(get, "my-project", tmp_path / "out")


def fake_clock(step: int) -> Callable[[], float]:
    ticks: Iterator[int] = iter(range(0, 100_000, step))
    return lambda: float(next(ticks))


@pytest.mark.parametrize(
    "echo",
    [
        quote(SLASHY, safe=""),
        quote(SLASHY),
        quote_plus(SLASHY),
        f"Bearer: {SLASHY}",
        f"bearer:{SLASHY}",
        f"Authorization: Bearer {quote(SLASHY)}",
    ],
)
@pytest.mark.parametrize("status", [302, 401, 500])
def test_encoded_forms_of_the_token_and_colon_bearer_are_scrubbed(
    echo: str, status: int, tmp_path: Path
) -> None:
    body = {"error": {"message": f"denied {echo} end"}}
    with pytest.raises(GcpError) as err:
        read(lambda _r: httpx.Response(status, json=body), tmp_path, SLASHY)
    text = str(err.value)
    assert f"returned {status}" in text
    for part in ("SECRETSECRET", "SMB", quote(SLASHY, safe="")):
        assert part not in text


def test_bytes_are_capped_across_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 300)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return pages(50)(request)

    with pytest.raises(GcpError, match="bytes in total"):
        read(handler, tmp_path)
    assert 1 < len(calls) < 50


def test_a_slow_run_hits_the_overall_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "_clock", fake_clock(40))
    monkeypatch.setattr(safe_http, "DEADLINE_SECONDS", 100)
    with pytest.raises(GcpError, match="took longer than 100 seconds"):
        read(pages(50), tmp_path)


def test_the_deadline_is_checked_while_a_body_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "_clock", fake_clock(40))
    monkeypatch.setattr(safe_http, "DEADLINE_SECONDS", 100)

    class Drip(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            for _ in range(20):
                yield b" "

    with pytest.raises(GcpError, match="took longer than"):
        read(lambda _r: httpx.Response(200, stream=Drip()), tmp_path)


def test_redirects_are_switched_off_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[dict[str, Any]] = []

    class Recording(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            made.append(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", Recording)
    with gx.http_getter("token"):
        pass
    assert made[0]["follow_redirects"] is False


@pytest.mark.parametrize("env", ["", "   "])
def test_an_empty_token_has_its_own_message(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_ACCESS_TOKEN", env)
    monkeypatch.setattr(gx, "_gcloud_token", lambda: "")
    with pytest.raises(GcpError) as err:
        access_token()
    assert "no access token was returned" in str(err.value)
    assert "plain ASCII" not in str(err.value)


@pytest.mark.parametrize("body", [[], [1, 2], "text", 5])
def test_a_200_that_is_not_a_json_object_is_an_error_not_zero_policies(
    body: Any, tmp_path: Path
) -> None:
    with pytest.raises(GcpError, match="not a JSON object"):
        read(lambda _r: httpx.Response(200, json=body), tmp_path)


def test_an_optional_list_with_that_answer_is_a_warning(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("alertPolicies"):
            return httpx.Response(200, json={"alertPolicies": [POLICY]})
        return httpx.Response(200, json=[])

    result = read(handler, tmp_path)
    assert result.policies == 1 and len(result.warnings) == 3
