"""The guided menu opens its HTML report twice (plan, then what was created), only for a person."""

import re
from pathlib import Path
from typing import Any

import pytest

from importer.core.menu import Menu
from menu_helpers import HybridRunner
from test_menu_onego import _grafana, _menu  # noqa: F401  (autouse fixture is re-used)


def _wire(menu: Menu, runner: HybridRunner, events: list[str], *, terminal: bool = True) -> None:
    def opener(url: str) -> None:
        events.append(f"open:{Path(url.removeprefix('file://')).name}")

    def tracking(args: list[str]) -> int:
        if args[0].startswith("apply") and "--apply" in args:
            events.append("create")
        return runner(args)

    menu.runner = tracking
    menu.opener = opener
    menu.terminal = lambda: terminal
    original = menu.c.ask

    def ask(prompt: str) -> str:
        if "Type 'yes'" in prompt:
            events.append("question")
        return original(prompt)

    menu.c.ask = ask  # type: ignore[method-assign]


def _run(tmp_path: Path, answers: list[str], **kw: Any) -> tuple[list[str], Any, Menu]:
    events: list[str] = []
    runner = kw.pop("runner", None) or HybridRunner()
    menu, console, _, _ = _menu(tmp_path, answers, runner)
    _wire(menu, runner, events, **kw)
    menu.run()
    return events, console, menu


def test_before_opens_after_convert_and_before_the_question_then_after_create(
    tmp_path: Path,
) -> None:
    events, console, _ = _run(tmp_path, ["3", "yes", "0"])
    opens = [e for e in events if e.startswith("open")]
    assert opens == ["open:report.html", "open:report-created.html"]
    assert events.index("open:report.html") < events.index("question")
    assert events.index("question") < events.index("create")
    assert events.index("create") < events.index("open:report-created.html")
    assert console.text.count("HTML report: file://") == 2


def test_decline_opens_only_the_plan(tmp_path: Path) -> None:
    events, console, _ = _run(tmp_path, ["3", "no", "0"])
    assert [e for e in events if e.startswith("open")] == ["open:report.html"]
    assert "Nothing was created." in console.text
    assert "report-created.html" not in console.text


def test_failed_create_does_not_open_the_after_report(tmp_path: Path) -> None:
    events, _, _ = _run(tmp_path, ["3", "yes", "0", "0"], runner=HybridRunner(fail={"apply"}))
    assert [e for e in events if e.startswith("open")] == ["open:report.html"]


def test_no_terminal_means_no_opening_but_the_path_is_printed(tmp_path: Path) -> None:
    events, console, _ = _run(tmp_path, ["3", "yes", "0"], terminal=False)
    assert not [e for e in events if e.startswith("open")]
    assert console.text.count("HTML report: file://") == 2


def test_env_and_flag_turn_opening_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORTER_NO_BROWSER", "1")
    (tmp_path / "a").mkdir()
    events, console, _ = _run(tmp_path / "a", ["3", "yes", "0"])
    assert not [e for e in events if e.startswith("open")]
    assert "HTML report: file://" in console.text
    monkeypatch.delenv("IMPORTER_NO_BROWSER")
    (tmp_path / "b").mkdir()
    runner = HybridRunner()
    menu, _, _, _ = _menu(tmp_path / "b", ["3", "no", "0"], runner)
    seen: list[str] = []
    _wire(menu, runner, seen)
    menu.open_reports = False
    menu.run()
    assert not [e for e in seen if e.startswith("open")]


def test_an_opener_that_fails_does_not_stop_the_flow(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, store, _ = _menu(tmp_path, ["3", "yes", "0"], runner)
    menu.terminal = lambda: True

    def broken(url: str) -> None:
        raise RuntimeError("no browser")

    menu.opener = broken
    assert menu.run() == 0
    assert "could not be opened" in console.text and "Done." in console.text
    assert len(store.list()) == 1


def test_each_phase_opens_once_even_if_shown_again(tmp_path: Path) -> None:
    events, _, menu = _run(tmp_path, ["3", "yes", "0"])
    lay_folder = next(Path(tmp_path).rglob("report.html")).parent
    from importer.core.menu_flow import Layout

    menu._show_report(Layout(lay_folder), "before", lay_folder / "report.html")
    assert [e for e in events if e.startswith("open")].count("open:report.html") == 1


def test_never_opens_a_file_outside_the_run_folder(tmp_path: Path) -> None:
    events, _, menu = _run(tmp_path, ["3", "no", "0"])
    from importer.core.menu_flow import Layout

    elsewhere = tmp_path / "elsewhere.html"
    elsewhere.write_text("x")
    events.clear()
    menu._show_report(Layout(tmp_path / "work"), "other", elsewhere)
    assert events == []


def _reports(tmp_path: Path) -> tuple[str, str]:
    folder = next(Path(tmp_path).rglob("report.html")).parent
    return (folder / "report.html").read_text(), (folder / "report-created.html").read_text()


def test_before_report_is_a_preview_with_all_kinds_reasons_links_and_logos(
    tmp_path: Path,
) -> None:
    _run(tmp_path, ["3", "yes", "0"])
    before, _ = _reports(tmp_path)
    assert "nothing has changed in New Relic" in before and "forecast" in before
    assert "Nothing is created until you answer" in before
    assert "alert rules, notifications and dashboards" in before
    for word in ("Alert rules", "Dashboards", "Notification subscriptions", "Grafana to New Relic"):
        assert word in before
    assert "<svg" in before
    assert 'href="remediation/remediation-plan.md"' in before
    assert 'href="datapath/datapath-plan.md"' in before
    assert "What was not migrated" in before


def test_after_report_states_what_was_created_without_the_preview_banner(tmp_path: Path) -> None:
    _run(tmp_path, ["3", "yes", "0"])
    _, after = _reports(tmp_path)
    assert "Preview" not in after and "nothing has changed" not in after
    assert "forecast" not in after
    assert "Objects were created" in after and "account <b>7</b>" in after
    assert re.search(r"Run id: <code>[^<]+</code>", after)
    assert "1 condition" in after and "1 dashboard" in after and "1 workflow" in after
    assert "<b>3</b> of them are disabled or private" in after
    assert "Roll back a run" in after
