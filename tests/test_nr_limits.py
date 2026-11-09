"""Bounds, fuzzed options and the behaviours that mutation testing found unguarded."""

import contextlib
import re
from typing import Any

import pytest

from hcl_schema import fmt_errors, schema_errors
from importer.core.emit import common
from importer.core.emit.common import CannotEmit
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.notifications import (
    notifications_terraform,
    nr_destination_input,
)
from importer.core.emit.terraform import resource_block, terraform_file
from importer.core.ir import (
    EmitOptions,
    IRMonitor,
)
from importer.core.notification_ir import (
    FILTER_LIMIT,
    MAX_WORKFLOWS,
    AuthKind,
    ChannelKind,
    DestinationAuth,
    DestinationType,
    IRChannel,
    IRTopic,
    NotificationPlan,
    NrDestination,
    PlannedDestination,
    PlannedWorkflow,
    build_plan,
    plan_violations,
    workflow_violations,
)
from importer.core.remediation import remedies_for
from test_emit import convert
from test_nr_conditions import log_count, with_emit


def monitor_with_signal(**changes: Any) -> IRMonitor:
    base = convert()
    return base.model_copy(update={"signal": base.signal.model_copy(update=changes)})


# --- item 6: nothing but CannotEmit or ValueError may escape --------------------------------

GRID: list[Any] = [
    0,
    -1,
    1,
    29,
    30,
    31,
    59,
    61,
    3601,
    10**18,
    -(10**18),
    1.5,
    True,
    "x",
    None,
    float("nan"),
    float("inf"),
    -float("inf"),
]
FIELDS = [
    "aggregation_timer_seconds",
    "fill_value",
    "slide_by_seconds",
    "violation_time_limit_seconds",
    "title_template",
    "runbook_url",
]


@pytest.mark.parametrize("method", ["EVENT_FLOW", "EVENT_TIMER", "CADENCE"])
@pytest.mark.parametrize("fill", [None, "NONE", "STATIC", "LAST_VALUE"])
def test_fuzzed_options_only_raise_cannot_emit_or_value_error(
    method: str, fill: str | None
) -> None:
    built = 0
    for field in FIELDS:
        for value in GRID:
            try:
                options = EmitOptions(
                    aggregation_method=method,
                    fill_option=fill,
                    **{field: value},
                )
            except ValueError:
                continue
            built += 1
            monitor = convert(notify_no_data=True).model_copy(update={"emit": options})
            for emit in (nerdgraph_payload, resource_block):
                with contextlib.suppress(ValueError):  # CannotEmit is one; others fail the test
                    emit(monitor)
    assert built > 20


def test_slide_by_zero_and_negative_are_range_errors_not_crashes() -> None:
    for bad in (0, -30):
        with pytest.raises(CannotEmit, match="slide-by"):
            nerdgraph_payload(with_emit(convert(), slide_by_seconds=bad))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_a_fill_value_must_be_finite(value: float) -> None:
    m = with_emit(convert(), fill_option="STATIC", fill_value=value)
    with pytest.raises(CannotEmit, match="fill value"):
        nerdgraph_payload(m)
    with pytest.raises(CannotEmit, match="fill value"):
        resource_block(m)


def test_a_timer_without_the_timer_method_is_not_silently_dropped() -> None:
    m = with_emit(convert(), aggregation_timer_seconds=90)
    with pytest.raises(CannotEmit, match="only used with EVENT_TIMER"):
        nerdgraph_payload(m)
    ok = with_emit(convert(), aggregation_method="EVENT_TIMER", aggregation_timer_seconds=90)
    assert nerdgraph_payload(ok)["variables"]["condition"]["signal"]["aggregationTimer"] == 90


def test_a_null_safe_scope_that_covers_every_filter_is_refused() -> None:
    m = with_emit(log_count(), null_safe_count=True, null_safe_scope=["service", "level"])
    with pytest.raises(CannotEmit, match="covers every filter"):
        nerdgraph_payload(m)
    one = with_emit(log_count(), null_safe_count=True, null_safe_scope=["service"])
    assert "filter(count(*)" in nerdgraph_payload(one)["variables"]["condition"]["nrql"]["query"]


