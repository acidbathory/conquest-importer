"""Expression pipelines: one classic condition, a resample step, and math over two queries."""

import importlib
from typing import Any

import pytest

from importer.core.ir import Aggregation, Confidence, IRMonitor, Operator
from importer.sources.grafana.models import GfNode, GrafanaRule
from test_grafana import make, one, text

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def expr_node(ref: str, model: dict[str, Any]) -> GfNode:
    return GfNode(refId=ref, datasourceUid="__expr__", model=model)


def classic(conditions: list[dict[str, Any]], expr: str = "avg(cpu)") -> GrafanaRule:
    """A rule whose condition node is a classic condition on query A."""
    rule = make(expr)
    rule.data = [
        rule.data[0],
        expr_node("B", {"type": "classic_conditions", "conditions": conditions}),
    ]
    rule.condition = "B"
    return rule


def cond(kind: str = "gt", params: list[Any] | None = None, reducer: str = "avg") -> dict[str, Any]:
    return {
        "evaluator": {"type": kind, "params": [80] if params is None else params},
        "operator": {"type": "and"},
        "query": {"params": ["A"]},
        "reducer": {"type": reducer, "params": []},
    }


def converted(rule: GrafanaRule) -> IRMonitor:
    monitor = one(rule)
    assert monitor.confidence is Confidence.APPROXIMATE, monitor.reasons
    return monitor


# classic conditions


def test_one_classic_condition_is_a_reduce_and_a_threshold() -> None:
    m = converted(classic([cond("gt", [80], "avg")]))
    assert (m.thresholds[0].operator, m.thresholds[0].value) == (Operator.ABOVE, 80)
    assert m.signal.metric and m.signal.metric.aggregation is Aggregation.AVG
    assert t.N_CLASSIC in m.reasons


@pytest.mark.parametrize(
    ("reducer", "aggregation"),
    [("avg", Aggregation.AVG), ("min", Aggregation.MIN), ("max", Aggregation.MAX)],
)
def test_the_classic_reducer_maps_like_the_reduce_node(
    reducer: str, aggregation: Aggregation
) -> None:
    m = converted(classic([cond(reducer=reducer)], expr=f"{reducer}(cpu)"))
    assert m.signal.metric and m.signal.metric.aggregation is aggregation


def test_a_classic_condition_agrees_with_the_reduce_node_version() -> None:
    from_classic = converted(classic([cond("lt", [5], "avg")], expr="avg(cpu)"))
    from_reduce = converted(make("avg(cpu)", reducer="mean", kind="lt", params=[5]))
    assert from_classic.signal == from_reduce.signal
    assert from_classic.thresholds == from_reduce.thresholds


@pytest.mark.parametrize("reducer", ["median", "diff", "percent_diff", "count", "count_non_null"])
def test_a_classic_reducer_with_no_new_relic_aggregation_is_manual(reducer: str) -> None:
    assert one(classic([cond(reducer=reducer)])).reasons == [t.R_REDUCER]


def test_several_classic_conditions_are_manual() -> None:
    m = one(classic([cond(), cond("lt", [1])]))
    assert m.reasons == [t.R_CLASSIC]


@pytest.mark.parametrize(
    ("kind", "params", "reason"),
    [
        ("within_range", [1, 2], t.R_RANGE),
        ("outside_range", [1, 2], t.R_RANGE),
        ("no_value", [], t.R_OPERATOR),
        ("gt", [], t.R_VALUE),
    ],
)
def test_a_classic_evaluator_that_is_not_one_threshold_is_manual(
    kind: str, params: list[Any], reason: str
) -> None:
    assert one(classic([cond(kind, params)])).reasons == [reason]


def test_a_classic_condition_that_names_another_query_is_manual() -> None:
    c = cond()
    c["query"] = {"params": ["Z"]}
    assert one(classic([c])).confidence is Confidence.MANUAL


def test_a_classic_condition_next_to_other_expressions_stays_manual() -> None:
    rule = classic([cond()])
    rule.data.append(expr_node("C", {"type": "reduce", "expression": "A", "reducer": "last"}))
    assert one(rule).reasons == [t.R_CLASSIC]


# resample


def with_resample(rule: GrafanaRule) -> GrafanaRule:
    """query A -> resample R -> reduce B -> threshold C."""
    model = {"type": "resample", "expression": "A", "window": "1m", "downsampler": "mean"}
    resample = expr_node("R", model)
    rule.data.insert(1, resample)
    rule.data[2].model["expression"] = "R"
    return rule


def test_a_resample_step_is_noted_and_the_rule_converts() -> None:
    m = converted(with_resample(make("avg(cpu)", reducer="mean")))
    assert t.N_RESAMPLE in m.reasons
    plain = converted(make("avg(cpu)", reducer="mean"))
    assert m.signal == plain.signal and m.thresholds == plain.thresholds


def test_a_resample_that_is_not_on_the_chain_is_manual() -> None:
    rule = make("avg(cpu)")
    rule.data.append(expr_node("R", {"type": "resample", "expression": "A"}))
    assert one(rule).reasons == [t.R_RESAMPLE]


def test_two_resample_steps_are_manual() -> None:
    rule = with_resample(make("avg(cpu)", reducer="mean"))
    rule.data.append(expr_node("R2", {"type": "resample", "expression": "R"}))
    assert one(rule).confidence is Confidence.MANUAL


# math


def math_rule(expression: str) -> GrafanaRule:
    rule = make("sum(errors_total)", reducer="mean")
    rule.data.insert(1, rule.data[0].model_copy(update={"refId": "Q"}))
    rule.data.append(expr_node("M", {"type": "math", "expression": expression}))
    rule.condition = "M"
    return rule


@pytest.mark.parametrize("expression", ["$A / $Q", "${A} + ${Q}", "$A * 100 / $Q"])
def test_math_over_two_queries_says_how_many(expression: str) -> None:
    m = one(math_rule(expression))
    assert m.reasons == [t.R_MATH_QUERIES.format(count=2)]


def test_math_over_one_query_keeps_the_plain_reason() -> None:
    rule = make("avg(cpu)")
    rule.data.append(expr_node("M", {"type": "math", "expression": "$B * 100"}))
    assert text(one(rule)) == t.R_MATH
