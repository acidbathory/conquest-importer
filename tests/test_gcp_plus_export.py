"""An export from extract-gcp carries the definitions; convert reads them beside policies/."""

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from importer.cli import app
from importer.sources.gcp.extract import extract
from importer.sources.gcp.models import load_policies
from test_gcp_extract_plus import Fake

ESTATE = Path(__file__).parent / "fixtures" / "gcp-estate" / "policies" / "synthetic"
NAMES = ("logging-user-metric", "slo-burn-fast", "uptime-check-failed")


def export(tmp_path: Path) -> Path:
    out = tmp_path / "export"
    extract(Fake(), "example-project", out)
    # the Fake serves one policy; replace it with the estate's three conditions
    policies = [json.loads((ESTATE / f"{n}.json").read_text()) for n in NAMES]
    for n, p in enumerate(policies, 1):
        p["name"] = f"projects/example-project/alertPolicies/{n}"
        for k, c in enumerate(p["conditions"], 1):
            c["name"] = f"{p['name']}/conditions/{k}"
    (out / "policies" / "policies.json").write_text(json.dumps(policies))
    return out


def convert(src: Path, out: Path) -> str:
    result = CliRunner().invoke(
        app, ["convert", "--source", "gcp", "--in", str(src / "policies"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    return "".join(p.read_text() for p in sorted(out.rglob("*.json")))


def test_the_policies_load_with_the_definitions_found_beside_them(tmp_path: Path) -> None:
    policies = load_policies(export(tmp_path) / "policies")
    extras = policies[0].extras
    assert extras is not None and extras.log_metrics and extras.slos and extras.uptime
    assert "checkout_errors" in extras.log_metrics


def test_convert_emits_log_ratio_and_synthetics_conditions_from_an_export(tmp_path: Path) -> None:
    text = convert(export(tmp_path), tmp_path / "conv")
    assert "FROM Log" in text and "SyntheticCheck" in text and "request_count" in text
    assert "not emitted" not in text.lower()


def test_an_older_export_without_the_definitions_converts_as_before(tmp_path: Path) -> None:
    src = export(tmp_path)
    for folder in ("log-metrics", "slos"):
        shutil.rmtree(src / folder)
    checks = json.loads((src / "uptime-checks" / "uptime-checks.json").read_text())
    for c in checks:
        c.pop("details", None)
    (src / "uptime-checks" / "uptime-checks.json").write_text(json.dumps(checks))
    text = convert(src, tmp_path / "old")
    assert "log filter is part of the metric definition" in text
    assert "SLO objects are not read" in text or "not read from Google yet" in text