@pytest.mark.parametrize(
    "reason",
    [
        "aggregation timer is only used with EVENT_TIMER",
        "a fill value must be a finite number",
        "null-safe count scope covers every filter, so the count would still be NULL",
        "ignore on expected termination needs a loss-of-signal timeout",
        "null-safe count scope names a filter that does not exist",
    ],
)
def test_the_new_and_older_limit_reasons_have_remediation_rules(reason: str) -> None:
    (remedy,) = remedies_for([reason])
    assert "outside" in remedy.title.lower() and remedy.steps


# --- item 10: boundaries --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "ok"), [(29, False), (30, True), (172800, True), (172801, False)]
)
def test_loss_of_signal_boundaries(seconds: int, ok: bool) -> None:
    if ok:
        common.check_expiration_seconds(seconds)
    else:
        with pytest.raises(CannotEmit, match="loss-of-signal"):
            common.check_expiration_seconds(seconds)


@pytest.mark.parametrize(
    ("method", "delay", "ok"),
    [
        ("EVENT_FLOW", 1200, True),
        ("EVENT_FLOW", 1201, False),
        ("CADENCE", 3600, True),
        ("CADENCE", 3601, False),
    ],
)
def test_delay_boundaries(method: str, delay: int, ok: bool) -> None:
    m = with_emit(monitor_with_signal(aggregation_delay_seconds=delay), aggregation_method=method)
    if ok:
        assert nerdgraph_payload(m)["variables"]["condition"]["signal"]["aggregationDelay"] == delay
    else:
        with pytest.raises(CannotEmit, match="aggregation delay"):
            nerdgraph_payload(m)


@pytest.mark.parametrize(
    ("window", "slide", "ok"),
    [(60, 30, True), (58, 29, False), (60, 60, False), (60, 20, False), (120, 30, True)],
)
def test_slide_by_boundaries(window: int, slide: int, ok: bool) -> None:
    m = with_emit(
        monitor_with_signal(aggregation_window_seconds=window, window_seconds=window * 2),
        slide_by_seconds=slide,
    )
    if ok:
        assert nerdgraph_payload(m)["variables"]["condition"]["signal"]["slideBy"] == slide
    else:
        with pytest.raises(CannotEmit, match="slide-by"):
            nerdgraph_payload(m)


@pytest.mark.parametrize(("window", "timer"), [(30, 30), (60, 60), (1800, 1200), (3600, 1200)])
def test_the_timer_defaults_to_the_window_within_bounds(window: int, timer: int) -> None:
    m = with_emit(
        monitor_with_signal(aggregation_window_seconds=window, window_seconds=max(window, 60)),
        aggregation_method="EVENT_TIMER",
    )
    assert nerdgraph_payload(m)["variables"]["condition"]["signal"]["aggregationTimer"] == timer


@pytest.mark.parametrize(
    ("value", "text"), [(0.0, "0"), (2.5, "2.5"), (-1.0, "-1"), (1e-07, "1e-07")]
)
def test_terraform_fill_value_formatting(value: float, text: str) -> None:
    tf = resource_block(with_emit(convert(), fill_option="STATIC", fill_value=value))
    assert re.search(rf"^  fill_value\s+= {re.escape(text)}$", tf, re.MULTILINE)


def test_terraform_flag_and_title_lines_are_exact() -> None:
    base = convert(notify_no_data=True)
    on = resource_block(
        base.model_copy(update={"emit": EmitOptions(ignore_on_expected_termination=True)})
    )
    off = resource_block(
        base.model_copy(update={"emit": EmitOptions(ignore_on_expected_termination=False)})
    )
    assert "ignore_on_expected_termination = true\n" in on
    assert "ignore_on_expected_termination = false\n" in off
    titled = resource_block(with_emit(convert(), title_template="{{tags.x}} high"))
    assert '  title_template = "{{tags.x}} high"\n' in titled
    assert fmt_errors(on, frozenset({"ignore_on_expected_termination"})) == []


