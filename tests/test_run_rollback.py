"""Rollback of a recorded run: preview, typed yes, prefix rule, read back, record update."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from importer.core.apply import NewRelicAPIError
from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.rollback import DELETE, READ
from importer.core.run_rollback import ALLOWED_PREFIXES, rollback_run
from importer.core.runs import RunRecord, RunStore


class ScriptConsole:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.lines: list[str] = []

    def say(self, text: str) -> None:
        self.lines.append(text)

    def ask(self, prompt: str) -> str:
        self.lines.append(prompt)
        return self.answers.pop(0)

    def secret(self, prompt: str) -> str:
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class FakeNR:
    """Holds objects by (kind, id); answers the queries that rollback() sends."""

    def __init__(self, objects: dict[tuple[str, str], str], stuck: bool = False) -> None:
        self.objects = dict(objects)
        self.deleted: list[tuple[str, str]] = []
        self.stuck = stuck

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        for kind, (q, _) in READ.items():
            if query == q and not is_write:
                oid = str(variables.get("id") or variables.get("g"))
                name = self.objects.get((kind, oid))
                if name is None:
                    raise NewRelicAPIError("Not Found")
                return _shape(kind, oid, name)
        for kind, (m, var) in DELETE.items():
            if query == m and is_write:
                oid = str(variables[var])
                self.deleted.append((kind, oid))
                if not self.stuck:
                    self.objects.pop((kind, oid), None)
                return {"dashboardDelete": {"status": "SUCCESS"}} if kind == "dashboard" else {}
        if "nrqlConditionsSearch" in query:
            return {
                "actor": {"account": {"alerts": {"nrqlConditionsSearch": {"nrqlConditions": []}}}}
            }
        raise AssertionError(f"unexpected query {query[:60]}")


def _shape(kind: str, oid: str, name: str) -> dict[str, Any]:
    item = {"id": oid, "name": name}
    if kind == "dashboard":
        return {"actor": {"entity": item}}
    if kind in ("condition", "policy"):
        key = "nrqlCondition" if kind == "condition" else "policy"
        return {"actor": {"account": {"alerts": {key: item}}}}
    group = {"destination": "aiNotifications", "channel": "aiNotifications"}.get(
        kind, "aiWorkflows"
    )
    sub = {"destination": "destinations", "channel": "channels", "workflow": "workflows"}[kind]
    return {"actor": {"account": {group: {sub: {"entities": [item]}}}}}


def obj(kind: str, id_: str, name: str) -> CreatedObject:
    return CreatedObject.model_validate({"kind": kind, "id": id_, "name": name})


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")


def setup(tmp_path: Path, objects: list[CreatedObject]) -> tuple[RunStore, RunRecord]:
    store = RunStore(tmp_path / "runs")
    record = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path / "out")
    record.objects = objects
    record.status = "applied"
    store.save(record)
    return store, record


def factory(nr: FakeNR) -> Any:
    @contextmanager
    def make(region: str) -> Any:
        yield nr

    return make


def test_prefix_constants() -> None:
    assert "ckg-" in ALLOWED_PREFIXES


@pytest.mark.usefixtures("env")
def test_yes_deletes_only_prefixed_recorded_objects(tmp_path: Path) -> None:
    mine, other = obj("condition", "1", "ckg-a"), obj("condition", "2", "someone-elses")
    store, record = setup(tmp_path, [mine, other])
    nr = FakeNR({("condition", "1"): "ckg-a", ("condition", "2"): "someone-elses"})
    console = ScriptConsole(["yes"])
    rollback_run(console, store, record, factory(nr), sleep=lambda _: None)
    assert nr.deleted == [("condition", "1")]
    assert ("condition", "2") in nr.objects
    assert "account 7" in console.text and "ckg-a" in console.text
    again = store.load(record.run_id)
    assert again.rolled_back == ["condition:1"]
    assert again.status == "partly-rolled-back"  # the refused one remains


@pytest.mark.usefixtures("env")
def test_anything_but_yes_cancels(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [obj("condition", "1", "ckg-a")])
    nr = FakeNR({("condition", "1"): "ckg-a"})
    for answer in ("y", "", "no", "YES!"):
        rollback_run(ScriptConsole([answer]), store, record, factory(nr), sleep=lambda _: None)
    assert nr.deleted == []
    assert store.load(record.run_id).rolled_back == []


@pytest.mark.usefixtures("env")
def test_full_rollback_marks_record(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [obj("policy", "5", "ckg-p"), obj("condition", "1", "ckg-a")])
    nr = FakeNR({("policy", "5"): "ckg-p", ("condition", "1"): "ckg-a"})
    rollback_run(ScriptConsole(["yes"]), store, record, factory(nr), sleep=lambda _: None)
    assert nr.objects == {}
    assert store.load(record.run_id).status == "rolled-back"


@pytest.mark.usefixtures("env")
def test_still_there_after_delete_is_not_recorded_as_gone(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [obj("condition", "1", "ckg-a")])
    nr = FakeNR({("condition", "1"): "ckg-a"}, stuck=True)
    console = ScriptConsole(["yes"])
    rollback_run(console, store, record, factory(nr), sleep=lambda _: None)
    assert store.load(record.run_id).rolled_back == []
    assert "still exists" in console.text


@pytest.mark.usefixtures("env")
def test_wrong_account_refuses_before_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "8")
    store, record = setup(tmp_path, [obj("condition", "1", "ckg-a")])
    nr = FakeNR({("condition", "1"): "ckg-a"})
    console = ScriptConsole([])
    rollback_run(console, store, record, factory(nr), sleep=lambda _: None)
    assert nr.deleted == []
    assert "account 7" in console.text and "8" in console.text


@pytest.mark.usefixtures("env")
def test_interrupted_run_picks_up_manifest_objects(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [])
    record.status = "in-progress"
    store.save(record)
    (tmp_path / "out").mkdir()
    save_manifest(
        Manifest(account_id=7, objects=[obj("condition", "1", "ckg-a")]),
        tmp_path / "out" / "manifest.json",
    )
    nr = FakeNR({("condition", "1"): "ckg-a"})
    rollback_run(ScriptConsole(["yes"]), store, record, factory(nr), sleep=lambda _: None)
    assert nr.deleted == [("condition", "1")]


@pytest.mark.usefixtures("env")
def test_nothing_to_delete(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [])
    console = ScriptConsole([])
    rollback_run(console, store, record, factory(FakeNR({})), sleep=lambda _: None)
    assert "nothing" in console.text.lower()


@pytest.mark.usefixtures("env")
def test_dashboards_and_notifications_made_by_the_menu_are_rolled_back(tmp_path: Path) -> None:
    objects = [
        obj("dashboard", "g1", "Checkout latency"),  # a dashboard keeps its Grafana title
        obj("workflow", "w1", "Imported: Grafana route 1"),
        obj("channel", "c1", "Imported: email"),
        obj("destination", "d1", "Imported: email dest"),
        obj("condition", "9", "someone-elses"),  # still refused
    ]
    store, record = setup(tmp_path, objects)
    nr = FakeNR({(o.kind, o.id): o.name for o in objects})
    rollback_run(ScriptConsole(["yes"]), store, record, factory(nr), sleep=lambda _: None)
    assert sorted(nr.deleted) == [
        ("channel", "c1"),
        ("dashboard", "g1"),
        ("destination", "d1"),
        ("workflow", "w1"),
    ]
    assert ("condition", "9") in nr.objects
    assert store.load(record.run_id).status == "partly-rolled-back"
