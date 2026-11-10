"""Pairing converted queries with the originals they came from."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.parity.model import ParityError
from importer.parity.pairs import build_pairs, default_export

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("guided")
    runner = CliRunner()
    args = ["convert-grafana-dashboards", "--in", str(FIX / "grafana-dashboards"), "--out"]
    assert runner.invoke(app, [*args, str(root / "converted" / "dashboards")]).exit_code == 0
    alerts = ["convert", "--source", "grafana", "--in", str(FIX / "grafana"), "--out"]
    assert runner.invoke(app, [*alerts, str(root / "converted" / "alerts")]).exit_code == 0
    shutil.copytree(FIX / "grafana-dashboards", root / "export" / "dashboards")
    shutil.copytree(FIX / "grafana", root / "export" / "alerts")
    return root / "converted"


def test_dashboard_widgets_are_paired_with_their_promql(converted: Path) -> None:
    pairs = build_pairs(converted, None)
    tiles = {p.title: p for p in pairs if p.kind == "tile"}
    cpu = tiles["CPU busy by instance"]
    assert cpu.original.startswith("sum by (instance) (rate(node_cpu_seconds_total")
    assert cpu.timeseries and not cpu.skip and cpu.family == "prometheus"
    assert "stat" in tiles["Load 1m (average)"].skip  # single value, no series


def test_alert_rules_are_paired(converted: Path) -> None:
    found = {p.title: p for p in build_pairs(converted, None) if p.kind == "alert"}
    cpu = found["ckg-prom-cpu-gauge"]
    assert cpu.original.startswith("avg by (instance) (node_cpu_busy")
    assert not cpu.skip and cpu.timeseries
    assert found["ckg-loki-errors"].family == "loki" and not found["ckg-loki-errors"].skip


def test_a_different_export_is_reported_not_guessed(converted: Path, tmp_path: Path) -> None:
    other = tmp_path / "export"
    (other / "dashboards").mkdir(parents=True)
    (other / "dashboards" / "x.json").write_text(
        '{"uid": "other", "title": "Other", "panels": [], "schemaVersion": 39}'
    )
    shutil.copytree(FIX / "grafana", other / "alerts")
    pairs = [p for p in build_pairs(converted, other) if p.kind == "tile"]
    assert pairs and all("not found in the export" in p.skip for p in pairs)


def test_a_missing_export_is_explained(converted: Path, tmp_path: Path) -> None:
    lonely = tmp_path / "converted"
    lonely.mkdir()
    (lonely / "ir").mkdir()
    with pytest.raises(ParityError, match="--export"):
        build_pairs(lonely, None)
    assert default_export(converted) is not None


def test_a_folder_without_converted_output_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ParityError, match="dashboard-ir"):
        build_pairs(tmp_path, tmp_path)
