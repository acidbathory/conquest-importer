"""Offline tests for the Azure estate plan, runner and cleanup (fake HTTP, no network)."""

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from test_azure_estate_arm import ENV, load

SUB = "00000000-0000-0000-0000-000000000001"
RG = "azure-signup-rg"


def rid(rg: str, kind: str, name: str) -> str:
    return f"/subscriptions/{SUB}/resourceGroups/{rg}/providers/Microsoft.Insights/{kind}/{name}"


def plan() -> list[Any]:
    items: list[Any] = load("estate").build_plan(SUB, RG, "abc123", "t-1")
    return items


def walk(o: Any) -> list[str]:
    if isinstance(o, dict):
        return [s for v in o.values() for s in walk(v)]
    if isinstance(o, list):
        return [s for v in o for s in walk(v)]
    return [o] if isinstance(o, str) else []


def test_every_name_is_ckg_and_in_the_one_resource_group() -> None:
    items = plan()
    assert items
    for it in items:
        assert it.name.startswith(("ckg-", "ckgstore")), it.name
        assert f"/resourceGroups/{RG}/" in it.path, it.path


def test_counts_cover_every_object_class() -> None:
    kinds: dict[str, int] = {}
    for it in plan():
        kinds[it.kind] = kinds.get(it.kind, 0) + 1
    assert kinds["storage"] == 1 and kinds["workspace"] == 1
    assert kinds["actionGroup"] >= 10
    assert kinds["metricAlert"] >= 8
    assert kinds["scheduledQueryRule"] >= 6
    assert kinds["activityLogAlert"] == 3
    assert kinds["alertProcessingRule"] == 2
    assert sum(v for k, v in kinds.items() if k.endswith(("Alert", "Rule"))) >= 20


def test_creation_order_puts_dependencies_first() -> None:
    order = [it.kind for it in plan()]
    assert order.index("storage") < order.index("metricAlert")
    assert order.index("workspace") < order.index("scheduledQueryRule")
    assert order.index("actionGroup") < order.index("metricAlert")
    assert order[-1] == "alertProcessingRule" or order.index("alertProcessingRule") > order.index(
        "activityLogAlert"
    )


def test_rules_are_disabled_and_only_use_safe_action_groups() -> None:
    safe = {"ckg-ag-email", "ckg-ag-webhook"}
    for it in plan():
        if it.kind in {
            "metricAlert",
            "scheduledQueryRule",
            "activityLogAlert",
            "alertProcessingRule",
        }:
            assert it.body["properties"]["enabled"] is False, it.name
            for s in walk(it.body):
                if "/actionGroups/" in s or "/actiongroups/" in s:
                    assert s.rsplit("/", 1)[1] in safe, (it.name, s)


def test_no_real_addresses_or_numbers() -> None:
    for it in plan():
        for s in walk(it.body):
            for mail in re.findall(r"[\w.+-]+@[\w.-]+", s):
                assert mail.endswith(".invalid"), mail
            for url in re.findall(r"https?://([^/\s?]+)", s):
                assert url.endswith((".invalid", "management.azure.com")), url
        assert "smsReceivers" not in json.dumps(it.body)
        assert "voiceReceivers" not in json.dumps(it.body)


def test_metric_alerts_vary_severity_and_automitigate() -> None:
    ma = [i.body["properties"] for i in plan() if i.kind == "metricAlert"]
    assert {p["severity"] for p in ma} == {0, 1, 2, 3, 4}
    assert {p["autoMitigate"] for p in ma} == {True, False}
    assert len({p["windowSize"] for p in ma}) >= 4


def fake_arm(log: list[tuple[str, str]], fail: dict[str, tuple[int, str]] | None = None) -> Any:
    def handler(req: httpx.Request) -> httpx.Response:
        if "login" in req.url.host:
            return httpx.Response(200, json={"access_token": "t"})
        log.append((req.method, req.url.path))
        for frag, (status, code) in (fail or {}).items():
            if frag in req.url.path:
                return httpx.Response(status, json={"error": {"code": code, "message": "m"}})
        return httpx.Response(
            200, json={"id": req.url.path, "properties": {"provisioningState": "Succeeded"}}
        )

    return load("arm").Arm(
        {**ENV, "AZURE_SUBSCRIPTION_ID": SUB}, transport=httpx.MockTransport(handler)
    )


