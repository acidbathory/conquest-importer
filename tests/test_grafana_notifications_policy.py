"""Flattening the notification policy tree: Alertmanager semantics, residual predicates, limits."""

import time
from typing import Any

import pytest

from importer.sources.grafana.notifications import matchers as mt
from importer.sources.grafana.notifications import policy as pol
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import PolicyTree
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.policy import (
    Predicate,
    RoutingPlan,
    RuleLabels,
    flatten_policy_tree,
)

AT = "accumulations.tag."


def tree(raw: dict[str, Any]) -> PolicyTree:
    return parse_policy_tree(raw)


def rule(name: str = "r1", by: Any = (), **labels: str) -> RuleLabels:
    """A rule whose query label set is empty unless `by` is a set, or None for 'unknown'."""
    return RuleLabels(
        name=name, labels=labels, folder="f", by_labels=None if by is None else frozenset(by)
    )


def route(receiver: str, *matchers: str, **kw: Any) -> dict[str, Any]:
    return {"receiver": receiver, "matchers": list(matchers), **kw}


def plan_for(raw: dict[str, Any], *rules: RuleLabels) -> RoutingPlan:
    return flatten_policy_tree(tree(raw), list(rules))


def receivers(plan: RoutingPlan) -> dict[str, list[str]]:
    """receiver -> rule names, over all entries."""
    found: dict[str, list[str]] = {}
    for e in plan.entries:
        found.setdefault(e.receiver, []).extend(e.condition_names)
    return found


# --- Alertmanager matcher semantics on a known label value (None = label missing) ---


@pytest.mark.parametrize(
    ("op", "value", "label", "expected"),
    [
        ("=", "a", "a", True),
        ("=", "a", "b", False),
        ("=", "a", None, False),
        ("=", "", None, True),  # a missing label is the empty string
        ("!=", "a", "b", True),
        ("!=", "a", "a", False),
        ("!=", "x", None, True),
        ("!=", "", None, False),
        ("=~", "a", "a", True),
        ("=~", "a", "ab", False),  # anchored at the end
        ("=~", "a", "ba", False),  # anchored at the start
        ("=~", "a|b", "b", True),
        ("=~", "a|b", "ab", False),  # alternation is anchored as a whole
        ("=~", "a.*", "abc", True),
        ("=~", ".*", None, True),
        ("=~", ".+", None, False),
        ("=~", ".*", "", True),
        ("!~", "x.*", None, True),
        ("!~", "x.*", "xyz", False),
        ("!~", ".+", None, True),
        ("=~", "(?i)WARN", "warn", True),
        ("=~", r"\d+", "123", True),
        ("=~", r"\d+", "٣", False),  # RE2 digits are ASCII
        ("=~", "a$", "a", True),
        ("=~", "a.b", "a\nb", False),  # dot does not match a newline
    ],
)
def test_matcher_semantics(op: str, value: str, label: str | None, expected: bool) -> None:
    assert mt.matches(op, value, label) is expected


@pytest.mark.parametrize(
    "pattern",
    [
        "(a+)+$",
        "(a|aa)+",
        "(a*)*",
        "(.*a){10}",
        "(a+)*b",
        "a*a*a*a*b",
        r"(a)\1",
        "(?=a)a",
        "(?!a)a",
        "(?<=a)a",
        "a{1,100000}",
        "x" * 300,
        "(",
        "[",
        "a**",
    ],
)
def test_unsafe_or_invalid_patterns_are_undecidable_and_fast(pattern: str) -> None:
    start = time.monotonic()
    assert mt.matches("=~", pattern, "a" * 400) is None
    assert time.monotonic() - start < 0.5


def test_a_long_label_value_is_not_matched_against_a_pattern() -> None:
    assert mt.matches("=~", "a.*", "a" * 5000) is None
    assert mt.matches("=", "a" * 5000, "a" * 5000) is True


@pytest.mark.parametrize(
    "pattern",
    ["a|b", "(warning|critical)", "x.*", ".*x", "plain", r"a\.b", ".*"],
)
def test_safe_patterns_are_accepted(pattern: str) -> None:
    assert mt.is_safe_pattern(pattern)


# --- the walk ---


def test_everything_reaches_the_root_receiver() -> None:
    plan = plan_for({"receiver": "root"}, rule("a"), rule("b"))
    assert receivers(plan) == {"root": ["a", "b"]}
    assert plan.undecidable == [] and plan.unrouted == []


