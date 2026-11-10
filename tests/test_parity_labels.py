"""`--parity` on convert-grafana-dashboards: tiles say 'verified on <date>'; default unchanged."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.parity.labels import load_labels
from importer.parity.model import ParityError
from importer.parity.report import nrql_digest

FIX = Path(__file__).parent / "fixtures" / "grafana-dashboards"
NRQL_CPU = (
    "SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric WHERE job IN ({{job}}) "
    "AND mode != 'idle' FACET instance LIMIT MAX TIMESERIES AUTO"
)


def convert(out: Path, *extra: str) -> str:
    args = ["convert-grafana-dashboards", "--in", str(FIX), "--out", str(out), *extra]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    return (out / "report.md").read_text()


def labels_file(path: Path, nrql: str = NRQL_CPU, date: str = "2026-11-10") -> Path:
    uid = next(
        d["source_key"]
        for f in (path.parent / "plain" / "dashboard-ir").glob("*.json")
        if (d := json.loads(f.read_text())) and "CPU busy by instance" in f.read_text()
    )
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "verified": [
                    {
                        "dashboard": uid,
                        "widget": "CPU busy by instance",
                        "nrql_sha256": nrql_digest(nrql),
                        "verified_on": date,
                    }
                ],
            }
        )
    )
    return path


def test_default_report_is_unchanged_and_parity_adds_the_date(tmp_path: Path) -> None:
    plain = convert(tmp_path / "plain")
    assert "verified on" not in plain and "| Parity |" not in plain
    again = convert(tmp_path / "again")
    assert plain == again
    labelled = convert(tmp_path / "lab", "--parity", str(labels_file(tmp_path / "labels.json")))
    assert "verified on 2026-11-10" in labelled and labelled.count("verified on") == 1
    # a changed query is not labelled
    stale = labels_file(tmp_path / "stale.json", nrql="SELECT 1 FROM Metric")
    assert "verified on" not in convert(tmp_path / "stale", "--parity", str(stale))


def test_bad_parity_files_are_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"version": 9, "verified": []}')
    with pytest.raises(ParityError):
        load_labels(bad)
    result = CliRunner().invoke(
        app,
        ["convert-grafana-dashboards", "--in", str(FIX), "--out", str(tmp_path / "o"),
         "--parity", str(bad)],
    )  # fmt: skip
    assert result.exit_code == 1 and "Error:" in result.output
