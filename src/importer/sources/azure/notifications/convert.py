"""Azure action groups and alert processing rules to New Relic plans, muting rules and counts.

Pure and offline. The routing follows `link.py`: every action group that a converted condition
points at gets one plan, with one workflow whose issue filter matches the tag `azure.actionGroup`
(or, with `route_by="name"`, the converted condition names). The group's receivers become the
workflow's destinations; the ones that need a person are kept in the plan with their steps. Every
object is planned disabled. Nothing here reads a secret or writes a file.
"""

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from importer.core.ir import IRMonitor
from importer.core.muting_ir import MutingRule
from importer.core.notification_ir import (
    NotificationPlan,
    PlannedWorkflow,
    plan_violations,
    tag_predicate,
)
from importer.core.text import printable
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications.link import ACTION_GROUP_TAG, monitor_action_groups
from importer.sources.azure.notifications.models import RECEIVER_KINDS, AzureExport
from importer.sources.azure.notifications.processing import (
    MonitorIndex,
    RuleResult,
    convert_rule,
)
from importer.sources.azure.notifications.receivers import (
    GroupResult,
    Outcome,
    SecretNamer,
    convert_group,
)

MAX_NAME = 120
STRICT = "DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES"
NOTE_TAG = (
    "The workflow filters issues on the tag azure.actionGroup, which the alert conversion writes "
    "on each converted condition. It is UNVERIFIED that the tag reaches the issue; check one "
    "issue before enabling, or convert again with --route-by name."
)
NOTE_DISABLED = (
    "The action group is disabled in Azure, so it sent nothing. Keep the workflow disabled until "
    "you want notifications again."
)
NOTE_MUTING = (
    "Muting rules were planned. They only affect workflows that honour muting, so this workflow "
    "does not notify for fully or partially muted issues."
)
NOTE_PARTIAL = (
    "Some receivers of the action group are not in this workflow (the report lists them and what "
    "to do); add their replacements to the workflow by hand."
)
ROUTE_BY = ("tag", "name")


@dataclass(frozen=True)
class Inputs:
    export: AzureExport
    monitors: Sequence[IRMonitor]  # the saved IR of the alert conversion
    prefix: str = "Imported:"
    route_by: str = "tag"
    today: date = field(default_factory=date.today)


@dataclass
class Conversion:
    groups: list[GroupResult] = field(default_factory=list)
    plans: list[NotificationPlan] = field(default_factory=list)
    muting_rules: list[MutingRule] = field(default_factory=list)
    rule_results: list[RuleResult] = field(default_factory=list)
    routes: dict[str, list[str]] = field(default_factory=dict)  # group key -> condition names
    unrouted: list[str] = field(default_factory=list)  # converted conditions with no group
    not_exported: list[str] = field(default_factory=list)  # keys of groups the export lacks
    unused: list[str] = field(default_factory=list)  # keys of groups no condition points at
    reasons: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # what the read side could not read
    route_by: str = "tag"

    @property
    def secrets(self) -> list[tuple[str, str]]:
        """(secret name, kind) of every secret a person must supply, in plan order."""
        return [(s, r.kind) for g in self.groups for r in g.results for s in r.secrets]

    def receiver_counts(self) -> dict[str, Counter[str]]:
        """Per receiver kind: converted, approximate, manual, none, skipped. Placeholders are
        never converted: a receiver without a usable address or URL is manual."""
        table: dict[str, Counter[str]] = {k: Counter() for k in RECEIVER_KINDS}
        for g in self.groups:
            for r in g.results:
                table[r.kind][r.outcome.value] += 1
                if r.outcome is Outcome.CONVERTED and r.approximate:
                    table[r.kind]["approximate"] += 1
        return table

    def rule_counts(self) -> Counter[str]:
        return Counter(r.outcome.value for r in self.rule_results)


def _topic_names(groups: list[GroupResult]) -> dict[str, str]:
    """A unique topic per group: the name, plus a short hash when two groups share a name."""
    seen = Counter(g.name.lower() for g in groups)
    names: dict[str, str] = {}
    for g in groups:
        base = printable(g.name, 100) or "action group"
        suffix = (
            ""
            if seen[g.name.lower()] == 1
            else " " + hashlib.sha256(g.key.encode()).hexdigest()[:6]
        )
        names[g.key] = base + suffix
    return names


def _plan(
    g: GroupResult,
    topic: str,
    conditions: list[str],
    prefix: str,
    route_by: str,
    muting: bool,
) -> NotificationPlan:
    notes = list(g.notes)
    if route_by == "tag":
        notes.append(NOTE_TAG)
    if not g.enabled:
        notes.append(NOTE_DISABLED)
    if muting:
        notes.append(NOTE_MUTING)
    if g.manual:
        notes.append(NOTE_PARTIAL)
    review = bool(g.manual) or not g.enabled or any(r.approximate for r in g.results)
    workflow: PlannedWorkflow | None = None
    if g.destinations:
        workflow = PlannedWorkflow(
            name=printable(f"{prefix} {topic}", MAX_NAME),
            condition_names=sorted(set(conditions)),
            destination_indexes=list(range(len(g.destinations))),
            predicates=[tag_predicate(ACTION_GROUP_TAG, [g.key])] if route_by == "tag" else [],
            muting_rules_handling=STRICT if muting else "",
        )
    return NotificationPlan(
        topic=topic,
        destinations=[],
        typed_destinations=g.destinations,
        workflow=workflow,
        needs_review=review,
        notes=notes,
        manual_destinations=g.manual,
    )


def _routes(index: MonitorIndex) -> tuple[dict[str, list[str]], list[str]]:
    routes: dict[str, list[str]] = {}
    unrouted: list[str] = []
    for m in index.monitors:
        keys = monitor_action_groups(m)
        if not keys:
            unrouted.append(index.names[m.source_id])
        for key in keys:
            routes.setdefault(key, []).append(index.names[m.source_id])
    return {k: sorted(set(v)) for k, v in routes.items()}, sorted(set(unrouted))


def convert_notifications(inputs: Inputs) -> Conversion:
    if inputs.route_by not in ROUTE_BY:
        raise ValueError("route_by is tag or name")
    result = Conversion(route_by=inputs.route_by, problems=list(inputs.export.problems))
    index = MonitorIndex.build(list(inputs.monitors))
    namer = SecretNamer()
    result.groups = [convert_group(g, namer, inputs.prefix) for g in inputs.export.action_groups]
    result.routes, result.unrouted = _routes(index)
    known = {g.key for g in result.groups}
    result.not_exported = sorted(set(result.routes) - known)
    result.unused = sorted(known - set(result.routes))
    result.rule_results = [
        convert_rule(r, index, inputs.prefix, inputs.today) for r in inputs.export.rules
    ]
    result.muting_rules = [m for r in result.rule_results for m in r.rules]
    topics = _topic_names(result.groups)
    for g in result.groups:
        if g.key in result.routes:
            result.plans.append(
                _plan(
                    g,
                    topics[g.key],
                    result.routes[g.key],
                    inputs.prefix,
                    inputs.route_by,
                    bool(result.muting_rules),
                )
            )
    for lst in (
        [x for g in result.groups for x in g.reasons],
        [x for r in result.rule_results for x in r.reasons],
    ):
        result.reasons += [x for x in lst if x not in result.reasons]
    if result.not_exported:
        result.reasons.append(R.R_GROUP_NOT_EXPORTED)
    if inputs.route_by == "tag" and result.plans:
        result.reasons.append(R.R_TAG_FILTER)
    result.violations = plan_violations(result.plans)
    return result
