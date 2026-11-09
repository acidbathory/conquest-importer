"""Routes: the notification policy walked once per converted rule, merged by what they share.

`flatten_policy_tree` already groups rules that share a route, but it names rules by title and
splits long lists by its own length rule. Two rules can share a title, and the split must follow
the real filter size, so each rule is walked alone here and the results are merged by
(receiver, predicates, mute set, active set). The merged route lists the New Relic condition
names, not titles.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from importer.sources.grafana.notifications.models import PolicyTree
from importer.sources.grafana.notifications.policy import (
    Predicate,
    RuleLabels,
    Undecided,
    flatten_policy_tree,
)


@dataclass
class Route:
    receiver: str
    predicates: list[Predicate]
    mute: list[str]
    active: list[str]
    names: list[str] = field(default_factory=list)  # New Relic condition names
    notes: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    incident_preference: str = ""


@dataclass
class RouteSet:
    routes: list[Route] = field(default_factory=list)
    undecidable: list[Undecided] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)  # titles of rules with no receiver
    reasons: list[str] = field(default_factory=list)


def _key(
    receiver: str, predicates: list[Predicate], mute: list[str], active: list[str]
) -> tuple[object, ...]:
    shape = tuple((p.attribute, p.operator, tuple(p.values)) for p in predicates)
    return (receiver, shape, tuple(mute), tuple(active))


def _add(into: list[str], items: Sequence[str]) -> None:
    into.extend(i for i in items if i not in into)


def collect_routes(tree: PolicyTree, rules: Sequence[tuple[RuleLabels, str]]) -> RouteSet:
    """Walk the policy for each (labels, New Relic condition name) and merge the routes."""
    merged: dict[tuple[object, ...], Route] = {}
    found = RouteSet()
    for labels, nr_name in rules:
        plan = flatten_policy_tree(tree, [labels])
        found.undecidable += plan.undecidable
        found.unrouted += plan.unrouted
        _add(found.reasons, plan.reasons)
        for entry in plan.entries:
            key = _key(
                entry.receiver,
                entry.predicates,
                entry.mute_time_intervals,
                entry.active_time_intervals,
            )
            route = merged.setdefault(
                key,
                Route(
                    receiver=entry.receiver,
                    predicates=entry.predicates,
                    mute=entry.mute_time_intervals,
                    active=entry.active_time_intervals,
                    incident_preference=entry.incident_preference,
                ),
            )
            route.names.append(nr_name)
            _add(route.notes, entry.notes)
            _add(route.reasons, entry.reasons)
    found.routes = [merged[k] for k in sorted(merged, key=repr)]
    for route in found.routes:
        route.names = sorted(set(route.names))
    return found
