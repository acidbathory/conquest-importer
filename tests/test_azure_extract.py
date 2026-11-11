"""Azure extractor: allow-listed GETs, token handling, caps, no secrets on disk (offline)."""

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from importer.core import safe_http
from importer.sources.azure import extract as ex
from importer.sources.azure.extract import AzureError, extract, open_client
from importer.sources.azure.models import load_alerts

SUB = "00000000-0000-0000-0000-000000000001"
TENANT = "00000000-0000-0000-0000-000000000002"
CLIENT = "00000000-0000-0000-0000-000000000003"
SECRET = "client-secret-value~123"  # noqa: S105
TOKEN = "eyJ0b2tlbi12YWx1ZQ.signature"  # noqa: S105
ARM = "management.azure.com"
PROVIDERS = {
    "metricAlerts": "Microsoft.Insights/metricAlerts",
    "scheduledQueryRules": "Microsoft.Insights/scheduledQueryRules",
    "activityLogAlerts": "Microsoft.Insights/activityLogAlerts",
    "actionGroups": "Microsoft.Insights/actionGroups",
    "alertProcessingRules": "Microsoft.AlertsManagement/actionRules",
    "prometheusRuleGroups": "Microsoft.AlertsManagement/prometheusRuleGroups",
    "smartDetectorAlertRules": "microsoft.alertsManagement/smartDetectorAlertRules",
}
VERSIONS = {
    "metricAlerts": "2026-01-01",
    "scheduledQueryRules": "2026-03-01",
    "activityLogAlerts": "2026-01-01",
    "actionGroups": "2023-01-01",
    "alertProcessingRules": "2021-08-08",
    "prometheusRuleGroups": "2023-03-01",
    "smartDetectorAlertRules": "2021-04-01",
}


def rule(name: str, kind: str = "metricAlerts") -> dict[str, Any]:
    return {
        "id": f"/subscriptions/{SUB}/resourceGroups/rg/providers/x/{name}",
        "name": name,
        "type": PROVIDERS[kind],
        "properties": {"severity": 2},
    }


class Fake:
    """A scripted Azure: records every request, answers lists and the token call."""

    def __init__(self, pages: dict[str, list[list[dict[str, Any]]]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.pages = pages or {}
        self.handler: Callable[[httpx.Request], httpx.Response] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        return self.default(request)

    def default(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3599})
        path = request.url.path.lower()
        for key, provider in PROVIDERS.items():
            if path.endswith("/" + provider.lower()):
                pages = self.pages.get(key, [[]])
                index = int(parse_qs(request.url.query.decode()).get("page", ["0"])[0])
                body: dict[str, Any] = {"value": pages[index]}
                if index + 1 < len(pages):
                    query = f"api-version={VERSIONS[key]}&page={index + 1}"
                    body["nextLink"] = f"https://{ARM}{request.url.path}?{query}"
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": {"code": "NotFound", "message": "no"}})

    def arm(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == ARM]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AZURE_ACCESS_TOKEN", "AZURE_SUBSCRIPTION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AZURE_TENANT_ID", TENANT)
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT)
    monkeypatch.setenv("AZURE_CLIENT_SECRET", SECRET)


def run(fake: Fake, out: Path, sub: str = SUB, **kw: Any) -> Any:
    sleeps: list[float] = kw.pop("sleeps", [])
    with open_client(transport=httpx.MockTransport(fake), sleep=sleeps.append, **kw) as get:
        return extract(get, sub, out)


def test_reads_every_resource_type_with_get_only_and_the_documented_versions(
    env: None, tmp_path: Path
) -> None:
    fake = Fake({"metricAlerts": [[rule("a"), rule("b")]]})
    result = run(fake, tmp_path / "out")
    assert result.counts["metricAlerts"] == 2
    arm = fake.arm()
    assert {r.method for r in arm} == {"GET"} and {r.url.host for r in arm} == {ARM}
    seen = {
        r.url.path.lower().rsplit("/providers/", 1)[1]: r.url.params["api-version"] for r in arm
    }
    assert seen == {PROVIDERS[k].lower(): v for k, v in VERSIONS.items()}
    for request in arm:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.path.startswith(f"/subscriptions/{SUB}/providers/")


def test_the_token_request_goes_only_to_the_login_host_with_the_right_form(
    env: None, tmp_path: Path
) -> None:
    fake = Fake()
    run(fake, tmp_path / "out")
    (login,) = [r for r in fake.requests if r.url.host != ARM]
    assert login.method == "POST" and login.url.host == "login.microsoftonline.com"
    assert login.url.path == f"/{TENANT}/oauth2/v2.0/token"
    form = parse_qs(login.content.decode())
    assert form["grant_type"] == ["client_credentials"] and form["client_id"] == [CLIENT]
    assert form["scope"] == ["https://management.azure.com/.default"]
    assert "authorization" not in login.headers


