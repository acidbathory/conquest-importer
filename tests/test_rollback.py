import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import NewRelicClient
from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.rollback import DELETE, READ, rollback

ACCOUNT = 7


class FakeNR:
    """A tiny New Relic: objects by (kind, id) -> name. Reads and deletes act on it."""

    def __init__(self, objects: dict[tuple[str, str], str], foreign: int = 0) -> None:
        self.objects = dict(objects)
        self.foreign = foreign
        self.ignore_deletes = False
        self.lag = 0  # reads after a delete that still see the object
        self.ghosts: dict[tuple[str, str], list[Any]] = {}
        self.deleted: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q, v = body["query"], body["variables"]
        if "nrqlConditionsSearch" in q:
            ours = [{"id": i} for (k, i) in self.objects if k == "condition"]
            found = ours + [{"id": f"foreign{n}"} for n in range(self.foreign)]
            data = {
                "actor": {
                    "account": {"alerts": {"nrqlConditionsSearch": {"nrqlConditions": found}}}
                }
            }
            return httpx.Response(200, json={"data": data})
        for kind, (mutation, var) in DELETE.items():
            if q == mutation:
                key = (kind, v[var])
                if not self.ignore_deletes:
                    gone = self.objects.pop(key, None)
                    if self.lag and gone is not None:
                        self.ghosts[key] = [gone, self.lag]
                self.deleted.append(key)
                return httpx.Response(200, json={"data": {"ok": {"id": v[var]}}})
        for kind, (query, path) in READ.items():
            if q == query:
                oid = v.get("id") or v.get("g")
                name = self.objects.get((kind, oid))
                ghost = self.ghosts.get((kind, oid))
                if name is None and ghost and ghost[1] > 0:
                    ghost[1] -= 1
                    name = ghost[0]
                return httpx.Response(200, json={"data": nest(path, name, oid)})
        raise AssertionError(f"unexpected query {q[:60]}")


def nest(path: str, name: str | None, oid: str) -> dict[str, Any]:
    """Build a response with the object at `path` (list index 0 for *.entities.0)."""
    parts = path.split(".")
    leaf: Any = None if name is None else {"id": oid, "name": name}
    for part in reversed(parts):
        leaf = ([] if leaf is None else [leaf]) if part.isdigit() else {part: leaf}
    result: dict[str, Any] = leaf
    return result


def client_for(fake: FakeNR) -> NewRelicClient:
    return NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(fake), sleep=lambda _: None)


def manifest(*objs: tuple[str, str, str]) -> Manifest:
    m = Manifest(account_id=ACCOUNT)
    m.add([CreatedObject(kind=k, id=i, name=n) for k, i, n in objs])
    return m


ALL = [
    ("dashboard", "g1", "dash"),
    ("policy", "p1", "pol"),
    ("condition", "c1", "cond"),
    ("destination", "d1", "dest"),
    ("channel", "ch1", "chan"),
    ("workflow", "w1", "wf"),
]


def world() -> dict[tuple[str, str], str]:
    return {(k, i): n for k, i, n in ALL}


def test_deletes_dependents_before_what_they_depend_on() -> None:
    fake = FakeNR(world())
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=True)
    assert [k for k, _ in fake.deleted] == [
        "workflow",
        "channel",
        "destination",
        "condition",
        "policy",
        "dashboard",
    ]
    assert len(result.deleted) == 6 and not result.skipped and not result.failed


def test_dry_run_deletes_nothing() -> None:
    fake = FakeNR(world())
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=False)
    assert len(result.deleted) == 6 and not fake.deleted


def test_missing_and_renamed_objects_are_left_alone() -> None:
    objects = world()
    del objects[("condition", "c1")]
    objects[("channel", "ch1")] = "someone renamed this"
    fake = FakeNR(objects)
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=True)
    reasons = {o.kind: why for o, why in result.skipped}
    assert (
        reasons["condition"] == "no longer exists" and "renamed" in reasons["channel"]
    ) or "changed" in reasons["channel"]
    assert ("condition", "c1") not in fake.deleted and ("channel", "ch1") not in fake.deleted


