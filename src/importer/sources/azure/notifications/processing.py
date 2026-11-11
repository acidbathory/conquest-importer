"""Alert processing rules to New Relic muting rules.

Only suppression (`RemoveAllActionGroups`) has a New Relic equivalent: a muting rule withholds the
notification of the matching issues while the event is still recorded. `AddActionGroups` adds
destinations to alerts, which a muting rule cannot do; it stays manual with the workflow workaround.

Scope and conditions are evaluated against the converted conditions here, in Python, so the muting
rule ends up with one exact `conditionName IN [...]` list (at most 500 names per rule; more names
are split into parts). That avoids depending on tags reaching alert events. Tag-valued fields
(severity, monitor service, resource type, resource group, resource) can be evaluated only when the
converted conditions carry the tag (see `link.py`); otherwise the rule is manual. A rule that
muted more than the Azure rule would be wrong in the quiet direction, so anything uncertain is
manual or leaves a condition unmuted.

Schedules: a one-off window is exact; every recurrence becomes its own muting rule (daily, weekly
with weekdays, monthly with one rule per day of the month). The first window starts at the later
of `today` and `effectiveFrom`; `effectiveUntil` is the end of the repetition. The window end does
nothing (`DO_NOTHING`), the closest match to Azure's permanent suppression (UNVERIFIED mapping).
Rules are created disabled.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from importer.core.emit.notifications import converted_condition_names
from importer.core.ir import IRMonitor
from importer.core.muting_ir import (
    MAX_NAME,
    MAX_VALUES,
    MutingCondition,
    MutingGroup,
    MutingOperator,
    MutingRule,
    MutingSchedule,
    Repeat,
    Weekday,
)
from importer.core.text import printable
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications.link import (
    RESOURCE_GROUP_TAG,
    RESOURCE_TAG,
    RESOURCE_TYPE_TAG,
    SERVICE_TAG,
    SEVERITY_TAG,
    action_group_key,
    tag_values,
)
from importer.sources.azure.notifications.models import ProcessingRule, Recurrence, RuleSchedule
from importer.sources.azure.notifications.timezones import to_iana

DESCRIPTION = "Imported from an Azure alert processing rule. Disabled until it has been reviewed."
SUPPRESS = "RemoveAllActionGroups"
ADD = "AddActionGroups"
MAX_UNROLL = 31
LOCAL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?")
CLOCK = re.compile(r"\d{2}:\d{2}(?::\d{2})?")
SUB_SCOPE = re.compile(r"^/subscriptions/([^/]+)$", re.IGNORECASE)
GROUP_SCOPE = re.compile(r"^/subscriptions/([^/]+)/resourcegroups/([^/]+)$", re.IGNORECASE)
RESOURCE_SCOPE = re.compile(
    r"^/subscriptions/[^/]+/resourcegroups/[^/]+/providers/.+$", re.IGNORECASE
)
# Azure condition field -> the tag the converted conditions must carry
TAG_FIELDS = {
    "severity": SEVERITY_TAG,
    "monitorservice": SERVICE_TAG,
    "targetresourcetype": RESOURCE_TYPE_TAG,
    "targetresourcegroup": RESOURCE_GROUP_TAG,
    "targetresource": RESOURCE_TAG,
}
NAME_FIELDS = {"alertrulename", "alertruleid"}
OPERATORS = {"equals", "notequals", "contains", "doesnotcontain"}
WEEKDAYS = {d.value.lower(): d for d in Weekday}
WEEKDAY_INDEX = {d: i for i, d in enumerate(Weekday)}  # Monday is 0, like date.weekday()

STEPS: dict[str, str] = {
    R.R_RULE_ADD: (
        "A muting rule only withholds notifications; it cannot add destinations. For each "
        "action group the rule adds, create a New Relic workflow whose issue filter matches the "
        "same alerts (the conditions this rule's scope and conditions select, for example by "
        "condition name) and whose destinations are those of the action group. The report lists "
        "the action groups."
    ),
    R.R_RULE_LEGACY: (
        "The 2019 preview action rule API is retired. Recreate the rule as an alert processing "
        "rule in Azure, export again, and convert again; or create the muting rule by hand."
    ),
    R.R_RULE_ACTION: "Create the matching muting rule or workflow by hand.",
    R.R_RULE_FIELD: (
        "New Relic carries nothing to match on for this field (monitor condition Fired or "
        "Resolved, signal type, description, alert context). Narrow the muting rule by hand "
        "once the converted conditions are known, or accept the wider rule."
    ),
    R.R_RULE_OPERATOR: "Create the muting rule by hand with the conditions that apply.",
    R.R_RULE_TAG: (
        "The converted conditions do not carry the tag this condition or scope needs (severity, "
        "monitor service, resource type, resource group or resource; docs/dev/"
        "azure-notification-link.md). Have the alert conversion write the tag, or list the "
        "condition names by hand in a muting rule."
    ),
    R.R_RULE_SCOPE: "Create the muting rule by hand for the conditions in this scope.",
    R.R_RULE_ZONE: "Use an IANA time zone name (for example Europe/Berlin) in a muting rule.",
    R.R_RULE_SCHEDULE: (
        "Create the muting rule by hand: it needs a start and end time in one time zone, "
        "optionally repeating daily, weekly or monthly."
    ),
}


class RuleOutcome(StrEnum):
    CONVERTED = "converted"  # at least one muting rule is planned
    MANUAL = "manual"
    NOTHING = "nothing"  # converted correctly, but there is nothing to mute (no match, expired)


@dataclass(frozen=True)
class MonitorIndex:
    """The converted conditions of the alert conversion, by their New Relic condition name."""

    monitors: tuple[IRMonitor, ...]
    names: dict[int, str]

    @classmethod
    def build(cls, monitors: list[IRMonitor]) -> "MonitorIndex":
        names = converted_condition_names(monitors)
        return cls(tuple(m for m in monitors if m.source_id in names), names)


@dataclass
class RuleResult:
    name: str
    azure_enabled: bool
    outcome: RuleOutcome
    rules: list[MutingRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    action_groups: list[str] = field(default_factory=list)  # keys, for AddActionGroups
    matched: int = 0  # converted conditions the rule covers


class _Manual(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Nothing(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _holds(operator: str, seen: str, values: list[str]) -> bool:
    low, wanted = seen.lower(), [v.lower() for v in values]
    return {
        "equals": low in wanted,
        "notequals": low not in wanted,
        "contains": any(w in low for w in wanted),
        "doesnotcontain": all(w not in low for w in wanted),
    }[operator]


def _test(operator: str, seen: list[str], values: list[str]) -> bool:
    """Positive operators need one value to hold, negative ones all. No value: no match."""
    if not seen:
        return False
    check = all if operator in ("notequals", "doesnotcontain") else any
    return bool(check(_holds(operator, s, values) for s in seen))


def _scope_matches(scope: str, m: IRMonitor, notes: set[str]) -> bool:
    text = scope.strip()
    if SUB_SCOPE.match(text):
        notes.add(R.R_RULE_SCOPE_RULE_SUB)
        return m.source_key.lower().startswith(text.lower() + "/")
    group = GROUP_SCOPE.match(text)
    if group:
        return any(v.lower() == group.group(2).lower() for v in tag_values(m, RESOURCE_GROUP_TAG))
    if RESOURCE_SCOPE.match(text):
        return any(
            v.lower() == text.lower() or v.lower().startswith(text.lower() + "/")
            for v in tag_values(m, RESOURCE_TAG)
        )
    raise _Manual(R.R_RULE_SCOPE)


def _need_tag(index: MonitorIndex, tag: str) -> None:
    if not any(tag_values(m, tag) for m in index.monitors):
        raise _Manual(R.R_RULE_TAG)


def _matched(rule: ProcessingRule, index: MonitorIndex, notes: set[str]) -> list[IRMonitor]:
    if not rule.scopes:
        raise _Manual(R.R_RULE_SCOPE)
    for scope in rule.scopes:
        if GROUP_SCOPE.match(scope.strip()):
            _need_tag(index, RESOURCE_GROUP_TAG)
        elif RESOURCE_SCOPE.match(scope.strip()):
            _need_tag(index, RESOURCE_TAG)
    chosen = [m for m in index.monitors if any(_scope_matches(s, m, notes) for s in rule.scopes)]
    for c in rule.conditions:
        field_name, operator = c.field.lower(), c.operator.lower()
        if field_name not in NAME_FIELDS and field_name not in TAG_FIELDS:
            raise _Manual(R.R_RULE_FIELD)
        if operator not in OPERATORS or not c.values:
            raise _Manual(R.R_RULE_OPERATOR)
        if field_name in TAG_FIELDS:
            _need_tag(index, TAG_FIELDS[field_name])
            chosen = [
                m
                for m in chosen
                if _test(operator, tag_values(m, TAG_FIELDS[field_name]), c.values)
            ]
        else:
            by_name = field_name == "alertrulename"
            chosen = [
                m
                for m in chosen
                if _test(operator, [m.name if by_name else m.source_key], c.values)
            ]
    return chosen


def _local(text: str) -> datetime:
    if not LOCAL.fullmatch(text):
        raise _Manual(R.R_RULE_SCHEDULE)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        raise _Manual(R.R_RULE_SCHEDULE) from None


def _clock(text: str) -> time:
    if not CLOCK.fullmatch(text):
        raise _Manual(R.R_RULE_SCHEDULE)
    try:
        return time.fromisoformat(text)
    except ValueError:
        raise _Manual(R.R_RULE_SCHEDULE) from None


def _stamp(day: date, clock: time) -> str:
    return f"{day.isoformat()}T{clock.strftime('%H:%M:%S')}"


def _first_day(start: date, weekdays: set[int] | None, day_of_month: int | None) -> date | None:
    for step in range(800):
        d = start + timedelta(days=step)
        if weekdays is not None and d.weekday() in weekdays:
            return d
        if day_of_month is not None and d.day == day_of_month:
            return d
    return None


def _recurrence_windows(
    rec: Recurrence, base: date, until: datetime | None, zone: str
) -> list[tuple[str, MutingSchedule]]:
    both = (bool(rec.start_time), bool(rec.end_time))
    if both == (True, False) or both == (False, True):
        raise _Manual(R.R_RULE_SCHEDULE)
    start_c = _clock(rec.start_time) if rec.start_time else time(0, 0)
    end_c = _clock(rec.end_time) if rec.end_time else time(0, 0)
    spill = timedelta(days=1 if end_c <= start_c else 0)  # a window past midnight, or all day
    kind = rec.type.lower()
    end_repeat = _stamp(until.date(), until.time()) if until is not None else None
    starts: list[tuple[str, date | None, Repeat, list[Weekday]]] = []
    if kind == "daily":
        starts.append(("daily", base, Repeat.DAILY, []))
    elif kind == "weekly":
        days = [WEEKDAYS.get(d.strip().lower()) for d in rec.days_of_week]
        if not days or None in days:
            raise _Manual(R.R_RULE_SCHEDULE)
        chosen = sorted({d for d in days if d is not None}, key=lambda d: WEEKDAY_INDEX[d])
        first = _first_day(base, {WEEKDAY_INDEX[d] for d in chosen}, None)
        starts.append(("weekly", first, Repeat.WEEKLY, chosen))
    elif kind == "monthly":
        wanted = sorted(set(rec.days_of_month))
        if not wanted or len(wanted) > MAX_UNROLL or any(not 1 <= d <= 31 for d in wanted):
            raise _Manual(R.R_RULE_SCHEDULE)
        starts += [(f"day {d}", _first_day(base, None, d), Repeat.MONTHLY, []) for d in wanted]
    else:
        raise _Manual(R.R_RULE_SCHEDULE)
    out: list[tuple[str, MutingSchedule]] = []
    for label, first, repeat, weekdays in starts:
        if first is None:
            raise _Manual(R.R_RULE_SCHEDULE)
        if until is not None and first > until.date():
            continue  # this part ended before it could start
        try:
            out.append(
                (
                    label,
                    MutingSchedule(
                        start_time=_stamp(first, start_c),
                        end_time=_stamp(first + spill, end_c),
                        time_zone=zone,
                        repeat=repeat,
                        end_repeat=end_repeat,
                        weekly_repeat_days=weekdays,
                    ),
                )
            )
        except ValueError:
            raise _Manual(R.R_RULE_SCHEDULE) from None
    return out


def _windows(schedule: RuleSchedule | None, today: date) -> list[tuple[str, MutingSchedule | None]]:
    """(label, schedule) per muting rule. One entry with no schedule means always active."""
    if schedule is None or not (
        schedule.effective_from or schedule.effective_until or schedule.recurrences
    ):
        return [("", None)]
    zone = to_iana(schedule.time_zone)
    if zone is None:
        raise _Manual(R.R_RULE_ZONE)
    frm = _local(schedule.effective_from) if schedule.effective_from else None
    until = _local(schedule.effective_until) if schedule.effective_until else None
    if until is not None and until.date() < today:
        raise _Nothing(R.R_RULE_EXPIRED)
    if not schedule.recurrences:
        if frm is None or until is None:
            raise _Manual(R.R_RULE_SCHEDULE)
        try:
            window = MutingSchedule(
                start_time=_stamp(frm.date(), frm.time()),
                end_time=_stamp(until.date(), until.time()),
                time_zone=zone,
            )
        except ValueError:
            raise _Manual(R.R_RULE_SCHEDULE) from None
        return [("", window)]
    base = max(today, frm.date()) if frm else today
    found: list[tuple[str, MutingSchedule | None]] = []
    for i, rec in enumerate(schedule.recurrences, start=1):
        for label, window in _recurrence_windows(rec, base, until, zone):
            found.append((f"recurrence {i}, {label}" if label else f"recurrence {i}", window))
    if not found:
        raise _Nothing(R.R_RULE_EXPIRED)
    return found


def _name(prefix: str, title: str, tail: str) -> str:
    suffix = f" ({tail})" if tail else ""
    return printable(f"{prefix} {title}", MAX_NAME - len(suffix)) + suffix


def _parts(names: list[str]) -> list[list[str]]:
    return [names[i : i + MAX_VALUES] for i in range(0, len(names), MAX_VALUES)]


def _suppress(rule: ProcessingRule, index: MonitorIndex, prefix: str, today: date) -> RuleResult:
    result = RuleResult(rule.name, rule.enabled, RuleOutcome.CONVERTED)
    notes: set[str] = set()
    try:
        chosen = _matched(rule, index, notes)
        result.matched = len(chosen)
        if not chosen:
            raise _Nothing(R.R_RULE_NO_MATCH)
        windows = _windows(rule.schedule, today)
    except _Nothing as stop:
        result.outcome = RuleOutcome.NOTHING
        result.reasons.append(stop.reason)
        return result
    names = sorted({index.names[m.source_id] for m in chosen})
    parts = _parts(names)
    for label, window in windows:
        for number, chunk in enumerate(parts, start=1):
            tail = ", ".join(x for x in (label, f"part {number}" if len(parts) > 1 else "") if x)
            result.rules.append(
                MutingRule(
                    name=_name(prefix, rule.name, tail),
                    description=DESCRIPTION,
                    condition=MutingGroup(
                        operator="AND",
                        conditions=[
                            MutingCondition(
                                attribute="conditionName",
                                operator=MutingOperator.IN,
                                values=chunk,
                            )
                        ],
                    ),
                    schedule=window,
                    action_on_window_ended="DO_NOTHING",
                )
            )
    if len(result.rules) > 1:
        result.reasons.append(R.R_RULE_SEVERAL)
    if windows[0][1] is None and windows[0][0] == "":
        result.reasons.append(R.R_RULE_ALWAYS)
    result.reasons += sorted(notes)
    return result


def convert_rule(rule: ProcessingRule, index: MonitorIndex, prefix: str, today: date) -> RuleResult:
    """One alert processing rule: muting rules, or the reason it stays manual."""

    def manual(reason: str, groups: list[str] | None = None) -> RuleResult:
        steps = [STEPS[reason]] if reason in STEPS else []
        return RuleResult(
            rule.name,
            rule.enabled,
            RuleOutcome.MANUAL,
            reasons=[reason],
            steps=steps,
            action_groups=groups or [],
        )

    if rule.legacy:
        return manual(R.R_RULE_LEGACY)
    kinds = {a.type for a in rule.actions}
    if ADD in kinds:
        keys = sorted({action_group_key(i) for a in rule.actions for i in a.action_group_ids})
        return manual(R.R_RULE_ADD, keys)
    if kinds != {SUPPRESS}:
        return manual(R.R_RULE_ACTION)
    try:
        return _suppress(rule, index, prefix, today)
    except _Manual as stop:
        return manual(stop.reason)