def test_an_existing_access_token_skips_the_login(
    env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AZURE_ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("AZURE_CLIENT_SECRET")
    fake = Fake()
    run(fake, tmp_path / "out")
    assert all(r.url.host == ARM for r in fake.requests)


def test_missing_or_unplain_credentials_are_a_clear_error(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AZURE_CLIENT_SECRET")
    with pytest.raises(AzureError, match="AZURE_CLIENT_SECRET"):
        open_client(transport=httpx.MockTransport(Fake()))
    monkeypatch.setenv("AZURE_ACCESS_TOKEN", "bad token”")
    with pytest.raises(AzureError, match="plain"):
        open_client(transport=httpx.MockTransport(Fake()))
    monkeypatch.delenv("AZURE_ACCESS_TOKEN")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", SECRET)
    monkeypatch.setenv("AZURE_TENANT_ID", "contoso.com/../x")
    with pytest.raises(AzureError, match="AZURE_TENANT_ID"):
        open_client(transport=httpx.MockTransport(Fake()))


@pytest.mark.parametrize(
    "sub", ["", "not-a-guid", f"{SUB}/../x", f"{SUB}?a=b", "00000000-0000-0000-0000-00000000000g"]
)
def test_the_subscription_must_be_a_uuid(env: None, tmp_path: Path, sub: str) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="UUID"):
        run(fake, tmp_path / "out", sub)
    assert fake.requests == [] or all(r.url.host != ARM for r in fake.requests)


def test_next_link_is_followed_on_the_same_host_and_path(env: None, tmp_path: Path) -> None:
    fake = Fake({"metricAlerts": [[rule("a")], [rule("b")], [rule("c")]]})
    result = run(fake, tmp_path / "out")
    assert result.counts["metricAlerts"] == 3


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example.com/subscriptions/x/providers/Microsoft.Insights/metricAlerts?api-version=1",
        "http://management.azure.com/subscriptions/x/providers/Microsoft.Insights/metricAlerts",
        "https://management.azure.com@evil.example.com/x",
        "https://management.azure.com:8443/subscriptions/x/providers/Microsoft.Insights/metricAlerts",
        "https://management.azure.com/subscriptions/x/providers/Microsoft.Compute/virtualMachines",
        "//evil.example.com/x",
    ],
)
def test_a_next_link_to_another_place_is_refused_and_gets_no_token(
    env: None, tmp_path: Path, link: str
) -> None:
    fake = Fake()
    base = fake.default

    def handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path.lower().endswith("/metricalerts")
            and "page" not in request.url.query.decode()
        ):
            return httpx.Response(200, json={"value": [rule("a")], "nextLink": link})
        return base(request)

    fake.handler = lambda r: (
        handler(r) if r.url.host == ARM else httpx.Response(200, json={"access_token": TOKEN})
    )
    out = tmp_path / "out"
    result = run(fake, out)
    assert "metricAlerts" in " ".join(result.warnings)
    assert all(r.url.host in (ARM, "login.microsoftonline.com") for r in fake.requests)
    assert (out / "extract-summary.json").is_file()


def test_throttling_waits_for_retry_after_then_continues(env: None, tmp_path: Path) -> None:
    fake = Fake({"metricAlerts": [[rule("a")]]})
    base = fake.default
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.lower().endswith("/metricalerts"):
            hits["n"] += 1
            if hits["n"] <= 2:
                return httpx.Response(
                    429, headers={"Retry-After": "7"}, json={"error": {"message": "slow"}}
                )
        return base(request)

    fake.handler = handler
    sleeps: list[float] = []
    result = run(fake, tmp_path / "out", sleeps=sleeps)
    assert result.counts["metricAlerts"] == 1 and sleeps == [7, 7]


def test_retry_after_is_capped_and_retries_are_bounded(env: None, tmp_path: Path) -> None:
    fake = Fake()
    fake.handler = lambda r: (
        httpx.Response(200, json={"access_token": TOKEN})
        if r.url.host != ARM
        else httpx.Response(429, headers={"Retry-After": "99999"}, json={})
    )
    sleeps: list[float] = []
    with pytest.raises(AzureError, match="nothing could be read"):
        run(fake, tmp_path / "out", sleeps=sleeps)
    assert sleeps and max(sleeps) <= ex.MAX_RETRY_WAIT and len(sleeps) <= ex.MAX_RETRIES * 7


def test_a_redirect_is_not_followed_and_the_token_goes_nowhere_else(
    env: None, tmp_path: Path
) -> None:
    fake = Fake()
    fake.handler = lambda r: (
        httpx.Response(200, json={"access_token": TOKEN})
        if r.url.host != ARM
        else httpx.Response(302, headers={"Location": "https://evil.example.com/steal"})
    )
    with pytest.raises(AzureError, match="302"):
        run(fake, tmp_path / "out")
    assert {r.url.host for r in fake.requests} <= {ARM, "login.microsoftonline.com"}


def test_the_token_and_secret_never_appear_in_errors_or_warnings(env: None, tmp_path: Path) -> None:
    fake = Fake()
    fake.handler = lambda r: (
        httpx.Response(200, json={"access_token": TOKEN})
        if r.url.host != ARM
        else httpx.Response(
            403, json={"error": {"message": f"denied for Bearer {TOKEN} and {SECRET}"}}
        )
    )
    with pytest.raises(AzureError, match="403") as raised:
        run(fake, tmp_path / "out")
    assert TOKEN not in str(raised.value) and SECRET not in str(raised.value)