def test_the_first_matching_child_wins() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("crit", 'severity="critical"'), route("any", 'team="x"'), route("late")],
    }
    plan = plan_for(raw, rule("a", severity="critical", team="x"), rule("b", team="x"), rule("c"))
    assert receivers(plan) == {"crit": ["a"], "any": ["b"], "late": ["c"]}


def test_continue_lets_later_siblings_match_too() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("one", 'team="x"', **{"continue": True}), route("two", 'team="x"')],
    }
    plan = plan_for(raw, rule("a", team="x"))
    assert receivers(plan) == {"one": ["a"], "two": ["a"]}


def test_a_node_with_no_matching_child_is_the_match() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("team", 'team="x"', routes=[route("deep", 'k="v"')])],
    }
    plan = plan_for(raw, rule("a", team="x"), rule("b", team="x", k="v"))
    assert receivers(plan) == {"team": ["a"], "deep": ["b"]}


def test_children_inherit_the_receiver_and_group_by_but_not_mute_intervals() -> None:
    raw = {
        "receiver": "root",
        "group_by": ["alertname"],
        "routes": [
            {
                "receiver": "mid",
                "matchers": ['team="x"'],
                "mute_time_intervals": ["nights"],
                "active_time_intervals": ["work"],
                "routes": [{"matchers": ['k="v"'], "group_by": ["k"]}, {"matchers": ['k="w"']}],
            }
        ],
    }
    plan = plan_for(
        raw, rule("a", team="x", k="v"), rule("b", team="x", k="w"), rule("c", team="x")
    )
    by_name = {n: e for e in plan.entries for n in e.condition_names}
    assert by_name["a"].receiver == "mid" and by_name["a"].group_by == ["k"]
    assert by_name["a"].mute_time_intervals == [] and by_name["a"].active_time_intervals == []
    assert by_name["b"] is by_name["a"] and pol.NOTE_GROUP_BY in by_name["a"].notes
    assert by_name["c"].group_by == ["alertname"] and by_name["c"].receiver == "mid"
    assert by_name["c"].mute_time_intervals == ["nights"]
    assert by_name["c"].active_time_intervals == ["work"]


def test_alertname_and_folder_are_labels_of_every_rule() -> None:
    raw = {
        "receiver": "root",
        "routes": [
            route("byname", 'alertname="HighCpu"'),
            route("byfolder", "grafana_folder=~f.*"),
        ],
    }
    plan = plan_for(raw, rule("HighCpu"), rule("Other"))
    assert receivers(plan) == {"byname": ["HighCpu"], "byfolder": ["Other"]}


def test_a_missing_static_label_is_the_empty_string_when_the_query_has_a_known_label_set() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("noteam", 'team=""'), route("notx", 'team!="x"'), route("rest")],
    }
    plan = plan_for(raw, rule("a", by=set()), rule("b", by=set(), team="y"))
    assert receivers(plan) == {"noteam": ["a"], "notx": ["b"]}


def test_an_unknown_label_set_makes_a_missing_label_undecidable() -> None:
    raw = {"receiver": "root", "routes": [route("r", 'pod="p1"')]}
    plan = plan_for(raw, rule("a", by=None))  # a bare selector may carry any label
    assert plan.entries == []
    assert next((u.rule, u.reason) for u in plan.undecidable) == ("a", R.R_POLICY_UNDECIDABLE)


def test_a_templated_rule_label_is_undecidable() -> None:
    raw = {"receiver": "root", "routes": [route("r", 'team="x"')]}
    plan = plan_for(raw, rule("a", team="{{ $labels.team }}"))
    assert plan.entries == [] and plan.undecidable[0].reason == R_UNDEC


R_UNDEC = R.R_POLICY_UNDECIDABLE


# --- dynamic labels become predicates ---


def test_a_label_of_the_query_becomes_a_tag_predicate() -> None:
    raw = {"receiver": "root", "routes": [route("pager", 'service="api"')]}
    plan = plan_for(raw, rule("a", by={"service"}))
    pager = next(e for e in plan.entries if e.receiver == "pager")
    assert pager.predicates == [
        Predicate(attribute=AT + "service", operator="EXACTLY_MATCHES", values=["api"])
    ]
    assert any("whole issue" in n for n in pager.notes)
    # nothing matches: the other series fall back to the root, with the negated predicate
    root = next(e for e in plan.entries if e.receiver == "root")
    assert root.predicates == [
        Predicate(attribute=AT + "service", operator="DOES_NOT_EXACTLY_MATCH", values=["api"])
    ]


