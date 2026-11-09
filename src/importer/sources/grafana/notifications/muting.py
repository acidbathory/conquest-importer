"""Mute timings to muting schedules, for the cases a New Relic muting rule says exactly.

Exact: a time range, weekdays (ranges and wrap-around expanded), a time zone, and a daily or
weekly repeat. Plain positive days of the month (1 to 28) become one schedule per day. Years
bound a schedule; a contiguous run of months inside named years becomes one schedule per year.
Everything else (negative days, days 29 to 31, months with no year, a run of months that wraps
the year end, `Local` as a location) is reported, never approximated. The result has clock times
and recurrence only: the dates a muting rule needs (start_time, end_repeat) are chosen when the
rule is created.

A mute timing is active when any interval matches; within one interval every listed field must
match, so intervals become separate schedules and fields combine inside one.
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel

from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import MuteTiming, TimeInterval
from importer.sources.grafana.notifications.timings import CLOCK, ZONE

MAX_SCHEDULES = 200
MAX_DAY_SCHEDULES = 10
MAX_YEARS = 5
YEAR_RANGE = (1970, 2100)
DAY_NAMES = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]  # fmt: skip
WINDOW_END_ACTION = "DO_NOTHING"  # Grafana does not close alerts when a mute timing ends
N_NO_INTERVALS = "the mute timing has no intervals, so it never mutes"
N_DST = (
    "how a muting rule schedule behaves when the clock changes for daylight saving time is "
    "UNVERIFIED; check the first changeover after the rule is created"
)


class MutingSchedule(BaseModel):
    start_clock: str  # HH:MM, local to time_zone
    end_clock: str
    end_next_day: bool
    repeat: Literal["DAILY", "WEEKLY", "MONTHLY"]
    weekly_repeat_days: list[str] = []
    day_of_month: int | None = None
    time_zone: str = "UTC"
    valid_from_year: int | None = None
    valid_to_year: int | None = None
    from_month: int | None = None
    to_month: int | None = None


class MutingPlan(BaseModel):
    name: str  # the mute timing name: untrusted, show it only through md_cell or hcl_string
    schedules: list[MutingSchedule] = []
    manual: list[str] = []  # fixed reasons for intervals that could not be converted
    notes: list[str] = []
    action_on_window_end: str = WINDOW_END_ACTION


class _Unconvertible(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _clock(text: str, *, end: bool) -> int:
    """Minutes since midnight; 24:00 is allowed as an end only."""
    found = CLOCK.fullmatch(text)
    if not found:
        raise _Unconvertible(R.R_MUTE_TIMES)
    hour, minute = int(found.group(1)), int(found.group(2))
    if minute > 59 or hour > 24 or (hour == 24 and not (end and minute == 0)):
        raise _Unconvertible(R.R_MUTE_TIMES)
    return hour * 60 + minute


def _windows(interval: TimeInterval) -> list[tuple[str, str, bool]]:
    """(start HH:MM, end HH:MM, ends the next day) per time range; one whole day if none."""
    if not interval.times:
        return [("00:00", "00:00", True)]
    found = []
    for span in interval.times:
        start, end = _clock(span.start, end=False), _clock(span.end, end=True)
        if start >= end:
            raise _Unconvertible(R.R_MUTE_TIMES)
        clock = f"{end % 1440 // 60:02d}:{end % 60:02d}"
        found.append((f"{start // 60:02d}:{start % 60:02d}", clock, end == 1440))
    return found


def _index(token: str, names: list[str], low: int, high: int, reason: str) -> int:
    """A name or a number as an index; weekdays count from Sunday = 0, months from 1."""
    text = token.strip().lower()
    if text in names:
        return names.index(text) + (1 if names is MONTH_NAMES else 0)
    if text.isdigit() and low <= int(text) <= high:
        return int(text)
    raise _Unconvertible(reason)


def _expand(tokens: list[str], names: list[str], low: int, high: int, reason: str) -> list[int]:
    """Values of `a`, `a:b` items in order, without repeats; `a:b` may wrap for weekdays."""
    found: list[int] = []
    for token in tokens:
        parts = token.split(":")
        if len(parts) > 2:
            raise _Unconvertible(reason)
        first = _index(parts[0], names, low, high, reason)
        last = _index(parts[1], names, low, high, reason) if len(parts) == 2 else first
        span = (
            range(first, last + 1)
            if first <= last
            else [*range(first, high + 1), *range(low, last + 1)]
        )
        found.extend(v for v in span if v not in found)
    return found


def _weekdays(interval: TimeInterval) -> list[str]:
    days = _expand(interval.weekdays, DAY_NAMES, 0, 6, R.R_MUTE_WEEKDAYS)
    return [DAY_NAMES[d].upper() for d in days]


@lru_cache(maxsize=1)
def _known_zones() -> frozenset[str]:
    return frozenset(available_timezones()) | {"UTC"}


def _zone(interval: TimeInterval) -> str:
    """An IANA zone name as the time zone database spells it (so `utc` and `Local` are not)."""
    if not interval.location:
        return "UTC"
    if len(interval.location) > 64 or not ZONE.fullmatch(interval.location):
        raise _Unconvertible(R.R_MUTE_LOCATION)
    if interval.location not in _known_zones():
        raise _Unconvertible(R.R_MUTE_LOCATION)
    return interval.location


def _month_days(interval: TimeInterval) -> list[int]:
    days: list[int] = []
    for token in interval.days_of_month:
        parts = token.split(":")
        if len(parts) > 2 or not all(p.isdigit() for p in parts):
            raise _Unconvertible(R.R_MUTE_DAYS)
        first, last = int(parts[0]), int(parts[-1])
        if not 1 <= first <= last <= 28:
            raise _Unconvertible(R.R_MUTE_DAYS)
        days.extend(d for d in range(first, last + 1) if d not in days)
    if len(days) > MAX_DAY_SCHEDULES:
        raise _Unconvertible(R.R_MUTE_DAYS)
    return days


def _years(interval: TimeInterval) -> list[int]:
    years: list[int] = []
    for token in interval.years:
        parts = token.split(":")
        if len(parts) > 2 or not all(p.isdigit() for p in parts):
            raise _Unconvertible(R.R_MUTE_MONTHS)
        first, last = int(parts[0]), int(parts[-1])
        if not YEAR_RANGE[0] <= first <= last <= YEAR_RANGE[1]:
            raise _Unconvertible(R.R_MUTE_MONTHS)
        if last - first >= 100:
            raise _Unconvertible(R.R_MUTE_MONTHS)
        years.extend(y for y in range(first, last + 1) if y not in years)
    return sorted(years)


def _month_span(interval: TimeInterval) -> tuple[int, int] | None:
    """(first, last) when the months are one run inside a year; None if none are listed."""
    if not interval.months:
        return None
    months = _expand(interval.months, MONTH_NAMES, 1, 12, R.R_MUTE_MONTHS)
    for token in interval.months:  # a range that wraps the year end is not one span
        parts = token.split(":")
        if len(parts) == 2 and _index(parts[0], MONTH_NAMES, 1, 12, R.R_MUTE_MONTHS) > _index(
            parts[1], MONTH_NAMES, 1, 12, R.R_MUTE_MONTHS
        ):
            raise _Unconvertible(R.R_MUTE_MONTHS)
    ordered = sorted(months)
    if ordered != list(range(ordered[0], ordered[-1] + 1)):
        raise _Unconvertible(R.R_MUTE_MONTHS)
    return ordered[0], ordered[-1]


def _year_bounds(
    years: list[int], span: tuple[int, int] | None
) -> list[tuple[int | None, int | None, int | None, int | None]]:
    """(from year, to year, from month, to month) per schedule."""
    if span is None:
        if not years:
            return [(None, None, None, None)]
        if years != list(range(years[0], years[-1] + 1)):
            raise _Unconvertible(R.R_MUTE_MONTHS)
        return [(years[0], years[-1], None, None)]
    if not years or len(years) > MAX_YEARS:
        raise _Unconvertible(R.R_MUTE_MONTHS)  # every year, or too many years to list
    return [(y, y, span[0], span[1]) for y in years]


def _interval_schedules(interval: TimeInterval) -> list[MutingSchedule]:
    windows, zone = _windows(interval), _zone(interval)
    weekdays = _weekdays(interval)
    days = _month_days(interval)
    bounds = _year_bounds(_years(interval), _month_span(interval))
    if days and weekdays:
        raise _Unconvertible(R.R_MUTE_COMBINED)
    shapes: list[tuple[Literal["DAILY", "WEEKLY", "MONTHLY"], list[str], int | None]] = (
        [("MONTHLY", [], d) for d in days]
        if days
        else [("WEEKLY", weekdays, None)]
        if weekdays
        else [("DAILY", [], None)]
    )
    return [
        MutingSchedule(
            start_clock=start,
            end_clock=end,
            end_next_day=next_day,
            repeat=repeat,
            weekly_repeat_days=on,
            day_of_month=day,
            time_zone=zone,
            valid_from_year=y0,
            valid_to_year=y1,
            from_month=m0,
            to_month=m1,
        )
        for (start, end, next_day) in windows
        for (repeat, on, day) in shapes
        for (y0, y1, m0, m1) in bounds
    ]


def mute_timing_to_muting(timing: MuteTiming) -> MutingPlan:
    """Schedules for the intervals that convert, and a reason for each that does not."""
    plan = MutingPlan(name=timing.name)
    if not timing.intervals:
        plan.notes.append(N_NO_INTERVALS)
    for interval in timing.intervals:
        try:
            found = _interval_schedules(interval)
        except _Unconvertible as exc:
            if exc.reason not in plan.manual:
                plan.manual.append(exc.reason)
            continue
        if len(plan.schedules) + len(found) > MAX_SCHEDULES:
            if R.R_MUTE_TOO_MANY not in plan.manual:
                plan.manual.append(R.R_MUTE_TOO_MANY)  # the whole interval, never a part of it
            continue
        plan.schedules.extend(found)
    if plan.schedules:
        plan.notes.append(N_DST)
    return plan
