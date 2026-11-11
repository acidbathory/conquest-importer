"""`convert-dashboards --source azure` and `plan-azure-dashboards` on the official fixtures."""

import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from importer.cli import app
from importer.sources import DASHBOARD_REGISTRY

FIX = Path(__file__).parent / "fixtures" / "azure"


def test_azure_is_a_registered_dashboard_source() -> None:
    assert "azure" in DASHBOARD_REGISTRY


def test_convert_dashboards_for_azure_writes_the_usual_files(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        app,
        [
            "convert-dashboards",
            "--source",
            "azure",
            "--in",
            str(FIX / "quickstart_templates"),
            "--out",
            str(tmp_path),
        ],
    )
    assert res.exit_code == 0, res.output
    report = (tmp_path / "report.md").read_text()
    assert report.startswith("# Dashboard conversion report: Azure Monitor to New Relic")
    assert list((tmp_path / "dashboard-ir").glob("*.json"))
    tf = (tmp_path / "dashboards.tf").read_text()
    assert "az_dashboard_" in tf
    graph = "".join(f.read_text() for f in (tmp_path / "nerdgraph").glob("*.json"))
    assert "**Not migrated:** VideoPart" in graph  # the placeholder is visible on the dashboard


def test_the_official_workbook_and_dashboard_examples_convert(tmp_path: Path) -> None:
    for folder in ("workbooks", "dashboards"):
        res = CliRunner().invoke(
            app,
            [
                "convert-dashboards",
                "--source",
                "azure",
                "--in",
                str(FIX / folder),
                "--out",
                str(tmp_path / folder),
            ],
        )
        assert res.exit_code == 0, res.output


def test_plan_azure_dashboards_counts_widgets_and_data_paths(
    tmp_path: Path, help_text: Callable[..., str]
) -> None:
    out = tmp_path / "plan"
    res = CliRunner().invoke(
        app, ["plan-azure-dashboards", "--in", str(FIX / "quickstart_templates"), "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert "converted" in res.output and "placeholder" in res.output
    plan = json.loads((out / "plan.json").read_text())
    totals = plan["totals"]
    assert totals["widgets"] == totals["converted"] + totals["placeholders"]
    assert (
        (out / "report.md").read_text().startswith("# Dashboard conversion report: Azure Monitor")
    )
    text = help_text("plan-azure-dashboards", "--help")
    assert "--in" in text and "Offline" in text


def test_plan_azure_dashboards_refuses_an_empty_folder(tmp_path: Path) -> None:
    (tmp_path / "in").mkdir()
    res = CliRunner().invoke(
        app, ["plan-azure-dashboards", "--in", str(tmp_path / "in"), "--out", str(tmp_path / "o")]
    )
    assert res.exit_code == 1
