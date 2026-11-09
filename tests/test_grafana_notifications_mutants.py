"""Tests that pin the small guards a mutation run showed were not covered, and a permanent check
that the notification rules and taxonomy leave every other string of the code base unchanged."""

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.core import remediation, taxonomy
from importer.core.remediation import remedies_for
from importer.core.taxonomy import classify
from importer.sources.grafana.extract import GrafanaError
from importer.sources.grafana.notifications import extract as nx
from importer.sources.grafana.notifications import loader, muting, rulefiles
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications import remediation_rules as rules_module
from importer.sources.grafana.notifications.models import (
    MAX_MATCHERS,
    MuteTiming,
    TimeInterval,
    TimeRange,
)
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.policy import RuleLabels, flatten_policy_tree

SRC = Path(__file__).parent.parent / "src" / "importer"
HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_TOKENTOKEN"  # noqa: S105 - a made-up value for leak tests


def timing(**kw: Any) -> muting.MutingPlan:
    return muting.mute_timing_to_muting(MuteTiming(name="t", intervals=[TimeInterval(**kw)]))


# --- mute timings ---


@pytest.mark.parametrize("span", [("24:00", "24:30"), ("24:00", "24:00"), ("23:59", "24:01")])
def test_24_00_is_an_end_only(span: tuple[str, str]) -> None:
    result = timing(times=[TimeRange(start=span[0], end=span[1])])
    assert result.schedules == [] and result.manual == [R.R_MUTE_TIMES]


def test_a_start_of_23_59_and_an_end_of_24_00_are_fine() -> None:
    result = timing(times=[TimeRange(start="23:59", end="24:00")])
    assert result.manual == [] and result.schedules[0].end_next_day


@pytest.mark.parametrize("years", [["2026", "2028"], ["2026:2027", "2029"], ["2030:2026"]])
def test_years_must_be_one_run(years: list[str]) -> None:
    result = timing(years=years, weekdays=["monday"])
    assert result.schedules == [] and result.manual == [R.R_MUTE_MONTHS]


# --- the extractor guards ---


@pytest.mark.parametrize(
    "base", ["https://MyStack.grafana.net", "https://mystack.grafana.net/", "mystack.grafana.net"]
)
def test_the_getter_wants_the_base_exactly_as_validated(base: str) -> None:
    with (
        pytest.raises(GrafanaError, match="exactly as validated"),
        nx.notifications_getter(
            TOKEN, base, transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ),
    ):
        pass


@pytest.mark.parametrize("token", ["has space", "", "tok\N{EN DASH}en", "line\nbreak"])
def test_the_getter_refuses_a_token_that_is_not_plain(token: str) -> None:
    with (
        pytest.raises(GrafanaError, match="plain ASCII"),
        nx.notifications_getter(
            token, HOST, transport=httpx.MockTransport(lambda _r: httpx.Response(200))
        ),
    ):
        pass


@pytest.mark.parametrize("uid", ["a/b", "x" * 41, "", "a b", "a?b", "../x"])
def test_ruler_path_refuses_an_uid_that_is_not_plain(uid: str) -> None:
    with pytest.raises(GrafanaError, match="allow-list"):
        nx.ruler_path(uid)


# --- loaders ---


def test_yaml_rule_files_are_refused_by_either_suffix(tmp_path: Path) -> None:
    for suffix in (".yml", ".yaml", ".YML"):
        path = tmp_path / f"rules{suffix}"
        path.write_text("groups: []\n")
        with pytest.raises(ValueError, match=r"^rule files must be JSON"):
            rulefiles.load_rule_file(path)


def test_the_rule_count_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rulefiles, "MAX_RULES", 2)
    rules = [{"alert": f"a{i}", "expr": "up == 0"} for i in range(5)]
    (group,) = rulefiles.load_rule_groups({"ns": [{"name": "g", "rules": rules}]})
    assert len(group.rules) == 2 and group.skipped == 3


