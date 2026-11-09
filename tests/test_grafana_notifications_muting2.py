"""Mute timings, round 2: real time zones, no silent truncation, one definition of the patterns."""

import pytest

from importer.sources.grafana.notifications import muting, timings
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import MuteTiming, TimeInterval, TimeRange


def interval(**kw: object) -> TimeInterval:
    return TimeInterval(**kw)  # type: ignore[arg-type]


def plan(*intervals: TimeInterval) -> muting.MutingPlan:
    return muting.mute_timing_to_muting(MuteTiming(name="t", intervals=list(intervals)))


@pytest.mark.parametrize("zone", ["UTC", "Europe/Berlin", "America/New_York", "Asia/Tokyo"])
def test_real_zones_are_accepted(zone: str) -> None:
    result = plan(interval(weekdays=["monday"], location=zone))
    assert result.manual == [] and result.schedules[0].time_zone == zone


@pytest.mark.parametrize(
    "zone", ["Not/AZone", "utc", "europe/berlin", "Local", "Europe/Berlino", "UTC+1", "Etc"]
)
def test_made_up_lower_case_and_local_zones_are_reported(zone: str) -> None:
    result = plan(interval(weekdays=["monday"], location=zone))
    assert result.schedules == [] and result.manual == [R.R_MUTE_LOCATION]


def test_a_cut_at_the_schedule_cap_makes_the_whole_interval_manual() -> None:
    many = interval(
        times=[TimeRange(start="01:00", end="02:00")] * 40, days_of_month=["1:8"]
    )  # 40 ranges x 8 days = 320 schedules
    small = interval(weekdays=["monday"])
    result = plan(many, small)
    assert result.manual == [R.R_MUTE_TOO_MANY]
    assert len(result.schedules) == 1 and result.schedules[0].repeat == "WEEKLY"


def test_an_interval_that_just_fits_is_kept_whole() -> None:
    fits = interval(times=[TimeRange(start="01:00", end="02:00")] * 20, days_of_month=["1:8"])
    result = plan(fits)
    assert result.manual == [] and len(result.schedules) == 160


def test_the_cap_counts_the_schedules_of_earlier_intervals() -> None:
    a = interval(times=[TimeRange(start="01:00", end="02:00")] * 19, days_of_month=["1:8"])  # 152
    b = interval(times=[TimeRange(start="03:00", end="04:00")] * 7, days_of_month=["1:8"])  # 56
    result = plan(a, b)
    assert len(result.schedules) == 152 and result.manual == [R.R_MUTE_TOO_MANY]


def test_dst_handling_is_noted_as_unverified_when_there_are_schedules() -> None:
    assert muting.N_DST in plan(interval(weekdays=["monday"])).notes
    assert muting.N_DST not in plan(interval(days_of_month=["-1"])).notes
    assert "UNVERIFIED" in muting.N_DST


def test_the_patterns_have_one_definition() -> None:
    assert muting.ZONE is timings.ZONE and muting.CLOCK is timings.CLOCK
