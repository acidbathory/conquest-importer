"""Dynatrace workflows and SLOs convert to IR, using scrubbed copies of real exports."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import Confidence, IRMonitor, Operator, SignalKind
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.dynatrace.models import (
    DtSlo,
    DtWorkflow,
    load_exports,
    load_slos,
    load_workflows,
)
from importer.sources.dynatrace.translate import translate, translate_workflow

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace"
DQL = "dynatrace.automations:execute-dql-query"


def by_name() -> dict[str, IRMonitor]:
    return {m.name: m for m in translate(load_exports(FIXTURES))}


def scheduled(query: str, condition: str, minutes: int = 15, active: bool = True) -> DtWorkflow:
    return DtWorkflow.model_validate(
        {
            "title": "w",
            "id": "w1",
            "tasks": {
                "count": {"action": DQL, "input": {"query": query}},
                "notify": {
                    "action": "dynatrace.email:send-email",
                    "conditions": {"custom": condition},
                    "input": {"to": ["a@example.com"]},
                },
            },
            "trigger": {
                "schedule": {
                    "isActive": active,
                    "trigger": {"type": "interval", "intervalMinutes": minutes},
                }
            },
        }
    )


GOOD_QUERY = 'fetch logs, from:now() - 15m | filter loglevel == "ERROR" | summarize n = count()'
NO_RANGE_QUERY = 'fetch logs | filter loglevel == "ERROR" | summarize n = count()'
GOOD_CONDITION = '{{ result("count")["records"][0]["n"] > 100 }}'


# models


def test_the_source_is_registered_and_loads_every_fixture() -> None:
    assert "dynatrace" in REGISTRY
    assert len(by_name()) == len(list(FIXTURES.glob("*.json")))


def test_workflows_and_slos_are_told_apart_by_shape() -> None:
    items = load_exports(FIXTURES)
    assert sum(isinstance(i, DtWorkflow) for i in items) == len(load_workflows(FIXTURES)) == 6
    assert sum(isinstance(i, DtSlo) for i in items) == len(load_slos(FIXTURES)) == 2


@pytest.mark.parametrize(
    "wrap",
    [lambda w: w, lambda w: [w], lambda w: {"results": [w], "count": 1}],
    ids=["object", "list", "api response"],
)
def test_a_workflow_loads_from_an_object_a_list_or_an_api_response(
    tmp_path: Path, wrap: Any
) -> None:
    workflow = json.loads((FIXTURES / "workflow-manual-trigger.json").read_text())
    file = tmp_path / "w.json"
    file.write_text(json.dumps(wrap(workflow)))
    assert [w.title for w in load_workflows(file)] == ["example-manual-runbook"]


def test_an_slo_loads_from_an_api_response(tmp_path: Path) -> None:
    slo = json.loads((FIXTURES / "slo-custom-sli.json").read_text())
    file = tmp_path / "s.json"
    file.write_text(json.dumps({"slos": [slo], "nextPageKey": None}))
    assert [s.name for s in load_slos(file)] == ["example-availability-slo"]


def test_a_folder_of_folders_loads(tmp_path: Path) -> None:
    (tmp_path / "workflows").mkdir()
    (tmp_path / "slos").mkdir()
    (tmp_path / "workflows" / "a.json").write_text(
        (FIXTURES / "workflow-manual-trigger.json").read_text()
    )
    (tmp_path / "slos" / "b.json").write_text((FIXTURES / "slo-second.json").read_text())
    assert len(load_exports(tmp_path)) == 2


def test_the_converted_output_inside_the_input_folder_is_not_read_again(tmp_path: Path) -> None:
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "a.json").write_text(
        (FIXTURES / "workflow-manual-trigger.json").read_text()
    )
    (tmp_path / "alerts" / "ir").mkdir(parents=True)
    (tmp_path / "alerts" / "ir" / "1.json").write_text('{"source_system": "dynatrace"}')
    assert len(load_exports(tmp_path)) == 1


def test_an_unknown_shape_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "x.json"
    file.write_text('[{"foo": 1}]')
    with pytest.raises(ValueError, match="neither a workflow nor an SLO"):
        load_exports(file)


def test_a_file_over_the_size_cap_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importer.sources.dynatrace import models

    monkeypatch.setattr(models, "MAX_INPUT_BYTES", 10)
    file = tmp_path / "big.json"
    file.write_text("[" + " " * 20 + "]")
    with pytest.raises(ValueError, match="larger than"):
        load_exports(file)
    assert MAX_INPUT_BYTES > 10


# scheduled workflows


def test_the_real_scheduled_check_becomes_a_log_count() -> None:
    m = by_name()["example-scheduled-error-check"]
    assert m.confidence is Confidence.APPROXIMATE and m.signal.kind is SignalKind.LOG
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (900, 900)
    assert m.thresholds[0].operator is Operator.ABOVE and m.thresholds[0].value == 100
    assert nrql_for(m) == "SELECT count(*) FROM Log WHERE loglevel = 'ERROR'"
    text = " ".join(m.reasons)
    assert "loglevel" in text and "not confirmed" in text
    assert "schedule" in text and "continuously" in text


def test_the_inactive_trigger_is_noted_and_the_condition_is_disabled() -> None:
    m = by_name()["example-scheduled-error-check"]
    assert "the Dynatrace trigger was inactive" in m.reasons
    assert m.unmapped["enabled"] is False
    assert "enabled" not in by_name()["example-checkout-errors"].unmapped


def test_several_equality_terms_and_an_hourly_schedule() -> None:
    m = by_name()["example-checkout-errors"]
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.window_seconds == 3600
    assert m.thresholds[0].operator is Operator.ABOVE_OR_EQUALS and m.thresholds[0].value == 20
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE dt.process.name = 'checkout' AND loglevel = 'ERROR'"
    )


@pytest.mark.parametrize(
    ("symbol", "operator"),
    [
        (">", Operator.ABOVE),
        (">=", Operator.ABOVE_OR_EQUALS),
        ("<", Operator.BELOW),
        ("<=", Operator.BELOW_OR_EQUALS),
    ],
)
def test_comparison_operators(symbol: str, operator: Operator) -> None:
    m = translate_workflow(scheduled(GOOD_QUERY, GOOD_CONDITION.replace(">", symbol)))
    assert m.confidence is Confidence.APPROXIMATE and m.thresholds[0].operator is operator


def test_a_below_condition_says_an_empty_result_is_not_a_zero() -> None:
    m = translate_workflow(scheduled(GOOD_QUERY, GOOD_CONDITION.replace(">", "<")))
    assert any("empty result" in r for r in m.reasons)


@pytest.mark.parametrize(
    ("minutes", "window"), [(1, 60), (5, 300), (30, 1800)], ids=["1m", "5m", "30m"]
)
def test_the_window_is_the_schedule_interval(minutes: int, window: int) -> None:
    m = translate_workflow(scheduled(NO_RANGE_QUERY, GOOD_CONDITION, minutes))
    assert m.signal.window_seconds == window == m.signal.aggregation_window_seconds
    assert any("default range is not modelled" in r for r in m.reasons)


def test_a_schedule_that_is_not_an_interval_uses_the_query_range_and_says_so() -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    wf.trigger["schedule"]["trigger"] = {"type": "time", "time": "08:00"}
    m = translate_workflow(wf)
    assert m.confidence is Confidence.APPROXIMATE and m.signal.window_seconds == 900
    assert any("not an interval" in r for r in m.reasons)


def test_a_schedule_longer_than_an_hour_is_manual() -> None:
    m = translate_workflow(scheduled(NO_RANGE_QUERY, GOOD_CONDITION, 120))
    assert m.confidence is Confidence.MANUAL
    assert any("longer than one hour" in r for r in m.reasons) and steps_for(m.reasons)


@pytest.mark.parametrize(
    "query",
    [
        'fetch logs | filter loglevel == "ERROR" | summarize n = count()',
        'fetch logs, from:now() - 1h | filter a == "b" AND c == "d" | summarize n = count()',
        'fetch logs | filter msg == "a | b and c == \\"d\\"" | summarize n = count()',
    ],
)
def test_narrow_queries_convert(query: str) -> None:
    assert translate_workflow(scheduled(query, GOOD_CONDITION)).confidence is Confidence.APPROXIMATE


def test_a_value_with_a_pipe_and_a_quote_reaches_nrql_escaped() -> None:
    query = 'fetch logs | filter msg == "it\'s | x" | summarize n = count()'
    m = translate_workflow(scheduled(query, GOOD_CONDITION))
    assert nrql_for(m) == "SELECT count(*) FROM Log WHERE msg = 'it\\'s | x'"


@pytest.mark.parametrize(
    "query",
    [
        "timeseries n = avg(dt.host.cpu.usage), from:-10m",
        'fetch spans | filter a == "b" | summarize n = count()',
        'fetch logs | filter a == "b" | summarize n = count() | sort n desc',
        'fetch logs | filter a == "b" | summarize n = count(), by:{host}',
        'fetch logs | filter a == "b" | summarize n = avg(x)',
        'fetch logs | filter a != "b" | summarize n = count()',
        'fetch logs | filter a == "b" or c == "d" | summarize n = count()',
        'fetch logs | filter matchesPhrase(a, "b") | summarize n = count()',
        'fetch logs | filter a == "b" | join [fetch logs] , on:{a} | summarize n = count()',
        'fetch logs | filter a == "b" | summarize count()',
        'fetch logs, from:now() - 2d | filter a == "b" | summarize n = count()',
        "fetch logs | summarize n = count()",
        "",
    ],
)
def test_other_queries_are_manual_and_quote_nothing(query: str) -> None:
    m = translate_workflow(scheduled(query, GOOD_CONDITION))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith("DQL query is not the supported pattern")
    assert query == "" or query not in " ".join(m.reasons)
    assert steps_for(m.reasons)


@pytest.mark.parametrize(
    "condition",
    [
        '{{ result("count")["records"][0]["n"] == 5 }}',
        '{{ result("other")["records"][0]["n"] > 5 }}',
        '{{ result("count")["records"][0]["x"] > 5 }}',
        '{{ result("count")["records"][0]["n"] > five }}',
        '{{ result("count")["records"][1]["n"] > 5 }}',
        "",
    ],
)
def test_other_notify_conditions_are_manual(condition: str) -> None:
    m = translate_workflow(scheduled(GOOD_QUERY, condition))
    assert m.confidence is Confidence.MANUAL
    assert "notify condition" in m.reasons[0] and steps_for(m.reasons)


def test_different_thresholds_on_two_notify_tasks_are_manual() -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    wf.tasks["second"] = wf.tasks["notify"].model_copy(
        update={"conditions": {"custom": GOOD_CONDITION.replace("100", "5")}}
    )
    assert translate_workflow(wf).confidence is Confidence.MANUAL


def test_two_notify_tasks_with_the_same_condition_convert() -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    wf.tasks["second"] = wf.tasks["notify"].model_copy()
    m = translate_workflow(wf)
    assert m.confidence is Confidence.APPROXIMATE
    assert any("notification tasks (email) must be configured manually" in r for r in m.reasons)


@pytest.mark.parametrize("drop", ["count", "notify"])
def test_the_workflow_must_be_one_query_plus_gated_notify_tasks(drop: str) -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    del wf.tasks[drop]
    m = translate_workflow(wf)
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith("workflow tasks")
    assert [r.title for r in remedies_for([m.reasons[0]])] == ["Workflow tasks not converted"]


def test_two_queries_are_manual() -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    wf.tasks["again"] = wf.tasks["count"].model_copy()
    assert translate_workflow(wf).reasons[0].startswith("workflow tasks")


def test_an_ungated_task_is_manual() -> None:
    wf = scheduled(GOOD_QUERY, GOOD_CONDITION)
    wf.tasks["notify"].conditions = {}
    assert translate_workflow(wf).confidence is Confidence.MANUAL


# other triggers


def test_a_davis_problem_workflow_is_a_notification_route() -> None:
    m = by_name()["example-problem-slack"]
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0] == "Davis problem notification workflow"
    assert "notification tasks (pagerduty, slack) must be configured manually" in m.reasons
    steps = " ".join(steps_for(m.reasons))
    assert "destination" in steps and "workflow" in steps and "priority" in steps


def test_the_inactive_davis_workflow_has_no_addresses_in_its_reasons() -> None:
    m = by_name()["example-problem-email"]
    assert m.reasons[0] == "Davis problem notification workflow"
    assert "the Dynatrace trigger was inactive" in m.reasons
    assert "example.com" not in json.dumps(m.model_dump())


def test_a_manual_trigger_is_manual_with_steps() -> None:
    m = by_name()["example-manual-runbook"]
    assert m.confidence is Confidence.MANUAL and "workflow trigger" in m.reasons[0]
    assert steps_for(m.reasons)


def test_a_non_davis_event_trigger_is_manual() -> None:
    wf = DtWorkflow.model_validate(
        {
            "title": "w",
            "tasks": {},
            "trigger": {"eventTrigger": {"triggerConfiguration": {"type": "event"}}},
        }
    )
    assert "workflow trigger" in translate_workflow(wf).reasons[0]


# SLOs


def test_an_slo_is_manual_and_lists_its_criteria_as_numbers() -> None:
    m = by_name()["example-availability-slo"]
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0] == "SLO is not converted automatically"
    assert "SLO criteria: target 99, warning 99.5, timeframe now-7d to now" in m.reasons
    assert "service level" in " ".join(steps_for(m.reasons))


def test_every_criterion_is_listed_and_an_odd_timeframe_is_not_quoted() -> None:
    m = by_name()["example-latency-slo"]
    assert "SLO criteria: target 95, timeframe now-30d to now" in m.reasons
    assert "SLO criteria: target 99.9, warning 99.95, timeframe now-1d to now" in m.reasons
    slo = DtSlo.model_validate(
        {"name": "s", "criteria": [{"target": 9, "timeframeFrom": "'; drop", "timeframeTo": "now"}]}
    )
    (odd,) = translate([slo])
    assert "drop" not in " ".join(odd.reasons)


# guidance and taxonomy


@pytest.mark.parametrize("name", sorted(by_name()))
def test_every_reason_has_a_category_and_every_manual_asset_has_steps(name: str) -> None:
    m = by_name()[name]
    for reason in m.reasons:
        assert classify(reason) is not Category.OTHER, reason
    if m.confidence is Confidence.MANUAL:
        assert all(steps_for([r]) for r in m.reasons), m.reasons


def test_specific_dql_reasons_beat_the_broader_query_rule() -> None:
    reason = "workflow tasks are not one DQL query plus notify tasks gated by a condition"
    assert [r.title for r in remedies_for([reason])] == ["Workflow tasks not converted"]


def test_translating_does_not_grow_the_rule_registry() -> None:
    from importer.core import remediation

    before = len(remediation._COMPILED)
    translate(load_exports(FIXTURES))
    assert len(remediation._COMPILED) == before
