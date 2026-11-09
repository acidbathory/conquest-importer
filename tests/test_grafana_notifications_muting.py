"""Mute timings to muting schedules: exact cases convert, everything else is reported."""

from typing import Any

import pytest

from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import (
    MuteTiming,
    TimeInterval,
    TimeRange,
)
from importer.sources.grafana.notifications.muting import mute_timing_to_muting

WORKWEEK = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]


def timing(*intervals: dict[str, Any]) -> MuteTiming:
    built = []
    for raw in intervals:
        times = [TimeRange(start=a, end=b) for a, b in raw.get("times", [])]
        built.append(TimeInterval(**{**raw, "times": times}))
    return MuteTiming(name="t", intervals=built)


def only(plan_input: MuteTiming) -> Any:
    plan = mute_timing_to_muting(plan_input)
    assert plan.manual == [], plan.manual
    assert len(plan.schedules) == 1
    return plan.schedules[0]


def test_a_time_range_on_weekdays_is_one_weekly_schedule() -> None:
    s = only(timing({"times": [("08:00", "17:30")], "weekdays": ["monday:friday"]}))
    assert (s.repeat, s.start_clock, s.end_clock, s.end_next_day) == (
        "WEEKLY",
        "08:00",
        "17:30",
        False,
    )
    assert s.weekly_repeat_days == WORKWEEK and s.time_zone == "UTC"


def test_a_range_ending_at_24_00_ends_at_midnight_of_the_next_day() -> None:
    s = only(timing({"times": [("22:00", "24:00")], "weekdays": ["saturday"]}))
    assert (s.start_clock, s.end_clock, s.end_next_day) == ("22:00", "00:00", True)


def test_weekdays_only_covers_whole_days() -> None:
    s = only(timing({"weekdays": ["saturday", "sunday"]}))
    assert (s.repeat, s.start_clock, s.end_clock, s.end_next_day) == (
        "WEEKLY",
        "00:00",
        "00:00",
        True,
    )
    assert s.weekly_repeat_days == ["SATURDAY", "SUNDAY"]


def test_times_only_repeats_daily() -> None:
    s = only(timing({"times": [("01:00", "02:00")]}))
    assert s.repeat == "DAILY" and s.weekly_repeat_days == []


def test_an_interval_with_no_fields_mutes_every_day_all_day() -> None:
    s = only(timing({}))
    assert (s.repeat, s.start_clock, s.end_clock, s.end_next_day) == (
        "DAILY",
        "00:00",
        "00:00",
        True,
    )


def test_several_time_ranges_become_one_schedule_each() -> None:
    plan = mute_timing_to_muting(
        timing({"times": [("00:00", "06:00"), ("22:00", "24:00")], "weekdays": ["monday"]})
    )
    assert [(s.start_clock, s.end_clock) for s in plan.schedules] == [
        ("00:00", "06:00"),
        ("22:00", "00:00"),
    ]
    assert plan.manual == []


@pytest.mark.parametrize(
    ("weekdays", "expected"),
    [
        (["friday:monday"], ["FRIDAY", "SATURDAY", "SUNDAY", "MONDAY"]),
        (["0", "6"], ["SUNDAY", "SATURDAY"]),
        (["1:3"], ["MONDAY", "TUESDAY", "WEDNESDAY"]),
        (["Monday", "MONDAY", "monday"], ["MONDAY"]),
        (["wednesday", "monday"], ["WEDNESDAY", "MONDAY"]),
    ],
)
def test_weekday_spellings(weekdays: list[str], expected: list[str]) -> None:
    s = only(timing({"weekdays": weekdays}))
    assert s.weekly_repeat_days == expected


@pytest.mark.parametrize(
    "weekdays", [["7"], ["funday"], ["monday:"], [":friday"], ["1:2:3"], ["-1"]]
)
def test_bad_weekdays_are_reported(weekdays: list[str]) -> None:
    plan = mute_timing_to_muting(timing({"weekdays": weekdays}))
    assert plan.schedules == [] and plan.manual == [R.R_MUTE_WEEKDAYS]


@pytest.mark.parametrize(
    "span",
    [
        ("10:00", "10:00"),
        ("11:00", "10:00"),
        ("24:00", "24:00"),
        ("9", "10:00"),
        ("25:00", "26:00"),
        ("10:60", "11:00"),
        ("ab:cd", "11:00"),
    ],
)
def test_bad_time_ranges_are_reported(span: tuple[str, str]) -> None:
    plan = mute_timing_to_muting(timing({"times": [span]}))
    assert plan.schedules == [] and plan.manual == [R.R_MUTE_TIMES]


def test_location_becomes_the_time_zone_and_is_validated_without_a_lookup() -> None:
    assert (
        only(timing({"times": [("01:00", "02:00")], "location": "Europe/Berlin"})).time_zone
        == "Europe/Berlin"
    )
    assert only(timing({"times": [("01:00", "02:00")], "location": "UTC"})).time_zone == "UTC"
    for bad in ("Local", "../../etc/passwd", "a b", "x" * 80, "Europe/Berlin\n"):
        plan = mute_timing_to_muting(timing({"times": [("01:00", "02:00")], "location": bad}))
        assert plan.schedules == [] and plan.manual == [R.R_MUTE_LOCATION]


