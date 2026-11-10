"""The numbered menu, driven by scripted answers and a fake command runner (no network)."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from importer.core.console import InputEnded
from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.menu import Menu
from importer.core.runs import RunStore


class ScriptConsole:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.lines: list[str] = []
        self.secrets_asked: list[str] = []

    def say(self, text: str) -> None:
        self.lines.append(text)

    def ask(self, prompt: str) -> str:
        self.lines.append(prompt)
        if not self.answers:
            raise InputEnded
        return self.answers.pop(0)

    def secret(self, prompt: str) -> str:
        self.secrets_asked.append(prompt)
        if not self.answers:
            raise InputEnded
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class FakeRunner:
    def __init__(self, fail: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail or {}
        self.on_apply: Callable[[list[str]], None] | None = None

    def __call__(self, args: list[str]) -> int:
        self.calls.append(args)
        if args[0] == "apply" and "--apply" in args and self.on_apply:
            self.on_apply(args)
        return self.fail.get(args[0], 0)

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")


def make(
    tmp_path: Path, answers: list[str], runner: FakeRunner | None = None
) -> tuple[Menu, ScriptConsole, FakeRunner, list[str], RunStore]:
    console = ScriptConsole(answers)
    run = runner or FakeRunner()
    opened: list[str] = []
    store = RunStore(tmp_path / "runs")
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "policies.json").write_text("{}")
    menu = Menu(
        console,
        run,
        store,
        work_dir=tmp_path / "work",
        opener=opened.append,
        sample_dir=sample,
        client_factory=None,
    )
    return menu, console, run, opened, store


def test_menu_lists_every_choice_and_quits(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, ["0"])
    assert menu.run() == 0
    for text in ("1", "GCP", "2", "AWS", "3", "Grafana", "4", "Dynatrace", "Roll back", "Quit"):
        assert text in console.text
    assert "6" in console.text and "Check my setup" in console.text
    assert "7" in console.text and "sample" in console.text.lower()


def test_bad_choice_reprompts(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, ["x", "9", "0"])
    menu.run()
    assert console.text.count("Please type a number") == 2


def test_input_ended_leaves_cleanly(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, [])
    assert menu.run() == 1
    assert "input ended" in console.text.lower()


def test_gcp_walks_steps_in_order_and_stops_before_write(tmp_path: Path) -> None:
    # 1 GCP, project, then at the apply submenu: 0 = back; then 0 quit
    menu, _, run, opened, store = make(tmp_path, ["1", "my-proj", "0", "0"])
    menu.run()
    assert run.names() == ["doctor", "extract-gcp", "convert", "apply"]
    assert "--apply" not in run.calls[-1]  # preview only
    assert opened == []
    assert store.list() == []


def test_apply_needs_typed_yes_shows_account_and_records_run(tmp_path: Path) -> None:
    runner = FakeRunner()

    def write_manifest(args: list[str]) -> None:
        path = Path(args[args.index("--manifest") + 1])
        save_manifest(
            Manifest(
                account_id=7,
                objects=[
                    CreatedObject.model_validate({"kind": "condition", "id": "1", "name": "ckg-a"})
                ],
            ),
            path,
        )

    runner.on_apply = write_manifest
    menu, console, run, _, store = make(tmp_path, ["1", "my-proj", "1", "yes", "0"], runner)
    menu.run()
    assert "account 7" in console.text
    last = run.calls[-1]
    assert "--apply" in last and last[last.index("--confirm-account") + 1] == "7"
    (record,) = store.list()
    assert record.status == "applied" and record.objects[0].name == "ckg-a"


def test_apply_declined_writes_nothing(tmp_path: Path) -> None:
    menu, _, run, _, store = make(tmp_path, ["1", "my-proj", "1", "no", "0", "0"])
    menu.run()
    assert not any("--apply" in c for c in run.calls)
    assert store.list() == []


def test_missing_allowed_accounts_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
    menu, console, run, _, _ = make(tmp_path, ["1", "my-proj", "1", "yes", "0", "0"])
    menu.run()
    assert not any("--apply" in c for c in run.calls)
    assert "NEW_RELIC_ALLOWED_ACCOUNT_IDS" in console.text


def test_failed_setup_check_gives_one_sentence_and_stops(tmp_path: Path) -> None:
    menu, console, run, _, _ = make(tmp_path, ["1", "0"], FakeRunner({"doctor": 1}))
    menu.run()
    assert run.names() == ["doctor"]
    assert "gcloud auth application-default login" in console.text


def test_missing_secret_is_asked_hidden_and_not_echoed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://x.grafana.net")
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)
    menu, console, run, _, _ = make(tmp_path, ["3", "tok-SECRET", "0", "0"])
    menu.run()
    assert console.secrets_asked and "GRAFANA_SA_TOKEN" in console.secrets_asked[0]
    assert "tok-SECRET" not in console.text
    assert "extract-grafana" in run.names()


def test_skipping_a_secret_stops_the_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://x.apps.dynatrace.com")
    monkeypatch.delenv("DYNATRACE_API_TOKEN", raising=False)
    menu, console, run, _, _ = make(tmp_path, ["4", "", "0"])
    menu.run()
    assert run.calls == []
    assert "DYNATRACE_API_TOKEN" in console.text


def test_report_opens_only_when_chosen(tmp_path: Path) -> None:
    menu, _, run, opened, _ = make(tmp_path, ["1", "p", "2", "0", "0"])
    menu.run()
    assert "report" in run.names()
    assert len(opened) == 1 and opened[0].endswith("report.html")


def test_check_my_setup_runs_doctor_for_each_source(tmp_path: Path) -> None:
    menu, console, run, _, _ = make(tmp_path, ["6", "0"], FakeRunner({"doctor": 1}))
    menu.run()
    assert run.names().count("doctor") == 4
    assert console.text.count("Next:") >= 4


def test_sample_runs_offline_and_does_not_open_report(tmp_path: Path) -> None:
    menu, _, run, opened, _ = make(tmp_path, ["7", "0", "0"])
    menu.run()
    assert run.names()[0] == "convert"
    assert "apply" not in run.names() and opened == []


def test_rollback_menu_lists_runs_and_cancels(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, ["5", "0", "0"])
    menu.run()
    assert "no recorded runs" in console.text.lower()


def test_interrupted_run_offers_continue_or_roll_back(tmp_path: Path) -> None:
    menu, console, run, _, store = make(tmp_path, ["5", "1", "1", "yes", "0"])
    record = store.start(source="gcp", account_id=7, region="us", out_dir=tmp_path / "o")
    (tmp_path / "o" / "ir").mkdir(parents=True)
    menu.run()
    assert "interrupted" in console.text.lower()
    assert "Continue" in console.text and "Roll back" in console.text
    assert any("--apply" in c for c in run.calls)
    assert record.run_id in console.text


def _unused(*_: Any) -> None: ...


def _write_manifest_for(kind: str, name: str) -> Callable[[list[str]], None]:
    def write(args: list[str]) -> None:
        path = Path(args[args.index("--manifest") + 1])
        obj = {"kind": kind, "id": "g1", "name": name}
        save_manifest(Manifest(account_id=7, objects=[CreatedObject.model_validate(obj)]), path)

    return write


def _apply_calls(run: FakeRunner, command: str) -> list[list[str]]:
    return [c for c in run.calls if c[0] == command]


class ManifestRunner(FakeRunner):
    """Writes a manifest the way an apply command does, for one command and one object kind."""

    def __init__(self, command: str, kind: str, name: str) -> None:
        super().__init__()
        self.command, self.kind, self.name = command, kind, name

    def __call__(self, args: list[str]) -> int:
        if args[0] == self.command and "--apply" in args:
            _write_manifest_for(self.kind, self.name)(args)
        return super().__call__(args)


@pytest.fixture
def grafana_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://x.grafana.net")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "t")


@pytest.mark.usefixtures("grafana_env")
def test_grafana_dashboards_read_convert_preview_create_and_record(tmp_path: Path) -> None:
    run = ManifestRunner("apply-dashboards", "dashboard", "Latency")
    menu, console, _, opened, store = make(tmp_path, ["3", "3", "1", "yes", "0", "0"], run)
    menu.run()
    names = run.names()
    i = names.index("extract-grafana-dashboards")
    assert names[i : i + 3] == [
        "extract-grafana-dashboards",
        "convert-grafana-dashboards",
        "apply-dashboards",
    ]
    assert "--apply" not in run.calls[i + 2]  # the preview writes nothing
    assert "account 7" in console.text and "private" in console.text
    assert opened == []
    (record,) = store.list()
    assert record.source == "grafana-dashboards" and record.status == "applied"
    assert [(o.kind, o.id, o.name) for o in record.objects] == [("dashboard", "g1", "Latency")]


@pytest.mark.usefixtures("grafana_env")
def test_grafana_notifications_use_the_converted_alerts_and_apply_notifications(
    tmp_path: Path,
) -> None:
    run = ManifestRunner("apply-notifications", "workflow", "Imported:wf")
    menu, console, _, _, store = make(tmp_path, ["3", "4", "1", "yes", "0", "0"], run)
    menu.run()
    convert = next(c for c in run.calls if c[0] == "convert-grafana-notifications")
    assert convert[convert.index("--alerts") + 1].endswith("alerts")
    (record,) = store.list()
    assert record.source == "grafana-notifications"
    assert record.objects[0].kind == "workflow"
    assert "disabled" in console.text


def test_dynatrace_notifications_ask_about_needs_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://x.apps.dynatrace.com")
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "t")
    menu, _, run, _, store = make(tmp_path, ["4", "4", "1", "yes", "yes", "0", "0"])
    menu.run()
    convert = next(c for c in run.calls if c[0] == "convert-notifications")
    assert convert[:3] == ["convert-notifications", "--source", "dynatrace"]
    last = _apply_calls(run, "apply-notifications")[-1]
    assert "--apply" in last and "--include-needs-review" in last
    (record,) = store.list()
    assert record.include_review and record.source == "dynatrace-notifications"


def test_dynatrace_dashboards_are_planned_never_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://x.apps.dynatrace.com")
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "t")
    menu, console, run, _, store = make(tmp_path, ["4", "3", "0", "0"])
    menu.run()
    assert "plan-dynatrace-dashboards" in run.names()
    assert "apply-dashboards" not in run.names() and store.list() == []
    assert "nothing to create" in console.text


@pytest.mark.usefixtures("grafana_env")
def test_declining_a_dashboard_create_writes_nothing(tmp_path: Path) -> None:
    menu, _, run, _, store = make(tmp_path, ["3", "3", "1", "no", "0", "0"])
    menu.run()
    assert not any("--apply" in c for c in run.calls) and store.list() == []


@pytest.mark.usefixtures("grafana_env")
def test_a_failed_read_stops_the_dashboard_flow(tmp_path: Path) -> None:
    menu, console, run, _, _ = make(
        tmp_path, ["3", "3", "0", "0"], FakeRunner({"extract-grafana-dashboards": 1})
    )
    menu.run()
    assert "convert-grafana-dashboards" not in run.names()
    assert "Nothing was created" in console.text


def _record_with(store: RunStore, source: str, objects: list[dict[str, str]]) -> str:
    record = store.start(source=source, account_id=7, region="us", out_dir=Path("o"))
    record.objects = [CreatedObject.model_validate(o) for o in objects]
    record.status = "applied"
    store.save(record)
    return record.run_id


def test_what_did_i_just_do_with_no_runs(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, ["8", "0"])
    menu.run()
    assert "nothing yet" in console.text.lower()


def test_what_did_i_just_do_summarises_the_last_run(tmp_path: Path) -> None:
    menu, console, _, _, store = make(tmp_path, ["8", "0"])
    _record_with(store, "gcp", [{"kind": "condition", "id": "1", "name": "ckg-old"}])
    run_id = _record_with(
        store,
        "grafana-dashboards",
        [
            {"kind": "dashboard", "id": "g1", "name": "A"},
            {"kind": "dashboard", "id": "g2", "name": "B"},
        ],
    )
    menu.run()
    text = console.text
    assert run_id in text and "grafana-dashboards" in text and "account 7" in text
    assert "2 dashboard" in text and "private" in text
    assert "Roll back a run" in text and "rollback-run" in text


def test_what_did_i_just_do_names_disabled_things_and_rolled_back_state(tmp_path: Path) -> None:
    menu, console, _, _, store = make(tmp_path, ["8", "0"])
    run_id = _record_with(
        store,
        "grafana",
        [
            {"kind": "condition", "id": "1", "name": "ckg-a"},
            {"kind": "workflow", "id": "2", "name": "Imported:w"},
        ],
    )
    record = store.load(run_id)
    record.rolled_back = ["condition:1"]
    record.status = "partly-rolled-back"
    store.save(record)
    menu.run()
    assert "1 still in New Relic" in console.text and "disabled" in console.text
