"""Flatten the notification policy tree into routes a New Relic workflow filter can say.

The walk follows Alertmanager (depth first, left to right; a node matches when all its matchers
do; the first matching child wins unless it has `continue`; with no matching child the node
itself is the match; mute and active intervals are not inherited). It is run once per rule,
with what is known about the rule's labels:

- STATIC: a label of the rule, `alertname` or `grafana_folder`. Decided now.
- DYNAMIC: a label of the query's `by (...)` clause. Becomes a tag predicate on the issue.
- ABSENT: not static, and the query has a known label set. The value is the empty string.
- UNKNOWN: not static, and the label set is unknown (a bare selector), or the rule label is a
  template. Not decidable.
- INJECTED: labels that Grafana adds to every alert instance (see `INJECTED_LABELS`). Decided only
  when the value is known from the rule being routed; otherwise not decidable, and never the empty
  string. That Grafana adds exactly these labels, with these names, is UNVERIFIED (from memory
  of the alerting code, not read from a source file).

Undecidable cases are listed, never guessed, and the routes that were decided are still
returned. See docs/research/grafana-notifications-datapath.md section 2.3.
"""

import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from importer.sources.grafana.notifications import matchers as mt
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import Matcher, PolicyNode, PolicyTree

MAX_WORK = 100_000  # node evaluations for one rule (a full tree has at most 10,000 nodes)
MAX_SECONDS = 120  # wall clock for one call; the rules after it are reported as limited
CLOCK_EVERY = 1024  # node evaluations between two looks at the clock
_clock = time.monotonic  # replaced in tests
# Labels Grafana puts on alert instances. UNVERIFIED: names taken from memory.
INJECTED_LABELS = frozenset(
    {"__alert_rule_uid__", "__alert_rule_namespace_uid__", "datasource_uid", "ref_id"}
)
MAX_LEAVES = 1000  # routes one rule may reach
MAX_RESIDUALS = 20  # predicates on one route (the muting rule limit is 20 conditions)
MAX_FILTER_CHARS = 4096
PER_NAME_CHARS = 4  # quotes, comma and space around a condition name in a filter
TAG = "accumulations.tag."
LABEL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEFAULT_GROUPS = ({"grafana_folder", "alertname"}, {"alertname"})
NOTE_ISSUE = (
    "tag predicates are tested on the whole issue; Alertmanager tests each alert instance, "
    "so an issue that holds several values of the label can behave differently"
)
NOTE_PREFIX = "STARTS_WITH and ENDS_WITH on a list attribute are not confirmed by the documentation"
NOTE_SPLIT = (
    "the condition names were split over several workflows to keep each filter under the size limit"
)
NOTE_GROUP_BY = "rules merged into this route have different group_by settings; the first is shown"
NOTE_TIMINGS = "rules merged into this route have different timings; the first is shown"
NOTE_PREFERENCE = "rules merged into this route map to different incident preferences"

Status = Literal["true", "false", "preds", "undecided"]


@dataclass(frozen=True)
class RuleLabels:
    """What the policy walk needs to know about one converted rule."""

    name: str
    labels: Mapping[str, str] = field(default_factory=dict)
    folder: str = ""
    by_labels: frozenset[str] | None = None  # labels of the query's by(...); None when unknown
    receiver: str | None = None  # simplified routing: skips the tree
    uid: str = ""  # the rule uid, the value of __alert_rule_uid__ (empty when not carried)
    namespace_uid: str = ""  # the folder uid, the value of __alert_rule_namespace_uid__
    settings: PolicyNode | None = None  # simplified routing: the rule's own route options


class Predicate(BaseModel):
    attribute: str
    operator: str
    values: list[str]


class Undecided(BaseModel):
    rule: str
    path: str  # child indexes from the root, such as "0.2"; no names
    reason: str


class RoutingEntry(BaseModel):
    """One route: the rules it carries, who is told, and the filter that decides it."""

    receiver: str
    condition_names: list[str]
    predicates: list[Predicate] = []
    mute_time_intervals: list[str] = []
    active_time_intervals: list[str] = []
    group_by: list[str] | None = None
    incident_preference: str = ""  # PER_CONDITION, PER_CONDITION_AND_TARGET, or "" when lost
    reasons: list[str] = []
    notes: list[str] = []


class RoutingPlan(BaseModel):
    entries: list[RoutingEntry] = []
    undecidable: list[Undecided] = []
    unrouted: list[str] = []  # rules with no receiver anywhere
    reasons: list[str] = []
    notes: list[str] = []


