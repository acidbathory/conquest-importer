"""Convert a Grafana notification export and its converted rules to New Relic plans.

Pure and offline: contact points to destinations, the policy tree to routes and workflows, mute
timings to muting rules. The result holds the plans (the shape `apply-notifications` reads), the
muting rules, and everything the report needs. Nothing here reads a secret or writes a file.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from importer.core.emit.notifications import converted_condition_names
from importer.core.ir import IRMonitor
from importer.core.muting_ir import MutingRule
from importer.core.notification_ir import NotificationPlan, plan_violations
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import NotificationExport
from importer.sources.grafana.notifications.muting import MutingPlan, mute_timing_to_muting
from importer.sources.grafana.notifications.policy import Undecided
from importer.sources.grafana.notify_convert import reasons as C
from importer.sources.grafana.notify_convert.destinations import Point, SecretNamer, convert_point
from importer.sources.grafana.notify_convert.destmap import DestinationMap
from importer.sources.grafana.notify_convert.labels import (
    policy_label_names,
    rule_keys,
    rule_labels,
)
from importer.sources.grafana.notify_convert.plans import RouteOutcome, build_plans
from importer.sources.grafana.notify_convert.routes import RouteSet, collect_routes


@dataclass(frozen=True)
class Inputs:
    export: NotificationExport
    monitors: Sequence[IRMonitor]  # the saved IR of `convert --source grafana`
    rules: Sequence[GrafanaRule] = ()  # the rules `convert` read; gives labels and folders
    dest_map: DestinationMap | None = None
    prefix: str = "Imported:"
    today: date = field(default_factory=date.today)


@dataclass
class Counts:
    contact_points: int = 0
    destinations: int = 0  # typed destinations created from contact points
    manual_points: int = 0
    no_equivalent_points: int = 0
    skipped_points: int = 0
    routes: int = 0
    workflows: int = 0
    muting_rules: int = 0
    rules_routed: int = 0
    rules_not_converted: int = 0
    routes_without_destination: int = 0
    rules_undecidable: int = 0
    secrets: int = 0


@dataclass
class Conversion:
    points: list[Point] = field(default_factory=list)
    plans: list[NotificationPlan] = field(default_factory=list)
    muting_rules: list[MutingRule] = field(default_factory=list)
    outcomes: list[RouteOutcome] = field(default_factory=list)
    undecidable: list[Undecided] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    mute_plans: dict[str, MutingPlan] = field(default_factory=dict)
    unused_mute_timings: int = 0
    unused_map_entries: int = 0
    expired_schedules: int = 0
    unscoped_schedules: int = 0
    counts: Counts = field(default_factory=Counts)


def _points(inputs: Inputs) -> list[Point]:
    namer = SecretNamer()
    found: list[Point] = []
    for cp in inputs.export.contact_points:
        entry = inputs.dest_map.entry_for(cp) if inputs.dest_map else None
        found.append(convert_point(cp, namer, inputs.prefix, entry))
    return found


def _rule_for(inputs: Inputs) -> dict[str, GrafanaRule]:
    return dict(zip(rule_keys(inputs.rules), inputs.rules, strict=True))


def _routes(inputs: Inputs, names: dict[int, str]) -> tuple[RouteSet, int]:
    """The routes of the converted rules, and how many rules did not convert."""
    converted = [m for m in inputs.monitors if m.source_id in names]
    not_converted = len(inputs.monitors) - len(converted)
    tree = inputs.export.policy
    if tree is None:
        return RouteSet(unrouted=[m.name for m in converted], reasons=[R.R_POLICY_NO_RECEIVER]), (
            not_converted
        )
    by_key = _rule_for(inputs)
    wanted = policy_label_names(tree)
    labeled = [
        (rule_labels(m, by_key.get(m.source_key), wanted), names[m.source_id]) for m in converted
    ]
    return collect_routes(tree, labeled), not_converted


def _add(into: list[str], items: Sequence[str]) -> None:
    into.extend(i for i in items if i not in into)


def convert_notifications(inputs: Inputs) -> Conversion:
    result = Conversion()
    result.points = _points(inputs)
    by_receiver: dict[str, list[Point]] = {}
    for point in result.points:
        by_receiver.setdefault(point.cp.name, []).append(point)
    routeset, not_converted = _routes(inputs, converted_condition_names(list(inputs.monitors)))
    result.undecidable, result.unrouted = routeset.undecidable, routeset.unrouted
    timings = {t.name: mute_timing_to_muting(t) for t in inputs.export.mute_timings}
    result.mute_plans = timings
    planned = build_plans(
        routeset.routes, by_receiver, timings, prefix=inputs.prefix, today=inputs.today
    )
    result.plans, result.muting_rules, result.outcomes = (
        planned.plans,
        planned.muting_rules,
        planned.outcomes,
    )
    result.unused_mute_timings = len(set(timings) - planned.used_timings)
    result.unused_map_entries = (
        inputs.dest_map.unused([p.cp for p in result.points]) if inputs.dest_map else 0
    )
    result.expired_schedules = planned.expired_schedules
    result.unscoped_schedules = planned.unscoped_schedules
    _add(result.reasons, routeset.reasons)
    _add(result.reasons, planned.reasons)
    _add(result.reasons, [r for p in result.points for r in p.reasons])
    if result.unused_mute_timings:
        _add(result.reasons, [C.R_ORPHAN_MUTE])
    result.violations = plan_violations(result.plans)
    result.counts = _counts(result, routeset, planned.routes_without_destination, not_converted)
    return result


def _counts(
    c: Conversion, routeset: RouteSet, without_destination: int, not_converted: int
) -> Counts:
    routed = {n for route in routeset.routes for n in route.names}
    return Counts(
        contact_points=len(c.points),
        destinations=sum(len(p.typed_destinations) for p in c.plans),
        manual_points=sum(p.manual is not None for p in c.points),
        no_equivalent_points=sum(
            p.destination is None and p.manual is None and not p.skipped for p in c.points
        ),
        skipped_points=sum(p.skipped for p in c.points),
        routes=len(routeset.routes),
        workflows=sum(p.workflow is not None for p in c.plans),
        muting_rules=len(c.muting_rules),
        rules_routed=len(routed),
        rules_not_converted=not_converted,
        routes_without_destination=without_destination,
        rules_undecidable=len({u.rule for u in c.undecidable}),
        secrets=len({s for p in c.points for s in p.secrets}),
    )
