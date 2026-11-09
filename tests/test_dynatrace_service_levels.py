"""A Dynatrace SLO becomes a service level plan that a person finishes. No data path is invented."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace.models import DtSlo, load_slos
from importer.sources.dynatrace.service_levels import (
    ServiceLevelPlan,
    render_report,
    service_level_plans,
    service_levels_terraform,
)

DT = Path(__file__).parent / "fixtures" / "dynatrace"
INDICATOR = (
    "timeseries { total = sum(dt.service.request.count), "
    "failed = sum(dt.service.request.failure_count) }, from:-7d "
    "| fieldsAdd sli = (total[] - failed[]) / total[] * 100"
)


def slo(
    criteria: list[dict[str, Any]] | None = None, indicator: str = INDICATOR, name: str = "s"
) -> DtSlo:
    return DtSlo.model_validate(
        {
            "id": "i",
            "name": name,
            "criteria": criteria
            if criteria is not None
            else [
                {"target": 99.0, "warning": 99.5, "timeframeFrom": "now-7d", "timeframeTo": "now"}
            ],
            "customSli": {"indicator": indicator},
        }
    )


def test_the_fixture_slo_becomes_a_plan_row() -> None:
    plans = service_level_plans(load_slos(DT))
    first = next(p for p in plans if p.name == "example-availability-slo")
    assert (first.target, first.warning, first.period_days) == (99.0, 99.5, 7)
    assert first.nr_period == "7 DAY" and first.needs_review
    assert first.valid_events_nrql == "TODO" and first.good_events_nrql == "TODO"
    assert first.source_metrics == ["dt.service.request.count", "dt.service.request.failure_count"]
    assert any("metrics" in n and "New Relic" in n for n in first.notes)
    assert any("warning" in n for n in first.notes)


@pytest.mark.parametrize(
    ("timeframe", "days", "nr_period"),
    [
        (("now-1d", "now"), 1, "1 DAY"),
        (("now-7d", "now"), 7, "7 DAY"),
        (("now-1w", "now"), 7, "7 DAY"),
        (("now-28d", "now"), 28, "28 DAY"),
        (("now-4w", "now"), 28, "28 DAY"),
        (("now-30d", "now"), 30, ""),
        (("now-12h", "now"), None, ""),
        (("2026-01-01", "now"), None, ""),
        (("now-7d", "now-1d"), None, ""),
    ],
)
def test_timeframes_map_only_to_periods_new_relic_offers(
    timeframe: tuple[str, str], days: int | None, nr_period: str
) -> None:
    crit = [{"target": 99, "timeframeFrom": timeframe[0], "timeframeTo": timeframe[1]}]
    (plan,) = service_level_plans([slo(crit)])
    assert (plan.period_days, plan.nr_period) == (days, nr_period)
    if not nr_period:
        assert any("period" in n for n in plan.notes)


@pytest.mark.parametrize("target", [0, -1, 100.5, float("inf"), float("nan"), "99", True, None])
def test_a_target_outside_zero_to_one_hundred_is_dropped_with_a_note(target: object) -> None:
    (plan,) = service_level_plans([slo([{"target": target, "timeframeFrom": "now-7d"}])])
    assert plan.target is None and any("target" in n for n in plan.notes)


def test_no_criteria_and_odd_shapes_do_not_crash() -> None:
    (plan,) = service_level_plans([slo([])])
    assert plan.target is None and plan.needs_review
    (odd,) = service_level_plans([slo([{"target": 99, "timeframeFrom": 5}])])
    assert odd.period_days is None


def test_a_second_criterion_is_noted_not_dropped_silently() -> None:
    crit: list[dict[str, Any]] = [{"target": 99, "timeframeTo": "now"}, {"target": 95}]
    (plan,) = service_level_plans([slo(crit)])
    assert any("more than one criterion" in n for n in plan.notes)


def test_an_indicator_that_is_not_the_total_failed_pattern_has_no_metrics() -> None:
    (plan,) = service_level_plans([slo(indicator="fetch logs | summarize count()")])
    assert plan.source_metrics == []
    assert plan.indicator.startswith("fetch logs")


def test_metric_extraction_is_safe_on_adversarial_input() -> None:
    for attack in (
        "timeseries { " + "total = sum(" * 20_000,
        "timeseries {" + " " * 100_000 + "}",
        "total = sum(" + "a" * 100_000,
        "timeseries { total = sum(" + "a." * 50_000 + ") }",
    ):
        start = time.perf_counter()
        (plan,) = service_level_plans([slo(indicator=attack)])
        assert time.perf_counter() - start < 5.0
        assert len(plan.indicator) <= 2000


def test_two_slos_with_one_name_get_distinct_keys() -> None:
    plans = service_level_plans([slo(), slo()])
    assert plans[0].source_key != plans[1].source_key


def test_terraform_is_only_comments_and_escapes_the_name() -> None:
    plans = service_level_plans([slo(name='n"\n${evil}‮')])
    tf = service_levels_terraform(plans)
    assert tf.strip()
    for line in tf.splitlines():
        assert not line.strip() or line.lstrip().startswith("#"), line
    assert "\n${" not in tf and "UNVERIFIED" in tf and "TODO" in tf


def test_the_report_escapes_text_and_says_what_is_unverified() -> None:
    text = render_report(service_level_plans([slo(name="a|b`c`<x>", indicator="x|`y`<z>")]))
    assert "a|b" not in text and "<x>" not in text and "<z>" not in text
    assert "needs review" in text and "no data path" in text


def test_cli_writes_plan_files(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["plan-service-levels", "--in", str(DT), "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    files = sorted((tmp_path / "service-levels").glob("*.json"))
    assert len(files) == len(load_slos(DT))
    plan = ServiceLevelPlan.model_validate_json(files[0].read_text())
    assert plan.needs_review and json.loads(files[0].read_text())["good_events_nrql"] == "TODO"
    assert (tmp_path / "report.md").exists() and (tmp_path / "service-levels.tf").exists()


def test_cli_fails_clearly_on_a_missing_input(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        app, ["plan-service-levels", "--in", str(tmp_path / "nope"), "--out", str(tmp_path)]
    )
    assert res.exit_code == 1 and "Error" in res.output