@dataclass(frozen=True)
class _P:
    attribute: str
    operator: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class _Eff:
    """Options a route inherits from its parent."""

    receiver: str
    group_by: tuple[str, ...] | None
    timings: tuple[int | None, int | None, int | None]


@dataclass(frozen=True)
class _Leaf:
    rule: str
    receiver: str
    preds: tuple[_P, ...]
    mute: tuple[str, ...]
    active: tuple[str, ...]
    group_by: tuple[str, ...] | None
    timings: tuple[int | None, int | None, int | None]


class _Limit(Exception):
    pass


NEGATED = {"EXACTLY_MATCHES": "DOES_NOT_EXACTLY_MATCH", "DOES_NOT_EXACTLY_MATCH": "EXACTLY_MATCHES"}


def _negate(preds: tuple[_P, ...]) -> tuple[_P, ...] | None:
    """The condition 'not (all of preds)' as predicates that are all ANDed; None if impossible."""
    if len(preds) != 1:
        return None
    p = preds[0]
    if p.operator == "EXACTLY_MATCHES":  # not (a or b) is (not a) and (not b)
        return tuple(_P(p.attribute, NEGATED[p.operator], (v,)) for v in p.values)
    if p.operator == "DOES_NOT_EXACTLY_MATCH" and len(p.values) == 1:
        return (_P(p.attribute, "EXACTLY_MATCHES", p.values),)
    return None


