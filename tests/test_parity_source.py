"""Reading the source: allow-list, host checks, caps and token scrubbing, all with fake HTTP."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from importer.core import safe_http
from importer.parity.model import ParityError
from importer.parity.source import (
    MAX_POINTS,
    MAX_SERIES,
    allowed,
    check_direct,
    open_reader,
    parse_matrix,
)

TOKEN = "glsa_secrettoken123"  # noqa: S105 - a fake
GRAFANA = "https://stack.grafana.net"


def matrix(series: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": "matrix", "result": series}}


def serve(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_allow_list_accepts_only_query_range_and_lookups() -> None:
    q = {"query": "up", "start": "1", "end": "2", "step": "60"}
    assert allowed("/api/v1/query_range", q)
    assert allowed("/api/datasources/proxy/uid/abc-1/api/v1/query_range", q)
    assert allowed("/api/datasources/name/Prom 1", {})
    assert not allowed("/api/v1/admin/tsdb/delete_series", q)
    assert not allowed("/api/v1/query_range", {"query": "up", "evil": "1"})
    assert not allowed("/api/datasources/proxy/uid/a/../../admin", {})


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:9090",
        "https://example.com",
        "http://user:pw@127.0.0.1:9090",
        "http://127.0.0.1:9090?x=1",
        "ftp://127.0.0.1",
        "http://127.0.0.1:9090/../x",
    ],
)
def test_direct_urls_are_checked(url: str) -> None:
    with pytest.raises(ParityError):
        check_direct(url, None)


def test_direct_loopback_and_named_https_host_pass() -> None:
    assert check_direct("http://127.0.0.1:9090/", None) == "http://127.0.0.1:9090"
    assert (
        check_direct("https://prom.example.com", "prom.example.com") == "https://prom.example.com"
    )
    with pytest.raises(ParityError):
        check_direct("https://prom.example.com", "other.example.com")


def test_direct_read_returns_series_and_sends_get_without_auth() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = matrix(
            [{"metric": {"__name__": "up", "job": "a"}, "values": [[60, "1"], [120, "NaN"]]}]
        )
        return httpx.Response(200, json=body)

    with open_reader("http://127.0.0.1:9090", transport=serve(handler)) as read:
        got = read("prometheus", "up", 0, 120, 60)
    assert got.series[0].points == {60: 1.0}  # NaN skipped
    assert seen[0].method == "GET" and "authorization" not in seen[0].headers
    assert seen[0].url.path == "/api/v1/query_range"


def test_grafana_proxy_resolves_a_name_and_sends_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/api/datasources/uid/"):
            return httpx.Response(404, json={"message": "not found"})
        if request.url.path.startswith("/api/datasources/name/"):
            return httpx.Response(200, json={"uid": "p1", "type": "prometheus"})
        return httpx.Response(200, json=matrix([]))

    with open_reader("My Prom", transport=serve(handler), token=TOKEN, grafana=GRAFANA) as read:
        read("prometheus", "up", 0, 60, 60)
    assert seen[-1].url.path == "/api/datasources/proxy/uid/p1/api/v1/query_range"
    assert seen[-1].headers["authorization"] == f"Bearer {TOKEN}"
    assert all(r.method == "GET" for r in seen)


def test_grafana_host_is_checked_before_anything_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("a request was sent")

    with (
        pytest.raises(ParityError),
        open_reader("p", transport=serve(handler), token=TOKEN, grafana="https://evil.example.com"),
    ):
        pass


def test_errors_never_show_the_token_and_redirects_are_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/datasources/uid/"):
            return httpx.Response(200, json={"uid": "p1"})
        if "step=61" in str(request.url):
            return httpx.Response(302, headers={"location": "https://evil.example.com/"})
        return httpx.Response(500, json={"message": f"bad {TOKEN} Bearer {TOKEN}"})

    with open_reader("p1", transport=serve(handler), token=TOKEN, grafana=GRAFANA) as read:
        with pytest.raises(ParityError) as err:
            read("prometheus", "up", 0, 60, 60)
        assert TOKEN not in str(err.value) and "500" in str(err.value)
        with pytest.raises(ParityError) as redirect:
            read("prometheus", "up", 0, 60, 61)
        assert "302" in str(redirect.value)


def test_series_and_points_are_capped_and_flagged() -> None:
    many = [{"metric": {"i": str(i)}, "values": [[60, "1"]]} for i in range(MAX_SERIES + 5)]
    got = parse_matrix(matrix(many))
    assert len(got.series) == MAX_SERIES and got.truncated
    long = [{"metric": {}, "values": [[t, "1"] for t in range(MAX_POINTS + 10)]}]
    cut = parse_matrix(matrix(long))
    assert len(cut.series[0].points) == MAX_POINTS and cut.truncated


def test_a_huge_response_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 2000)
    big = matrix([{"metric": {"a": "x" * 50}, "values": [[t, "1"] for t in range(500)]}])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(big).encode())

    with (
        open_reader("http://127.0.0.1:9090", transport=serve(handler)) as read,
        pytest.raises(ParityError, match="larger than"),
    ):
        read("prometheus", "up", 0, 60, 60)


def test_hostile_label_values_are_made_printable_and_short() -> None:
    evil = "a\x1b[31m‮" + "z" * 5000
    got = parse_matrix(matrix([{"metric": {"x": evil}, "values": [[60, "1"]]}]))
    value = got.series[0].labels["x"]
    assert "\x1b" not in value and "‮" not in value and len(value) <= 200


def test_a_vector_answer_is_refused() -> None:
    with pytest.raises(ParityError):
        parse_matrix({"status": "success", "data": {"resultType": "vector", "result": []}})
