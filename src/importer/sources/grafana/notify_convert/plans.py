"""Routes to notification plans: workflows, their destinations, muting rules and the limits.

One route gives one or more workflows. Each workflow has its own copy of the route's
destinations, because a New Relic channel can serve one workflow only. A route is split when:

- its condition names do not fit one 4,096 character issue filter (the filter is measured with
  `issue_filter_size`, the same function `plan_violations` uses);
- some of its destinations had resolve messages switched off in Grafana (that is a trigger list,
  and a workflow has one list).

A route that has a mute timing converts it with `mutes.muting_rules`; the workflows of that route
then use the strict muting handling, so the account-wide muting rule is honoured by them and
only by them. Everything the importer could not say stays a reason on the route.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from importer.core.muting_ir import MutingRule
from importer.core.notification_ir import (
    TAG_NAME,
    ManualDestination,
    NotificationPlan,
    NrDestination,
    PlannedPredicate,
    PlannedWorkflow,
    issue_filter_size,
    tag_predicate,
)
from importer.core.text import printable
from importer.sources.grafana.notifications.muting import MutingPlan
from importer.sources.grafana.notify_convert import mutes
from importer.sources.grafana.notify_convert import reasons as C
from importer.sources.grafana.notify_convert.destinations import Point
from importer.sources.grafana.notify_convert.routes import Route

MAX_NAME = 120
FILTER_LIMIT = 4096
STRICT = "DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES"
NO_CLOSE = ("ACTIVATED", "ACKNOWLEDGED", "INVESTIGATING", "PRIORITY_CHANGED", "OTHER_UPDATES")
TAG = "accumulations.tag."
NOTE_RESOLVE = (
    "resolve messages are switched off for the destinations of this workflow in Grafana, so it "
    "does not notify when an issue closes"
)
NOTE_PREFERENCE = {
    "PER_CONDITION_AND_TARGET": (
        "Grafana groups by every label; the closest New Relic incident preference is "
        "PER_CONDITION_AND_TARGET, which is set on the policy that holds the conditions"
    ),
    "": (
        "the Grafana grouping labels have no New Relic incident preference; the conditions use "
        "the preference of their policy"
    ),
}
NOTE_DROPPED = (
    "{count} other integration(s) of this receiver have no New Relic equivalent and were not "
    "converted; the report lists them"
)
NOTE_MUTE_SCOPE = (
    "the muting rule covers these conditions in every workflow; only workflows with the strict "
    "muting handling honour it"
)


@dataclass
class RouteOutcome:
    """What one route became, for the report. The receiver is untrusted: show it via md_cell."""

    number: int
    receiver: str
    rules: int
    workflows: int = 0
    destinations: int = 0
    manual: int = 0
    skipped: int = 0
    muting_rules: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlanSet:
    plans: list[NotificationPlan] = field(default_factory=list)
    muting_rules: list[MutingRule] = field(default_factory=list)
    outcomes: list[RouteOutcome] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    routes_without_destination: int = 0
    used_timings: set[str] = field(default_factory=set)
    expired_schedules: int = 0
    unscoped_schedules: int = 0


def _add(into: list[str], items: Sequence[str]) -> None:
    into.extend(i for i in items if i not in into)


def _predicates(route: Route, names: list[str]) -> list[PlannedPredicate]:
    """The workflow's predicates. With tag predicates, the condition names are one of them (a
    workflow that sets predicates no longer filters on the condition names by itself)."""
    if not route.predicates:
        return []
    tags = [
        tag_predicate(p.attribute[len(TAG) :], p.values)
        if p.operator == "EXACTLY_MATCHES"
        and p.attribute.startswith(TAG)
        and TAG_NAME.fullmatch(p.attribute[len(TAG) :])
        else PlannedPredicate(attribute=p.attribute, operator=p.operator, values=p.values)
        for p in route.predicates
    ]
    by_name = PlannedPredicate(
        attribute="accumulations.conditionName", operator="EXACTLY_MATCHES", values=names
    )
    return [by_name, *tags]


def _size(route: Route, names: list[str]) -> int:
    probe = PlannedWorkflow(
        name="x",
        condition_names=names,
        destination_indexes=[0],
        predicates=_predicates(route, names),
    )
    return issue_filter_size(probe)


def split_names(route: Route) -> list[list[str]]:
    """Chunks of the route's condition names, each of which fits one issue filter."""
    base = _size(route, [])
    chunks: list[list[str]] = [[]]
    used = base
    for name in route.names:
        add = len(json.dumps(name, ensure_ascii=False)) + (1 if chunks[-1] else 0)
        if chunks[-1] and used + add > FILTER_LIMIT:
            chunks.append([])
            used = base
            add = len(json.dumps(name, ensure_ascii=False))
        chunks[-1].append(name)
        used += add
    return [c for c in chunks if c]


def _label(number: int, resolve_off: bool, part: int, parts: int) -> str:
    return (
        f"route {number}"
        + (", resolve messages off" if resolve_off else "")
        + (f", part {part}" if parts > 1 else "")
    )


def _named(base: str, label: str) -> str:
    tail = f" [{label}]"
    return printable(base, MAX_NAME - len(tail)) + tail


def _copy(dest: NrDestination, label: str) -> NrDestination:
    return dest.model_copy(update={"name": _named(dest.name, label)})


@dataclass
class _Mutes:
    rules: list[MutingRule] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    expired: int = 0
    unscoped: int = 0
    used: set[str] = field(default_factory=set)


