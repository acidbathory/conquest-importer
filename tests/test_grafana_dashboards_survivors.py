"""Tests added for mutants that the first test set did not catch."""

from pathlib import Path
from typing import Any

from importer.sources.grafana.dashboards import extract as dx
from importer.sources.grafana.dashboards.load import load_data
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import MAX_MANUAL_ROWS, build_report


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    result = dx.DashboardExtract()
    target = tmp_path / "x.json"
    assert dx._write(target, {"a": 1}, result)
    assert not dx._write(target, {"a": 2}, result)
    assert '"a": 1' in target.read_text()


def test_reuse_is_followed_one_level_only() -> None:
    reuse = {"uid": "-- Dashboard --"}

    def reusing(panel_id: int, source: int) -> dict[str, Any]:
        return {
            "id": panel_id,
            "type": "stat",
            "datasource": reuse,
            "targets": [{"refId": "A", "datasource": reuse, "panelId": source}],
        }

    body = {
        "uid": "u",
        "title": "t",
        "panels": [
            {"id": 1, "type": "stat", "targets": [{"refId": "A", "expr": "up"}]},
            reusing(2, 1),
            reusing(3, 2),
        ],
    }
    panels = build_plan(load_data(body, "x")).dashboards[0].panels
    assert [p.tier for p in panels] == [1, 1, None]


def test_a_variable_used_but_not_defined_is_listed() -> None:
    body = {
        "uid": "u",
        "title": "t",
        "panels": [
            {"id": 1, "type": "stat", "targets": [{"refId": "A", "expr": 'up{a=~"$ghost"}'}]}
        ],
    }
    d = build_plan(load_data(body, "x")).dashboards[0]
    assert d.variables_undefined == ["ghost"]


def test_the_report_lists_a_bounded_number_of_manual_panels_per_dashboard() -> None:
    panels = [
        {"id": i, "type": "alertlist", "title": f"manual-{i:03d}"}
        for i in range(MAX_MANUAL_ROWS + 5)
    ]
    report = build_report(build_plan(load_data({"uid": "u", "title": "t", "panels": panels}, "x")))
    assert report.count("| manual-") == MAX_MANUAL_ROWS
    assert "| ... 5 more in plan.json |" in report