class _Walk:
    """The walk for one rule."""

    def __init__(self, rule: RuleLabels, deadline: float) -> None:
        self.rule = rule
        self.deadline = deadline
        self.work = 0
        self.leaves: list[_Leaf] = []
        self.undecided: dict[tuple[str, str], Undecided] = {}
        own = {k: v for k, v in rule.labels.items() if isinstance(v, str)}
        self.static = {k: v for k, v in own.items() if k not in INJECTED_LABELS}
        self.static["alertname"] = rule.name
        self.static["grafana_folder"] = rule.folder
        if rule.uid:
            self.static["__alert_rule_uid__"] = rule.uid
        if rule.namespace_uid:
            self.static["__alert_rule_namespace_uid__"] = rule.namespace_uid
        self.templated = {k for k, v in rule.labels.items() if not isinstance(v, str) or "{{" in v}
        # injected labels with no known value are never the empty string
        self.unknown = (INJECTED_LABELS - set(self.static)) | self.templated

    # -- one matcher --

    def _undecided(self, path: str, reason: str) -> None:
        key = (path, reason)
        if key not in self.undecided:
            self.undecided[key] = Undecided(rule=self.rule.name, path=path, reason=reason)

    def _dynamic(self, m: Matcher) -> tuple[Status, tuple[_P, ...], str]:
        if not LABEL_NAME.fullmatch(m.name):
            return "undecided", (), R.R_POLICY_BAD_MATCHER
        attribute = TAG + m.name
        if m.op in ("=", "!="):
            if m.value == "":
                return "undecided", (), R.R_POLICY_UNDECIDABLE
            op = "EXACTLY_MATCHES" if m.op == "=" else "DOES_NOT_EXACTLY_MATCH"
            return "preds", (_P(attribute, op, (m.value,)),), ""
        said = mt.express(m.value)
        if said is None or (said[0] == "literals" and "" in said[1]):
            return "undecided", (), R.R_POLICY_REGEX
        kind, values = said
        if kind == "any":
            return ("true" if m.op == "=~" else "false"), (), ""
        if kind == "literals" and m.op == "=~":
            return "preds", (_P(attribute, "EXACTLY_MATCHES", values),), ""
        if kind == "literals":
            return "preds", tuple(_P(attribute, "DOES_NOT_EXACTLY_MATCH", (v,)) for v in values), ""
        if m.op == "=~":
            return (
                "preds",
                (_P(attribute, "STARTS_WITH" if kind == "prefix" else "ENDS_WITH", values),),
                "",
            )
        return "undecided", (), R.R_POLICY_REGEX  # no negated prefix or suffix operator exists

    def _matcher(self, m: Matcher) -> tuple[Status, tuple[_P, ...], str]:
        by = self.rule.by_labels
        if m.name in self.unknown:
            return "undecided", (), R.R_POLICY_UNDECIDABLE
        if m.name in self.static:
            value: str | None = self.static[m.name]
        elif by is None:
            return "undecided", (), R.R_POLICY_UNDECIDABLE
        elif m.name in by:
            return self._dynamic(m)
        else:
            value = None  # the query has a known label set without it: the empty string
        held = mt.matches(m.op, m.value, value)
        if held is None:
            return "undecided", (), R.R_POLICY_REGEX
        return ("true" if held else "false"), (), ""

    def _own(self, node: PolicyNode, path: str) -> tuple[Status, tuple[_P, ...]]:
        """Do the node's own matchers hold? A false matcher beats an undecidable one."""
        self.work += 1
        if self.work > MAX_WORK or (self.work % CLOCK_EVERY == 0 and _clock() > self.deadline):
            raise _Limit
        preds: list[_P] = []
        reason = R.R_POLICY_BAD_MATCHER if node.unparsed_matchers else ""
        for m in node.matchers:
            status, found, why = self._matcher(m)
            if status == "false":
                return "false", ()
            if status == "undecided":
                reason = reason or why
            preds.extend(p for p in found if p not in preds)
        if reason:
            self._undecided(path, reason)
            return "undecided", ()
        return ("preds", tuple(preds)) if preds else ("true", ())

    # -- the tree --

    def _emit(self, node: PolicyNode, eff: _Eff, preds: tuple[_P, ...]) -> None:
        if len(self.leaves) >= MAX_LEAVES:
            raise _Limit
        self.leaves.append(
            _Leaf(
                self.rule.name,
                eff.receiver,
                preds,
                tuple(node.mute_time_intervals),
                tuple(node.active_time_intervals),
                eff.group_by,
                eff.timings,
            )
        )

    @staticmethod
    def _inherit(node: PolicyNode, eff: _Eff) -> _Eff:
        timings = (
            node.group_wait_seconds,
            node.group_interval_seconds,
            node.repeat_interval_seconds,
        )
        return _Eff(
            node.receiver or eff.receiver,
            tuple(node.group_by) if node.group_by is not None else eff.group_by,
            (
                timings[0] if timings[0] is not None else eff.timings[0],
                timings[1] if timings[1] is not None else eff.timings[1],
                timings[2] if timings[2] is not None else eff.timings[2],
            ),
        )

    def visit(self, node: PolicyNode, path: str, eff: _Eff, preds: tuple[_P, ...]) -> None:
        """`node` matched: emit it, or walk its children as Alertmanager does."""
        if not node.routes:
            self._emit(node, eff, preds)
            return
        negations: list[_P] = []  # earlier conditional siblings that did not continue
        default: list[
            _P
        ] = []  # the condition 'no child matched', under which the node itself is it
        cut = unknown = False  # a child always matched; a child may or may not have matched
        for i, child in enumerate(node.routes):
            child_path = f"{path}.{i}" if path else str(i)
            status, own = self._own(child, child_path)
            if status == "false":
                continue
            if status == "undecided":
                if not child.continue_:
                    self._undecided(path, R.R_POLICY_ORDER)  # later routes depend on it
                    return
                unknown = True
                continue
            self.visit(
                child, child_path, self._inherit(child, eff), self._join(preds, negations, own)
            )
            if not own:
                cut = True
                if not child.continue_:
                    return
                continue
            negated = _negate(own)
            if negated is None:
                if not child.continue_:
                    self._undecided(child_path, R.R_POLICY_ORDER)
                    return
                unknown = True
                continue
            default.extend(p for p in negated if p not in default)
            if not child.continue_:
                negations.extend(p for p in negated if p not in negations)
        if cut:
            return
        if unknown:
            self._undecided(path, R.R_POLICY_ORDER)
            return
        self._emit(node, eff, self._join(preds, default))

    @staticmethod
    def _join(*parts: Sequence[_P]) -> tuple[_P, ...]:
        joined: list[_P] = []
        for part in parts:
            joined.extend(p for p in part if p not in joined)
            if len(joined) > MAX_RESIDUALS:
                raise _Limit
        return tuple(joined)


# --- grouping ---


def _preference(group_by: tuple[str, ...] | None) -> str:
    """The incident preference that is close to a Grafana group_by, or "" when none is."""
    if group_by is None or set(group_by) in DEFAULT_GROUPS:
        return "PER_CONDITION"
    return "PER_CONDITION_AND_TARGET" if group_by == ("...",) else ""


