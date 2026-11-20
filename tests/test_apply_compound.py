"""Apply and roll back compound conditions with a fake New Relic (no network)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from importer.core.apply import NewRelicClient, apply_plan, load_plan
from importer.core.ir import IRMonitor
from importer.core.manifest import CreatedObject, Manifest
from importer.core.rollback import DELETE, rollback
from importer.sources.grafana.translate import translate
from test_grafana import make
from test_grafana_or_split import or_rule

POLICY = "Invented policy"
PARTS = "Invented policy (compound parts)"


class FakeNR:
    """Policies, NRQL conditions and compound conditions in memory; every call is recorded."""

    def __init__(self) -> None:
        self.policies: dict[str, str] = {}
        self.conditions: dict[str, tuple[str, str, bool]] = {}  # id -> (policy id, name, enabled)
        self.compounds: dict[str, tuple[str, dict[str, Any]]] = {}  # id -> (policy id, input)
        self.calls: list[dict[str, Any]] = []
        self.fail_names: set[str] = set()
        self.vanish_on_delete = False  # the delete answers "Not Found" (a policy took it with it)
        self.next = 1000

    def _id(self) -> str:
        self.next += 1
        return str(self.next)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        q, v = body["query"], body["variables"]
        if "alertsCompoundConditionCreate" in q:
            new = self._id()
            self.compounds[new] = (v["policyId"], v["condition"])
            made = {"id": new, "name": v["condition"]["name"]}
            return ok({"alertsCompoundConditionCreate": made})
        if "alertsNrqlConditionStaticCreate" in q:
            cond = v["condition"]
            if cond["name"] in self.fail_names:
                return httpx.Response(200, json={"errors": [{"message": "refused"}]})
            new = self._id()
            self.conditions[new] = (v["policyId"], cond["name"], cond["enabled"])
            return ok({"alertsNrqlConditionStaticCreate": {"id": new, "name": cond["name"]}})
        if "alertsPolicyCreate" in q:
            new = self._id()
            self.policies[v["policy"]["name"]] = new
            return ok({"alertsPolicyCreate": {"id": new, "name": v["policy"]["name"]}})
        if "policiesSearch" in q:
            found = [{"id": i, "name": n} for n, i in self.policies.items() if n == v["name"]]
            return alerts({"policiesSearch": {"policies": found, "nextCursor": None}})
        if "nrqlConditionsSearch" in q:
            pid = v.get("policyId") or v.get("p")
            found = [{"id": i, "name": n} for i, (p, n, _) in self.conditions.items() if p == pid]
            return alerts({"nrqlConditionsSearch": {"nrqlConditions": found, "nextCursor": None}})
        if "compoundConditions" in q:
            items = [{"id": i, "name": c["name"]} for i, (_, c) in self.compounds.items()]
            return alerts({"compoundConditions": {"items": items}})
        for kind, (mutation, var) in DELETE.items():
            if q == mutation:
                store: dict[str, Any] = self.compounds if kind == "compound" else self.conditions
                if store.pop(v[var], None) is None or self.vanish_on_delete:
                    return httpx.Response(200, json={"errors": [{"message": "Not Found"}]})
                return ok({"deleted": {"id": v[var]}})
        if "policy(id" in q:
            name = next((n for n, i in self.policies.items() if i == v["id"]), None)
            return alerts({"policy": {"id": v["id"], "name": name} if name else None})
        if "nrqlCondition(id" in q:
            held = self.conditions.get(v["id"])
            return alerts({"nrqlCondition": {"id": v["id"], "name": held[1]} if held else None})
        raise AssertionError(f"unexpected query {q[:60]}")

    def creates(self, what: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if what in c["query"]]


def ok(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def alerts(data: dict[str, Any]) -> httpx.Response:
    return ok({"actor": {"account": {"alerts": data}}})


def client_for(fake: FakeNR) -> NewRelicClient:
    return NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(fake), sleep=lambda _: None)


def write_ir(folder: Path, monitors: list[IRMonitor]) -> Path:
    ir = folder / "ir"
    ir.mkdir(parents=True, exist_ok=True)
    for m in monitors:
        (ir / f"{m.source_id}.json").write_text(m.model_dump_json())
    return ir


@pytest.fixture
def and_rule_ir(tmp_path: Path) -> Path:
    rule = or_rule(3, ["and", "and", "or"], for_seconds=300)
    return write_ir(tmp_path, translate([rule, make("avg(single)", uid="u2")]))


def test_the_plan_marks_parts_and_the_compound(and_rule_ir: Path) -> None:
    plan = load_plan(and_rule_ir)
    roles = sorted((i.role, i.name) for i in plan.items)
    assert roles == [
        ("compound", "r"),
        ("condition", "r"),
        ("part", "r (A)"),
        ("part", "r (Q)"),
        ("part", "r (R)"),
    ]
    compound = next(i for i in plan.items if i.role == "compound")
    assert compound.components == ["u1/and-A", "u1/and-Q", "u1/and-R"]
    assert compound.nrql == "((A AND B) OR C)"
    for part in (i for i in plan.items if i.role == "part"):
        assert part.payload["variables"]["condition"]["enabled"] is True
    plain = next(i for i in plan.items if i.role == "condition")
    assert plain.payload["variables"]["condition"]["enabled"] is False


def test_parts_go_enabled_to_their_own_policy_and_the_compound_gets_their_ids(
    and_rule_ir: Path,
) -> None:
    fake = FakeNR()
    with client_for(fake) as client:
        result = apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
    assert set(fake.policies) == {POLICY, PARTS}
    by_name = {n: (p, e, i) for i, (p, n, e) in fake.conditions.items()}
    for name in ("r (A)", "r (Q)", "r (R)"):
        assert by_name[name][:2] == (fake.policies[PARTS], True)
    assert by_name["r"][:2] == (fake.policies[POLICY], False)
    ((policy_id, sent),) = fake.compounds.values()
    assert policy_id == fake.policies[POLICY]
    assert sent["enabled"] is False
    assert sent["triggerExpression"] == "((A AND B) OR C)"
    assert sent["componentConditions"] == [
        {"id": by_name["r (A)"][2], "alias": "A"},
        {"id": by_name["r (Q)"][2], "alias": "B"},
        {"id": by_name["r (R)"][2], "alias": "C"},
    ]
    # the parts are created before the compound condition
    order = [c["query"] for c in fake.calls if "Create" in c["query"]]
    assert "alertsCompoundConditionCreate" in order[-1]
    kinds = sorted(o.kind for o in result.objects)
    assert kinds == ["compound", *["condition"] * 4, "policy", "policy"]
    assert sorted(result.enabled_parts) == ["r (A)", "r (Q)", "r (R)"]
    assert result.failed == []


def test_a_second_run_reuses_the_parts_and_skips_the_compound(and_rule_ir: Path) -> None:
    fake = FakeNR()
    with client_for(fake) as client:
        apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
        again = apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
    assert again.created == []
    assert sorted(again.skipped_existing) == ["r", "r", "r (A)", "r (Q)", "r (R)"]
    assert len(fake.compounds) == 1 and len(fake.conditions) == 4


def test_a_part_that_fails_stops_its_compound_condition(and_rule_ir: Path) -> None:
    fake = FakeNR()
    fake.fail_names = {"r (Q)"}
    with client_for(fake) as client:
        result = apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
    assert fake.compounds == {}
    failed = dict(result.failed)
    assert "refused" in failed["r (Q)"]
    assert failed["r"] == "part B of the compound condition was not created"


def test_a_compound_whose_part_is_missing_is_not_planned(tmp_path: Path) -> None:
    monitors = translate([or_rule(2, ["and", "and"])])
    ir = write_ir(tmp_path, [monitors[0], monitors[2]])  # part B left out
    plan = load_plan(ir)
    assert plan.items == []
    assert plan.skipped == 2


def test_rollback_deletes_the_compound_before_its_parts(and_rule_ir: Path) -> None:
    fake = FakeNR()
    with client_for(fake) as client:
        result = apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
        manifest = Manifest(account_id=7)
        manifest.add([o for o in result.objects if o.kind != "policy"])
        undone = rollback(client, 7, manifest, apply=True)
    assert fake.compounds == {} and fake.conditions == {}
    kinds = [o.kind for o in undone.deleted]
    assert kinds[0] == "compound" and set(kinds[1:]) == {"condition"}
    assert undone.failed == []


def test_rollback_treats_a_compound_already_gone_as_gone() -> None:
    fake = FakeNR()
    manifest = Manifest(account_id=7)
    manifest.add([CreatedObject(kind="compound", id="404", name="r")])
    with client_for(fake) as client:
        undone = rollback(client, 7, manifest, apply=True)
    assert undone.failed == [] and undone.deleted == []
    assert [reason for _, reason in undone.skipped] == ["no longer exists"]


def test_rollback_counts_a_compound_that_vanished_during_the_delete_as_gone() -> None:
    fake = FakeNR()
    fake.compounds["9"] = ("1", {"name": "r"})
    fake.vanish_on_delete = True
    manifest = Manifest(account_id=7)
    manifest.add([CreatedObject(kind="compound", id="9", name="r")])
    with client_for(fake) as client:
        undone = rollback(client, 7, manifest, apply=True)
    assert undone.failed == []
    assert [reason for _, reason in undone.skipped] == ["already gone"]


def test_a_dry_run_rollback_sends_no_delete(and_rule_ir: Path) -> None:
    fake = FakeNR()
    with client_for(fake) as client:
        result = apply_plan(client, 7, POLICY, load_plan(and_rule_ir))
        manifest = Manifest(account_id=7)
        manifest.add(result.objects)
        before = len(fake.calls)
        undone = rollback(client, 7, manifest, apply=False)
    assert not any("Delete" in c["query"] for c in fake.calls[before:])
    assert any(o.kind == "compound" for o in undone.deleted)
