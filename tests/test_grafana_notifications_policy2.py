"""Policy walk, round 2: injected labels, budgets, merged leaves."""

import time
from typing import Any

import pytest

from importer.sources.grafana.notifications import policy as pol
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.policy import RuleLabels, flatten_policy_tree


def plan_for(raw: dict[str, Any], *rules: RuleLabels) -> pol.RoutingPlan:
    return flatten_policy_tree(parse_policy_tree(raw), list(rules))


def route(receiver: str, *matchers: str, **kw: Any) -> dict[str, Any]:
    return {"receiver": receiver, "matchers": list(matchers), **kw}


KNOWN = RuleLabels(name="a", by_labels=frozenset(), uid="rule-uid-1", namespace_uid="ns-1")


@pytest.mark.parametrize(
    "matcher",
    [
        'datasource_uid="x"',
        'ref_id="A"',
        'datasource_uid=""',
        'ref_id=~".*"',
        '__alert_rule_uid__="other"',  # not known: this rule has no uid
        "__alert_rule_namespace_uid__=ns",
    ],
)
def test_injected_labels_that_are_not_known_are_undecidable_never_empty(matcher: str) -> None:
    raw = {"receiver": "root", "routes": [route("x", matcher)]}
    rule = RuleLabels(name="a", by_labels=frozenset())
    plan = plan_for(raw, rule)
    assert plan.entries == []
    assert plan.undecidable[0].reason == R.R_POLICY_UNDECIDABLE


def test_a_matcher_on_an_injected_label_never_falls_through_to_the_default() -> None:
    """The reviewer's example: treating the label as empty would send the rule to the default."""
    raw = {"receiver": "root", "routes": [route("x", 'datasource_uid!="prom"')]}
    plan = plan_for(raw, RuleLabels(name="a", by_labels=frozenset()))
    assert not any(e.receiver == "root" for e in plan.entries) and plan.undecidable


@pytest.mark.parametrize(
    ("matcher", "receiver"),
    [
        ('__alert_rule_uid__="rule-uid-1"', "hit"),
        ('__alert_rule_uid__="other"', "rest"),
        ('__alert_rule_namespace_uid__=~"ns-.*"', "hit"),
        ('alertname="a"', "hit"),
        ('grafana_folder="f"', "rest"),  # the folder title is "", not "f"
    ],
)
def test_injected_labels_with_a_value_known_from_the_rule_are_decided(
    matcher: str, receiver: str
) -> None:
    raw = {"receiver": "root", "routes": [route("hit", matcher), route("rest")]}
    plan = plan_for(raw, KNOWN)
    assert [e.receiver for e in plan.entries] == [receiver] and plan.undecidable == []


def test_a_rule_label_cannot_stand_in_for_an_injected_one() -> None:
    raw = {"receiver": "root", "routes": [route("x", 'datasource_uid="x"')]}
    rule = RuleLabels(name="a", labels={"datasource_uid": "x"}, by_labels=frozenset())
    assert plan_for(raw, rule).undecidable


def test_the_injected_names_are_marked_unverified_in_the_code() -> None:
    assert {"__alert_rule_uid__", "datasource_uid", "ref_id"} <= pol.INJECTED_LABELS
    assert "UNVERIFIED" in (pol.__doc__ or "")


# --- budgets ---


def test_the_work_budget_is_per_rule_not_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pol, "MAX_WORK", 15_000)
    raw = {"receiver": "root", "routes": [route(f"c{i}", f'k="{i}"') for i in range(9_999)]}
    rules = [
        RuleLabels(name=f"r{i}", labels={"k": "5000"}, by_labels=frozenset()) for i in range(40)
    ]
    plan = plan_for(raw, *rules)
    assert plan.undecidable == []
    assert sum(len(e.condition_names) for e in plan.entries) == 40


def test_one_rule_over_its_own_budget_is_limited_and_the_others_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pol, "MAX_WORK", 100)
    raw = {"receiver": "root", "routes": [route(f"c{i}", f'k="{i}"') for i in range(300)]}
    far = RuleLabels(name="far", labels={"k": "250"}, by_labels=frozenset())
    near = RuleLabels(name="near", labels={"k": "3"}, by_labels=frozenset())
    plan = plan_for(raw, far, near)
    assert [(u.rule, u.reason) for u in plan.undecidable] == [("far", R.R_POLICY_LIMIT)]
    assert [e.receiver for e in plan.entries] == ["c3"]


def test_the_wall_clock_deadline_limits_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    monkeypatch.setattr(pol, "_clock", lambda: now[0])
    monkeypatch.setattr(pol, "MAX_SECONDS", 10)
    monkeypatch.setattr(pol, "CLOCK_EVERY", 1)
    raw = {"receiver": "root", "routes": [route("x", 'k="1"')]}
    rules = [RuleLabels(name=f"r{i}", by_labels=frozenset()) for i in range(5)]

    def tick(*_a: Any) -> float:
        now[0] += 6
        return now[0]

    monkeypatch.setattr(pol, "_clock", tick)
    plan = plan_for(raw, *rules)
    assert any(u.reason == R.R_POLICY_LIMIT for u in plan.undecidable)
    assert len({u.rule for u in plan.undecidable}) >= 2


def test_many_undecided_siblings_do_not_make_the_walk_quadratic() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("u", f'p{i}="x"', **{"continue": True}) for i in range(8_000)],
    }
    start = time.monotonic()
    plan = plan_for(raw, RuleLabels(name="a", by_labels=None))
    assert time.monotonic() - start < 3
    assert len(plan.undecidable) >= 8_000


# --- merged leaves ---


def test_merged_leaves_with_different_timings_or_preferences_are_noted() -> None:
    raw = {
        "receiver": "root",
        "routes": [
            route("m", 'k="1"', group_wait="10s", group_by=["alertname"], **{"continue": True}),
            route("m", 'k="1"', group_wait="20s", group_by=["x", "y"], **{"continue": True}),
        ],
    }
    (entry,) = plan_for(raw, RuleLabels(name="a", labels={"k": "1"}, by_labels=frozenset())).entries
    assert pol.NOTE_TIMINGS in entry.notes and pol.NOTE_PREFERENCE in entry.notes
    assert R.R_POLICY_TIMING in entry.reasons


def test_a_lost_group_by_in_any_merged_leaf_is_reported() -> None:
    raw = {
        "receiver": "root",
        "routes": [
            route("m", 'k="1"', group_by=["alertname"], **{"continue": True}),
            route("m", 'k="1"', group_by=["x", "y"], **{"continue": True}),
        ],
    }
    (entry,) = plan_for(raw, RuleLabels(name="a", labels={"k": "1"}, by_labels=frozenset())).entries
    assert R.R_POLICY_TIMING in entry.reasons
