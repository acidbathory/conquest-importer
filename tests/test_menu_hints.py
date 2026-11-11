"""The guided Grafana flow asks for a Prometheus URL and feeds the hints to the converter."""

from pathlib import Path

import pytest

from menu_helpers import HybridRunner
from test_menu import make


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "NEW_RELIC_ACCOUNT_ID": "7",
        "NEW_RELIC_API_KEY": "k",
        "IMPORTER_NO_BROWSER": "1",
        "GRAFANA_URL": "https://stack.grafana.net",
        "GRAFANA_SA_TOKEN": "t",
    }.items():
        monkeypatch.setenv(name, value)


def _calls(runner: HybridRunner, name: str) -> list[list[str]]:
    return [c for c in runner.calls if c[0] == name]


def test_with_a_url_hints_run_before_and_feed_the_dashboard_conversion(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, *_ = make(tmp_path, ["3", "http://127.0.0.1:9090", "no", "0"], runner)  # type: ignore[arg-type]
    menu.run()
    (hint,) = _calls(runner, "grafana-prom-hints")
    assert hint[hint.index("--prometheus-url") + 1] == "http://127.0.0.1:9090"
    (convert,) = _calls(runner, "convert-grafana-dashboards")
    assert convert[convert.index("--hints") + 1] == hint[hint.index("--out") + 1]
    assert runner.names().index("grafana-prom-hints") < runner.names().index(
        "convert-grafana-dashboards"
    )
    assert "exact metric types" in console.text


def test_without_a_url_the_step_is_skipped_and_says_what_is_lost(tmp_path: Path) -> None:
    runner = HybridRunner()
    menu, console, *_ = make(tmp_path, ["3", "", "no", "0"], runner)  # type: ignore[arg-type]
    menu.run()
    assert not _calls(runner, "grafana-prom-hints")
    assert "--hints" not in _calls(runner, "convert-grafana-dashboards")[0]
    assert "sum() or count() a gauge" in console.text and "stay manual" in console.text


def test_a_failed_hints_read_does_not_stop_the_flow(tmp_path: Path) -> None:
    runner = HybridRunner(fail={"grafana-prom-hints"})
    menu, console, *_ = make(tmp_path, ["3", "http://127.0.0.1:9090", "no", "0"], runner)  # type: ignore[arg-type]
    menu.run()
    (convert,) = _calls(runner, "convert-grafana-dashboards")
    assert "--hints" not in convert
    assert "without hints" in console.text
