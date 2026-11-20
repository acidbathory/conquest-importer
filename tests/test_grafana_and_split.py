"""One classic condition whose tests read different queries joined by AND (or AND and OR):
one condition per query plus one compound condition that combines them."""

import importlib

import pytest

from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.core.scoreboard import _rule_of
from importer.sources.grafana.translate import translate
from test_grafana import make
from test_grafana_notify_convert_routes import email, export, names_of, run
from test_grafana_or_split import approximate, or_rule

t = importlib.import_module("importer.sources.grafana.translate")


def split(monitors: list[IRMonitor]) -> tuple[list[IRMonitor], IRMonitor]:
    *parts, compound = monitors
    assert compound.compound is not None
    return parts, compound


def test_two_and_legs_become_two_parts_and_one_compound_condition() -> None:
    monitors = translate([or_rule(2, ["and", "and"], labels={"team": "invented"})])
    approximate(monitors)
    parts, compound = split(monitors)
    assert [m.source_key for m in monitors] == ["u1/and-A", "u1/and-Q", "u1/and"]
    assert len({m.source_id for m in monitors}) == 3
    assert [m.name for m in parts] == ["r (A)", "r (Q)"]
    assert all(m.compound is None for m in parts)
    assert compound.name == "r"
    assert compound.signal.kind is SignalKind.COMPOSITE
    assert compound.compound is not None
    assert compound.compound.trigger == "(A AND B)"
    assert compound.compound.components == ["u1/and-A", "u1/and-Q"]
    assert t.N_COMPOUND in compound.reasons
    assert t.N_COMPOUND_TERRAFORM in compound.reasons
    assert compound.reasons[-1] == t.N_ROUTING
    assert t.N_AND_PART.format(alias="A") in parts[0].reasons
    assert t.N_AND_PART.format(alias="B") in parts[1].reasons
    assert "invented_a" in str(parts[0].signal) and "invented_q" in str(parts[1].signal)


@pytest.mark.parametrize(
    ("ops", "trigger"),
    [
        (["and", "and"], "(A AND B)"),
        ([None, "and"], "(A AND B)"),  # the first operator is ignored (classic.go)
        (["or", "and"], "(A AND B)"),
        (["and", "and", "and"], "(A AND B AND C)"),
        (["and", "and", "or"], "((A AND B) OR C)"),
        (["and", "or", "and"], "((A OR B) AND C)"),
        (["or", "and", "or", "and"], "(((A AND B) OR C) AND D)"),
        (["and", "or", "or", "and", "and"], "((A OR B OR C) AND D AND E)"),
    ],
)
def test_the_trigger_combines_left_to_right_as_grafana_does(
    ops: list[str | None], trigger: str
) -> None:
    monitors = translate([or_rule(len(ops), ops)])
    approximate(monitors)
    parts, compound = split(monitors)
    assert len(parts) == len(ops)
    assert compound.compound is not None and compound.compound.trigger == trigger


def test_or_only_rules_still_split_without_a_compound_condition() -> None:
    monitors = translate([or_rule(3)])
    assert [m.source_key for m in monitors] == ["u1/or-A", "u1/or-Q", "u1/or-R"]
    assert all(m.compound is None for m in monitors)


def test_six_and_legs_stay_refused() -> None:
    (m,) = translate([or_rule(6, ["and"] * 6)])
    assert m.reasons == [t.R_MULTI]


@pytest.mark.parametrize(
    "ops",
    [
        ["and", None],  # Grafana refuses a missing operator after the first (classic.go)
        ["and", "logic-or"],
        ["and", "and", "xor"],
        ["and", "or", "logic-or"],
    ],
)
def test_an_unknown_operator_is_refused_with_its_own_reason(ops: list[str | None]) -> None:
    (m,) = translate([or_rule(len(ops), ops)])
    assert m.confidence is Confidence.MANUAL
    assert m.source_key == "u1"
    assert m.reasons == [t.R_AND_OPERATOR.format(count=len(ops))]


def test_one_failing_leg_makes_one_manual_monitor_that_names_it() -> None:
    rule = or_rule(3, ["and", "and", "or"], exprs={"Q": "avg(invented_q) offset 5m"})
    (m,) = translate([rule])
    assert m.confidence is Confidence.MANUAL
    assert (m.source_key, m.name) == ("u1", "r")
    (lone,) = translate([or_rule(1, exprs={"A": "avg(invented_q) offset 5m"})])
    assert m.reasons == [t.R_AND_LEG.format(count=3, position=2), lone.reasons[0]]