def test_a_failed_login_names_the_status_and_hides_the_secret(env: None) -> None:
    fake = Fake()
    fake.handler = lambda _r: httpx.Response(
        401, json={"error": "invalid_client", "error_description": f"bad {SECRET}"}
    )
    with pytest.raises(AzureError) as raised:
        open_client(transport=httpx.MockTransport(fake))
    assert "401" in str(raised.value) and SECRET not in str(raised.value)


def test_every_type_failing_is_an_error(env: None, tmp_path: Path) -> None:
    fake = Fake()
    fake.handler = lambda r: (
        httpx.Response(200, json={"access_token": TOKEN})
        if r.url.host != ARM
        else httpx.Response(500, json={})
    )
    with pytest.raises(AzureError, match="nothing could be read"):
        run(fake, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_page_cap_stops_a_runaway_list(
    env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ex, "MAX_PAGES", 3)
    fake = Fake({"metricAlerts": [[rule(f"r{i}")] for i in range(10)]})
    result = run(fake, tmp_path / "out")
    assert "more than 3 pages" in " ".join(result.warnings)


def test_the_byte_budget_and_deadline_stop_the_run(
    env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 50)
    fake = Fake({"metricAlerts": [[rule("a")]]})
    with pytest.raises(AzureError, match="in total"):
        run(fake, tmp_path / "out")


def test_the_deadline_stops_the_run(
    env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = iter([0.0] + [10_000.0] * 500)
    monkeypatch.setattr(safe_http, "_clock", lambda: next(clock))
    fake = Fake()
    with pytest.raises(AzureError):
        run(fake, tmp_path / "out")


def test_secrets_are_not_written_and_files_are_private(env: None, tmp_path: Path) -> None:
    group = {
        "id": "/subscriptions/s/resourceGroups/rg/providers/microsoft.insights/actionGroups/ag1",
        "name": "ag1",
        "type": "Microsoft.Insights/ActionGroups",
        "properties": {
            "enabled": True,
            "webhookReceivers": [
                {"name": "w", "serviceUri": "https://h.example.com/p/SECRETPATH?sig=ZZ"}
            ],
            "logicAppReceivers": [
                {"name": "l", "callbackUrl": "https://logic.example.com/x?sig=LOGICSIG"}
            ],
        },
    }
    alert = rule("a") | {
        "properties": {
            "severity": 1,
            "actions": [{"actionGroupId": "/g", "webHookProperties": {"k": "TOKENVALUE"}}],
        }
    }
    fake = Fake({"actionGroups": [[group]], "metricAlerts": [[alert]]})
    out = tmp_path / "out"
    result = run(fake, out)
    blob = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    for secret in ("SECRETPATH", "sig=ZZ", "LOGICSIG", "TOKENVALUE", TOKEN, SECRET):
        assert secret not in blob, secret
    assert "secretref:azure/actionGroups/ag1/" in blob and result.secrets_dropped >= 3
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for path in out.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name


def test_the_export_is_what_convert_reads(env: None, tmp_path: Path) -> None:
    alert = rule("cpu") | {
        "properties": {
            "severity": 2,
            "scopes": [
                f"/subscriptions/{SUB}/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1"
            ],
            "evaluationFrequency": "PT5M",
            "windowSize": "PT5M",
            "criteria": {
                "odata.type": "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
                "allOf": [
                    {
                        "criterionType": "StaticThresholdCriterion",
                        "name": "c",
                        "metricName": "Percentage CPU",
                        "metricNamespace": "Microsoft.Compute/virtualMachines",
                        "timeAggregation": "Average",
                        "operator": "GreaterThan",
                        "threshold": 80,
                    }
                ],
            },
        }
    }
    out = tmp_path / "out"
    run(Fake({"metricAlerts": [[alert]]}), out)
    assert [a.name for a in load_alerts(out)] == ["cpu"]


def test_nothing_is_left_behind_when_the_run_fails_midway(
    env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ex, "_write_private", boom)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="disk full"):
        run(Fake(), out)
    assert not out.exists() and list(tmp_path.iterdir()) == []


def test_a_folder_with_files_is_refused(env: None, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("1")
    with pytest.raises(ValueError, match="already has files"):
        run(Fake(), out)


def test_a_nested_or_non_json_response_is_an_error_not_a_crash(env: None, tmp_path: Path) -> None:
    fake = Fake()
    fake.handler = lambda r: (
        httpx.Response(200, json={"access_token": TOKEN})
        if r.url.host != ARM
        else httpx.Response(200, content=b"[" * 500 + b"]" * 500)
    )
    with pytest.raises(AzureError):
        run(fake, tmp_path / "out")
    assert os.listdir(tmp_path) == []
    _ = json  # keep the import used


def test_alert_processing_rules_use_the_api_version_azure_documents_and_accepts() -> None:
    """2021-08-01 was a typo: the live API answered 404 for it; the swagger says 2021-08-08."""
    from importer.sources.azure.extract import READS

    assert READS["alertProcessingRules"][1] == "2021-08-08"
