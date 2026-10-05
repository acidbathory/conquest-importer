import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from importer.core.apply import NewRelicClient, Plan, PlanItem, apply_plan
from importer.core.manifest import CreatedObject, Manifest, load_manifest, save_manifest


def test_manifest_round_trip_and_dedupe(tmp_path: Path) -> None:
    m = Manifest(account_id=1)
    m.add([CreatedObject(kind="policy", id="5", name="p")])
    m.add(
        [
            CreatedObject(kind="policy", id="5", name="p"),
            CreatedObject(kind="condition", id="9", name="c"),
        ]
    )
    assert len(m.objects) == 2
    save_manifest(m, tmp_path / "sub" / "m.json")
    assert load_manifest(tmp_path / "sub" / "m.json") == m


def test_delete_order_puts_dependents_first() -> None:
    m = Manifest(account_id=1)
    for kind in ("dashboard", "policy", "condition", "destination", "channel", "workflow"):
        m.add([CreatedObject(kind=kind, id=kind, name=kind)])
    assert [o.kind for o in m.in_delete_order()] == [
        "workflow",
        "channel",
        "destination",
        "condition",
        "policy",
        "dashboard",
    ]


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CreatedObject.model_validate({"kind": "entity", "id": "1", "name": "x"})


class Fake:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        q = body["query"]
        if "alertsPolicyCreate" in q:
            return httpx.Response(
                200, json={"data": {"alertsPolicyCreate": {"id": "77", "name": "x"}}}
            )
        if "alertsNrqlConditionStaticCreate" in q:
            return httpx.Response(
                200, json={"data": {"alertsNrqlConditionStaticCreate": {"id": "88", "name": "c"}}}
            )
        data: dict[str, Any]
        if "policiesSearch" in q:
            data = {"policiesSearch": {"policies": [], "nextCursor": None}}
        else:
            data = {"nrqlConditionsSearch": {"nrqlConditions": [], "nextCursor": None}}
        return httpx.Response(200, json={"data": {"actor": {"account": {"alerts": data}}}})


def test_apply_records_ids_of_what_it_created() -> None:
    payload = {
        "query": "mutation { alertsNrqlConditionStaticCreate }",
        "variables": {"accountId": "ACCOUNT_ID_PLACEHOLDER", "policyId": "POLICY_ID_PLACEHOLDER"},
    }
    plan = Plan(
        items=[PlanItem(source_id=1, name="c", nrql="SELECT 1", payload=payload)], skipped=0
    )
    client = NewRelicClient(
        SecretStr("k"), transport=httpx.MockTransport(Fake()), sleep=lambda _: None
    )
    result = apply_plan(client, 1, "pol", plan)
    assert [(o.kind, o.id) for o in result.objects] == [("policy", "77"), ("condition", "88")]