def _mutes_for(
    route: Route,
    number: int,
    timings: Mapping[str, MutingPlan],
    prefix: str,
    today: date,
) -> _Mutes:
    found = _Mutes()
    found.used |= set(route.mute) | set(route.active)
    for name in route.mute:
        plan = timings.get(name)
        if plan is None:
            _add(found.reasons, [C.R_UNKNOWN_TIMING])
            continue
        _add(found.reasons, plan.manual)
        result = mutes.muting_rules(
            plan,
            names=route.names,
            predicates=route.predicates,
            label=f"route {number}",
            prefix=prefix,
            today=today,
        )
        found.rules += result.rules
        found.expired += result.expired
        found.unscoped += result.unscoped
    if found.rules:
        _add(found.reasons, [C.R_MUTE_SCOPE])
    return found


def _group_points(points: Sequence[Point]) -> list[tuple[bool, list[Point]]]:
    """Points with a destination, split by whether resolve messages were switched off."""
    typed = [p for p in points if p.destination is not None]
    groups = [(off, [p for p in typed if p.disable_resolve is off]) for off in (False, True)]
    return [(off, members) for off, members in groups if members]


def _notes(route: Route, resolve_off: bool, muted: bool) -> list[str]:
    notes = list(route.notes)
    if resolve_off:
        notes.append(NOTE_RESOLVE)
    if muted:
        notes.append(NOTE_MUTE_SCOPE)
    preference = NOTE_PREFERENCE.get(route.incident_preference)
    if preference is not None:
        notes.append(preference)
    return notes


def _needs_review(
    members: Sequence[Point],
    manual: Sequence[ManualDestination],
    route: Route,
    mute_reasons: Sequence[str],
) -> bool:
    return bool(
        manual
        or route.reasons
        or mute_reasons
        or any(p.secrets or p.steps or p.unverified or p.reasons for p in members)
    )


def build_plans(
    routes: Sequence[Route],
    points: Mapping[str, Sequence[Point]],
    timings: Mapping[str, MutingPlan],
    *,
    prefix: str,
    today: date,
) -> PlanSet:
    out = PlanSet()
    for number, route in enumerate(routes, start=1):
        _route(out, number, route, points.get(route.receiver), timings, prefix, today)
    return out


def _route(
    out: PlanSet,
    number: int,
    route: Route,
    members: Sequence[Point] | None,
    timings: Mapping[str, MutingPlan],
    prefix: str,
    today: date,
) -> None:
    outcome = RouteOutcome(number, route.receiver, len(route.names), reasons=list(route.reasons))
    out.outcomes.append(outcome)
    out.used_timings |= set(route.mute) | set(route.active)
    _add(out.reasons, route.reasons)
    if members is None:
        _add(out.reasons, [C.R_UNKNOWN_RECEIVER])
        _add(outcome.reasons, [C.R_UNKNOWN_RECEIVER])
        out.routes_without_destination += 1
        return
    manual = [p.manual for p in members if p.manual is not None]
    outcome.manual = len(manual)
    outcome.skipped = sum(p.skipped for p in members)
    groups = _group_points(members)
    if not groups:
        _add(out.reasons, [C.R_NO_DESTINATION])
        _add(outcome.reasons, [C.R_NO_DESTINATION])
        out.routes_without_destination += 1
        if manual:
            out.plans.append(
                NotificationPlan(
                    topic=f"route {number}",
                    destinations=[],
                    workflow=None,
                    needs_review=True,
                    notes=_notes(route, False, False),
                    manual_destinations=manual,
                )
            )
        return
    muted = _mutes_for(route, number, timings, prefix, today)
    out.muting_rules += muted.rules
    out.expired_schedules += muted.expired
    out.unscoped_schedules += muted.unscoped
    outcome.muting_rules = len(muted.rules)
    _add(out.reasons, muted.reasons)
    _add(outcome.reasons, muted.reasons)
    chunks = split_names(route)
    dropped = sum(p.destination is None and p.manual is None and not p.skipped for p in members)
    for off, group in groups:
        for part, chunk in enumerate(chunks, start=1):
            label = _label(number, off, part, len(chunks))
            plan = _plan(route, chunk, group, label, off, bool(muted.rules), muted.reasons, prefix)
            if dropped:
                plan.notes.append(NOTE_DROPPED.format(count=dropped))
            if manual and outcome.workflows == 0:  # the manual steps ride on the first workflow
                plan.manual_destinations = manual
                plan.needs_review = True
            out.plans.append(plan)
            outcome.workflows += 1
            outcome.destinations += len(plan.typed_destinations)
    _add(out.reasons, [r for p in members for r in p.reasons])
    _add(outcome.reasons, [r for p in members for r in p.reasons])


def _plan(
    route: Route,
    names: list[str],
    group: Sequence[Point],
    label: str,
    resolve_off: bool,
    muted: bool,
    mute_reasons: Sequence[str],
    prefix: str,
) -> NotificationPlan:
    typed = [_copy(p.destination, label) for p in group if p.destination is not None]
    workflow = PlannedWorkflow(
        name=_named(f"{prefix} {route.receiver}", label),
        condition_names=names,
        destination_indexes=list(range(len(typed))),
        predicates=_predicates(route, names),
        notification_triggers=list(NO_CLOSE) if resolve_off else [],
        muting_rules_handling=STRICT if muted else "",
    )
    return NotificationPlan(
        topic=label,
        destinations=[],
        workflow=workflow,
        typed_destinations=typed,
        needs_review=_needs_review(group, [], route, mute_reasons),
        notes=_notes(route, resolve_off, muted),
    )