def _entry(leaves: list[_Leaf], names: list[str]) -> RoutingEntry:
    first = leaves[0]
    preds = sorted(first.preds, key=lambda p: (p.attribute, p.operator, p.values))
    preferences = {_preference(leaf.group_by) for leaf in leaves}
    reasons: list[str] = []
    notes: list[str] = []
    if any(t is not None for leaf in leaves for t in leaf.timings) or "" in preferences:
        reasons.append(R.R_POLICY_TIMING)
    if first.active:
        reasons.append(R.R_MUTE_ACTIVE)
    if preds:
        notes.append(NOTE_ISSUE)
    if any(p.operator in ("STARTS_WITH", "ENDS_WITH") for p in preds):
        notes.append(NOTE_PREFIX)
    if len({leaf.group_by for leaf in leaves}) > 1:
        notes.append(NOTE_GROUP_BY)
    if len({leaf.timings for leaf in leaves}) > 1:
        notes.append(NOTE_TIMINGS)
    if len(preferences) > 1:
        notes.append(NOTE_PREFERENCE)
    return RoutingEntry(
        receiver=first.receiver,
        condition_names=names,
        predicates=[
            Predicate(attribute=p.attribute, operator=p.operator, values=list(p.values))
            for p in preds
        ],
        mute_time_intervals=list(first.mute),
        active_time_intervals=list(first.active),
        group_by=list(first.group_by) if first.group_by is not None else None,
        incident_preference=_preference(first.group_by),
        reasons=reasons,
        notes=notes,
    )


def _split(entry: RoutingEntry) -> list[RoutingEntry]:
    """Entries whose condition names fit one filter each."""
    chunks: list[list[str]] = [[]]
    size = 0
    for name in entry.condition_names:
        need = len(name) + PER_NAME_CHARS
        if chunks[-1] and size + need > MAX_FILTER_CHARS:
            chunks.append([])
            size = 0
        chunks[-1].append(name)
        size += need
    if len(chunks) == 1:
        return [entry]
    return [
        entry.model_copy(update={"condition_names": chunk, "notes": [*entry.notes, NOTE_SPLIT]})
        for chunk in chunks
    ]


def _group(leaves: list[_Leaf]) -> list[RoutingEntry]:
    """One entry per (receiver, predicates, mute set, active set), as the research note says."""
    groups: dict[tuple[object, ...], list[_Leaf]] = {}
    for leaf in leaves:
        preds = tuple(sorted((p.attribute, p.operator, p.values) for p in leaf.preds))
        groups.setdefault((leaf.receiver, preds, leaf.mute, leaf.active), []).append(leaf)
    entries: list[RoutingEntry] = []
    for key in sorted(groups, key=repr):
        members = groups[key]
        entries.extend(_split(_entry(members, sorted({leaf.rule for leaf in members}))))
    return entries


def flatten_policy_tree(tree: PolicyTree, rules: Sequence[RuleLabels]) -> RoutingPlan:
    """Routes for the rules. Undecidable cases are listed, decided routes are still returned."""
    plan = RoutingPlan()
    if tree.incomplete:
        plan.undecidable = [
            Undecided(rule=r.name, path="", reason=R.R_POLICY_INCOMPLETE) for r in rules
        ]
        plan.reasons = [R.R_POLICY_INCOMPLETE] if rules else []
        return plan
    root = tree.root
    root_eff = _Eff(
        root.receiver,
        tuple(root.group_by) if root.group_by is not None else None,
        (root.group_wait_seconds, root.group_interval_seconds, root.repeat_interval_seconds),
    )
    deadline = _clock() + MAX_SECONDS
    leaves: list[_Leaf] = []
    for rule in rules:
        if rule.receiver:  # simplified routing names its receiver and skips the tree
            own = rule.settings
            leaves.append(
                _Leaf(
                    rule.name,
                    rule.receiver,
                    (),
                    tuple(own.mute_time_intervals) if own else (),
                    tuple(own.active_time_intervals) if own else (),
                    tuple(own.group_by) if own and own.group_by is not None else None,
                    (
                        own.group_wait_seconds,
                        own.group_interval_seconds,
                        own.repeat_interval_seconds,
                    )
                    if own
                    else (None, None, None),
                )
            )
            continue
        walk = _Walk(rule, deadline)
        try:
            if _clock() > deadline:
                raise _Limit
            walk.visit(root, "", root_eff, ())
        except _Limit:
            plan.undecidable.append(Undecided(rule=rule.name, path="", reason=R.R_POLICY_LIMIT))
            continue
        plan.undecidable.extend(walk.undecided.values())
        leaves.extend(walk.leaves)
    plan.unrouted = sorted({leaf.rule for leaf in leaves if not leaf.receiver})
    if plan.unrouted:
        plan.reasons.append(R.R_POLICY_NO_RECEIVER)
    plan.entries = _group([leaf for leaf in leaves if leaf.receiver])
    plan.reasons.extend(
        r for r in dict.fromkeys(u.reason for u in plan.undecidable) if r not in plan.reasons
    )
    return plan
