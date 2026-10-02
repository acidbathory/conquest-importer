from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from importer.extract.datadog import DatadogAPIError, DatadogClient, InvalidSiteError
from importer.secrets import DatadogCredentials

API_KEY = "api-key-SECRET-111"
APP_KEY = "app-key-SECRET-222"


def creds(site: str = "datadoghq.com") -> DatadogCredentials:
    return DatadogCredentials(api_key=SecretStr(API_KEY), app_key=SecretStr(APP_KEY), site=site)


def monitor(i: int, **extra: Any) -> dict[str, Any]:
    return {"id": i, "name": f"m{i}", "type": "metric alert", "query": "q", **extra}


def client_for(
    handler: httpx.MockTransport, sleeps: list[float] | None = None, **kw: Any
) -> DatadogClient:
    rec = sleeps if sleeps is not None else []
    return DatadogClient(creds(), transport=handler, sleep=rec.append, **kw)


def test_rejects_unknown_site() -> None:
    with pytest.raises(InvalidSiteError):
        DatadogClient(creds("evil.example.com"))


def test_sends_keys_as_headers_and_only_get() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=[])

    list(client_for(httpx.MockTransport(handler)).list_monitors())
    assert seen[0].method == "GET"
    assert seen[0].headers["DD-API-KEY"] == API_KEY
    assert seen[0].headers["DD-APPLICATION-KEY"] == APP_KEY
    assert seen[0].url.host == "api.datadoghq.com"


def test_paginates_until_short_page() -> None:
    pages = {"0": [monitor(1), monitor(2)], "1": [monitor(3)]}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[req.url.params["page"]])

    got = list(client_for(httpx.MockTransport(handler), page_size=2).list_monitors())
    assert [m.id for m in got] == [1, 2, 3]


def test_unknown_fields_are_preserved() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[monitor(1, brand_new_field={"a": 1})])

    (m,) = client_for(httpx.MockTransport(handler)).list_monitors()
    assert m.model_dump()["brand_new_field"] == {"a": 1}


def test_retries_on_429_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"X-RateLimit-Reset": "5"})
        return httpx.Response(200, json=[monitor(1)])

    sleeps: list[float] = []
    got = list(client_for(httpx.MockTransport(handler), sleeps).list_monitors())
    assert len(got) == 1
    assert sleeps == [5.0]


def test_retries_are_bounded() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    with pytest.raises(DatadogAPIError):
        list(client_for(httpx.MockTransport(handler), max_retries=2).list_monitors())
    assert calls["n"] == 3


def test_wait_is_capped() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if not sleeps:
            return httpx.Response(429, headers={"X-RateLimit-Reset": "99999"})
        return httpx.Response(200, json=[])

    sleeps: list[float] = []
    list(client_for(httpx.MockTransport(handler), sleeps).list_monitors())
    assert sleeps == [60.0]


def test_auth_failure_is_not_retried_and_gives_hint() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="Forbidden")

    with pytest.raises(DatadogAPIError, match="monitors_read"):
        list(client_for(httpx.MockTransport(handler)).list_monitors())
    assert calls["n"] == 1


def test_error_body_is_redacted() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad key {API_KEY} and {APP_KEY}")

    with pytest.raises(DatadogAPIError) as info:
        list(client_for(httpx.MockTransport(handler)).list_monitors())
    assert API_KEY not in str(info.value)
    assert APP_KEY not in str(info.value)


def test_network_error_message_is_redacted() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with {API_KEY}")

    with pytest.raises(DatadogAPIError) as info:
        list(client_for(httpx.MockTransport(handler), max_retries=0).list_monitors())
    assert API_KEY not in str(info.value)


def test_unexpected_shape_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    with pytest.raises(DatadogAPIError):
        list(client_for(httpx.MockTransport(handler)).list_monitors())