def test_a_policy_with_foreign_conditions_is_kept() -> None:
    fake = FakeNR(world(), foreign=2)
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=True)
    assert ("policy", "p1") not in fake.deleted
    assert any(o.kind == "policy" and "not ours" in why for o, why in result.skipped)


def test_only_listed_objects_are_touched() -> None:
    objects = world() | {("condition", "other"): "not in the manifest"}
    fake = FakeNR(objects)
    rollback(client_for(fake), ACCOUNT, manifest(("condition", "c1", "cond")), apply=True)
    assert fake.deleted == [("condition", "c1")] and ("condition", "other") in fake.objects


def test_every_kind_has_a_read_and_a_delete() -> None:
    assert set(READ) == set(DELETE) == {k for k, _, _ in ALL}


def test_cli_dry_run_then_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "manifest.json"
    save_manifest(manifest(("condition", "c1", "cond")), path)
    runner = CliRunner()
    assert runner.invoke(app, ["rollback", "--manifest", str(path)]).exit_code == 1  # no account
    wrong = runner.invoke(app, ["rollback", "--manifest", str(path), "--account-id", "9"])
    assert wrong.exit_code == 1 and "belongs to account" in wrong.output
    monkeypatch.delenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", raising=False)
    res = runner.invoke(
        app,
        [
            "rollback",
            "--manifest",
            str(path),
            "--account-id",
            "7",
            "--apply",
            "--confirm-account",
            "7",
        ],
    )
    assert res.exit_code == 1 and "ALLOWED" in res.output


def test_a_dry_run_counts_our_own_conditions_as_about_to_go() -> None:
    fake = FakeNR(world())
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=False)
    assert ("policy", "p1") in [(o.kind, o.id) for o in result.deleted]
    assert not result.skipped


def test_a_delete_that_did_nothing_is_not_reported_as_deleted() -> None:
    fake = FakeNR(world())
    fake.ignore_deletes = True
    result = rollback(
        client_for(fake),
        ACCOUNT,
        manifest(("condition", "c1", "cond")),
        apply=True,
        sleep=lambda _: None,
    )
    assert not result.deleted
    assert result.failed and "still exists" in result.failed[0][1]


def test_a_delete_that_takes_a_moment_to_show_is_not_called_a_failure() -> None:
    fake = FakeNR(world())
    fake.lag = 3  # New Relic still returns each object for three reads after deleting it
    waits: list[float] = []
    result = rollback(client_for(fake), ACCOUNT, manifest(*ALL), apply=True, sleep=waits.append)
    assert not result.failed and len(result.deleted) == len(ALL)
    assert any(w > 0 for w in waits)


def _dashboard_world(answer: dict[str, Any]) -> tuple[FakeNR, NewRelicClient]:
    """A New Relic whose dashboard stays readable (stale index) and whose delete gives `answer`."""
    fake = FakeNR({("dashboard", "g1"): "dash"})
    fake.ignore_deletes = True  # the entity index never drops it

    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["query"] == DELETE["dashboard"][0]:
            return httpx.Response(200, json={"data": {"dashboardDelete": answer}})
        return fake(request)

    client = NewRelicClient(
        SecretStr("k"), transport=httpx.MockTransport(handler), sleep=lambda _: None
    )
    return fake, client


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "SUCCESS", "errors": []},
        {"status": "FAILURE", "errors": [{"type": "DASHBOARD_NOT_FOUND", "description": "gone"}]},
    ],
)
def test_a_dashboard_is_deleted_when_the_delete_call_says_so_even_if_the_index_is_stale(
    answer: dict[str, Any],
) -> None:
    _, client = _dashboard_world(answer)
    result = rollback(
        client, ACCOUNT, manifest(("dashboard", "g1", "dash")), apply=True, sleep=lambda _: None
    )
    assert [o.kind for o in result.deleted] == ["dashboard"] and not result.failed


def test_a_dashboard_delete_that_fails_for_another_reason_is_reported() -> None:
    answer = {"status": "FAILURE", "errors": [{"type": "FORBIDDEN", "description": "no access"}]}
    _, client = _dashboard_world(answer)
    result = rollback(
        client, ACCOUNT, manifest(("dashboard", "g1", "dash")), apply=True, sleep=lambda _: None
    )
    assert not result.deleted and "no access" in result.failed[0][1]
