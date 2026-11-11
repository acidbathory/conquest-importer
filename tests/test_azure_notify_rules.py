"""Alert processing rules to muting rules: schedules, conditions, scopes, and what stays manual."""

from datetime import date
from pathlib import Path

import pytest

from importer.core.emit.muting import muting_payload, muting_terraform
from importer.core.ir import (
    Aggregation,
    Confidence,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.core.muting_ir import Repeat
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications.models import ProcessingRule, RuleCondition, load_export
from importer.sources.azure.notifications.processing import (
    MonitorIndex,
    RuleOutcome,
    RuleResult,
    convert_rule,
)
from importer.sources.azure.notifications.timezones import WINDOWS_TO_IANA, to_iana

FIX = Path(__file__).parent / "fixtures" / "azure" / "alert_processing_rules"
TODAY = date(2026, 11, 11)  # a Wednesday
SUB = "/subscriptions/subId1/resourceGroups/RGId1/providers/microsoft.insights/metricAlerts/"


def monitor(n: int, name: str, key: str = "", tags: tuple[str, ...] = ()) -> IRMonitor:
    return IRMonitor(
        source_system="azure",
        source_id=n,
        source_key=key or f"{SUB}{name}",
        source_type="metricAlerts",
        name=name,
        tags=list(tags),
        signal=Signal(
            kind=SignalKind.METRIC,
            raw_query="q",
            window_seconds=300,
            aggregation_window_seconds=60,
            metric=MetricSignal(
                terms={
                    "a": MetricTerm(
                        metric="m",
                        space_aggregation=Aggregation.AVG,
                        nr_target=NrTarget(event="Metric", attribute="x"),
                    )
                },
                aggregation=Aggregation.AVG,
            ),
        ),
        thresholds=[Threshold(severity=Severity.CRITICAL, operator=Operator.ABOVE, value=1)],
        confidence=Confidence.EXACT,
    )


MONITORS = [monitor(1, "cpu high"), monitor(2, "disk full")]


def convert(r: ProcessingRule, monitors: list[IRMonitor] | None = None) -> RuleResult:
    index = MonitorIndex.build(MONITORS if monitors is None else monitors)
    return convert_rule(r, index, "Imported:", TODAY)


def by_description(text: str) -> ProcessingRule:
    return next(r for r in load_export(FIX).rules if text in r.description)


def test_outside_business_hours_is_a_daily_and_a_weekly_rule_in_iana_time() -> None:
    res = convert(by_description("outside business hours"))
    assert res.outcome is RuleOutcome.CONVERTED
    daily, weekly = res.rules
    assert daily.schedule is not None and weekly.schedule is not None
    assert daily.schedule.repeat is Repeat.DAILY
    assert daily.schedule.start_time == "2026-11-11T17:00:00"
    assert daily.schedule.end_time == "2026-11-12T09:00:00"  # crosses midnight
    assert daily.schedule.time_zone == "America/New_York"
    assert weekly.schedule.repeat is Repeat.WEEKLY
    assert [d.value for d in weekly.schedule.weekly_repeat_days] == ["SATURDAY", "SUNDAY"]
    assert weekly.schedule.start_time == "2026-11-14T00:00:00"  # the next Saturday
    assert weekly.schedule.end_time == "2026-11-15T00:00:00"
    assert R.R_RULE_SEVERAL in res.reasons
    assert {c.attribute for r in res.rules for c in r.condition.conditions} == {"conditionName"}
    assert res.rules[0].condition.conditions[0].values == ["cpu high", "disk full"]
    assert all(not r.enabled and r.action_on_window_ended == "DO_NOTHING" for r in res.rules)
    assert len({r.name for r in res.rules}) == 2


def test_one_off_window_is_exact_and_an_expired_one_makes_no_rule() -> None:
    vm = (
        "/subscriptions/subId1/resourceGroups/RGId1/providers/Microsoft.Compute"
        "/virtualMachines/VMName"
    )
    tagged = [monitor(1, "cpu high", tags=(f"azure.resourceId:{vm}",)), monitor(2, "disk full")]
    r = by_description("on VMName")
    expired = convert(r, tagged)
    assert expired.outcome is RuleOutcome.NOTHING and R.R_RULE_EXPIRED in expired.reasons
    res = convert_rule(r, MonitorIndex.build(tagged), "Imported:", date(2021, 4, 1))
    assert res.outcome is RuleOutcome.CONVERTED
    (only,) = res.rules
    assert only.schedule is not None and only.schedule.repeat is None
    assert only.schedule.start_time == "2021-04-15T18:00:00"
    assert only.schedule.time_zone == "America/Los_Angeles"
    assert only.condition.conditions[0].values == ["cpu high"]  # only the tagged condition


def test_a_resource_scope_without_tags_on_the_converted_conditions_is_manual() -> None:
    res = convert_rule(
        by_description("on VMName"), MonitorIndex.build(MONITORS), "I:", date(2021, 4, 1)
    )
    assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_TAG in res.reasons and res.steps


def test_resource_group_scopes_and_resource_type_use_tags_when_they_exist() -> None:
    desc = "recurring maintenance"
    plain = convert(by_description(desc))
    assert plain.outcome is RuleOutcome.MANUAL and R.R_RULE_TAG in plain.reasons
    tagged = [
        monitor(
            1,
            "vm cpu",
            tags=(
                "azure.resourceGroup:RGId1",
                "azure.resourceType:microsoft.compute/virtualmachines",
            ),
        ),
        monitor(
            2,
            "db cpu",
            tags=("azure.resourceGroup:RGId1", "azure.resourceType:microsoft.sql/servers"),
        ),
        monitor(
            3,
            "other rg",
            tags=(
                "azure.resourceGroup:Elsewhere",
                "azure.resourceType:microsoft.compute/virtualmachines",
            ),
        ),
    ]
    res = convert(by_description(desc), tagged)
    assert res.outcome is RuleOutcome.CONVERTED
    (only,) = res.rules
    assert only.condition.conditions[0].values == ["vm cpu"]
    assert only.schedule is not None and only.schedule.time_zone == "Asia/Kolkata"
    assert only.schedule.start_time == "2026-11-14T22:00:00"
    assert only.schedule.end_time == "2026-11-15T04:00:00"


def test_alert_rule_id_is_resolved_through_the_converted_monitor() -> None:
    wanted = SUB + "RuleName"
    r = by_description("above AlertRule").model_copy(deep=True)
    r.conditions[0].values = [wanted.upper()]  # Azure ids are case-insensitive
    res = convert(r, [monitor(3, "RuleName"), *MONITORS])
    assert res.outcome is RuleOutcome.CONVERTED
    (only,) = res.rules
    assert only.schedule is None and R.R_RULE_ALWAYS in res.reasons
    assert only.condition.conditions[0].values == ["RuleName"]
    # not converted: nothing to mute, and not a manual job either
    assert convert(r).outcome is RuleOutcome.NOTHING


def test_add_action_groups_stays_manual_with_the_workflow_workaround() -> None:
    res = convert(by_description("Add AGId1 and AGId2"))
    assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_ADD in res.reasons
    assert res.rules == []
    assert res.action_groups == [
        "subid1/rgid1/agid1",
        "subid1/rgid1/agid2",
    ]
    assert any("workflow" in s for s in res.steps)


def test_the_old_preview_api_and_unknown_actions_are_manual() -> None:
    legacy = load_export(FIX / "2019-05-05-preview__ActionRules_Create_SuppressionRule.json").rules[
        0
    ]
    assert R.R_RULE_LEGACY in convert(legacy).reasons
    synthetic = load_export(
        Path(__file__).parent
        / "fixtures"
        / "azure"
        / "synthetic"
        / "SYNTHETIC__alert_processing_rule_unknown_action.json"
    ).rules[0]
    res = convert(synthetic)
    assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_ACTION in res.reasons


def test_monthly_recurrences_are_unrolled_one_rule_per_day() -> None:
    r = by_description("outside business hours").model_copy(deep=True)
    assert r.schedule is not None
    r.schedule.recurrences = [
        r.schedule.recurrences[0].model_copy(
            update={
                "type": "Monthly",
                "days_of_month": [20, 5],
                "start_time": "01:00:00",
                "end_time": "02:00:00",
            }
        )
    ]
    res = convert(r)
    starts = sorted(x.schedule.start_time for x in res.rules if x.schedule)
    assert starts == ["2026-11-20T01:00:00", "2026-12-05T01:00:00"]
    assert all(x.schedule and x.schedule.repeat is Repeat.MONTHLY for x in res.rules)


def test_unknown_zone_and_broken_schedules_are_manual_without_echoing_the_text() -> None:
    r = by_description("outside business hours").model_copy(deep=True)
    assert r.schedule is not None
    r.schedule.time_zone = "Mars/Olympus-SECRETWORD"
    res = convert(r)
    assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_ZONE in res.reasons
    assert "SECRETWORD" not in repr(res)
    r.schedule.time_zone = "UTC"
    r.schedule.effective_from = "2024-02-30T25:61:00"
    res = convert(r)
    assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_SCHEDULE in res.reasons


def test_fields_new_relic_cannot_match_are_manual() -> None:
    r = by_description("outside business hours").model_copy(deep=True)
    for field in ("MonitorCondition", "SignalType", "Description", "AlertContext", "Future"):
        r.conditions = [RuleCondition(field=field, operator="Equals", values=["x"])]
        res = convert(r)
        assert res.outcome is RuleOutcome.MANUAL and R.R_RULE_FIELD in res.reasons, field


def test_more_than_500_names_split_into_parts() -> None:
    many = [monitor(i, f"cond {i:04d}") for i in range(1, 1202)]
    res = convert(by_description("outside business hours"), many)
    assert len(res.rules) == 2 * 3  # two recurrences, three parts each
    assert all(len(r.condition.conditions[0].values) <= 500 for r in res.rules)
    assert len({r.name for r in res.rules}) == len(res.rules)


def test_results_emit_through_the_shared_muting_emitters() -> None:
    res = convert(by_description("outside business hours"))
    tf = muting_terraform(res.rules)
    assert tf.count("newrelic_alert_muting_rule") == 2 and "time_zone" in tf
    assert muting_payload(res.rules[0])["variables"]["rule"]["schedule"]["repeat"] == "DAILY"


@pytest.mark.parametrize("windows", sorted(WINDOWS_TO_IANA))
def test_every_windows_zone_maps_to_a_real_iana_zone(windows: str) -> None:
    from zoneinfo import available_timezones

    assert to_iana(windows) in available_timezones() | {"UTC"}


def test_zone_lookup_is_case_insensitive_and_does_not_guess() -> None:
    assert to_iana("eastern standard time") == "America/New_York"
    assert to_iana("europe/berlin") == "Europe/Berlin"
    assert to_iana("") == "UTC"
    assert to_iana("Nowhere Standard Time") is None