@pytest.mark.parametrize(
    ("matcher", "predicates"),
    [
        ('service!="api"', [("DOES_NOT_EXACTLY_MATCH", ["api"])]),
        ('service=~"api|web"', [("EXACTLY_MATCHES", ["api", "web"])]),
        ('service=~"(api|web)"', [("EXACTLY_MATCHES", ["api", "web"])]),
        (
            'service!~"api|web"',
            [("DOES_NOT_EXACTLY_MATCH", ["api"]), ("DOES_NOT_EXACTLY_MATCH", ["web"])],
        ),
        ('service=~"api.*"', [("STARTS_WITH", ["api"])]),
        ('service=~".*api"', [("ENDS_WITH", ["api"])]),
        ('service=~".*"', []),
        (r'service=~"a\.b"', [("EXACTLY_MATCHES", ["a.b"])]),
    ],
)
def test_dynamic_matcher_operators(matcher: str, predicates: list[tuple[str, list[str]]]) -> None:
    raw = {"receiver": "root", "routes": [route("pager", matcher, **{"continue": True})]}
    plan = plan_for(raw, rule("a", by={"service"}))
    pager = next(e for e in plan.entries if e.receiver == "pager")
    assert [(p.operator, p.values) for p in pager.predicates] == predicates


@pytest.mark.parametrize(
    "matcher",
    [
        'service=~".+"',
        'service=~"a.*b"',
        'service!~"api.*"',
        'service=""',
        'service!=""',
        'service=~"x("',
    ],
)
def test_dynamic_matchers_that_cannot_be_expressed_are_undecidable(matcher: str) -> None:
    raw = {"receiver": "root", "routes": [route("pager", matcher)]}
    plan = plan_for(raw, rule("a", by={"service"}))
    assert not any(e.receiver == "pager" for e in plan.entries)
    assert plan.undecidable and plan.undecidable[0].rule == "a"


def test_a_regex_that_cannot_be_expressed_has_the_regex_reason() -> None:
    raw = {"receiver": "root", "routes": [route("pager", 'service=~"a.*b"')]}
    plan = plan_for(raw, rule("a", by={"service"}))
    assert plan.undecidable[0].reason == R.R_POLICY_REGEX


# --- order between conditional siblings ---


def test_a_later_sibling_holds_the_negation_of_an_earlier_conditional_one() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("api", 'service="api"'), route("web", 'service="web"')],
    }
    plan = plan_for(raw, rule("a", by={"service"}))
    web = next(e for e in plan.entries if e.receiver == "web")
    assert {(p.operator, tuple(p.values)) for p in web.predicates} == {
        ("EXACTLY_MATCHES", ("web",)),
        ("DOES_NOT_EXACTLY_MATCH", ("api",)),
    }


def test_a_continue_sibling_adds_no_negation() -> None:
    raw = {
        "receiver": "root",
        "routes": [
            route("api", 'service="api"', **{"continue": True}),
            route("web", 'service="web"'),
        ],
    }
    plan = plan_for(raw, rule("a", by={"service"}))
    web = next(e for e in plan.entries if e.receiver == "web")
    assert [p.operator for p in web.predicates] == ["EXACTLY_MATCHES"]


def test_an_earlier_sibling_with_two_predicates_cannot_be_negated() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("both", 'a="1"', 'b="2"'), route("later", 'c="3"')],
    }
    plan = plan_for(raw, rule("x", by={"a", "b", "c"}))
    assert not any(e.receiver == "later" for e in plan.entries)
    assert R.R_POLICY_ORDER in {u.reason for u in plan.undecidable}


def test_an_earlier_prefix_matcher_cannot_be_negated() -> None:
    raw = {"receiver": "root", "routes": [route("p", 'a=~"x.*"'), route("later")]}
    plan = plan_for(raw, rule("r", by={"a"}))
    assert R.R_POLICY_ORDER in {u.reason for u in plan.undecidable}
    assert not any(e.receiver == "later" for e in plan.entries)


def test_an_undecidable_earlier_sibling_blocks_the_later_ones_and_the_fallback() -> None:
    raw = {"receiver": "root", "routes": [route("u", 'pod="p"'), route("later")]}
    plan = plan_for(raw, rule("r", by=None))  # `pod` is unknown for a bare selector
    assert plan.entries == []
    assert {u.reason for u in plan.undecidable} == {R.R_POLICY_UNDECIDABLE, R.R_POLICY_ORDER}


