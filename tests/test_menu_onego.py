"""The one-pass source flow: read everything, convert everything, one preview, one yes."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from menu_helpers import HybridRunner
from test_menu import OPENED, ScriptConsole, make
from test_run_rollback import FakeNR

from importer.core.apply import ACCOUNT_QUERY


class NamedNR(FakeNR):
    """Also answers the account-name query."""

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        if query == ACCOUNT_QUERY:
            return {"actor": {"account": {"name": "Sandbox Team"}}}
        return super().run(query, variables, is_write=is_write)


@pytest.fixture(autouse=True)
def _grafana(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    monkeypatch.setenv("GRAFANA_URL", "https://x.grafana.net")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "t")


def _menu(
    tmp_path: Path, answers: list[str], runner: HybridRunner, nr: FakeNR | None = None
) -> Any:
    menu, console, _, _, store = make(tmp_path, answers, None)
    menu.runner = runner
    fake = nr or NamedNR({})

    @contextmanager
    def client(region: str) -> Any:
        yield fake

    menu.client_factory = client
    return menu, console, store, fake


def test_grafana_one_pass_preview_names_account_counts_and_reasons(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, store, _ = _menu(tmp_path, ["3", "no", "0"], runner)
    assert menu.run() == 0
    text = console.text
    # everything is read and converted before the single question
    names = runner.names()
    for command in ("extract-grafana", "extract-grafana-dashboards", "extract-grafana-notifications",
                    "convert", "convert-grafana-dashboards", "convert-grafana-notifications",
                    "plan-grafana-datapath", "triage-grafana-dashboards",
                    "plan-grafana-quickstarts", "plan-remediation"):  # fmt: skip
        assert command in names
    assert "will be created" in text and "need your attention" in text
    assert "account 7 (Sandbox Team)" in text
    assert "disabled or private" in text
    assert "datapath-plan.md" in text and "remediation-plan.md" in text
    assert text.count("Type 'yes'") == 1  # ONE confirmation
    assert "Create these" not in text and "Also bring over" not in text
    assert "Nothing was created." in text
    assert runner.writes() == [] and store.list() == [] and OPENED == []


def test_reasons_come_from_the_real_remediation_reader(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, _, _ = _menu(tmp_path, ["3", "no", "0"], runner)
    menu.run()
    plan = next(Path(tmp_path).rglob("remediation-plan.md")).read_text()
    start = next(i for i, line in enumerate(console.lines) if "Most common reasons" in line)
    shown = [x.strip() for x in console.lines[start + 1 : start + 6] if x.startswith("  ")]
    shown = [x for x in shown if x.split(" ", 1)[0].isdigit()]
    assert 1 <= len(shown) <= 5
    for line in shown:
        title = line.strip().split("  ", 1)[1]
        assert title in plan  # each shown reason is a group of the generated plan


def test_one_yes_creates_all_kinds_in_order_with_one_run_and_rolls_all_back(
    tmp_path: Path,
) -> None:
    runner = HybridRunner()
    nr = NamedNR({})
    menu, console, store, _ = _menu(
        tmp_path, ["3", "yes", "8", "5", "1", "yes", "5", "0"], runner, nr
    )
    original = runner.__call__

    def tracking(args: list[str]) -> int:
        code = original(args)
        if "--apply" in args:
            from menu_helpers import CREATED

            kind, oid, label = CREATED[args[0]]
            nr.objects[(kind, oid)] = label
        return code

    menu.runner = tracking
    assert menu.run() == 0
    assert runner.writes() == ["apply", "apply-notifications", "apply-dashboards"]
    (record,) = store.list()
    assert record.source == "grafana" and record.status == "rolled-back"
    assert {o.kind for o in record.objects} == {"condition", "workflow", "dashboard"}
    assert nr.objects == {} and len(nr.deleted) == 3
    text = console.text
    assert "Done." in text and "pick 5 Roll back a run" in text
    assert "turn on the alert conditions" in text
    assert OPENED == []
    assert "no recorded runs to roll back" in text.lower()


def test_failure_midway_stops_says_what_exists_and_offers_rollback(tmp_path: Path) -> None:
    runner = HybridRunner()
    nr = NamedNR({})
    original = runner.__call__

    def failing(args: list[str]) -> int:
        if args[0] == "apply-notifications" and "--apply" in args:
            runner.calls.append(args)
            return 1
        code = original(args)
        if args[0] == "apply" and "--apply" in args:
            nr.objects[("condition", "10")] = "ckg-grafana-cpu"
        return code

    menu, console, store, _ = _menu(tmp_path, ["3", "yes", "1", "yes", "0"], runner, nr)
    menu.runner = failing
    menu.run()
    assert runner.writes() == ["apply", "apply-notifications"]  # dashboards never tried
    assert "Notification workflows could not all be created" in console.text
    assert "Created so far: 1 condition" in console.text
    (record,) = store.list()
    assert record.status == "rolled-back" and nr.objects == {}


def test_failure_midway_can_continue(tmp_path: Path) -> None:
    runner = HybridRunner()
    state = {"failed": False}
    original = runner.__call__

    def flaky(args: list[str]) -> int:
        if args[0] == "apply-notifications" and "--apply" in args and not state["failed"]:
            state["failed"] = True
            runner.calls.append(args)
            return 1
        return original(args)

    menu, console, store, _ = _menu(tmp_path, ["3", "yes", "2", "0"], runner)
    menu.runner = flaky
    menu.run()
    assert runner.writes()[-1] == "apply-dashboards"
    (record,) = store.list()
    assert record.status == "applied" and len(record.objects) == 3
    assert "Done." in console.text


def test_declining_leaves_nothing_created(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, _, store, _ = _menu(tmp_path, ["3", "", "0"], runner)  # Enter is not yes
    menu.run()
    assert runner.writes() == [] and store.list() == []


def test_not_allow_listed_account_is_refused_before_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
    runner = HybridRunner()
    menu, console, store, _ = _menu(tmp_path, ["3", "0"], runner)
    menu.run()
    assert "NEW_RELIC_ALLOWED_ACCOUNT_IDS" in console.text
    assert "Type 'yes'" not in console.text
    assert runner.writes() == [] and store.list() == []


def test_a_failed_dashboard_read_leaves_those_out_but_continues(tmp_path: Path) -> None:
    runner = HybridRunner(fail={"extract-grafana-dashboards"})
    menu, console, store, _ = _menu(tmp_path, ["3", "yes", "0"], runner)
    menu.run()
    assert "convert-grafana-dashboards" not in runner.names()
    assert "dashboards did not work" in console.text
    assert runner.writes() == ["apply", "apply-notifications"]
    (record,) = store.list()
    assert {o.kind for o in record.objects} == {"condition", "workflow"}


def test_a_failed_alert_read_stops_everything(tmp_path: Path) -> None:
    runner = HybridRunner(fail={"extract-grafana"})
    menu, console, store, _ = _menu(tmp_path, ["3", "0"], runner)
    menu.run()
    assert runner.names() == ["doctor", "extract-grafana"]
    assert "Nothing was changed" in console.text and store.list() == []


def test_gcp_one_pass_converts_dashboards_and_notifications_too(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, _, _ = _menu(tmp_path, ["1", "my-proj", "no", "0"], runner)
    menu.run()
    for command in ("extract-gcp", "convert", "convert-gcp-notifications", "convert-dashboards"):
        assert command in runner.names()
    assert "plan-grafana-datapath" not in runner.names()
    assert "datapath-plan.md" not in console.text


def test_dynatrace_dashboards_are_planned_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://x.apps.dynatrace.com")
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "t")
    runner = HybridRunner(fail={"extract-dynatrace-inventory"})
    menu, console, store, _ = _menu(tmp_path, ["4", "0"], runner)
    menu.run()
    assert "apply-dashboards" not in runner.names()
    assert "convert-notifications" in runner.names()
    assert isinstance(console, ScriptConsole) and store.list() == []
