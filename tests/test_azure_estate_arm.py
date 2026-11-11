"""Offline tests for the Azure estate ARM client (fake HTTP, no network)."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

HERE = Path(__file__).resolve().parents[1] / "scripts" / "live-test" / "azure-estate"


def load(name: str) -> ModuleType:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ENV = {
    "AZURE_TENANT_ID": "t-1",
    "AZURE_CLIENT_ID": "c-1",
    "AZURE_CLIENT_SECRET": "s3cret-value",
    "AZURE_SUBSCRIPTION_ID": "sub-1",
}


def make_client(handler: httpx.MockTransport) -> object:
    arm = load("arm")
    return arm.Arm(ENV, transport=handler)


def test_token_by_client_credentials_and_bearer_header() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "login.microsoftonline.com" in str(req.url):
            body = req.content.decode()
            assert "grant_type=client_credentials" in body
            assert "scope=https%3A%2F%2Fmanagement.azure.com%2F.default" in body
            return httpx.Response(200, json={"access_token": "tok-abc"})
        assert req.headers["authorization"] == "Bearer tok-abc"
        return httpx.Response(200, json={"value": []})

    arm = load("arm").Arm(ENV, transport=httpx.MockTransport(handler))
    assert arm.get("/subscriptions/sub-1/providers/Microsoft.Insights", "2023-01-01") == {"value": []}
    assert "api-version=2023-01-01" in str(seen[-1].url)


def test_list_follows_next_link() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "login" in req.url.host:
            return httpx.Response(200, json={"access_token": "t"})
        if "page2" in str(req.url):
            return httpx.Response(200, json={"value": [{"n": 2}]})
        return httpx.Response(
            200, json={"value": [{"n": 1}], "nextLink": "https://management.azure.com/page2"}
        )

    arm = load("arm").Arm(ENV, transport=httpx.MockTransport(handler))
    pages = arm.list_all("/x", "2021-01-01")
    assert [i["n"] for i in pages] == [1, 2]


def test_error_reports_status_and_code_without_secret() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "login" in req.url.host:
            return httpx.Response(200, json={"access_token": "tok-abc"})
        return httpx.Response(403, json={"error": {"code": "AuthorizationFailed", "message": "no"}})

    mod = load("arm")
    arm = mod.Arm(ENV, transport=httpx.MockTransport(handler))
    with pytest.raises(mod.ArmError) as exc:
        arm.put("/x", "2021-01-01", {"a": 1})
    text = str(exc.value)
    assert "403" in text and "AuthorizationFailed" in text
    assert "s3cret-value" not in text and "tok-abc" not in text


def test_missing_env_names_the_variable_not_the_value() -> None:
    mod = load("arm")
    with pytest.raises(SystemExit) as exc:
        mod.Arm({"AZURE_TENANT_ID": "x"})
    assert "AZURE_CLIENT_ID" in str(exc.value)


def test_dotenv_parse_ignores_comments_and_quotes(tmp_path: Path) -> None:
    mod = load("arm")
    f = tmp_path / ".env"
    f.write_text('# c\nA=1\nB="two"\n\nexport C=3\n')
    assert mod.parse_dotenv(f) == {"A": "1", "B": "two", "C": "3"}
    assert json.dumps(mod.parse_dotenv(f))
