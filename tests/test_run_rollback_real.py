"""Rollback of dashboards by kind: sandbox runs stay strict, real migrations need guid + marker."""

from pathlib import Path
from typing import Any

import pytest

from importer.core.manifest import CreatedObject
from importer.core.run_rollback import DASHBOARD_DETAIL, is_sandbox_account, rollback_run
from importer.core.runs import RunRecord, RunStore
from test_run_rollback import FakeNR, ScriptConsole, factory

MARKER = "Imported from a Grafana dashboard. Review it."


class DashNR(FakeNR):
    """Also answers the dashboard detail read (name + description)."""

    def __init__(self, dashboards: dict[str, tuple[str, str]]) -> None:
        super().__init__({("dashboard", g): n for g, (n, _) in dashboards.items()})
        self.descriptions = {g: d for g, (_, d) in dashboards.items()}

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        if query == DASHBOARD_DETAIL and not is_write:
            guid = str(variables["g"])
            name = self.objects.get(("dashboard", guid))
            entity = None if name is None else {"guid": guid, "name": name}
            if entity is not None:
                entity["description"] = self.descriptions[guid]
            return {"actor": {"entity": entity}}
        return super().run(query, variables, is_write=is_write)


def dash(guid: str = "g1", name: str = "Checkout latency", description: str = MARKER) -> Any:
    return CreatedObject.model_validate(
        {"kind": "dashboard", "id": guid, "name": name, "description": description}
    )


def setup(
    tmp_path: Path, objects: list[CreatedObject], *, sandbox: bool
) -> tuple[RunStore, RunRecord]:
    store = RunStore(tmp_path / "runs")
    record = store.start(
        source="grafana-dashboards",
        account_id=7,
        region="us",
        out_dir=tmp_path / "out",
        sandbox=sandbox,
    )
    record.objects = objects
    record.status = "applied"
    store.save(record)
    return store, record


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")


def go(store: RunStore, record: RunRecord, nr: DashNR, *, real: bool = False) -> ScriptConsole:
    console = ScriptConsole(["yes"])
    rollback_run(console, store, record, factory(nr), sleep=lambda _: None, real_migration=real)
    return console


def test_sandbox_flag_from_allowed_list() -> None:
    assert is_sandbox_account(7, "7,8") is True
    assert is_sandbox_account(9, "7,8") is False
    assert is_sandbox_account(9, None) is True  # unknown: strict
    assert is_sandbox_account(9, "") is True


def test_old_records_are_sandbox(tmp_path: Path) -> None:
    _, record = setup(tmp_path, [], sandbox=True)
    text = (tmp_path / "runs" / f"{record.run_id}.json").read_text().replace('"sandbox": true,', "")
    assert RunRecord.model_validate_json(text).sandbox is True


def test_sandbox_record_refuses_dashboard_with_foreign_name(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [dash()], sandbox=True)
    nr = DashNR({"g1": ("Checkout latency", MARKER)})
    console = go(store, record, nr, real=True)  # the flag does not loosen a sandbox record
    assert nr.deleted == []
    assert "name not ours" in console.text


def test_sandbox_record_accepts_dashboard_with_ckg_prefix(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [dash(name="ckg-board")], sandbox=True)
    nr = DashNR({"g1": ("ckg-board", MARKER)})
    go(store, record, nr)
    assert nr.deleted == [("dashboard", "g1")]


def test_real_migration_deletes_dashboard_by_kind_with_matching_marker(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [dash()], sandbox=False)
    nr = DashNR({"g1": ("Checkout latency", MARKER)})
    go(store, record, nr, real=True)
    assert nr.deleted == [("dashboard", "g1")]
    assert store.load(record.run_id).status == "rolled-back"


def test_real_migration_without_flag_stays_strict(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [dash()], sandbox=False)
    nr = DashNR({"g1": ("Checkout latency", MARKER)})
    console = go(store, record, nr)
    assert nr.deleted == []
    assert "--real-migration" in console.text


def test_entry_without_marker_is_refused(tmp_path: Path) -> None:
    store, record = setup(tmp_path, [dash(description="")], sandbox=False)
    nr = DashNR({"g1": ("Checkout latency", "")})
    console = go(store, record, nr, real=True)
    assert nr.deleted == []
    assert "marker" in console.text


@pytest.mark.parametrize(
    ("live_name", "live_desc", "why"),
    [
        ("Renamed by someone", MARKER, "name"),
        ("Checkout latency", "Hand written now", "description"),
    ],
)
def test_live_mismatch_is_refused_with_reason(
    tmp_path: Path, live_name: str, live_desc: str, why: str
) -> None:
    store, record = setup(tmp_path, [dash()], sandbox=False)
    nr = DashNR({"g1": (live_name, live_desc)})
    console = go(store, record, nr, real=True)
    assert nr.deleted == []
    assert why in console.text and "changed" in console.text
    assert store.load(record.run_id).rolled_back == []
