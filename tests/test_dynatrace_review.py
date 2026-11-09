"""Dynatrace conversion: cases found in review (bad numbers, repeated ids, notes, escapes)."""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.ir import Confidence
from importer.core.remediation import steps_for
from importer.core.report import md_cell
from importer.core.taxonomy import Category, classify
from importer.sources.dynatrace.dql import NOT_NARROW
from importer.sources.dynatrace.models import DtSlo, DtWorkflow
from importer.sources.dynatrace.translate import translate, translate_workflow

# the package re-exports the function `translate`, which hides the module of the same name
t = importlib.import_module("importer.sources.dynatrace.translate")
FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace"
DQL = "dynatrace.automations:execute-dql-query"
QUERY = 'fetch logs, from:now() - 15m | filter loglevel == "ERROR" | summarize n = count()'
CONDITION = '{{ result("count")["records"][0]["n"] > 100 }}'
HUGE = "9" * 400


def workflow(query: str = QUERY, condition: str = CONDITION, minutes: int = 15) -> dict[str, Any]:
    return {
        "title": "w",
        "id": "w1",
        "tasks": {
            "count": {"action": DQL, "input": {"query": query}},
            "notify": {
                "action": "dynatrace.email:send-email",
                "conditions": {"custom": condition},
            },
        },
        "trigger": {
            "schedule": {
                "isActive": True,
                "trigger": {"type": "interval", "intervalMinutes": minutes},
            }
        },
    }


def wf(**kw: Any) -> DtWorkflow:
    return DtWorkflow.model_validate(workflow(**kw))


def run_cli(tmp_path: Path, items: list[Any]) -> tuple[Any, Any]:
    source = tmp_path / "in"
    source.mkdir()
    (source / "x.json").write_text(json.dumps(items))
    runner = CliRunner()
    args = ["convert", "--source", "dynatrace", "--in", str(source)]
    converted = runner.invoke(app, [*args, "--out", str(tmp_path / "run" / "alerts")])
    report = runner.invoke(app, ["report", str(tmp_path / "run"), "--source", "dynatrace"])
    return converted, report


@pytest.mark.parametrize("number", [HUGE, "-5", HUGE + ".5"])
def test_an_unusable_threshold_is_manual_and_convert_and_report_still_work(
    tmp_path: Path, number: str
) -> None:
    item = workflow(condition=CONDITION.replace("100", number))
    m = translate_workflow(DtWorkflow.model_validate(item))
    assert m.confidence is Confidence.MANUAL and "usable number" in m.reasons[0]
    assert steps_for(m.reasons)
    converted, report = run_cli(tmp_path, [item])
    assert converted.exit_code == 0, converted.output
    assert report.exit_code == 0, report.output
    assert "inf" not in (tmp_path / "run/alerts/main.tf").read_text().lower()


@pytest.mark.parametrize("target", [float("inf"), float("nan"), True, "99", None])
def test_an_slo_target_that_is_not_a_plain_number_is_not_printed(target: Any) -> None:
    slo = DtSlo.model_validate({"name": "s", "criteria": [{"target": target}]})
    (m,) = translate([slo])
    assert m.reasons[1] == "SLO criteria: target" and m.confidence is Confidence.MANUAL


def test_a_huge_slo_target_does_not_crash_convert(tmp_path: Path) -> None:
    source = tmp_path / "in"
    source.mkdir()
    (source / "s.json").write_text('[{"name": "s", "criteria": [{"target": 1%s}]}]' % ("0" * 400))
    out = CliRunner().invoke(
        app,
        ["convert", "--source", "dynatrace", "--in", str(source), "--out", str(tmp_path / "o")],
    )
    assert out.exit_code == 0, out.output


def test_identical_items_get_distinct_ids_and_convert_emits_both(tmp_path: Path) -> None:
    item = json.loads((FIXTURES / "workflow-scheduled-log-and-terms.json").read_text())
    first, second = translate([DtWorkflow.model_validate(item)] * 2)
    assert first.source_id != second.source_id
    converted, _ = run_cli(tmp_path, [item, item])
    assert converted.exit_code == 0 and "emitted 2" in converted.output
    assert len(list((tmp_path / "run/alerts/ir").glob("*.json"))) == 2
    assert len(list((tmp_path / "run/alerts/nerdgraph").glob("*.json"))) == 2
    assert (tmp_path / "run/alerts/main.tf").read_text().count("newrelic_nrql_alert_condition") == 2


def test_the_important_notes_come_first_and_survive_the_report_cut() -> None:
    item = workflow(
        query='fetch logs, from:now() - 1h | filter a == "b" | summarize n = count()',
        condition=CONDITION.replace(">", "<"),
        minutes=5,
    )
    item["isDeployed"] = False
    m = translate_workflow(DtWorkflow.model_validate(item))
    cell = md_cell("; ".join(m.reasons), 300)
    for note in ("below:", "notification tasks (email)", "ran every 300s", "draft"):
        assert note in cell, cell
    assert m.reasons[-2:] == [t.LOG_NOTE, t.SCHEDULE_NOTE.format(window=3600)]


@pytest.mark.parametrize(
    ("query", "minutes", "window"),
    [
        ('fetch logs, from:now() - 1h | filter a == "b" | summarize n = count()', 5, 3600),
        ('fetch logs, from:now() - 10m | filter a == "b" | summarize n = count()', 60, 600),
    ],
)
def test_the_query_range_is_the_window_and_a_different_interval_is_noted(
    query: str, minutes: int, window: int
) -> None:
    m = translate_workflow(wf(query=query, minutes=minutes))
    assert m.signal.window_seconds == m.signal.aggregation_window_seconds == window
    assert any(f"ran every {minutes * 60}s but counts the last {window}s" in r for r in m.reasons)