def test_an_undecidable_continue_sibling_does_not_block_the_rest() -> None:
    raw = {
        "receiver": "root",
        "routes": [route("u", 'pod="p"', **{"continue": True}), route("later")],
    }
    plan = plan_for(raw, rule("r", by=None))
    assert receivers(plan) == {"later": ["r"]}
    assert plan.undecidable[0].reason == R.R_POLICY_UNDECIDABLE


def test_a_false_sibling_is_skipped_even_when_another_matcher_is_undecidable() -> None:
    raw = {"receiver": "root", "routes": [route("x", 'team="no"', 'pod="p"'), route("later")]}
    plan = plan_for(raw, rule("r", team="yes"))
    assert receivers(plan) == {"later": ["r"]} and plan.undecidable == []


def test_a_node_whose_matchers_could_not_be_read_is_undecidable() -> None:
    raw = {
        "receiver": "root",
        "routes": [{"receiver": "x", "matchers": ["garbage"]}, route("later")],
    }
    plan = plan_for(raw, rule("r", team="x"))
    assert plan.entries == [] and plan.undecidable[0].reason == R.R_POLICY_BAD_MATCHER


# --- grouping of the output ---


def test_rules_with_the_same_route_share_one_entry_with_sorted_names() -> None:
    raw = {"receiver": "root", "routes": [route("team", 'team="x"')]}
    plan = plan_for(raw, rule("b", team="x"), rule("a", team="x"), rule("c", team="y"))
    assert [(e.receiver, e.condition_names) for e in plan.entries] == [
        ("root", ["c"]),
        ("team", ["a", "b"]),
    ]


def test_routes_with_different_mute_intervals_stay_apart() -> None:
    raw = {
        "receiver": "root",
        "routes": [
            route("m", 'team="x"', mute_time_intervals=["n"], **{"continue": True}),
            route("m", 'team="x"', **{"continue": True}),
        ],
    }
    plan = plan_for(raw, rule("a", team="x"))
    assert sorted(e.mute_time_intervals for e in plan.entries) == [[], ["n"]]


def test_a_rule_with_simplified_routing_skips_the_tree() -> None:
    raw = {"receiver": "root", "routes": [route("tree", 'team="x"')]}
    direct = RuleLabels(name="d", labels={"team": "x"}, by_labels=frozenset(), receiver="chosen")
    plan = plan_for(raw, direct, rule("t", team="x"))
    assert receivers(plan) == {"chosen": ["d"], "tree": ["t"]}


def test_no_receiver_anywhere_is_reported_not_guessed() -> None:
    plan = plan_for({"receiver": ""}, rule("a"))
    assert plan.entries == [] and plan.unrouted == ["a"]
    assert any(R.R_POLICY_NO_RECEIVER in n for n in plan.reasons)


def test_a_tree_cut_by_a_limit_is_not_planned() -> None:
    cut = tree({"receiver": "r", "routes": [{"receiver": "x"}] * 20_000})
    plan = flatten_policy_tree(cut, [rule("a")])
    assert plan.entries == [] and plan.undecidable[0].reason == R.R_POLICY_INCOMPLETE


# --- flags and notes ---


def test_group_by_and_timings_that_are_lost_are_reported() -> None:
    raw = {
        "receiver": "root",
        "group_by": ["cluster", "alertname"],
        "group_wait": "10s",
        "routes": [],
    }
    (entry,) = plan_for(raw, rule("a")).entries
    assert R.R_POLICY_TIMING in entry.reasons
    assert entry.incident_preference == ""


@pytest.mark.parametrize(
    ("group_by", "preference"),
    [
        (None, "PER_CONDITION"),
        (["grafana_folder", "alertname"], "PER_CONDITION"),
        (["alertname"], "PER_CONDITION"),
        (["..."], "PER_CONDITION_AND_TARGET"),
    ],
)
def test_group_by_maps_to_an_incident_preference_when_it_is_close(
    group_by: list[str] | None, preference: str
) -> None:
    raw: dict[str, Any] = {"receiver": "root"}
    if group_by is not None:
        raw["group_by"] = group_by
    (entry,) = plan_for(raw, rule("a")).entries
    assert entry.incident_preference == preference and R.R_POLICY_TIMING not in entry.reasons


