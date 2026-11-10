"""Menu item 9: ask for the source Prometheus and run verify-parity (fake runner)."""

from pathlib import Path

import pytest

from test_menu import FakeRunner, make


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    monkeypatch.setenv("IMPORTER_NO_BROWSER", "1")
    monkeypatch.setenv("GRAFANA_URL", "https://stack.grafana.net")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", "t")


def test_menu_lists_the_parity_choice(tmp_path: Path) -> None:
    menu, console, _, _, _ = make(tmp_path, ["0"])
    menu.run()
    assert "9" in console.text and "Check my migration (parity)" in console.text


def test_without_a_conversion_it_says_what_to_do_first(tmp_path: Path) -> None:
    menu, console, run, _, _ = make(tmp_path, ["9", "0"])
    menu.run()
    assert "Pick 3" in console.text and run.calls == []


def test_it_runs_verify_parity_on_the_newest_run(tmp_path: Path) -> None:
    for name in ("grafana-20260101-000000", "grafana-20260202-000000"):
        (tmp_path / "work" / name / "converted").mkdir(parents=True)
    menu, console, run, _, _ = make(tmp_path, ["9", "http://127.0.0.1:9090", "0"])
    menu.run()
    (call,) = run.calls
    assert call[0] == "verify-parity"
    assert "grafana-20260202-000000" in call[call.index("--in") + 1]
    assert call[call.index("--source-prom") + 1] == "http://127.0.0.1:9090"
    assert call[call.index("--nr-account-id") + 1] == "7"
    assert "Parity report: file://" in console.text and "--parity" in console.text


def test_enter_goes_back_and_failure_is_reported(tmp_path: Path) -> None:
    (tmp_path / "work" / "grafana-20260101-000000" / "converted").mkdir(parents=True)
    menu, console, run, _, _ = make(
        tmp_path, ["9", "", "9", "prom", "0"], FakeRunner({"verify-parity": 1})
    )
    menu.run()
    assert [c[0] for c in run.calls] == ["verify-parity"]
    assert "did not finish" in console.text
