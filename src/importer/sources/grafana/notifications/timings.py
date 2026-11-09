"""Read mute timings (time intervals) with per-object validation and caps."""

from typing import Any

from importer.core.text import printable
from importer.sources.grafana.notifications.models import (
    MAX_INTERVALS,
    MAX_MUTE_TIMINGS,
    MAX_NAME,
    MAX_RANGES,
    MuteTiming,
    TimeInterval,
    TimeRange,
    as_dict,
    as_list,
)

MAX_ITEM_CHARS = 40
MAX_LIST = 400


def _strings(value: Any) -> list[str]:
    """Scalars as short strings (a weekday may be written 1 or "monday")."""
    return [
        printable(str(v), MAX_ITEM_CHARS)
        for v in as_list(value)[:MAX_LIST]
        if isinstance(v, str | int) and not isinstance(v, bool)
    ]


def _ranges(value: Any) -> list[TimeRange]:
    found: list[TimeRange] = []
    for item in as_list(value)[:MAX_RANGES]:
        entry = as_dict(item)
        start, end = entry.get("start_time"), entry.get("end_time")
        if isinstance(start, str) and isinstance(end, str):
            found.append(TimeRange(start=printable(start, 8), end=printable(end, 8)))
    return found


def _interval(raw: dict[str, Any]) -> TimeInterval:
    location = raw.get("location")
    return TimeInterval(
        times=_ranges(raw.get("times")),
        weekdays=_strings(raw.get("weekdays")),
        days_of_month=_strings(raw.get("days_of_month")),
        months=_strings(raw.get("months")),
        years=_strings(raw.get("years")),
        location=printable(location, 64) if isinstance(location, str) else "",
    )


def parse_mute_timings(payload: Any) -> list[MuteTiming]:
    """Mute timings from the provisioning list; an entry that is not an object is skipped."""
    found: list[MuteTiming] = []
    for item in as_list(payload)[:MAX_MUTE_TIMINGS]:
        entry = as_dict(item)
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_intervals = as_list(entry.get("time_intervals") or entry.get("intervals"))
        intervals = [_interval(as_dict(i)) for i in raw_intervals[:MAX_INTERVALS]]
        found.append(MuteTiming(name=printable(name, MAX_NAME), intervals=intervals))
    return found
