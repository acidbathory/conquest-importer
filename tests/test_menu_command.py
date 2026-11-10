"""The `menu`, `runs` and `rollback-run` commands: wiring and non-interactive behaviour."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.runs import RunStore


def test_menu_without_terminal_exits_with_message() -> None:
    result = CliRunner().invoke(app, ["menu"], input="1\n")
    assert result.exit_code == 1
    assert "terminal" in result.output.lower()


def test_rollback_run_without_terminal_exits_with_message() -> None:
    result = CliRunner().invoke(app, ["rollback-run"], input="")
    assert result.exit_code == 1
    assert "terminal" in result.output.lower()


def test_runs_lists_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORTER_RUNS_DIR", str(tmp_path / "runs"))
    record = RunStore().start(source="gcp", account_id=7, region="us", out_dir=tmp_path)
    result = CliRunner().invoke(app, ["runs"])
    assert result.exit_code == 0
    assert record.run_id in result.output and "account 7" in result.output


def test_runs_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORTER_RUNS_DIR", str(tmp_path / "none"))
    result = CliRunner().invoke(app, ["runs"])
    assert result.exit_code == 0 and "no recorded runs" in result.output.lower()


def test_menu_help_lists_in_cli() -> None:
    assert "menu" in CliRunner().invoke(app, ["--help"]).output