def test_a_file_over_the_size_cap_is_a_problem_not_a_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "policies.json").write_text(json.dumps({"receiver": "r"}))
    monkeypatch.setattr(loader, "MAX_INPUT_BYTES", 5)
    export = loader.load_export(tmp_path)
    assert export.policy is None and any("larger than" in p for p in export.problems)


def test_more_matchers_than_the_cap_make_the_node_undecidable() -> None:
    matchers = [f'k{i}="v"' for i in range(MAX_MATCHERS + 50)]
    tree = parse_policy_tree({"receiver": "r", "routes": [{"receiver": "x", "matchers": matchers}]})
    node = tree.root.routes[0]
    assert len(node.matchers) == MAX_MATCHERS and node.unparsed_matchers >= 1
    labels = {f"k{i}": "v" for i in range(MAX_MATCHERS + 50)}  # every matcher holds
    plan = flatten_policy_tree(tree, [RuleLabels(name="a", labels=labels, by_labels=frozenset())])
    assert plan.undecidable[0].reason == R.R_POLICY_BAD_MATCHER


def test_an_undecidable_continue_sibling_cuts_off_the_default() -> None:
    raw = {
        "receiver": "root",
        "routes": [{"receiver": "u", "matchers": ['pod="p"'], "continue": True}],
    }
    plan = flatten_policy_tree(parse_policy_tree(raw), [RuleLabels(name="a", by_labels=None)])
    assert plan.entries == []
    assert {u.reason for u in plan.undecidable} == {R.R_POLICY_UNDECIDABLE, R.R_POLICY_ORDER}


# --- the other sources' strings do not move ---


def _constants() -> set[str]:
    """Every string constant of the code base outside this feature (a few thousand)."""
    skip = ("notifications", "datapath", "grafana_notifications.py")
    found: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        if any(part in skip or path.name in skip for part in (*path.parts, path.name)):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value.strip()
                if 8 <= len(text) <= 400 and "\n" not in text:
                    found.add(text)
    return found


def _answers(strings: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    return [(classify(s).value, tuple(r.title for r in remedies_for([s]))) for s in strings]


def test_our_rules_and_taxonomy_change_no_answer_for_any_other_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strings = sorted(_constants())
    assert len(strings) > 2000
    with_ours = _answers(strings)
    ours = {t for _, t, _ in rules_module.RULES}
    mine = {(c, p) for c, p in rules_module.TAXONOMY}
    monkeypatch.setattr(
        remediation, "_COMPILED", [r for r in remediation._COMPILED if r[1] not in ours]
    )
    monkeypatch.setattr(
        taxonomy, "_EXTRA", [e for e in taxonomy._EXTRA if (e[0], e[1].pattern) not in mine]
    )
    assert _answers(strings) == with_ours


def test_the_clock_is_also_looked_at_before_each_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from importer.sources.grafana.notifications import policy as pol

    ticks = iter(range(0, 1000, 100))
    monkeypatch.setattr(pol, "_clock", lambda: float(next(ticks)))
    monkeypatch.setattr(pol, "MAX_SECONDS", 150)
    monkeypatch.setattr(pol, "CLOCK_EVERY", 10**9)  # only the check before a rule can fire
    tree = parse_policy_tree({"receiver": "root"})
    rules = [RuleLabels(name=f"r{i}", by_labels=frozenset()) for i in range(4)]
    plan = flatten_policy_tree(tree, rules)
    assert [u.reason for u in plan.undecidable] and all(
        u.reason == R.R_POLICY_LIMIT for u in plan.undecidable
    )
    assert len(plan.entries) == 1  # the first rule ran in time, the rest were limited


def test_a_nested_settings_object_is_cut_at_the_name_cap() -> None:
    from importer.sources.grafana.notifications.contact_points import sanitize_contact_point

    nested = {"group": {f"k{i}": "v" for i in range(300)}}
    cp = sanitize_contact_point({"name": "n", "type": "other", "settings": nested})
    assert len(cp.fields_set) == 200
