"""Mute timings to muting rules: the exact cases, scoping, dates, and what stays manual."""

from datetime import date

import pytest

from hcl_schema import resource_count, schema_errors
from importer.core.emit.muting import muting_payload, muting_terraform
from importer.core.muting_ir import MAX_VALUES, MutingRule
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import MuteTiming, TimeInterval, TimeRange
from importer.sources.grafana.notifications.muting import mute_timing_to_muting
from importer.sources.grafana.notifications.policy import Predicate
from importer.sources.grafana.notify_convert import mutes

TODAY = date(2026, 11, 20)  # a Friday


def timing(name: str = "Nights", **interval: object) -> MuteTiming:
    return MuteTiming(name=name, intervals=[TimeInterval.model_validate(interval)])


def rules_for(
    t: MuteTiming,
    names: list[str] | None = None,
    predicates: list[Predicate] | None = None,
) -> mutes.MuteResult:
    return mutes.muting_rules(
        mute_timing_to_muting(t),
        names=names or ["cond a", "cond b"],
        predicates=predicates or [],
        label="route 1",
        prefix="Imported:",
        today=TODAY,
    )


def only(result: mutes.MuteResult) -> MutingRule:
    assert len(result.rules) == 1, result
    return result.rules[0]


def test_weekday_evenings_become_one_weekly_rule_in_the_zone() -> None:
    t = timing(
        times=[TimeRange(start="22:00", end="24:00")],
        weekdays=["monday:friday"],
        location="Europe/Berlin",
    )
    rule = only(rules_for(t))
    s = rule.schedule
    assert s is not None
    assert s.repeat is not None and s.repeat.value == "WEEKLY"
    assert [d.value for d in s.weekly_repeat_days] == [
        "MONDAY",
        "TUESDAY",
        "WEDNESDAY",
        "THURSDAY",
        "FRIDAY",
    ]
    assert (s.start_time, s.end_time) == ("2026-11-20T22:00:00", "2026-11-21T00:00:00")
    assert s.time_zone == "Europe/Berlin" and s.end_repeat is None
    assert rule.enabled is False and rule.action_on_window_ended == "DO_NOTHING"
    cond = rule.condition.conditions[0]
    assert (cond.attribute, cond.operator.value) == ("conditionName", "IN")
    assert cond.values == ["cond a", "cond b"]


def test_times_only_is_daily_and_stays_inside_one_day() -> None:
    s = only(rules_for(timing(times=[TimeRange(start="09:00", end="17:30")]))).schedule
    assert s is not None and s.repeat is not None and s.repeat.value == "DAILY"
    assert (s.start_time, s.end_time) == ("2026-11-20T09:00:00", "2026-11-20T17:30:00")
    assert s.time_zone == "UTC"


def test_weekdays_only_mute_the_whole_day() -> None:
    s = only(rules_for(timing(weekdays=["saturday", "sunday"]))).schedule
    assert s is not None
    assert (s.start_time, s.end_time) == ("2026-11-20T00:00:00", "2026-11-21T00:00:00")
    assert [d.value for d in s.weekly_repeat_days] == ["SATURDAY", "SUNDAY"]


def test_several_time_ranges_make_several_rules_with_distinct_names() -> None:
    t = timing(times=[TimeRange(start="01:00", end="02:00"), TimeRange(start="03:00", end="04:00")])
    result = rules_for(t)
    assert len(result.rules) == 2
    assert len({r.name for r in result.rules}) == 2


def test_a_day_of_the_month_starts_on_the_next_such_day() -> None:
    s = only(rules_for(timing(days_of_month=["15"], times=[TimeRange(start="01:00", end="02:00")])))
    assert s.schedule is not None and s.schedule.repeat is not None
    assert s.schedule.repeat.value == "MONTHLY"
    assert s.schedule.start_time == "2026-12-15T01:00:00"


def test_a_year_and_month_span_bound_the_rule_with_an_end_date() -> None:
    t = timing(years=["2027"], months=["march:april"], weekdays=["monday"])
    s = only(rules_for(t)).schedule
    assert s is not None
    assert s.start_time.startswith("2027-03-01")
    assert s.end_repeat == "2027-04-30T23:59:59"