def test_active_time_intervals_are_flagged_for_the_complement_step() -> None:
    raw = {"receiver": "root", "routes": [route("w", 'team="x"', active_time_intervals=["work"])]}
    entry = next(e for e in plan_for(raw, rule("a", team="x")).entries if e.receiver == "w")
    assert entry.active_time_intervals == ["work"] and R.R_MUTE_ACTIVE in entry.reasons


def test_a_long_condition_list_is_split_to_fit_the_filter_limit() -> None:
    names = [f"rule-{i:05d}-" + "x" * 80 for i in range(200)]
    plan = plan_for({"receiver": "root"}, *[rule(n) for n in names])
    assert len(plan.entries) > 1
    assert sorted(n for e in plan.entries for n in e.condition_names) == sorted(names)
    for e in plan.entries:
        assert sum(len(n) + pol.PER_NAME_CHARS for n in e.condition_names) <= pol.MAX_FILTER_CHARS
        assert any("split" in n for n in e.notes)


def test_receiver_names_and_labels_never_reach_notes_or_reasons() -> None:
    raw = {
        "receiver": "SECRET-RECEIVER`'|",
        "group_by": ["SECRET-LABEL"],
        "group_wait": "5s",
        "routes": [
            route("SECRET-R2", 'SECRET-LABEL="SECRET-VALUE"', mute_time_intervals=["SECRET-M"])
        ],
    }
    plan = plan_for(raw, rule("a", by={"SECRET-LABEL"}), rule("b", by={"service"}))
    text = " ".join(
        [*plan.reasons, *(n for u in plan.undecidable for n in (u.reason, u.path))]
        + [n for e in plan.entries for n in (*e.notes, *e.reasons)]
    )
    assert "SECRET" not in text


# --- hostile trees ---


def test_a_regex_bomb_in_a_matcher_does_not_hang() -> None:
    raw = {"receiver": "root", "routes": [route("x", 'team=~"(a+)+$"')]}
    start = time.monotonic()
    plan = plan_for(raw, rule("r", team="a" * 400 + "!"))
    assert time.monotonic() - start < 0.5
    assert plan.entries == [] and plan.undecidable[0].reason == R.R_POLICY_REGEX


def test_a_fan_out_bomb_stops_at_the_work_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pol, "MAX_WORK", 5_000)
    raw = {"receiver": "root", "routes": [route("c", 'team=~"x.*"', **{"continue": True})] * 3000}
    start = time.monotonic()
    plan = plan_for(raw, *[rule(f"r{i}", team="x") for i in range(500)])
    assert time.monotonic() - start < 5
    assert any(u.reason == R.R_POLICY_LIMIT for u in plan.undecidable)


def test_a_rule_that_reaches_too_many_leaves_is_undecidable() -> None:
    raw = {
        "receiver": "root",
        "routes": [route(f"c{i}", 'team="x"', **{"continue": True}) for i in range(2000)],
    }
    plan = plan_for(raw, rule("a", team="x"))
    assert plan.undecidable[0].reason == R.R_POLICY_LIMIT and plan.entries == []


def test_many_conditional_siblings_exceed_the_predicate_limit_cleanly() -> None:
    raw = {"receiver": "root", "routes": [route(f"c{i}", f'k="{i}"') for i in range(500)]}
    plan = plan_for(raw, rule("a", by={"k"}))
    assert any(u.reason == R.R_POLICY_LIMIT for u in plan.undecidable)


def test_a_deep_tree_of_single_children_plans_to_the_leaf() -> None:
    raw: dict[str, Any] = {"receiver": "leaf", "matchers": ['team="x"']}
    for _ in range(18):
        raw = {"receiver": "n", "matchers": ['team="x"'], "routes": [raw]}
    plan = plan_for({"receiver": "root", "routes": [raw]}, rule("a", team="x"))
    assert receivers(plan) == {"leaf": ["a"]}


def test_ten_thousand_rules_over_a_big_flat_tree_finish_quickly() -> None:
    raw = {"receiver": "root", "routes": [route(f"c{i}", f'team="t{i}"') for i in range(200)]}
    rules = [rule(f"r{i}", team=f"t{i % 250}") for i in range(10_000)]
    start = time.monotonic()
    plan = plan_for(raw, *rules)
    assert time.monotonic() - start < 10
    assert sum(len(e.condition_names) for e in plan.entries) == 10_000
