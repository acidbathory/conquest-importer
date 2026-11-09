"""Google uptime checks become an inventory with the steps to recreate each as a monitor."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.remediation import remedies_for
from importer.core.taxonomy import Category, classify
from importer.sources.gcp.uptime import load_checks, plan_check

FIXTURES = Path(__file__).parent / "fixtures" / "gcp-uptime"


def run(tmp_path: Path, source: Path = FIXTURES) -> Path:
    out = tmp_path / "uptime"
    done = CliRunner().invoke(
        app, ["convert-gcp-uptime", "--checks", str(source), "--out", str(out)]
    )
    assert done.exit_code == 0, done.output
    return out


def test_one_report_row_per_check_with_path_period_and_steps(tmp_path: Path) -> None:
    report = (run(tmp_path) / "report.md").read_text()
    rows = [line for line in report.splitlines() if line.startswith("| ")][1:]
    assert len(rows) == 5
    assert "/health" in rows[0] and "60s" in rows[0] and "ping" in rows[0]
    assert "Home" in report and "| Home |" not in report  # the pipe cannot split the row
    assert "TCP" in rows[2]


@pytest.mark.parametrize(
    ("period", "word"),
    [
        ("60s", "every minute"),
        ("300s", "5 minutes"),
        ("600s", "10 minutes"),
        ("900s", "15 minutes"),
        ("120s", "5 minutes"),  # no 2 minute frequency: rounded up
        ("", "5 minutes"),
    ],
)
def test_the_period_maps_to_the_nearest_run_frequency_not_below(period: str, word: str) -> None:
    plan = plan_check({"kind": "http", "period": period, "displayName": "x"})
    assert word in plan.frequency


@pytest.mark.parametrize(
    ("kind", "word"), [("http", "ping"), ("tcp", "TCP"), ("other", "group"), ("odd", "group")]
)
def test_every_kind_has_a_reason_a_category_and_a_rule(kind: str, word: str) -> None:
    plan = plan_check({"kind": kind, "period": "60s", "displayName": "x"})
    assert classify(plan.reason) is not Category.OTHER
    remedies = remedies_for([plan.reason])
    assert remedies and word in " ".join(remedies[0].steps)


def test_the_specific_inventory_rule_wins_over_the_uptime_alert_rule() -> None:
    plan = plan_check({"kind": "http", "period": "60s", "displayName": "x"})
    assert remedies_for([plan.reason])[0].title != "Uptime check alert"


def test_the_alert_rule_points_at_the_inventory_command() -> None:
    reason = "an uptime check alert depends on the check itself, which is not migrated"
    assert "convert-gcp-uptime" in " ".join(remedies_for([reason])[0].steps)


def test_the_host_is_never_written(tmp_path: Path) -> None:
    out = run(tmp_path)
    text = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert "http://" not in text and "https://" not in text and "demo-project" not in text


def test_the_plan_file_holds_one_entry_per_check(tmp_path: Path) -> None:
    data = json.loads((run(tmp_path) / "uptime-plan.json").read_text())
    assert len(data) == 5 and {d["kind"] for d in data} == {"http", "tcp", "other"}


def test_a_missing_file_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"uptime-checks\.json"):
        load_checks(tmp_path)
    done = CliRunner().invoke(
        app, ["convert-gcp-uptime", "--checks", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert done.exit_code == 1 and "Error" in done.output
