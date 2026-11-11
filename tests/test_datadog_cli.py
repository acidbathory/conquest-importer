"""D8: `convert --source datadog`, the report header, the menu doc and the measure script."""

import importlib.util
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.common import source_description
from importer.core.improve import FIXTURES, measure
from importer.sources import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "tests" / "fixtures" / "datadog" / "real" / "monitors"


def test_convert_writes_ir_payloads_terraform_and_a_branded_report(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        app, ["convert", "--source", "datadog", "--in", str(REAL), "--out", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    assert "Read 70 monitors, emitted 50" in res.output
    report = (tmp_path / "report.md").read_text()
    assert report.startswith("# Conversion report: Datadog to New Relic")
    assert len(list((tmp_path / "ir").glob("*.json"))) == 70
    assert len(list((tmp_path / "nerdgraph").glob("*.json"))) == 50
    assert 'resource "newrelic_nrql_alert_condition" "dd_' in (tmp_path / "main.tf").read_text()
    payload = json.loads(next((tmp_path / "nerdgraph").glob("*.json")).read_text())
    assert payload["variables"]["condition"]["enabled"] is False


def test_description_names_the_source() -> None:
    (monitor,) = REGISTRY["datadog"].convert(REAL / "celery__celery_workers_down.json")
    assert source_description(monitor).startswith("Imported from Datadog monitor datadog/")


def test_input_help_and_fixtures_are_registered() -> None:
    assert "no credentials" in REGISTRY["datadog"].input_help
    assert FIXTURES["datadog"] == ["tests/fixtures/datadog"]
    result = measure(ROOT)["sources"]["datadog"]
    assert result["total"] == 70 + 14 and result["converted"] >= 50


@pytest.mark.skipif(
    not (ROOT / "scripts" / "analysis" / "datadog_measure.py").exists(),
    reason="dev-only file is not published",
)
def test_the_measure_script_reproduces_the_baseline_numbers() -> None:
    spec = importlib.util.spec_from_file_location(
        "datadog_measure", ROOT / "scripts" / "analysis" / "datadog_measure.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.measure(REAL)
    assert (result["total"], result["converted"], result["manual"]) == (70, 50, 20)
    assert result["unguided"] == 0
    baseline = (ROOT / "docs" / "research" / "datadog-baseline.md").read_text()
    assert "Converted (approximate or better): 50. Manual: 20" in baseline


def test_the_old_paused_page_is_gone() -> None:
    assert not (ROOT / "docs" / "user" / "runbook-datadog-and-azure.md").exists()
    assert (ROOT / "docs" / "user" / "runbook-datadog.md").exists()
