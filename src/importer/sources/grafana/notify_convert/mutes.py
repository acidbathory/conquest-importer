"""Mute timings to New Relic muting rules, for one route.

The exact cases come from `notifications.muting` (times, weekdays, a time zone, plain days of the
month, a run of months inside named years). This module chooses the dates that muting rules need
and the scope:

- the first window starts today (or on the first day of the bound, if it is later), at the
  clock time of the schedule, in the schedule's zone; a monthly schedule starts on its day;
- a schedule with a year bound ends at the end of the last bound month; one without a bound
  repeats until someone deletes the rule (that a rule with neither an end date nor a count is
  accepted is UNVERIFIED);
- the rule mutes the conditions the route carries (`conditionName IN names`, 500 values per rule)
  and, when the route filters on tags, the same tags (`tags.<label>`, UNVERIFIED to be the same
  attribute as the issue tag). A predicate that cannot be said as a muting condition stops the
  rule: a rule that muted more than the route would be wrong in the quiet direction.
- the window end does nothing (`DO_NOTHING`): Grafana does not close alerts when a mute ends;
- rules are created disabled, like every object here.

Names are untrusted: they go through `printable` and, on output, `hcl_string` and GraphQL
variables.
"""

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from importer.core.muting_ir import (
    MAX_CONDITIONS,
    MAX_NAME,
    MAX_VALUES,
    MutingCondition,
    MutingGroup,
    MutingOperator,
    MutingRule,
    Repeat,
    Weekday,
)
from importer.core.muting_ir import MutingSchedule as NrSchedule
from importer.core.text import printable
from importer.sources.grafana.notifications.muting import MutingPlan
from importer.sources.grafana.notifications.muting import MutingSchedule as GfSchedule
from importer.sources.grafana.notifications.policy import Predicate

TAG = "accumulations.tag."
DESCRIPTION = "Imported from a Grafana mute timing. Disabled until it has been reviewed."
# issue-filter operator -> (muting operator, takes a list of values)
OPERATORS = {
    "EXACTLY_MATCHES": (MutingOperator.IN, True),
    "DOES_NOT_EXACTLY_MATCH": (MutingOperator.NOT_IN, True),
    "STARTS_WITH": (MutingOperator.STARTS_WITH, False),
    "ENDS_WITH": (MutingOperator.ENDS_WITH, False),
}


@dataclass
class MuteResult:
    rules: list[MutingRule] = field(default_factory=list)
    expired: int = 0  # schedules whose bound ended before today: nothing to create
    unscoped: int = 0  # schedules left out because the route's filter cannot be a muting scope


def _last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _first_day(s: GfSchedule, today: date) -> tuple[date, date | None]:
    """(first date the window can start, last date the rule may repeat) for a schedule."""
    lower = upper = None
    if s.valid_from_year is not None and s.valid_to_year is not None:
        lower = date(s.valid_from_year, s.from_month or 1, 1)
        upper = _last_day(s.valid_to_year, s.to_month or 12)
    start = max(today, lower) if lower else today
    if s.day_of_month is not None and start.day > s.day_of_month:
        month_start = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        start = month_start
    if s.day_of_month is not None:
        start = start.replace(day=s.day_of_month)
    return start, upper


def _schedule(s: GfSchedule, today: date) -> NrSchedule | None:
    start, upper = _first_day(s, today)
    if upper is not None and start > upper:
        return None
    end = start + timedelta(days=1 if s.end_next_day else 0)
    repeat = Repeat(s.repeat)
    return NrSchedule(
        start_time=f"{start.isoformat()}T{s.start_clock}:00",
        end_time=f"{end.isoformat()}T{s.end_clock}:00",
        time_zone=s.time_zone,
        repeat=repeat,
        end_repeat=f"{upper.isoformat()}T23:59:59" if upper else None,
        weekly_repeat_days=[Weekday(d) for d in s.weekly_repeat_days]
        if repeat is Repeat.WEEKLY
        else [],
    )


def _scope(predicates: Sequence[Predicate]) -> list[MutingCondition] | None:
    """The route's tag predicates as muting conditions, or None if any cannot be said."""
    found: list[MutingCondition] = []
    for p in predicates:
        mapped = OPERATORS.get(p.operator)
        if mapped is None or not p.attribute.startswith(TAG):
            return None
        operator, listy = mapped
        if not listy and len(p.values) != 1:
            return None
        found.append(
            MutingCondition(
                attribute="tags." + p.attribute[len(TAG) :], operator=operator, values=p.values
            )
        )
    return found


def _chunks(names: Sequence[str]) -> list[list[str]]:
    ordered = sorted(set(names))
    return [ordered[i : i + MAX_VALUES] for i in range(0, len(ordered), MAX_VALUES)]


def _name(prefix: str, timing: str, label: str, index: int, part: int, parts: int) -> str:
    tail = f" ({label}, schedule {index}" + (f", part {part}" if parts > 1 else "") + ")"
    return printable(f"{prefix} mute {timing}", MAX_NAME - len(tail)) + tail


def muting_rules(
    plan: MutingPlan,
    *,
    names: Sequence[str],
    predicates: Sequence[Predicate],
    label: str,
    prefix: str,
    today: date,
) -> MuteResult:
    """The muting rules for one mute timing on one route."""
    result = MuteResult()
    scope = _scope(predicates)
    if scope is not None and len(scope) + 1 > MAX_CONDITIONS:
        scope = None
    for index, gf in enumerate(plan.schedules, start=1):
        schedule = _schedule(gf, today)
        if schedule is None:
            result.expired += 1
        elif scope is None:
            result.unscoped += 1
        else:
            parts = _chunks(names)
            for part, chunk in enumerate(parts, start=1):
                by_name = MutingCondition(
                    attribute="conditionName", operator=MutingOperator.IN, values=chunk
                )
                result.rules.append(
                    MutingRule(
                        name=_name(prefix, plan.name, label, index, part, len(parts)),
                        description=DESCRIPTION,
                        condition=MutingGroup(operator="AND", conditions=[by_name, *scope]),
                        schedule=schedule,
                        action_on_window_ended="DO_NOTHING",  # Grafana does not close on mute end
                    )
                )
    return result
