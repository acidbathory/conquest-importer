"""The plan-dynatrace-dashboards command, alone and after extract-dynatrace-inventory."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace.dashboards.plan_models import DashboardPlans
from importer.sources.dynatrace.inventory import extract as ix

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"
HOST = "https://abc12345.apps.dynatrace.com"
UUID = "00000000-0000-0000-0000-000000000001"


def plan(source: Path, out: Path) -> Any:
    return CliRunner().invoke(
        app, ["plan-dynatrace-dashboards", "--in", str(source), "--out", str(out)]
    )


def test_the_command_writes_a_report_and_a_plan_that_reads_back(tmp_path: Path) -> None:
    result = plan(FIXTURES, tmp_path / "out")
    assert result.exit_code == 0, result.output
    assert (
        "Planned 5 dashboards" in result.output and "10 exact, 22 draft, 32 manual" in result.output
    )
    plans = DashboardPlans.model_validate_json((tmp_path / "out" / "plan.json").read_text())
    assert (plans.totals.exact, plans.totals.draft) == (10, 22)
    assert len(plans.dashboards) == 5
    report = (tmp_path / "out" / "report.md").read_text()
    assert report.startswith("# Dynatrace dashboard plan") and "no New Relic data source" in report


def test_a_single_file_is_accepted(tmp_path: Path) -> None:
    result = plan(FIXTURES / "azure-app-service-plan.json", tmp_path / "out")
    assert result.exit_code == 0 and "Planned 1 dashboards: 19 data tiles" in result.output


def test_a_folder_that_already_has_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("1")
    result = plan(FIXTURES, tmp_path / "out")
    assert result.exit_code == 1 and "already has files" in result.output


def test_a_folder_without_dashboards_says_so(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    result = plan(tmp_path / "in", tmp_path / "out")
    assert result.exit_code == 1 and "no dashboard JSON found" in result.output


def test_a_hostile_folder_is_planned_not_crashed(tmp_path: Path) -> None:
    source = tmp_path / "in"
    source.mkdir()
    (source / "a.json").write_text("[" * 100_000)
    (source / "b.json").write_text(
        '{"tiles": {"1": {"type": "data", "query": "' + "(" * 5000 + '"}}}'
    )
    (source / "c.json").write_text(
        json.dumps({"tiles": {"1": 5, "2": {"type": "data", "query": "x"}}})
    )
    result = plan(source, tmp_path / "out")
    assert result.exit_code == 0, result.output
    text = (tmp_path / "out" / "report.md").read_text()
    assert "unreadable 1" in text


def serve(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/platform/document/v1/documents":
        return httpx.Response(
            200, json={"documents": [{"id": UUID, "name": "Board", "type": "dashboard"}]}
        )
    if path.endswith("/content"):
        board = {"version": 21, "tiles": {"1": {"type": "markdown", "content": "hello"}}}
        return httpx.Response(200, json=board)
    return httpx.Response(404, json={})


def test_the_documented_sequence_extract_then_plan_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "dt0s16.AAAA.SECRETSECRETSECRET")
    monkeypatch.setattr(
        "importer.commands.dynatrace_inventory.http_getter",
        lambda t: ix.http_getter(t, httpx.MockTransport(serve)),
    )
    runner = CliRunner()
    extracted = runner.invoke(app, ["extract-dynatrace-inventory", "--out", str(tmp_path / "inv")])
    assert extracted.exit_code == 0, extracted.output
    command = next(x for x in extracted.output.splitlines() if "plan-dynatrace-dashboards" in x)
    args = command.split()[1:]
    assert args[0] == "plan-dynatrace-dashboards"
    planned = runner.invoke(app, args)
    assert planned.exit_code == 0, planned.output
    assert "Planned 1 dashboards" in planned.output and "1 markdown tiles" in planned.output
