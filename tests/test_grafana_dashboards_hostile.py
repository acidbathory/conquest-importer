"""Hostile and malformed dashboards through the loader, the planner, the report and the command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.dashboards import models as m
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import build_report, plan_json

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-dashboards"
HOSTILE = [*sorted((FIXTURES / "hostile").glob("*.json")), FIXTURES / "hostile.json"]
NEVER_IN_OUTPUT = ("\x1b", "\u202e", "\u202c", "\x00", "<script", "<img", "<iframe", "`", "](java")


def test_the_hostile_folder_is_not_empty() -> None:
    assert len(HOSTILE) == 5


@pytest.mark.parametrize("path", HOSTILE, ids=lambda p: p.name)
def test_a_hostile_file_loads_plans_and_reports_without_error(path: Path) -> None:
    estate = load_estate(path)
    plan = build_plan(estate)
    report, data = build_report(plan), plan_json(plan)
    assert plan.totals.dashboards + plan.totals.unreadable >= 1
    for bad in NEVER_IN_OUTPUT:
        assert bad not in report, (path.name, repr(bad))
    assert data.isascii() and json.loads(data)["totals"]["dashboards"] == len(estate.dashboards)
    headings = [x for x in report.splitlines() if x.startswith("# ")]
    assert headings == ["# Grafana dashboards: coverage plan"]  # no title became a heading
    for dashboard in plan.dashboards:
        for panel in dashboard.panels:
            assert len(panel.title) <= m.MAX_TEXT
            assert all(reason.startswith("dashboard ") for reason in panel.reasons)


@pytest.mark.parametrize("path", HOSTILE, ids=lambda p: p.name)
def test_table_rows_keep_their_columns_whatever_the_titles_hold(path: Path) -> None:
    lines = build_report(build_plan(load_estate(path))).splitlines()
    separators = [i for i, line in enumerate(lines) if line.startswith("|---")]
    assert separators
    for start in separators:
        columns = lines[start].count("|")
        assert lines[start - 1].count("|") == columns, lines[start - 1]  # the header
        for line in lines[start + 1 :]:
            if not line.startswith("|"):
                break
            assert line.count("|") == columns, line


def test_wrappers_and_lists_give_two_dashboards_and_the_rest_are_unreadable() -> None:
    estate = load_estate(FIXTURES / "hostile" / "wrappers-and-lists.json")
    # the envelope inside an envelope and the last entry; an `items` object inside a list is not
    # expanded, and a search-result-like entry has no panels
    assert len(estate.dashboards) == 2
    reasons = [u.reason for u in estate.unreadable]
    assert set(reasons) <= {m.U_NOT_OBJECT, m.U_UNKNOWN_SHAPE}
    assert reasons.count(m.U_NOT_OBJECT) == 4


def test_the_v2_injection_file_places_what_it_can_and_notes_the_rest() -> None:
    d = load_estate(FIXTURES / "hostile" / "injection-v2.json").dashboards[0]
    assert d.shape == "v2" and d.tabs == 3
    titles = [p.title for p in d.panels]
    assert "fine" in titles and "<script>alert(1)</script>" in titles
    assert m.W_UNPLACED.format(count=1) in d.warnings  # `unplaced` and `weird` is not a panel
    plan = build_plan(load_estate(FIXTURES / "hostile" / "injection-v2.json")).dashboards[0]
    by_title = {p.title: p for p in plan.panels}
    assert by_title["fine"].tier == 1
    assert [v.status for v in plan.variables if v.reason] != []


def test_the_wrong_types_file_keeps_the_good_panel_inside_the_open_row() -> None:
    d = load_estate(FIXTURES / "hostile" / "wrong-types.json").dashboards[0]
    assert d.schema_version is None and d.uid == "" and d.title == ""
    inside = [p for p in d.panels if p.title == "inside"]
    assert len(inside) == 1 and inside[0].section == "r"
    assert d.inputs == {"DS_X": "prometheus"}
    assert any(p.title == "from elements" and p.library_found for p in d.panels)
    assert all(0 <= p.x <= m.GRID_COLUMNS and 1 <= p.w <= m.GRID_COLUMNS for p in d.panels)


def test_the_terminal_codes_file_is_printed_safely_by_the_command(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan-grafana-dashboards",
            "--in",
            str(FIXTURES / "hostile"),
            "--out",
            str(tmp_path / "o"),
        ],
    )
    assert result.exit_code == 0, result.output
    for bad in NEVER_IN_OUTPUT:
        assert bad not in result.output
        assert bad not in (tmp_path / "o" / "report.md").read_text()
    assert (tmp_path / "o" / "plan.json").read_text().isascii()