@pytest.mark.parametrize(
    "exprs",
    [
        {"A": "avg by (host) (invented_a)", "Q": "avg by (host) (invented_q)"},
        {"A": "avg by (host) (invented_a)", "Q": "avg(invented_q)"},
        {"A": "avg by (host) (invented_a)", "Q": "avg by (pod) (invented_q)"},
    ],
)
def test_faceted_parts_convert_and_the_payload_ignores_facets(exprs: dict[str, str]) -> None:
    """The classic condition drops labels: it fires on any series of each query, so New Relic
    must not match facet values across the parts."""
    monitors = translate([or_rule(2, ["and", "and"], exprs=exprs)])
    approximate(monitors)
    _, compound = split(monitors)
    condition = nerdgraph_payload(compound)["variables"]["condition"]
    assert condition["facetMatchingBehavior"] == "FACETS_IGNORED"


@pytest.mark.parametrize("pending", [0, 60, 90, 300])
def test_the_compound_does_not_wait_again_for_the_pending_period(pending: int) -> None:
    """Each part already waits for the pending period; New Relic's default (60s) is kept."""
    monitors = translate([or_rule(2, ["and", "and"], for_seconds=pending)])
    approximate(monitors)
    _, compound = split(monitors)
    assert "thresholdDuration" not in nerdgraph_payload(compound)["variables"]["condition"]
    assert compound.signal.window_seconds is None
    assert not any(r.startswith("the compound condition waits") for r in compound.reasons)


def test_a_paused_rule_keeps_its_notes_on_every_part_and_the_compound() -> None:
    monitors = translate([or_rule(2, ["and", "and"], is_paused=True)])
    approximate(monitors)
    assert all(m.unmapped.get("enabled") is False for m in monitors)
    assert all(t.N_PAUSED in m.reasons for m in monitors)


def test_the_nerdgraph_payload_of_the_compound_has_placeholders_for_the_parts() -> None:
    monitors = translate([or_rule(3, ["and", "and", "or"], for_seconds=300)])
    parts, compound = split(monitors)
    payload = nerdgraph_payload(compound)
    assert "alertsCompoundConditionCreate" in payload["query"]
    assert "compoundAlertCondition: $condition" in payload["query"]
    condition = payload["variables"]["condition"]
    assert condition == {
        "name": "r",
        "enabled": False,
        "triggerExpression": "((A AND B) OR C)",
        "facetMatchingBehavior": "FACETS_IGNORED",
        "componentConditions": [
            {"id": "COMPONENT_A_ID_PLACEHOLDER", "alias": "A"},
            {"id": "COMPONENT_B_ID_PLACEHOLDER", "alias": "B"},
            {"id": "COMPONENT_C_ID_PLACEHOLDER", "alias": "C"},
        ],
    }
    assert payload["variables"]["policyId"] == "POLICY_ID_PLACEHOLDER"
    for m in parts:  # the parts are ordinary NRQL conditions
        assert "alertsNrqlConditionStaticCreate" in nerdgraph_payload(m)["query"]


def test_terraform_leaves_out_compound_conditions_and_their_parts() -> None:
    monitors = translate([or_rule(2, ["and", "and"]), make("avg(single)", uid="u2")])
    tf = terraform_file(monitors)
    assert tf.count('resource "newrelic_nrql_alert_condition"') == 1
    assert "# 1 compound condition and its 2 parts are not in this file" in tf
    _, compound = split(monitors[:3])
    assert "resource" not in terraform_file([compound]).split("newrelic_alert_policy", 1)[1]


def test_the_saved_ir_of_the_compound_round_trips_and_others_have_no_compound_key() -> None:
    parts, compound = split(translate([or_rule(2, ["and", "and"])]))
    assert "compound" not in parts[0].model_dump()
    again = IRMonitor.model_validate_json(compound.model_dump_json())
    assert again == compound


@pytest.mark.parametrize("key", ["u1/and-A", "u1/and-Q", "u1/and", "f/g/a/and-b/and-Q"])
def test_rule_key_drops_the_and_part(key: str) -> None:
    expected = "f/g/a/and-b" if key.startswith("f/") else "u1"
    assert t.rule_key(key) == expected
    assert _rule_of("grafana", {"source_key": key}, "x") == expected


def test_the_compound_routes_like_the_rule_and_the_parts_do_not() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [{"receiver": "Pager", "matchers": ['alertname="r"']}],
    }
    ex = export([email("Ops"), email("Pager", "p@example.com")], policy)
    rule = or_rule(2, ["and", "and"])
    monitors = translate([rule])
    known = run(ex, monitors, rules=[rule])
    assert names_of(known) == [["r"]]


def test_each_part_is_paired_with_its_own_query_for_parity() -> None:
    pairs = t.translate_pairs([or_rule(2, ["and", "and"])])
    assert [m.source_key for _, m in pairs] == ["u1/and-A", "u1/and-Q", "u1/and"]
    assert [t._plan(rule).expr for rule, _ in pairs[:2]] == ["avg(invented_a)", "avg(invented_q)"]
