"""Classic [value, 0] evaluator params, and queries the rule's condition never reads."""

import importlib
from typing import Any

import pytest

from importer.core.ir import Confidence, Operator
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one
from test_grafana_rules2_pipeline import classic, cond, expr_node

t = importlib.import_module("importer.sources.grafana.translate")


def second_query(rule: GrafanaRule, ref: str = "Q") -> None:
    other = rule.data[0].model_copy(deep=True)
    other.refId = ref
    other.model["refId"] = ref
    other.model["expr"] = "avg(other_invented)"
    rule.data.insert(1, other)


def ignored(count: int) -> str:
    return str(t.N_UNUSED_QUERIES.format(count=count))


@pytest.mark.parametrize(("kind", "op"), [("gt", Operator.ABOVE), ("lt", Operator.BELOW)])
def test_classic_gt_lt_ignore_the_trailing_zero(kind: str, op: Operator) -> None:
    m = one(classic([cond(kind, [7, 0])]))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert (m.thresholds[0].operator, m.thresholds[0].value) == (op, 7)


@pytest.mark.parametrize("params", [[7, 1], [7, 0, 0], [7.0, -2.5]])
def test_classic_single_value_kinds_read_the_first_param_as_grafana_does(
    params: list[float],
) -> None:
    # grafana/grafana 0468ba7 pkg/expr/classic/evaluator.go: newThresholdEvaluator uses Params[0]
    m = one(classic([cond("gt", params)]))
    assert m.thresholds[0].value == 7, m.reasons


@pytest.mark.parametrize(
    ("kind", "params"),
    [("gt", ["7", 0]), ("gt", [7, "0"]), ("gt", [True, 0]), ("gt", [])],
)
def test_classic_gt_lt_other_param_lists_stay_manual(kind: str, params: list[Any]) -> None:
    # Grafana reads params as []float64, so a string or a bool is not a rule it would run
    assert one(classic([cond(kind, params)])).reasons == [t.R_VALUE]


def test_range_kinds_keep_their_two_params_refusal() -> None:
    assert one(classic([cond("within_range", [1, 2])])).reasons == [t.R_RANGE]


@pytest.mark.parametrize("kind", ["gt", "lt", "gte", "lte", "eq", "ne"])
@pytest.mark.parametrize("params", [[7, 0], [7, 3], [7.5, 0, 0]])
def test_a_threshold_node_reads_the_first_param_as_grafana_does(
    kind: str, params: list[float]
) -> None:
    # grafana/grafana 0468ba7 pkg/expr/threshold.go: single-value kinds use conditions[0] only
    m = one(make("avg(cpu)", kind=kind, params=params))
    assert m.thresholds[0].value == params[0], m.reasons


@pytest.mark.parametrize("params", [["7", 0], [True, 0], [7, "0"], [], [float("inf"), 0]])
def test_a_threshold_node_with_a_value_grafana_cannot_read_stays_manual(
    params: list[object],
) -> None:
    assert one(make("avg(cpu)", params=params)).reasons == [t.R_VALUE]


def test_classic_with_an_unused_query_converts_with_a_note() -> None:
    rule = classic([cond()])
    second_query(rule)
    m = one(rule)
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert ignored(1) in m.reasons
    assert "other_invented" not in str(m.signal)


def test_threshold_chain_with_an_unused_query_converts_with_a_note() -> None:
    rule = make("avg(cpu)", reducer="mean")
    second_query(rule)
    m = one(rule)
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert ignored(1) in m.reasons


def test_an_unused_expression_node_is_counted_and_ignored() -> None:
    rule = classic([cond()])
    rule.data.append(expr_node("C", {"type": "reduce", "expression": "A", "reducer": "last"}))
    second_query(rule)
    m = one(rule)
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert ignored(2) in m.reasons


def test_a_rule_that_reads_everything_has_no_such_note() -> None:
    m = one(classic([cond()]))
    assert not any("not used by its condition" in r for r in m.reasons)


def test_classic_reading_two_queries_stays_manual() -> None:
    rule = classic([cond(), cond()])
    second_query(rule)
    rule.data[2].model["conditions"][1]["query"] = {"params": ["Q"]}
    assert one(rule).reasons == [t.R_MULTI]


def test_math_over_two_queries_stays_manual() -> None:
    rule = classic([cond()])
    second_query(rule)
    rule.data[2] = expr_node("B", {"type": "math", "expression": "$A + ${Q}"})
    m = one(rule)
    assert m.confidence is Confidence.MANUAL
    assert m.reasons == [t.R_MATH_QUERIES.format(count=2)]