def test_a_span_that_ended_before_today_makes_no_rule_and_is_counted() -> None:
    result = rules_for(timing(years=["2020"], weekdays=["monday"]))
    assert result.rules == [] and result.expired == 1


def test_what_the_plan_calls_manual_stays_manual() -> None:
    t = timing(days_of_month=["-1"])
    plan = mute_timing_to_muting(t)
    result = rules_for(t)
    assert plan.manual == [R.R_MUTE_DAYS] and result.rules == []


def test_names_over_the_value_limit_are_split_over_rules() -> None:
    names = [f"c{i}" for i in range(MAX_VALUES + 1)]
    result = rules_for(timing(weekdays=["monday"]), names=names)
    assert len(result.rules) == 2
    sizes = sorted(len(r.condition.conditions[0].values) for r in result.rules)
    assert sizes == [1, MAX_VALUES]
    assert len({r.name for r in result.rules}) == 2


@pytest.mark.parametrize(
    ("predicate", "attribute", "operator", "values"),
    [
        (
            Predicate(
                attribute="accumulations.tag.team", operator="EXACTLY_MATCHES", values=["a", "b"]
            ),
            "tags.team",
            "IN",
            ["a", "b"],
        ),
        (
            Predicate(
                attribute="accumulations.tag.team", operator="DOES_NOT_EXACTLY_MATCH", values=["a"]
            ),
            "tags.team",
            "NOT_IN",
            ["a"],
        ),
        (
            Predicate(attribute="accumulations.tag.env", operator="STARTS_WITH", values=["prod"]),
            "tags.env",
            "STARTS_WITH",
            ["prod"],
        ),
        (
            Predicate(attribute="accumulations.tag.env", operator="ENDS_WITH", values=["-eu"]),
            "tags.env",
            "ENDS_WITH",
            ["-eu"],
        ),
    ],
)
def test_tag_predicates_scope_the_muting_rule(
    predicate: Predicate, attribute: str, operator: str, values: list[str]
) -> None:
    rule = only(rules_for(timing(weekdays=["monday"]), predicates=[predicate]))
    assert rule.condition.operator == "AND"
    extra = rule.condition.conditions[1]
    assert (extra.attribute, extra.operator.value, extra.values) == (attribute, operator, values)


def test_a_predicate_that_cannot_be_said_stops_the_rule_instead_of_widening_it() -> None:
    wide = Predicate(attribute="accumulations.tag.env", operator="STARTS_WITH", values=["a", "b"])
    other = Predicate(attribute="priority", operator="EQUAL", values=["HIGH"])
    for p in (wide, other):
        result = rules_for(timing(weekdays=["monday"]), predicates=[p])
        assert result.rules == [] and result.unscoped == 1


def test_more_predicates_than_a_rule_can_hold_stop_the_rule() -> None:
    many = [
        Predicate(attribute=f"accumulations.tag.t{i}", operator="EXACTLY_MATCHES", values=["x"])
        for i in range(20)
    ]
    result = rules_for(timing(weekdays=["monday"]), predicates=many)
    assert result.rules == [] and result.unscoped == 1


def test_hostile_timing_names_are_cut_and_made_printable() -> None:
    t = timing(name="bad\x1b[31m`name`\n" + "x" * 500, weekdays=["monday"])
    rule = only(rules_for(t))
    assert "\x1b" not in rule.name and "\n" not in rule.name and len(rule.name) <= 255


def test_the_terraform_and_graphql_output_is_valid_for_the_provider() -> None:
    t = timing(
        name='Quote " and ${x}',
        times=[TimeRange(start="22:00", end="24:00")],
        weekdays=["monday:friday"],
        location="Europe/Berlin",
    )
    rules = rules_for(t, names=['cond "q" ${y}', "b"]).rules
    tf = muting_terraform(rules)
    assert schema_errors(tf) == [] and resource_count(tf) == 1
    assert "${x}" not in tf.replace("$${x}", "")  # escaped by hcl_string
    payload = muting_payload(rules[0])
    assert payload["variables"]["rule"]["schedule"]["weeklyRepeatDays"][0] == "MONDAY"


def test_a_timing_with_no_intervals_makes_no_rule() -> None:
    assert rules_for(MuteTiming(name="empty")).rules == []
