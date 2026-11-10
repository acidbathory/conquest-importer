"""The triage pipeline and command, run over the converter's own fixtures."""

import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.triage.pipeline import run_triage
from importer.sources.grafana.triage.score import Bucket
from importer.sources.grafana.triage.usage import Usage

MINE = Path(__file__).parent / "fixtures" / "grafana-dashboard-convert"
runner = CliRunner()
TODAY = date(2026, 11, 10)


def test_every_dashboard_gets_one_row_and_numbers_come_from_the_converter() -> None:
    result = run_triage(MINE, {}, today=TODAY)
    assert len(result.rows) == 6
    total = sum(r.widgets for r in result.rows)
    done = sum(r.converted for r in result.rows)
    assert result.all_widgets == total and result.all_converted == done
    assert 0 < done <= total
    assert {r.verdict.bucket for r in result.rows} <= set(Bucket)


def test_usage_moves_dashboards_between_buckets() -> None:
    base = run_triage(MINE, {}, today=TODAY)
    uid = base.rows[0].signals.uid
    quiet = {r.signals.uid: Usage(0, None) for r in base.rows}
    quiet[uid] = Usage(80, date(2026, 11, 9))
    result = run_triage(MINE, quiet, today=TODAY)
    now = [r for r in result.rows if r.verdict.bucket is Bucket.NOW]
    assert [r.signals.uid for r in now] == [uid]
    assert result.now_widgets == now[0].widgets
    assert "Of 6 dashboards, 1 is worth migrating now" in result.headline
    assert result.unmatched_usage == 0


def test_usage_rows_for_unknown_dashboards_are_counted() -> None:
    result = run_triage(MINE, {"nope": Usage(5, None)}, today=TODAY)
    assert result.unmatched_usage == 1


def test_command_writes_report_and_json(tmp_path: Path) -> None:
    usage = tmp_path / "u.json"
    usage.write_text(json.dumps([{"uid": "x", "views_30d": 1}]))
    out = tmp_path / "out"
    r = runner.invoke(
        app,
        ["triage-grafana-dashboards", "--in", str(MINE), "--out", str(out), "--usage", str(usage)],
    )
    assert r.exit_code == 0, r.output
    md = (out / "triage-report.md").read_text()
    assert md.startswith("# Grafana dashboard triage")
    assert "| Dashboard |" in md and "Of 6 dashboards" in md
    data = json.loads((out / "triage-report.json").read_text())
    assert len(data["dashboards"]) == 6 and "headline" in data


def test_command_refuses_output_inside_input_and_missing_input(tmp_path: Path) -> None:
    inside = runner.invoke(
        app, ["triage-grafana-dashboards", "--in", str(MINE), "--out", str(MINE / "o")]
    )
    assert inside.exit_code == 1 and "inside" in inside.output
    empty = runner.invoke(app, ["triage-grafana-dashboards", "--in", str(tmp_path)])
    assert empty.exit_code == 1


def test_markdown_cells_escape_hostile_titles(tmp_path: Path) -> None:
    src = tmp_path / "in"
    (src / "dashboards").mkdir(parents=True)
    d = {"dashboard": {"uid": "h", "title": "x | `y` <b>[z](http://e)", "panels": []}, "meta": {}}
    (src / "dashboards" / "dashboard-1.json").write_text(json.dumps(d))
    out = tmp_path / "o"
    r = runner.invoke(app, ["triage-grafana-dashboards", "--in", str(src), "--out", str(out)])
    assert r.exit_code == 0, r.output
    row = next(x for x in (out / "triage-report.md").read_text().splitlines() if "| h |" in x)
    assert "`" not in row and "<b>" not in row and row.count("|") == 10
