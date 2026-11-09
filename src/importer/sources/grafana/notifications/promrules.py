"""Turn a Prometheus-style alert (`expr` with the comparison inside) into a Grafana rule object.

The Grafana translator reads rules shaped as a query, a reduce and a threshold. A Prometheus
rule has one `expr` such as `sum(errors) > 5`. This module splits a single top-level comparison
against a number and builds the same three-step pipeline, so the existing translator can be
pointed at it in the next wave. It does not call the translator. Anything it cannot split
(two comparisons, a comparison between two series, `bool`, a number on the left) is returned
as a data source-managed rule, which the translator reports as manual.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

from importer.sources.grafana.models import GfNode, GrafanaRule
from importer.sources.grafana.notifications.models import PromRule, PromRuleGroup

NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
OPERATOR_NAMES = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "==": "eq", "!=": "ne"}
OPEN, CLOSE = "([{", ")]}"
QUOTES = "\"'`"
RANGE_FROM_SECONDS = 300  # ignored by the reducer `last`, but the node needs one


@dataclass(frozen=True)
class Comparison:
    query: str
    operator: str  # a Grafana evaluator type: gt gte lt lte eq ne
    value: float


def _skip_string(expr: str, start: int) -> int:
    """Index after the string that opens at `start`, or -1 when it is not closed."""
    quote = expr[start]
    i = start + 1
    while i < len(expr):
        if expr[i] == "\\" and quote != "`":
            i += 2
            continue
        if expr[i] == quote:
            return i + 1
        i += 1
    return -1


def _top_level_comparisons(expr: str) -> list[tuple[int, str]] | None:
    """(index, operator) of each comparison outside brackets and strings; None if malformed."""
    found: list[tuple[int, str]] = []
    depth, i = 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch in QUOTES:
            i = _skip_string(expr, i)
            if i < 0:
                return None
            continue
        depth += (ch in OPEN) - (ch in CLOSE)
        if depth < 0:
            return None
        if depth == 0 and ch in "<>=!":
            two = expr[i : i + 2]
            op = two if two in OPERATOR_NAMES else ch
            if op in OPERATOR_NAMES:
                found.append((i, op))
                i += len(op)
                continue
        i += 1
    return found if depth == 0 else None


def split_comparison(expr: str) -> Comparison | None:
    """`query OP number` when `expr` has exactly one top-level comparison against a number."""
    comparisons = _top_level_comparisons(expr)
    if not comparisons or len(comparisons) != 1:
        return None
    index, op = comparisons[0]
    query, right = expr[:index].strip(), expr[index + len(op) :].strip()
    if not query or not NUMBER.fullmatch(right):
        return None
    value = float(right)
    if not math.isfinite(value):
        return None
    return Comparison(query=query, operator=OPERATOR_NAMES[op], value=value)


def _node(ref: str, uid: str, model: dict[str, Any]) -> GfNode:
    return GfNode(
        refId=ref,
        datasourceUid=uid,
        relativeTimeRange={"from": RANGE_FROM_SECONDS, "to": 0},
        model={"refId": ref, **model},
    )


def _pipeline(comparison: Comparison, kind: str, uid: str) -> list[GfNode]:
    query = _node(
        "A",
        uid,
        {
            "expr": comparison.query,
            "instant": True,
            "datasource": {"type": kind, "uid": uid},
        },
    )
    reduce = _node("B", "__expr__", {"type": "reduce", "expression": "A", "reducer": "last"})
    threshold = _node(
        "C",
        "__expr__",
        {
            "type": "threshold",
            "expression": "B",
            "conditions": [
                {"evaluator": {"type": comparison.operator, "params": [comparison.value]}}
            ],
        },
    )
    return [query, reduce, threshold]


def to_grafana_rule(rule: PromRule, group: PromRuleGroup, datasource_uid: str) -> GrafanaRule:
    """The rule as a `GrafanaRule`; `expr` is kept as an extra field for the next wave."""
    fields: dict[str, Any] = {
        "title": rule.name,
        "folder": group.namespace,
        "group": group.name,
        "interval_seconds": group.interval_seconds,
        "for_seconds": rule.for_seconds,
        "keep_firing_for_seconds": rule.keep_firing_for_seconds,
        "labels": dict(rule.labels),
        "shape": "ruler",
        "expr": rule.expr,
    }
    if rule.kind == "record":
        return GrafanaRule.model_validate({**fields, "record": {"metric": rule.name, "from": "A"}})
    comparison = split_comparison(rule.expr)
    if comparison is None:
        return GrafanaRule.model_validate({**fields, "datasource_managed": True})
    data = _pipeline(comparison, group.kind, datasource_uid)
    return GrafanaRule.model_validate({**fields, "condition": "C", "data": data})
