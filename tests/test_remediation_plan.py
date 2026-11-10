"""plan-remediation: reads converted output and writes the remediation plan."""

import csv
import io
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
    assert sum(g.count for g in plan.groups) == sum(i.count for i in plan.items)


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
    rows = list(csv.DictReader(io.StringIO((out / "remediation-plan.csv").read_text())))
    assert rows and {"reason", "item", "effort", "steps"} <= set(rows[0])
    assert len(rows) == len(read_items(converted))


def test_csv_cells_cannot_start_a_spreadsheet_formula(tmp_path: Path) -> None:
    folder = tmp_path / "d" / "dashboard-ir"
    folder.mkdir(parents=True)
    widget = {
        "kind": "unsupported",
        "source_type": "stat",
        "title": '=HYPERLINK("http://x")',
        "layout": {"column": 1, "row": 1, "width": 3, "height": 3},
        "confidence": "manual",
        "reasons": [
            "dashboard panel has transformations, which run in the browser and cannot be "
            "reproduced in a query",
            "query: up",
        ],
    }
    dash = {
        "source_system": "grafana",
        "source_key": "k",
        "name": "+evil",
        "confidence": "manual",
        "widgets": [widget],
    }
    (folder / "1.json").write_text(json.dumps(dash))
    out = tmp_path / "plan"
    _run("plan-remediation", "--in", str(tmp_path / "d"), "--out", str(out))
    for row in csv.DictReader(io.StringIO((out / "remediation-plan.csv").read_text())):
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


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('sum(kube_pod_info{cluster=~"$cluster"})', ("kube_pod_info", (), "cluster")),
        (
            "sum by (instance, device) (rate(node_disk_io_time_seconds_total[5m]))",
            ("node_disk_io_time_seconds_total", ("instance", "device"), ""),
        ),
        ("label_values(__name__)", ("", (), "")),
        ('x{a="b"} * on(job) group_left(z) y', ("x", (), "")),
    ],
)
def test_names_are_read_from_promql(query: str, expected: tuple[str, tuple[str, ...], str]) -> None:
    from importer.core.remediation_plan import promql_names

    assert promql_names(query) == expected


def test_a_template_gets_the_items_own_metric(plan: Plan) -> None:
    from importer.core.remediation_plan import render_csv

    rows = [
        row
        for row in csv.DictReader(io.StringIO(render_csv(plan)))
        if row["reason"] == "Dashboard sum or count across the series of a gauge"
    ]
    assert rows
    assert all("<metric>" not in row["template"] for row in rows)


def test_plan_prints_recommended_method_first_then_alternatives(plan: Plan) -> None:
    from importer.core.remediation_plan import render_markdown

    text = render_markdown(plan)
    assert "Cookbook methods (ranked)" in text
    assert "What to ask the customer" in text
    assert "recommended method:" in text
    block = text.split("Cookbook methods (ranked)")[1]
    assert block.index("1. ") < block.index("2. ")


def test_csv_keeps_old_columns_and_appends_the_recommended_method(plan: Plan) -> None:
    from importer.core.remediation_plan import render_csv

    header = next(csv.reader(io.StringIO(render_csv(plan))))
    assert header[:12] == [
        "source", "kind", "container", "item", "count", "reason", "also_blocked_by",
        "effort", "effort_minutes", "steps", "template", "done",
    ]  # fmt: skip
    assert header[12:] == ["cookbook_recommended", "cookbook_alternatives"]
    rows = list(csv.DictReader(io.StringIO(render_csv(plan))))
    assert any(r["cookbook_recommended"] for r in rows)


def test_items_without_a_cookbook_entry_print_as_before(plan: Plan) -> None:
    from importer.core.remediation_plan import render_markdown

    text = render_markdown(plan)
    assert "Template (edit the parts in angle brackets)" in text or "Steps:" in text


def test_top_fixes_survives_a_reason_that_is_never_an_items_first_remedy() -> None:
    """A second remedy title has no group of its own; the scoreboard used to crash on it."""
    from importer.core.remediation import Remedy
    from importer.core.remediation_plan import Group, Item, Plan

    only_a = Item("grafana", "dashboard panel", "d", "p1", ("a",))
    only_a.remedies = [Remedy("Problem A", ())]
    a_and_b = Item("grafana", "dashboard panel", "d", "p2", ("a", "b"))
    a_and_b.remedies = [Remedy("Problem A", ()), Remedy("Problem B", ())]
    plan = Plan([only_a, a_and_b], [Group("Problem A", None, [only_a, a_and_b], 0)], [])
    top = plan.top_fixes(5)
    assert [t.title for t in top] == ["Problem A", "Problem B"]
    assert top[1].unlocks == 1
