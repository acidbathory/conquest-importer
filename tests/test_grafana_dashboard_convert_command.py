"""The command, the registry path and the name map file."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import (
    MAP_ENV,
    NameMapError,
    active_name_map,
    load_name_map,
)

MINE = Path(__file__).parent / "fixtures" / "grafana-dashboard-convert"
runner = CliRunner()


def test_the_command_writes_ir_nerdgraph_terraform_and_a_report(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["convert-grafana-dashboards", "--in", str(MINE), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "Read 6 dashboards, emitted 6:" in result.output
    assert r.D_DATA in result.output
    assert len(list((out / "dashboard-ir").glob("*.json"))) == 6
    assert len(list((out / "nerdgraph").glob("*.json"))) == 6
    assert (out / "dashboards.tf").read_text().count('resource "newrelic_one_dashboard"') == 6
    report = (out / "report.md").read_text()
    assert "## Grafana data prerequisites" in report
    assert "Data must reach New Relic by Prometheus remote write" in report
    assert "| node_load1 |" in report and "Name map entries loaded: **0**" in report


def test_the_registry_path_gives_the_same_dashboards(tmp_path: Path) -> None:
    out = tmp_path / "viaregistry"
    args = ["convert-dashboards", "--source", "grafana", "--in", str(MINE), "--out", str(out)]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert len(list((out / "nerdgraph").glob("*.json"))) == 6
    assert r.D_DATA in (out / "report.md").read_text()


def test_a_name_map_changes_the_queries_and_is_counted(tmp_path: Path) -> None:
    names = tmp_path / "names.json"
    names.write_text(
        json.dumps({"metrics": {"node_load1": {"event": "Metric", "attribute": "node.load.1m"}}})
    )
    out = tmp_path / "out"
    args = ["convert-grafana-dashboards", "--in", str(MINE / "node-exporter-rows.json")]
    result = runner.invoke(app, [*args, "--out", str(out), "--name-map", str(names)])
    assert result.exit_code == 0, result.output
    text = "".join(p.read_text() for p in (out / "nerdgraph").glob("*.json"))
    assert "average(`node.load.1m`)" in text and "average(node_load1)" not in text
    assert "Name map entries loaded: **1**" in (out / "report.md").read_text()


def test_the_environment_variable_names_a_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = tmp_path / "n.json"
    names.write_text(json.dumps({"metrics": {"up": {"event": "Sample", "attribute": "up"}}}))
    monkeypatch.setenv(MAP_ENV, str(names))
    assert list(active_name_map().metrics) == ["up"]
    monkeypatch.delenv(MAP_ENV)
    assert active_name_map().metrics == {}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "is not valid JSON"),
        ('{"metrics": {"up": {"event": "Metric"}}}', "metrics.up.attribute"),
        ('{"metrics": {"up": {"event": "Metric", "attribute": "a b"}}}', "invalid NRQL identifier"),
        ('{"metrics": {"up": {"event": "M", "attribute": "a", "extra": 1}}}', "metrics.up.extra"),
        ('{"metrics": {"not a name": {"event": "M", "attribute": "a"}}}', "Prometheus metric name"),
        ('{"extra": 1}', "extra"),
        ("[]", "valid"),
    ],
)
def test_a_bad_name_map_fails_at_load_with_a_message(
    tmp_path: Path, content: str, message: str
) -> None:
    file = tmp_path / "bad.json"
    file.write_text(content)
    with pytest.raises(NameMapError) as caught:
        load_name_map(file)
    assert message in str(caught.value) and "bad.json" in str(caught.value)


def test_a_bad_name_map_stops_the_command_and_writes_nothing(tmp_path: Path) -> None:
    file = tmp_path / "bad.json"
    file.write_text('{"metrics": {"up": {"event": "Metric"}}}')
    out = tmp_path / "out"
    args = ["convert-grafana-dashboards", "--in", str(MINE), "--out", str(out)]
    result = runner.invoke(app, [*args, "--name-map", str(file)])
    assert result.exit_code == 1 and "bad.json" in result.output
    assert not out.exists()


def test_a_map_comment_is_allowed_and_a_huge_or_missing_file_is_refused(tmp_path: Path) -> None:
    ok = tmp_path / "ok.json"
    ok.write_text('{"_comment": "for the sandbox", "metrics": {}}')
    assert load_name_map(ok).metrics == {}
    with pytest.raises(NameMapError):
        load_name_map(tmp_path / "missing.json")


def test_out_inside_in_is_refused_and_so_is_an_empty_folder(tmp_path: Path) -> None:
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "d.json").write_text("{}")
    args = ["convert-grafana-dashboards", "--in", str(folder)]
    inside = runner.invoke(app, [*args, "--out", str(folder / "out")])
    assert inside.exit_code == 1 and "inside --in" in inside.output
    empty = tmp_path / "empty"
    empty.mkdir()
    none = runner.invoke(
        app, ["convert-grafana-dashboards", "--in", str(empty), "--out", str(tmp_path / "o")]
    )
    assert none.exit_code == 1 and "no Grafana dashboards" in none.output


def test_a_folder_with_only_unreadable_files_still_writes_a_report(tmp_path: Path) -> None:
    folder = tmp_path / "export"
    folder.mkdir()
    (folder / "broken.json").write_text("{not json")
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["convert-grafana-dashboards", "--in", str(folder), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "the file could not be read" in (out / "report.md").read_text()


def test_the_metric_table_is_bounded(tmp_path: Path) -> None:
    folder = tmp_path / "many"
    folder.mkdir()
    panels = [
        {
            "id": i,
            "type": "timeseries",
            "title": f"p{i}",
            "gridPos": {"x": 0, "y": i, "w": 24, "h": 1},
            "datasource": {"type": "prometheus", "uid": "p"},
            "targets": [{"refId": "A", "expr": f"sum(metric_{i})"}],
        }
        for i in range(120)
    ]
    (folder / "d.json").write_text(json.dumps({"uid": "d", "title": "d", "panels": panels}))
    out = tmp_path / "out"
    result = runner.invoke(
        app, ["convert-grafana-dashboards", "--in", str(folder), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    report = (out / "report.md").read_text()
    assert "Metric names used by converted queries: **120**" in report
    assert "80 more names are not listed." in report
    assert report.count("| metric_") == 40