def test_run_records_manifest_before_each_put(tmp_path: Path) -> None:
    est = load("estate")
    man = tmp_path / "m.json"
    seen_before_put: list[int] = []
    log: list[tuple[str, str]] = []
    arm = fake_arm(log)
    orig = arm.put

    def spy(path: str, api: str, body: Any) -> Any:
        seen_before_put.append(len(json.loads(man.read_text())["resources"]) if man.exists() else 0)
        return orig(path, api, body)

    arm.put = spy
    items = plan()
    est.run(arm, items, man, sleep=lambda _s: None)
    assert seen_before_put == list(range(1, len(items) + 1))
    assert len(json.loads(man.read_text())["resources"]) == len(items)


def test_run_stops_on_permission_error_and_keeps_manifest(tmp_path: Path) -> None:
    est = load("estate")
    man = tmp_path / "m.json"
    arm = fake_arm([], {"/actionGroups/ckg-ag-webhook": (403, "AuthorizationFailed")})
    with pytest.raises(load("arm").ArmError):
        est.run(arm, plan(), man, sleep=lambda _s: None)
    names = [r["name"] for r in json.loads(man.read_text())["resources"]]
    assert "ckg-ag-webhook" in names and not any(n.startswith("ckg-ma-") for n in names)


def test_run_records_validation_rejection_and_continues(tmp_path: Path) -> None:
    est = load("estate")
    man = tmp_path / "m.json"
    arm = fake_arm([], {"ckg-ag-itsm": (400, "BadRequest")})
    est.run(arm, plan(), man, sleep=lambda _s: None)
    data = json.loads(man.read_text())
    assert [r["name"] for r in data["rejected"]] == ["ckg-ag-itsm"]
    assert "ckg-ag-itsm" not in [r["name"] for r in data["resources"]]
    assert any(r["name"].startswith("ckg-ma-") for r in data["resources"])


def test_cleanup_previews_and_refuses_foreign_names(tmp_path: Path) -> None:
    cl = load("cleanup")
    man = tmp_path / "m.json"
    good = rid(RG, "metricAlerts", "ckg-ma-x")
    bad = rid(RG, "metricAlerts", "prod-alert")
    other_rg = rid("other", "metricAlerts", "ckg-ma-y")
    man.write_text(
        json.dumps(
            {
                "resources": [
                    {"name": "ckg-ma-x", "kind": "metricAlert", "id": good, "api": "2026-01-01"},
                    {"name": "prod-alert", "kind": "metricAlert", "id": bad, "api": "2026-01-01"},
                    {
                        "name": "ckg-ma-y",
                        "kind": "metricAlert",
                        "id": other_rg,
                        "api": "2026-01-01",
                    },
                ]
            }
        )
    )
    log: list[tuple[str, str]] = []
    out = cl.cleanup(fake_arm(log), man, RG, yes=False)
    assert log == [] and sorted(out["refused"]) == ["ckg-ma-y", "prod-alert"]
    log.clear()
    out = cl.cleanup(fake_arm(log), man, RG, yes=True)
    assert [m for m, _ in log] == ["DELETE"] and log[0][1].endswith("/ckg-ma-x")
    assert out["deleted"] == ["ckg-ma-x"]


def test_cleanup_deletes_in_reverse_order(tmp_path: Path) -> None:
    cl = load("cleanup")
    man = tmp_path / "m.json"
    ids = [
        rid(RG, "actionGroups", "ckg-ag-a"),
        rid(RG, "metricAlerts", "ckg-ma-b"),
    ]
    man.write_text(
        json.dumps(
            {
                "resources": [
                    {"name": "ckg-ag-a", "kind": "actionGroup", "id": ids[0], "api": "2023-01-01"},
                    {"name": "ckg-ma-b", "kind": "metricAlert", "id": ids[1], "api": "2026-01-01"},
                ]
            }
        )
    )
    log: list[tuple[str, str]] = []
    cl.cleanup(fake_arm(log), man, RG, yes=True)
    assert [p.rsplit("/", 1)[1] for _, p in log if _ == "DELETE"] == ["ckg-ma-b", "ckg-ag-a"]
