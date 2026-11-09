"""Condition emitters: optional IR settings, documented bounds, policy preference, null-safe counts.

Every option is off by default and the default output is pinned by test_emitter_byte_identity.py.
"""

from typing import Any

import pytest

from hcl_schema import resource_count, schema_errors
from importer.core.emit import common
from importer.core.emit.common import CannotEmit, EmitDefaults
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.nrql import build_nrql
from importer.core.emit.terraform import resource_block, terraform_file
from importer.core.ir import (
    Confidence,
    CountFilter,
    CountSignal,
    EmitOptions,
    IRMonitor,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.core.remediation import remedies_for
from test_emit import convert


def with_emit(monitor: IRMonitor, **options: Any) -> IRMonitor:
    return monitor.model_copy(update={"emit": EmitOptions(**options)})


def condition(monitor: IRMonitor) -> dict[str, Any]:
    cond: dict[str, Any] = nerdgraph_payload(monitor)["variables"]["condition"]
    return cond


def log_count(thresholds: list[Threshold] | None = None) -> IRMonitor:
    signal = Signal(
        kind=SignalKind.LOG,
        raw_query="q",
        window_seconds=300,
        aggregation_window_seconds=60,
        count=CountSignal(
            filters=[
                CountFilter(key="service", value="api"),
                CountFilter(key="level", value="error"),
            ]
        ),
    )
    below = Threshold(severity=Severity.CRITICAL, operator=Operator.BELOW, value=1)
    return IRMonitor(
        source_system="gcp",
        source_id=9,
        source_type="log",
        name="no errors",
        signal=signal,
        thresholds=thresholds or [below],
        confidence=Confidence.APPROXIMATE,
    )


def test_defaults_add_nothing() -> None:
    cond = condition(convert())
    assert cond["signal"] == {
        "aggregationWindow": 60,
        "aggregationMethod": "EVENT_FLOW",
        "aggregationDelay": 120,
    }
    for key in ("violationTimeLimitSeconds", "titleTemplate", "runbookUrl"):
        assert key not in cond
    tf = terraform_file([convert()])
    for word in ("fill_option", "slide_by", "runbook_url", "title_template", "incident_preference"):
        assert word not in tf


def test_the_saved_ir_of_a_monitor_without_options_has_no_emit_key() -> None:
    assert '"emit"' not in convert().model_dump_json()
    assert '"emit"' in with_emit(convert(), fill_option="NONE").model_dump_json()


def test_event_timer_uses_the_timer_and_not_the_delay() -> None:
    m = with_emit(convert(), aggregation_method="EVENT_TIMER", aggregation_timer_seconds=90)
    assert condition(m)["signal"] == {
        "aggregationWindow": 60,
        "aggregationMethod": "EVENT_TIMER",
        "aggregationTimer": 90,
    }
    tf = terraform_file([m])
    assert 'aggregation_method = "event_timer"' in tf and "aggregation_timer  = 90" in tf
    assert "aggregation_delay" not in tf and schema_errors(tf) == []


def test_event_timer_timer_defaults_to_the_window() -> None:
    m = with_emit(convert(), aggregation_method="EVENT_TIMER")
    assert condition(m)["signal"]["aggregationTimer"] == 60


def test_cadence_keeps_the_delay() -> None:
    m = with_emit(convert(), aggregation_method="CADENCE")
    assert condition(m)["signal"]["aggregationMethod"] == "CADENCE"
    assert condition(m)["signal"]["aggregationDelay"] == 120
    assert 'aggregation_method = "cadence"' in terraform_file([m])


def test_fill_slide_limit_title_runbook_and_termination() -> None:
    m = convert(notify_no_data=True, no_data_minutes=10)
    m = m.model_copy(
        update={
            "emit": EmitOptions(
                fill_option="STATIC",
                fill_value=0,
                slide_by_seconds=30,
                violation_time_limit_seconds=86400,
                title_template="{{tags.InstanceId}} is hot",
                runbook_url="https://wiki.example.com/cpu",
                ignore_on_expected_termination=True,
            ),
            "signal": convert().signal.model_copy(update={"aggregation_window_seconds": 60}),
        }
    )
    cond = condition(m)
    assert cond["signal"]["fillOption"] == "STATIC" and cond["signal"]["fillValue"] == 0
    assert cond["signal"]["slideBy"] == 30
    assert cond["violationTimeLimitSeconds"] == 86400
    assert cond["titleTemplate"] == "{{tags.InstanceId}} is hot"
    assert cond["runbookUrl"] == "https://wiki.example.com/cpu"
    assert cond["expiration"]["ignoreOnExpectedTermination"] is True
    tf = terraform_file([m])
    assert schema_errors(tf) == [] and resource_count(tf) == 2
    for line in (
        'fill_option                    = "static"',
        "fill_value                     = 0",
        "slide_by                       = 30",
        "violation_time_limit_seconds   = 86400",
        "ignore_on_expected_termination = true",
    ):
        assert line in tf, line


def test_title_and_runbook_are_escaped_in_terraform() -> None:
    m = with_emit(convert(), title_template='x"${evil}', runbook_url='https://e.example.com/"${u}')
    tf = resource_block(m)
    assert "${evil}" not in tf.replace("$${evil}", "")
    assert "${u}" not in tf.replace("$${u}", "")


@pytest.mark.parametrize(
    ("monitor", "message"),
    [
        (convert(notify_no_data=True, no_data_minutes=2881), "loss-of-signal timeout .* outside"),
        (
            with_emit(convert(), aggregation_method="EVENT_TIMER", aggregation_timer_seconds=4),
            "aggregation timer 4s",
        ),
        (
            with_emit(convert(), aggregation_method="EVENT_TIMER", aggregation_timer_seconds=1201),
            "aggregation timer 1201s",
        ),
        (
            with_emit(convert(), violation_time_limit_seconds=299),
            "violation time limit 299s",
        ),
        (with_emit(convert(), violation_time_limit_seconds=2592001), "violation time limit"),
        (with_emit(convert(), title_template="x" * 257), "title template is 257"),
        (with_emit(convert(), runbook_url="javascript:alert(1)"), "runbook URL"),
        (with_emit(convert(), slide_by_seconds=45), "slide-by 45s"),
        (with_emit(convert(), slide_by_seconds=60), "slide-by 60s"),
        (with_emit(convert(), fill_option="STATIC"), "fill option STATIC needs a fill value"),
        (with_emit(convert(), fill_value=1), "fill value needs fill option STATIC"),
        (
            with_emit(convert(), ignore_on_expected_termination=True),
            "needs a loss-of-signal timeout",
        ),
    ],
)
def test_bounds_are_cannot_emit_reasons(monitor: IRMonitor, message: str) -> None:
    with pytest.raises(CannotEmit, match=message):
        nerdgraph_payload(monitor)
    with pytest.raises(CannotEmit, match=message):
        resource_block(monitor)


def test_the_loss_of_signal_bounds_are_inclusive() -> None:
    # 30 s is the lower bound but the importer works in whole minutes, so 1 minute is the floor
    ok = condition(convert(notify_no_data=True, no_data_minutes=2880))
    assert ok["expiration"]["expirationDuration"] == 172800
    with pytest.raises(CannotEmit, match="outside 30s-48h"):
        nerdgraph_payload(convert(notify_no_data=True, no_data_minutes=2881))


def test_delay_bounds_depend_on_the_method() -> None:
    long_delay = convert().signal.model_copy(update={"aggregation_delay_seconds": 1300})
    flow = convert().model_copy(update={"signal": long_delay})
    with pytest.raises(CannotEmit, match="aggregation delay 1300s"):
        nerdgraph_payload(flow)
    assert (
        condition(with_emit(flow, aggregation_method="CADENCE"))["signal"]["aggregationDelay"]
        == 1300
    )
    too_long = long_delay.model_copy(update={"aggregation_delay_seconds": 3601})
    cadence = with_emit(
        convert().model_copy(update={"signal": too_long}), aggregation_method="CADENCE"
    )
    with pytest.raises(CannotEmit, match="aggregation delay 3601s"):
        nerdgraph_payload(cadence)


def test_at_most_two_terms_one_per_severity() -> None:
    crit = Threshold(severity=Severity.CRITICAL, operator=Operator.ABOVE, value=90)
    warn = Threshold(severity=Severity.WARNING, operator=Operator.ABOVE, value=80)
    base = convert()
    ok = base.model_copy(update={"thresholds": [crit, warn]})
    assert len(condition(ok)["terms"]) == 2
    three = base.model_copy(update={"thresholds": [crit, warn, warn]})
    with pytest.raises(CannotEmit, match="more than two thresholds"):
        nerdgraph_payload(three)
    twice = base.model_copy(update={"thresholds": [crit, crit]})
    with pytest.raises(CannotEmit, match="same severity"):
        resource_block(twice)


def test_the_existing_window_conflict_is_resolved_as_the_research_note_says() -> None:
    # 30..3600 s aggregation windows and 60..86400 s durations are kept (note, section 2.1)
    assert (common.MIN_AGGREGATION_WINDOW_SECONDS, common.MAX_AGGREGATION_WINDOW_SECONDS) == (
        30,
        3600,
    )
    assert (common.MIN_DURATION_SECONDS, common.MAX_DURATION_SECONDS) == (60, 86400)


@pytest.mark.parametrize(
    "reason",
    [
        "more than two thresholds: New Relic allows one critical and one warning term",
        "loss-of-signal timeout 999999s is outside 30s-48h",
        "aggregation timer 4s is outside 5s-20min",
        "aggregation delay 1300s is outside 0s-20min for EVENT_FLOW",
        "violation time limit 299s is outside 5min-30 days",
        "title template is 300 characters; New Relic allows 256",
        "runbook URL is not an http or https address",
        "slide-by 45s must divide the 60s aggregation window",
        "fill option STATIC needs a fill value",
    ],
)
def test_limit_reasons_have_remediation_rules(reason: str) -> None:
    remedies = remedies_for([reason])
    assert remedies and remedies[0].steps
    assert "outside" in remedies[0].title.lower() or "thresholds" in remedies[0].title.lower()


# --- policy incident preference ------------------------------------------------------------


def test_incident_preference_on_the_policy_is_opt_in() -> None:
    assert "incident_preference" not in terraform_file([convert()])
    tf = terraform_file([convert()], incident_preference="PER_CONDITION")
    assert '  incident_preference = "PER_CONDITION"' in tf and schema_errors(tf) == []
    with pytest.raises(ValueError, match="incident preference"):
        terraform_file([convert()], incident_preference="PER_DAY")


# --- null-safe counts ----------------------------------------------------------------------


def test_counts_are_plain_by_default() -> None:
    assert build_nrql(log_count()) == (
        "SELECT count(*) FROM Log WHERE service = 'api' AND level = 'error'"
    )


def test_null_safe_count_wraps_the_filters() -> None:
    m = with_emit(log_count(), null_safe_count=True)
    assert build_nrql(m) == (
        "SELECT filter(count(*), WHERE service = 'api' AND level = 'error') FROM Log"
    )


def test_null_safe_count_keeps_the_scope_filters_outside() -> None:
    m = with_emit(log_count(), null_safe_count=True, null_safe_scope=["service"])
    assert build_nrql(m) == (
        "SELECT filter(count(*), WHERE level = 'error') FROM Log WHERE service = 'api'"
    )


def test_null_safe_scope_must_name_a_filter() -> None:
    m = with_emit(log_count(), null_safe_count=True, null_safe_scope=["nope"])
    with pytest.raises(CannotEmit, match="null-safe count scope"):
        nerdgraph_payload(m)


def test_null_safe_never_touches_a_heartbeat_or_metric() -> None:
    plain = convert()
    assert build_nrql(with_emit(plain, null_safe_count=True)) == build_nrql(plain)


def test_null_safe_can_be_switched_off_per_monitor_when_the_default_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(common, "EMIT_DEFAULTS", EmitDefaults(null_safe_count=True))
    assert (
        "filter(count(*)"
        in nerdgraph_payload(log_count())["variables"]["condition"]["nrql"]["query"]
    )
    off = with_emit(log_count(), null_safe_count=False)
    assert "filter(" not in nerdgraph_payload(off)["variables"]["condition"]["nrql"]["query"]


# --- changing the defaults is visible and local --------------------------------------------


def test_the_method_default_can_follow_the_source_for_metrics_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        common,
        "EMIT_DEFAULTS",
        EmitDefaults(aggregation_method_by_source=(("cloudwatch", "EVENT_TIMER"),)),
    )
    assert condition(convert())["signal"]["aggregationMethod"] == "EVENT_TIMER"
    assert "aggregationDelay" not in condition(convert())["signal"]
    assert condition(log_count())["signal"]["aggregationMethod"] == "EVENT_FLOW"
    explicit = with_emit(convert(), aggregation_method="EVENT_FLOW")
    assert condition(explicit)["signal"]["aggregationMethod"] == "EVENT_FLOW"


def test_count_fill_default_applies_to_counts_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "EMIT_DEFAULTS", EmitDefaults(count_fill=("STATIC", 0.0)))
    assert condition(log_count())["signal"]["fillOption"] == "STATIC"
    assert "fillOption" not in condition(convert())["signal"]
