"""Report injection through types and kinds, and commands that must never show a traceback."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace.dashboards import models as m
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report
from importer.sources.dynatrace.inventory import models as im
from importer.sources.dynatrace.inventory import synthetics as sy

TILE_KIND = "x|y<img src=x onerror=alert(1)>\n# H"
VARIABLE_TYPE = "bad|type\x1b[2J<script>"
MONITOR_TYPE = "HT|TP<b>"
RAW = ("x|y", "<img", "\x1b", "<script>", "bad|type", "HT|TP", "<b>")


def run(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def assert_clean(report: str) -> None:
    for text in RAW:
        assert text not in report, text
    assert not any(
        line.startswith("#") and line.lstrip("# ") == "H" for line in report.splitlines()
    )


def test_a_tile_kind_and_a_variable_type_cannot_break_the_report(tmp_path: Path) -> None:
    board = {
        "version": 21,
        "tiles": {"1": {"type": TILE_KIND, "title": "t"}},
        "variables": [{"key": "v", "type": VARIABLE_TYPE, "input": "x"}],
    }
    (doc,) = (m.parse_document("d", board),)
    plans = plan_documents([doc])
    assert_clean(render_report(plans))
    assert len(plans.dashboards[0].variables[0].type) <= 40
    assert len(plans.dashboards[0].tiles[0].kind) <= 40


def test_a_monitor_type_cannot_break_the_synthetics_report() -> None:
    monitor = im.parse_monitor("r", {"type": MONITOR_TYPE, "name": "n"})
    assert monitor is not None
    report = sy.render_report(sy.plan_synthetics([monitor], []))
    assert_clean(report)


def test_a_long_variable_type_is_cut_when_it_is_read() -> None:
    parsed = m.parse_variable(0, {"key": "k", "type": "q" * 5000})
    assert isinstance(parsed, m.Variable) and len(parsed.type) == 40


@pytest.mark.parametrize("query", ["|", "// TODO write query", "/* c */", ""])
def test_a_comment_only_tile_query_does_not_crash_the_command(query: str, tmp_path: Path) -> None:
    board = {
        "version": 21,
        "tiles": {"1": {"type": "data", "query": query, "visualization": "table"}},
        "variables": [
            {"key": "a", "type": "query", "input": 'data record(a="*") | append [ ]'},
            {"key": "b", "type": "query", "input": 'data record(a="*") | append [ // c ]'},
            {"key": "c", "type": "query", "input": 'data record(a="*") | append [|]'},
            {"key": "d", "type": "query", "input": query},
        ],
    }
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "b.json").write_text(json.dumps(board))
    result = run(
        "plan-dynatrace-dashboards", "--in", str(tmp_path / "in"), "--out", str(tmp_path / "o")
    )
    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "no query" in (tmp_path / "o" / "report.md").read_text()


@pytest.mark.parametrize(
    ("command", "patched"),
    [
        ("plan-dynatrace-dashboards", "load_documents"),
        ("plan-dynatrace-reinstrumentation", "load_entities"),
        ("plan-dynatrace-synthetics", "load_monitors"),
    ],
)
def test_an_unexpected_error_is_a_clean_exit_that_names_only_its_type(
    command: str, patched: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> object:
        raise KeyError("SECRET-INPUT-TEXT")

    monkeypatch.setattr(f"importer.commands.dynatrace_inventory.{patched}", boom)
    result = run(command, "--in", str(tmp_path), "--out", str(tmp_path / "o"))
    assert result.exit_code == 1 and "unexpected KeyError" in result.output
    assert "SECRET-INPUT-TEXT" not in result.output and "Traceback" not in result.output


def test_the_extract_command_also_hides_an_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object) -> object:
        raise RuntimeError("SECRET-TEXT")

    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://abc.apps.dynatrace.com")
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "dt0s16.AAAA.SECRETSECRET")
    monkeypatch.setattr("importer.commands.dynatrace_inventory.extract_inventory", boom)
    result = run("extract-dynatrace-inventory", "--out", str(tmp_path / "o"))
    assert result.exit_code == 1 and "unexpected RuntimeError" in result.output
    assert "SECRET" not in result.output
