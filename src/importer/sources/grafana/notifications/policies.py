"""Read the notification policy tree (the routing tree) with depth, size and matcher caps.

Accepts the provisioning shape (`matchers` as strings, `object_matchers` as triples, the
deprecated `match` and `match_re` maps). A matcher that cannot be read is counted on its node,
and the node is then undecidable when the tree is flattened; it is never guessed. A tree that
is cut by a cap is marked incomplete and is not planned from.
"""

import re
from typing import Any

from importer.core.text import printable
from importer.sources.grafana.models import parse_duration
from importer.sources.grafana.notifications.models import (
    MAX_GROUP_BY,
    MAX_MATCHER_CHARS,
    MAX_MATCHERS,
    MAX_NAME,
    MAX_POLICY_DEPTH,
    MAX_POLICY_NODES,
    Matcher,
    NotificationError,
    PolicyNode,
    PolicyTree,
    as_dict,
    as_list,
)

OPERATORS = ("=~", "!~", "!=", "=")
_MATCHER = re.compile(
    r'\s*([A-Za-z_][A-Za-z0-9_.:-]*|"(?:[^"\\]|\\.)*")\s*(=~|!~|!=|=)\s*(.*?)\s*', re.DOTALL
)
_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
P_TOO_DEEP = "the notification policy tree is deeper than the importer reads; the rest was left out"
P_TOO_MANY = "the notification policy tree has more routes than the importer reads"
P_NOT_OBJECT = "a notification policy route was not an object and was left out"
P_ROOT_MATCHERS = (
    "the root route has matchers, which Alertmanager does not allow; they were ignored"
)


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _ESCAPE.sub(lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), text[1:-1])
    return text


def parse_matcher_string(text: str) -> Matcher | None:
    """`severity="critical"`, `team=~"a|b"`, `env!=prod`; None when it is not a matcher."""
    if len(text) > MAX_MATCHER_CHARS:
        return None
    found = _MATCHER.fullmatch(text)
    if not found:
        return None
    return Matcher(name=_unquote(found.group(1)), op=found.group(2), value=_unquote(found.group(3)))


def _matcher_from_triple(item: Any) -> Matcher | None:
    if not isinstance(item, list) or len(item) != 3:
        return None
    name, op, value = item
    if not (isinstance(name, str) and isinstance(value, str) and op in OPERATORS):
        return None
    return Matcher(name=name, op=op, value=value) if len(value) <= MAX_MATCHER_CHARS else None


def _matchers(raw: dict[str, Any]) -> tuple[list[Matcher], int]:
    """(matchers, how many entries could not be read)."""
    found: list[Matcher | None] = []
    found += [
        parse_matcher_string(m) if isinstance(m, str) else None
        for m in as_list(raw.get("matchers"))[: MAX_MATCHERS + 1]
    ]
    found += [_matcher_from_triple(t) for t in as_list(raw.get("object_matchers"))[:MAX_MATCHERS]]
    for key, op in (("match", "="), ("match_re", "=~")):
        pairs = as_dict(raw.get(key))
        found += [
            Matcher(name=k, op=op, value=v) if isinstance(v, str) else None
            for k, v in list(pairs.items())[:MAX_MATCHERS]
        ]
    good = [m for m in found if m is not None]
    bad = len(found) - len(good)
    if len(good) > MAX_MATCHERS:
        return good[:MAX_MATCHERS], bad + 1
    return good, bad


def _names(value: Any) -> list[str]:
    return [printable(n, MAX_NAME) for n in as_list(value)[:MAX_GROUP_BY] if isinstance(n, str)]


def _seconds(value: Any) -> int | None:
    try:
        return parse_duration(value) or None
    except ValueError:
        return None


class _Budget:
    def __init__(self) -> None:
        self.nodes = 1
        self.problems: list[str] = []
        self.incomplete = False

    def problem(self, text: str, *, cuts: bool) -> None:
        if text not in self.problems:
            self.problems.append(text)
        self.incomplete = self.incomplete or cuts


def _node(raw: dict[str, Any]) -> PolicyNode:
    matchers, bad = _matchers(raw)
    group_by = raw.get("group_by")
    receiver = raw.get("receiver")
    return PolicyNode(
        receiver=printable(receiver, MAX_NAME) if isinstance(receiver, str) else "",
        group_by=_names(group_by) if isinstance(group_by, list) else None,
        matchers=matchers,
        unparsed_matchers=bad,
        continue_=raw.get("continue") is True,
        mute_time_intervals=_names(raw.get("mute_time_intervals")),
        active_time_intervals=_names(raw.get("active_time_intervals")),
        group_wait_seconds=_seconds(raw.get("group_wait")),
        group_interval_seconds=_seconds(raw.get("group_interval")),
        repeat_interval_seconds=_seconds(raw.get("repeat_interval")),
    )


def _children(raw: dict[str, Any], depth: int, budget: _Budget) -> list[PolicyNode]:
    routes = as_list(raw.get("routes"))
    if not routes:
        return []
    if depth >= MAX_POLICY_DEPTH:
        budget.problem(P_TOO_DEEP, cuts=True)
        return []
    children: list[PolicyNode] = []
    for item in routes:
        if budget.nodes >= MAX_POLICY_NODES:
            budget.problem(P_TOO_MANY, cuts=True)
            break
        if not isinstance(item, dict):
            budget.problem(P_NOT_OBJECT, cuts=True)
            continue
        budget.nodes += 1
        child = _node(item)
        child.routes = _children(item, depth + 1, budget)
        children.append(child)
    return children


def parse_policy_tree(raw: Any) -> PolicyTree:
    """The policy tree from the provisioning payload. Raises ValueError for a non-object."""
    if not isinstance(raw, dict):
        raise NotificationError("the notification policy tree must be a JSON object")
    budget = _Budget()
    root = _node(raw)
    if root.matchers or root.unparsed_matchers:
        budget.problem(P_ROOT_MATCHERS, cuts=False)
        root.matchers, root.unparsed_matchers = [], 0
    root.routes = _children(raw, 0, budget)
    return PolicyTree(
        root=root, node_count=budget.nodes, incomplete=budget.incomplete, problems=budget.problems
    )
