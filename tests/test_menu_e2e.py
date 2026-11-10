"""The whole Grafana menu flow offline: read, convert, preview, create three kinds, undo them."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from importer.core.manifest import CreatedObject, Manifest, load_manifest, save_manifest
from test_menu import ScriptConsole, make
from test_run_rollback import FakeNR

CREATED = {
    "apply": ("condition", "10", "ckg-grafana-cpu"),
    "apply-dashboards": ("dashboard", "g1", "Checkout latency"),
    "apply-notifications": ("workflow", "w1", "Imported:route 1"),
}


class GrafanaRunner:
    """Stands in for the CLI: records every command, writes manifests like the apply commands."""

    def __init__(self, nr: FakeNR) -> None:
        self.calls: list[list[str]] = []
        self.nr = nr

    def __call__(self, args: list[str]) -> int:
        self.calls.append(args)
        if args[0] in CREATED and "--apply" in args:
            kind, oid, name = CREATED[args[0]]
            path = Path(args[args.index("--manifest") + 1])
            save_manifest(
                Manifest(
                    account_id=7,
                    objects=[CreatedObject.model_validate({"kind": kind, "id": oid, "name": name})],
                ),
                path,
            )
            assert load_manifest(path).account_id == 7
            self.nr.objects[(kind, oid)] = name  # what New Relic now holds
        return 0

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


def test_grafana_end_to_end_create_everything_then_roll_it_all_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://x.grafana.net")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "t")
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    nr = FakeNR({})
    runner = GrafanaRunner(nr)
    answers = [
        "3", "1", "yes",  # Grafana: alert conditions
        "3", "3", "1", "yes", "0",  # dashboards (then back to the main menu)
        "3", "4", "1", "yes", "0",  # notifications
        "8",  # what did I just do?
        "5", "1", "yes",  # roll back the newest run (notifications)
        "5", "1", "yes",  # then dashboards
        "5", "1", "yes",  # then alert conditions
        "5",  # nothing left to roll back
        "8",  # summary again
        "0",
    ]  # fmt: skip
    menu, console, _, opened, store = make(tmp_path, answers, None)
    menu.runner = runner

    @contextmanager
    def client(region: str) -> Any:
        yield nr

    menu.client_factory = client
    assert menu.run() == 0

    names = runner.names()
    for command in ("extract-grafana", "convert", "extract-grafana-dashboards",
                    "convert-grafana-dashboards", "extract-grafana-notifications",
                    "convert-grafana-notifications"):  # fmt: skip
        assert command in names
    applied = [c[0] for c in runner.calls if c[0].startswith("apply") and "--apply" in c]
    assert applied == ["apply", "apply-dashboards", "apply-notifications"]
    for call in runner.calls:  # every write names the account twice
        if "--apply" in call:
            assert call[call.index("--account-id") + 1] == "7"
            assert call[call.index("--confirm-account") + 1] == "7"
    assert console.text.count("New Relic account 7") >= 6
    assert opened == []  # no report or browser was opened

    runs = store.list()
    assert [r.source for r in runs] == ["grafana-notifications", "grafana-dashboards", "grafana"]
    assert all(r.status == "rolled-back" for r in runs)
    assert nr.objects == {}
    assert sorted(k for k, _ in nr.deleted) == ["condition", "dashboard", "workflow"]
    assert "no recorded runs to roll back" in console.text.lower()
    assert "Last run:" in console.text
    assert "0 still in New Relic" in console.text
    assert isinstance(console, ScriptConsole) and not console.answers
