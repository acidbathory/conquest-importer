"""Golden-file tests over the Datadog fixtures.

tests/golden/expected.json records, per fixture, the confidence label and the NRQL
produced. If a change to the translator alters it, the test fails: review the
difference, and only then regenerate with  UPDATE_GOLDEN=1 uv run pytest tests/test_fixtures.py
"""

import json
import os
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from importer.cli import app
from importer.emit.common import CannotEmit, nrql_for
from importer.models.ir import Confidence
from importer.models.source import DatadogMonitor
from importer.translate.monitors import load_metric_map, translate_monitor

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures" / "datadog"
GOLDEN = ROOT / "golden" / "expected.json"


def current_results() -> dict[str, dict[str, Any]]:
    metric_map = load_metric_map()
    results: dict[str, dict[str, Any]] = {}
    for path in sorted(FIXTURES.glob("*.json")):
        ir = translate_monitor(DatadogMonitor.model_validate_json(path.read_text()), metric_map)
        try:
            nrql: str | None = nrql_for(ir)
        except CannotEmit as exc:
            nrql = (
                None
                if ir.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE)
                else str(exc)
            )
        results[path.stem] = {"confidence": ir.confidence.value, "nrql": nrql}
    return results


def test_fixtures_match_golden() -> None:
    results = current_results()
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(results, indent=2) + "\n")
    assert results == json.loads(GOLDEN.read_text())


def test_fixture_set_covers_every_confidence_level() -> None:
    labels = {r["confidence"] for r in current_results().values()}
    assert labels == {"exact", "approximate", "manual", "unsupported"}


def test_full_pipeline_on_fixtures(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["convert", "--in", str(FIXTURES), "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    terraform = (tmp_path / "main.tf").read_text()
    report = (tmp_path / "report.md").read_text()
    # hostile text is escaped, never interpreted
    assert "$${evil}" in terraform
    assert "\n}\nresource" not in terraform.split("dd_9000021")[1].split("resource ")[0]
    # the fixture whose window is too long is reported as not emitted
    assert "not emitted" in report
    assert "Monitors read: **30**" in report
    assert not (tmp_path / "nerdgraph" / "9000027.json").exists()
    assert (tmp_path / "nerdgraph" / "9000009.json").exists()
    # one Terraform resource per emitted NerdGraph payload
    emitted = len(list((tmp_path / "nerdgraph").glob("*.json")))
    assert terraform.count('resource "newrelic_nrql_alert_condition"') == emitted
