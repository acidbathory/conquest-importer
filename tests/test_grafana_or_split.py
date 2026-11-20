"""One classic condition whose tests read different queries combined with OR: one condition each."""

import importlib
from typing import Any

import pytest

from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, IRMonitor, Operator
from importer.sources.grafana.models import GfNode, GrafanaRule
from importer.sources.grafana.notify_convert.labels import rule_labels
from importer.sources.grafana.translate import translate
from test_grafana import make
from test_grafana_notify_convert_routes import email, export, run, workflows
from test_grafana_rules2_pipeline import expr_node

t = importlib.import_module("importer.sources.grafana.translate")

REFS = ["A", "Q", "R", "S", "T", "U"]


def leg(ref: str, op: str | None = "or", kind: str = "gt", value: float = 80) -> dict[str, Any]:
    test: dict[str, Any] = {
        "evaluator": {"type": kind, "params": [value]},
        "query": {"params": [ref]},
        "reducer": {"type": "avg", "params": []},
    }
    if op is not None:
        test["operator"] = {"type": op}
    return test


def query(ref: str, expr: str, uid: str = "ds-1") -> GfNode:
    base = make(expr, uid=uid).data[0]
    node = base.model_copy(deep=True)
    node.refId = ref
    node.model["refId"] = ref
    return node


def or_rule(
    count: int = 2,
    ops: list[str | None] | None = None,
    exprs: dict[str, str] | None = None,
    **rule: Any,
) -> GrafanaRule:
    """A rule with `count` queries and one classic condition testing each, first operator and."""
    refs = REFS[:count]
    ops = ops if ops is not None else ["and", *["or"] * (count - 1)]
    exprs = exprs or {}
    nodes = [query(ref, exprs.get(ref, f"avg(invented_{ref.lower()})")) for ref in refs]
    tests = [
        leg(ref, op, value=10 * (i + 1)) for i, (ref, op) in enumerate(zip(refs, ops, strict=True))
    ]
    base = make("avg(x)", **rule)
    base.data = [*nodes, expr_node("B", {"type": "classic_conditions", "conditions": tests})]
    base.condition = "B"
    return base


def approximate(monitors: list[IRMonitor]) -> None:
    assert all(m.confidence is Confidence.APPROXIMATE for m in monitors), [
        m.reasons for m in monitors
    ]


def test_two_or_legs_become_two_approximate_conditions_with_the_note() -> None:
    monitors = translate([or_rule(2, labels={"team": "invented"})])
    approximate(monitors)
    assert [m.source_key for m in monitors] == ["u1/or-A", "u1/or-Q"]
    assert len({m.source_id for m in monitors}) == 2
    assert [m.name for m in monitors] == ["r (A)", "r (Q)"]
    assert [(m.thresholds[0].operator, m.thresholds[0].value) for m in monitors] == [
        (Operator.ABOVE, 10),
        (Operator.ABOVE, 20),
    ]
    assert all(t.N_OR_SPLIT.format(count=2) in m.reasons for m in monitors)
    assert "invented_a" in str(monitors[0].signal) and "invented_q" in str(monitors[1].signal)
    assert "invented_q" not in str(monitors[0].signal)


def test_each_leg_keeps_the_notes_of_the_rule_settings() -> None:
    monitors = translate([or_rule(2, no_data_state="KeepLast", is_paused=True)])
    approximate(monitors)
    for m in monitors:
        assert t.N_NODATA_KEEP in m.reasons and t.N_PAUSED in m.reasons
        assert m.unmapped.get("enabled") is False
        assert m.reasons[-1] == t.N_ROUTING


@pytest.mark.parametrize("count", [3, 5])
def test_up_to_five_or_legs_convert(count: int) -> None:
    monitors = translate([or_rule(count)])
    approximate(monitors)
    assert [m.source_key for m in monitors] == [f"u1/or-{r}" for r in REFS[:count]]
    assert all(t.N_OR_SPLIT.format(count=count) in m.reasons for m in monitors)


def test_six_or_legs_stay_refused() -> None:
    (m,) = translate([or_rule(6)])
    assert m.reasons == [t.R_MULTI]


@pytest.mark.parametrize(
    "ops",
    [
        ["and", "and"],
        ["or", "and"],
        ["and", None],  # Grafana refuses a missing operator after the first (classic.go)
        ["and", "or", "and"],
        ["or", "and", "or"],
    ],
)
def test_any_and_is_not_split_as_or(ops: list[str | None]) -> None:
    """AND rules become a compound condition (tests/test_grafana_and_split.py) or stay refused."""
    monitors = translate([or_rule(len(ops), ops)])
    assert not any(t.OR_LEG in m.source_key for m in monitors)
    assert all(t.N_OR_SPLIT.format(count=len(ops)) not in m.reasons for m in monitors)