def test_all_optional_condition_lines_are_aligned_like_terraform_fmt() -> None:
    keys = frozenset(
        {
            "fill_option",
            "fill_value",
            "slide_by",
            "violation_time_limit_seconds",
            "title_template",
            "runbook_url",
            "ignore_on_expected_termination",
        }
    )
    full = convert(notify_no_data=True).model_copy(
        update={
            "emit": EmitOptions(
                fill_option="STATIC",
                fill_value=0,
                slide_by_seconds=30,
                violation_time_limit_seconds=600,
                title_template="t",
                runbook_url="https://x.example.com/r",
                ignore_on_expected_termination=True,
            )
        }
    )
    partial = with_emit(convert(), fill_option="LAST_VALUE", runbook_url="https://x.example.com/r")
    for m in (full, partial):
        tf = resource_block(m)
        assert fmt_errors(tf, keys) == [] and schema_errors(tf) == []


def test_the_incident_preference_header_is_aligned() -> None:
    tf = terraform_file([], incident_preference="PER_CONDITION")
    assert fmt_errors(tf) == [] and "incident_preference" in tf


def test_workflow_filter_boundaries() -> None:
    empty = build_plan(
        IRTopic(
            source_system="x",
            source_key="k",
            name="t",
            channels=[IRChannel(kind=ChannelKind.EMAIL, target="a@example.com")],
        ),
        ["a"],
        "P:",
    )
    assert empty.workflow is not None
    from importer.core.notification_ir import issue_filter_size

    room = FILTER_LIMIT - issue_filter_size(empty.workflow) + 1
    for extra, ok in ((0, True), (1, False), (904, False)):
        plan = build_plan(
            IRTopic(
                source_system="x",
                source_key="k",
                name="t",
                channels=[IRChannel(kind=ChannelKind.EMAIL, target="a@example.com")],
            ),
            ["n" * (room + extra)],
            "P:",
        )
        assert (workflow_violations(plan) == []) is ok, extra


def test_workflow_account_limit_boundaries() -> None:
    dest = PlannedDestination(kind=ChannelKind.EMAIL, name="n", target="a@example.com")
    wf = PlannedWorkflow(name="w", condition_names=["c"], destination_indexes=[0])

    def plans(n: int) -> list[NotificationPlan]:
        return [
            NotificationPlan(
                topic=f"t{i}", destinations=[dest], workflow=wf.model_copy(update={"name": f"w{i}"})
            )
            for i in range(n)
        ]

    assert plan_violations(plans(MAX_WORKFLOWS)) == []
    assert any("1001" in p for p in plan_violations(plans(MAX_WORKFLOWS + 1)))


def test_workflow_options_are_omitted_from_the_plan_file_until_used() -> None:
    wf = PlannedWorkflow(name="w", condition_names=["c"], destination_indexes=[0])
    dumped = wf.model_dump()
    for key in ("notification_triggers", "muting_rules_handling", "enrichments"):
        assert key not in dumped
    used = wf.model_copy(
        update={"notification_triggers": ["CLOSED"], "muting_rules_handling": "NOTIFY_ALL_ISSUES"}
    )
    dumped = used.model_dump()
    assert dumped["notification_triggers"] == ["CLOSED"]
    assert dumped["muting_rules_handling"] == "NOTIFY_ALL_ISSUES"


def test_a_slack_destination_with_oauth_auth_is_still_refused() -> None:
    d = NrDestination(
        type=DestinationType.SLACK,
        name="s",
        auth=DestinationAuth(kind=AuthKind.OAUTH),
        channel_properties={"channelId": "C1"},
    )
    with pytest.raises(CannotEmit, match="OAuth"):
        nr_destination_input(d, {})
    plan = NotificationPlan(
        topic="t",
        destinations=[],
        typed_destinations=[d],
        workflow=PlannedWorkflow(name="w", condition_names=[], destination_indexes=[0]),
    )
    assert "newrelic_notification_destination" not in notifications_terraform([plan])
