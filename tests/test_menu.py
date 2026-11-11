"""The numbered menu, driven by scripted answers and a fake command runner (no network)."""

import webbrowser
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from importer.core.console import InputEnded
from importer.core.manifest import CreatedObject
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


OPENED: list[str] = []


@contextmanager
def _no_network(region: str) -> Any:
    raise OSError("no network in tests")
    yield


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    OPENED.clear()
    monkeypatch.setattr(webbrowser, "open", lambda url, *_a, **_k: OPENED.append(url))
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")


def make(
    tmp_path: Path, answers: list[str], runner: FakeRunner | None = None
) -> tuple[Menu, ScriptConsole, FakeRunner, list[str], RunStore]:
    console = ScriptConsole(answers)
    run = runner or FakeRunner()
    opened = OPENED
    store = RunStore(tmp_path / "runs")
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "policies.json").write_text("{}")
    menu = Menu(
        console,
        run,
        store,
        work_dir=tmp_path / "work",
        sample_dir=sample,
        client_factory=_no_network,
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
    menu, console, _, _, _ = make(tmp_path, ["x", "11", "0"])
    menu.run()
    assert console.text.count("Please type a number") == 2


def test_input_ended_leaves_cleanly(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, [])
    assert menu.run() == 1
    assert "input ended" in console.text.lower()


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


def test_check_my_setup_runs_doctor_for_each_source(tmp_path: Path) -> None:
    menu, console, run, _, _ = make(tmp_path, ["6", "0"], FakeRunner({"doctor": 1}))
    menu.run()
    assert run.names().count("doctor") == 5
    assert console.text.count("Next:") >= 5


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


@pytest.fixture
def grafana_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://x.grafana.net")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "t")


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


def test_azure_is_choice_10_and_runs_the_azure_extract_and_convert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in (
        ("AZURE_TENANT_ID", "t"),
        ("AZURE_CLIENT_ID", "c"),
        ("AZURE_SUBSCRIPTION_ID", "s"),
        ("AZURE_CLIENT_SECRET", "x"),
    ):
        monkeypatch.setenv(name, value)
    menu, console, run, _, _ = make(tmp_path, ["10", "0"])
    menu.run()
    assert "10  Azure" in console.text
    assert run.calls[0] == ["doctor", "--source", "azure"]
    assert any(c[0] == "extract-azure" for c in run.calls)
    assert any(c[:3] == ["convert", "--source", "azure"] for c in run.calls)
