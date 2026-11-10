"""plan-remediation: reads converted output and writes the remediation plan."""

import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.remediation_plan import Plan, build_plan, read_items

FIX = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _run(*args: str) -> None:
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("converted")
    _run("convert", "--source", "grafana", "--in", str(FIX / "grafana"), "--out", str(root / "a"))
    _run(
        "convert-grafana-dashboards",
        "--in",
        str(FIX / "grafana-dashboard-convert"),
        "--out",
        str(root / "d"),
    )
    notify = FIX / "grafana-notify-convert"
    _run(
        "convert-grafana-notifications",
        "--in",
        str(notify),
        "--alerts",
        str(root / "a"),
        "--rules",
        str(notify / "rules.json"),
        "--out",
        str(root / "n"),
    )
    return root


@pytest.fixture(scope="module")
def plan(converted: Path) -> Plan:
    return build_plan(read_items(converted))


def test_reads_alerts_dashboards_and_notifications(plan: Plan) -> None:
    kinds = {i.kind for i in plan.items}
    assert {"alert rule", "dashboard panel", "contact point", "mute timing"} <= kinds


def test_every_item_has_a_remedy_and_most_have_a_playbook(plan: Plan) -> None:
    assert plan.items
    assert all(i.title for i in plan.items), [i.reasons for i in plan.items if not i.title]
    with_playbook = sum(i.playbook is not None for i in plan.items)
    assert with_playbook / len(plan.items) >= 0.95


def test_groups_are_counted_and_sorted_by_items(plan: Plan) -> None:
    counts = [g.count for g in plan.groups]
    assert counts == sorted(counts, reverse=True)
    assert sum(g.count for g in plan.groups) == len(plan.items)


def test_top_five_is_a_greedy_cover_that_unlocks_items(plan: Plan) -> None:
    top = plan.top_fixes(5)
    assert 1 <= len(top) <= 5
    unlocked = [t.unlocks for t in top]
    assert all(u > 0 for u in unlocked)
    assert unlocked == sorted(unlocked, reverse=True)


def test_command_writes_markdown_and_csv(converted: Path, tmp_path: Path) -> None:
    out = tmp_path / "plan"
    _run("plan-remediation", "--in", str(converted), "--out", str(out))
    text = (out / "remediation-plan.md").read_text()
    assert "Top 5 fixes" in text and "- [ ]" in text
    rows = list(csv.DictReader((out / "remediation-plan.csv").open()))
    assert rows and {"reason", "item", "effort", "steps"} <= set(rows[0])
    assert len(rows) == len(read_items(converted))


def test_csv_cells_cannot_start_a_spreadsheet_formula(tmp_path: Path) -> None:
    folder = tmp_path / "d" / "dashboard-ir"
    folder.mkdir(parents=True)
    widget = {
        "kind": "unsupported",
        "source_type": "stat",
        "title": "=HYPERLINK(\"http://x\")",
        "layout": {"column": 1, "row": 1, "width": 3, "height": 3},
        "confidence": "manual",
        "reasons": ["dashboard panel has transformations, which run in the browser and cannot be "
                    "reproduced in a query", "query: up"],
    }
    dash = {"source_system": "grafana", "source_key": "k", "name": "+evil", "widgets": [widget]}
    (folder / "1.json").write_text(json.dumps(dash))
    out = tmp_path / "plan"
    _run("plan-remediation", "--in", str(tmp_path / "d"), "--out", str(out))
    for row in csv.DictReader((out / "remediation-plan.csv").open()):
        for value in row.values():
            assert not value.startswith(("=", "+", "-", "@")), value


def test_refuses_an_output_folder_inside_the_input(converted: Path) -> None:
    result = runner.invoke(
        app, ["plan-remediation", "--in", str(converted), "--out", str(converted / "x")]
    )
    assert result.exit_code == 1


def test_empty_input_is_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["plan-remediation", "--in", str(tmp_path)])
    assert result.exit_code == 1
