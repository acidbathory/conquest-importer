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