@pytest.mark.parametrize(
    ("days", "expected"),
    [(["1"], [1]), (["15", "1"], [15, 1]), (["1:3"], [1, 2, 3]), (["28"], [28])],
)
def test_plain_positive_days_of_the_month_become_one_schedule_per_day(
    days: list[str], expected: list[int]
) -> None:
    plan = mute_timing_to_muting(timing({"days_of_month": days, "times": [("01:00", "02:00")]}))
    assert plan.manual == []
    assert [s.day_of_month for s in plan.schedules] == expected
    assert all(s.repeat == "MONTHLY" for s in plan.schedules)


@pytest.mark.parametrize("days", [["-1"], ["1:-1"], ["29"], ["31"], ["0"], ["x"], ["1:20"]])
def test_other_days_of_the_month_are_manual(days: list[str]) -> None:
    plan = mute_timing_to_muting(timing({"days_of_month": days}))
    assert plan.schedules == [] and plan.manual == [R.R_MUTE_DAYS]


def test_days_of_the_month_with_weekdays_cannot_be_one_muting_rule() -> None:
    plan = mute_timing_to_muting(timing({"days_of_month": ["1"], "weekdays": ["monday"]}))
    assert plan.schedules == [] and plan.manual == [R.R_MUTE_COMBINED]


def test_years_alone_bound_the_schedule() -> None:
    s = only(timing({"times": [("01:00", "02:00")], "years": ["2026:2028"]}))
    assert (s.valid_from_year, s.valid_to_year) == (2026, 2028)


def test_a_contiguous_month_span_in_one_year_is_bounded() -> None:
    s = only(timing({"weekdays": ["saturday"], "months": ["june:august"], "years": ["2026"]}))
    assert (s.valid_from_year, s.valid_to_year, s.from_month, s.to_month) == (2026, 2026, 6, 8)


def test_a_month_span_over_several_years_is_one_schedule_per_year() -> None:
    plan = mute_timing_to_muting(timing({"months": ["12"], "years": ["2026:2027"]}))
    assert [(s.valid_from_year, s.from_month, s.to_month) for s in plan.schedules] == [
        (2026, 12, 12),
        (2027, 12, 12),
    ]


@pytest.mark.parametrize(
    "fields",
    [
        {"months": ["june"]},  # every year: no yearly recurrence
        {"months": ["november:february"], "years": ["2026"]},  # wraps the year end
        {"months": ["1", "3"], "years": ["2026"]},  # not contiguous
        {"months": ["13"], "years": ["2026"]},
        {"years": ["2026:2200"]},
        {"years": ["0"]},
        {"months": ["june"], "years": ["2000:2100"]},
    ],
)
def test_other_months_and_years_are_manual(fields: dict[str, Any]) -> None:
    plan = mute_timing_to_muting(timing(fields))
    assert plan.schedules == [] and plan.manual == [R.R_MUTE_MONTHS]


def test_a_timing_with_several_intervals_keeps_the_ones_that_convert() -> None:
    plan = mute_timing_to_muting(
        timing({"weekdays": ["monday"]}, {"days_of_month": ["-1"]}, {"times": [("01:00", "02:00")]})
    )
    assert [s.repeat for s in plan.schedules] == ["WEEKLY", "DAILY"]
    assert plan.manual == [R.R_MUTE_DAYS]


def test_a_timing_with_no_intervals_mutes_nothing() -> None:
    plan = mute_timing_to_muting(timing())
    assert plan.schedules == [] and plan.manual == []
    assert any("no intervals" in n for n in plan.notes)


def test_every_schedule_is_a_muting_rule_that_does_not_close_issues() -> None:
    plan = mute_timing_to_muting(timing({"weekdays": ["monday"]}))
    assert plan.action_on_window_end == "DO_NOTHING"


def test_the_name_and_hostile_text_never_reach_notes_or_reasons() -> None:
    hostile = MuteTiming(
        name="SECRET`name|",
        intervals=[
            TimeInterval(weekdays=["SECRET-day"], location="SECRET-zone"),
            TimeInterval(days_of_month=["SECRET"], months=["SECRET"]),
        ],
    )
    plan = mute_timing_to_muting(hostile)
    text = " ".join([*plan.manual, *plan.notes])
    assert "SECRET" not in text


def test_a_large_timing_stays_bounded() -> None:
    big = MuteTiming(
        name="t",
        intervals=[
            TimeInterval(
                times=[TimeRange(start="01:00", end="02:00")] * 100,
                weekdays=["monday"] * 400,
            )
        ]
        * 100,
    )
    plan = mute_timing_to_muting(big)
    assert len(plan.schedules) <= 200