@pytest.mark.parametrize("first", ["or", None, "and"])
def test_the_first_operator_is_ignored(first: str | None) -> None:
    monitors = translate([or_rule(2, [first, "or"])])
    approximate(monitors)
    assert len(monitors) == 2


def test_one_failing_leg_makes_one_manual_monitor_that_names_it() -> None:
    rule = or_rule(2, exprs={"Q": "avg(invented_q) offset 5m"})
    (m,) = translate([rule])
    assert m.confidence is Confidence.MANUAL
    assert m.source_key == "u1"
    assert m.name == "r"
    (lone,) = translate([or_rule(1, exprs={"A": "avg(invented_q) offset 5m"})])
    assert m.reasons == [t.R_OR_LEG.format(count=2, position=2), lone.reasons[0]]


def test_two_data_sources_keep_the_mixed_reason() -> None:
    rule = or_rule(2)
    rule.data[1] = query("Q", "avg(invented_q)", uid="ds-2")
    (m,) = translate([rule])
    assert m.reasons == [t.R_MIXED]


@pytest.mark.parametrize(
    "change",
    ["same-query", "missing-query", "math-node"],
)
def test_other_shapes_are_not_split(change: str) -> None:
    rule = or_rule(2)
    tests = rule.data[2].model["conditions"]
    if change == "same-query":
        tests[1]["query"] = {"params": ["A"]}
    elif change == "missing-query":
        tests[1]["query"] = {"params": ["Z"]}
    else:
        rule.data.append(expr_node("M", {"type": "math", "expression": "$A + $Q"}))
        rule.condition = "M"
    assert len(translate([rule])) == 1


def test_a_single_classic_condition_is_unchanged() -> None:
    (m,) = translate([or_rule(1)])
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.source_key == "u1" and m.name == "r"
    assert t.N_OR_SPLIT.format(count=1) not in m.reasons


def test_a_long_title_keeps_the_leg_suffix_so_names_stay_distinct() -> None:
    monitors = translate([or_rule(2, title="x" * 300)])
    names = [m.name for m in monitors]
    assert len(set(names)) == 2
    assert all(len(n) <= 128 and n.endswith(f"({r})") for n, r in zip(names, "AQ", strict=True))


def test_the_emitters_make_one_resource_per_leg() -> None:
    monitors = translate([or_rule(2)])
    payloads = [nerdgraph_payload(m)["variables"]["condition"] for m in monitors]
    assert [p["name"] for p in payloads] == ["r (A)", "r (Q)"]
    tf = terraform_file(monitors)
    assert tf.count('resource "newrelic_nrql_alert_condition"') == 2
    assert tf.count("policy_id = newrelic_alert_policy.imported.id") == 2
    for m in monitors:
        assert f'_{m.source_id}" {{' in tf


def test_repeated_uids_get_distinct_leg_keys() -> None:
    first = or_rule(2)
    second = or_rule(2, title="other")
    monitors = translate([first, second])
    assert [m.source_key for m in monitors] == ["u1/or-A", "u1/or-Q", "u1#2/or-A", "u1#2/or-Q"]
    assert len({m.source_id for m in monitors}) == 4


def test_the_legs_route_like_the_rule() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [
            {"receiver": "Pager", "matchers": ['severity="critical"', 'alertname="r"']},
        ],
    }
    ex = export([email("Ops"), email("Pager", "p@example.com")], policy)
    rule = or_rule(2, labels={"severity": "critical"})
    monitors = translate([rule])
    assert all(rule_labels(m, rule, set()).name == "r" for m in monitors)
    known = run(ex, monitors, rules=[rule])
    assert known.undecidable == []
    assert [w.destination_indexes for w in workflows(known)] == [[0]]


def test_each_leg_is_paired_with_its_own_query_for_parity() -> None:
    pairs = t.translate_pairs([or_rule(2), make("avg(single)")])
    assert [m.source_key for _, m in pairs] == ["u1/or-A", "u1/or-Q", "u1#2"]
    assert [t._plan(rule).expr for rule, _ in pairs] == [
        "avg(invented_a)",
        "avg(invented_q)",
        "avg(single)",
    ]


@pytest.mark.parametrize("key", ["u1/or-A", "f/g/a/or-b/or-Q"])
def test_rule_key_drops_only_the_last_leg_part(key: str) -> None:
    assert t.rule_key(key) == key.rpartition("/or-")[0]
    assert t.rule_key("u1") == "u1"