@pytest.mark.parametrize("amount", ["2h", "61m", "120m"])
def test_a_range_over_an_hour_is_manual(amount: str) -> None:
    query = f'fetch logs, from:now() - {amount} | filter a == "b" | summarize n = count()'
    m = translate_workflow(wf(query=query))
    assert m.confidence is Confidence.MANUAL and "longer than one hour" in m.reasons[0]
    assert steps_for(m.reasons)


def test_a_zero_range_is_manual() -> None:
    query = 'fetch logs, from:now() - 0m | filter a == "b" | summarize n = count()'
    assert translate_workflow(wf(query=query)).confidence is Confidence.MANUAL


@pytest.mark.parametrize(
    "value",
    ["a\\nb", "a\\tb", "a\\u0041", "a\\x41", "a\nb", "a\tb", "a\x00b", "a\x7fb"],
)
def test_other_escapes_and_control_characters_are_manual(value: str) -> None:
    query = f'fetch logs | filter msg == "{value}" | summarize n = count()'
    m = translate_workflow(wf(query=query))
    assert m.confidence is Confidence.MANUAL and m.reasons[0].startswith("DQL query is not")


def test_only_a_quote_and_a_backslash_escape_are_interpreted() -> None:
    query = 'fetch logs | filter msg == "a\\"b\\\\c" | summarize n = count()'
    m = translate_workflow(wf(query=query))
    assert m.signal.count is not None and m.signal.count.filters[0].value == 'a"b\\c'


def test_a_state_other_than_ok_is_manual() -> None:
    item = workflow()
    item["tasks"]["notify"]["conditions"]["states"] = {"count": "ERROR"}
    m = translate_workflow(DtWorkflow.model_validate(item))
    assert m.confidence is Confidence.MANUAL and m.reasons[0].startswith("workflow tasks run on")
    assert steps_for(m.reasons)
    item["tasks"]["notify"]["conditions"]["states"] = {"count": "OK"}
    assert translate_workflow(DtWorkflow.model_validate(item)).confidence is Confidence.APPROXIMATE


def test_a_draft_workflow_is_noted() -> None:
    item = workflow()
    item["isDeployed"] = False
    m = translate_workflow(DtWorkflow.model_validate(item))
    assert "the workflow was a draft (not deployed)" in m.reasons and steps_for(m.reasons)


def test_a_missing_time_range_is_noted() -> None:
    query = 'fetch logs | filter a == "b" | summarize n = count()'
    assert any("not modelled" in r for r in translate_workflow(wf(query=query)).reasons)


def test_every_dynatrace_note_has_a_sensible_category() -> None:
    expected = {
        t.LOG_NOTE: Category.UNVERIFIED_NAMES,
        t.SCHEDULE_NOTE.format(window=60): Category.APPROXIMATION,
        t.BELOW_NOTE: Category.APPROXIMATION,
        t.NOT_INTERVAL_NOTE: Category.APPROXIMATION,
        t.NO_RANGE_NOTE: Category.APPROXIMATION,
        t.LOOKBACK_NOTE.format(interval=300, window=3600): Category.APPROXIMATION,
        t.INACTIVE_REASON: Category.MANUAL_FOLLOWUP,
        t.DRAFT_REASON: Category.MANUAL_FOLLOWUP,
        "notification tasks (email) must be configured manually": Category.MANUAL_FOLLOWUP,
        "SLO criteria: target 99, timeframe now-7d to now": Category.MANUAL_FOLLOWUP,
        t.DAVIS_REASON: Category.UNSUPPORTED_FEATURE,
        t.SLO_REASON: Category.UNSUPPORTED_FEATURE,
        t.OTHER_TRIGGER: Category.UNSUPPORTED_FEATURE,
        t.SHAPE: Category.UNSUPPORTED_FEATURE,
        t.STATES: Category.UNSUPPORTED_FEATURE,
        t.LONG_REASON.format(what="the schedule interval of 7200s"): Category.UNSUPPORTED_FEATURE,
        NOT_NARROW: Category.UNSUPPORTED_QUERY,
        t.BAD_THRESHOLD: Category.UNSUPPORTED_QUERY,
    }
    assert {r: classify(r) for r in expected} == expected


@pytest.mark.parametrize(
    "reason",
    ["the workflow tasks list is long", "the trigger was inactive in the console"],
)
def test_the_dynatrace_taxonomy_phrases_are_anchored(reason: str) -> None:
    assert classify(reason) is Category.OTHER


def test_the_documented_report_flow_works_end_to_end(tmp_path: Path) -> None:
    run = tmp_path / "run"
    runner = CliRunner()
    args = ["convert", "--source", "dynatrace", "--in", str(FIXTURES), "--out", str(run / "alerts")]
    assert runner.invoke(app, args).exit_code == 0
    target = tmp_path / "r.html"
    report = runner.invoke(app, ["report", str(run), "--source", "dynatrace", "--out", str(target)])
    assert report.exit_code == 0, report.output
    html = target.read_text()
    assert "2 of 8 workflows and SLOs migrated" in html and "Davis problem notification" in html
    assert "importer apply --in" in html and "Dynatrace tenant" in html
