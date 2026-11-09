"""Remediation rules and taxonomy phrases for the dashboard and inventory reasons."""

import importlib
import json
from pathlib import Path

import pytest

from importer.core import remediation, taxonomy
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.dynatrace.dashboards import reasons, remediation_rules
from importer.sources.dynatrace.inventory import reasons as inventory_reasons

FIXTURES = Path(__file__).parent / "fixtures"
CATEGORY_BY_PREFIX = [
    (reasons.QUERY, Category.UNSUPPORTED_QUERY),
    (reasons.TILE, Category.UNSUPPORTED_FEATURE),
    (reasons.VARIABLE, Category.UNSUPPORTED_QUERY),
    (reasons.NOTE, Category.APPROXIMATION),
    (reasons.DATA, Category.MISSING_MAPPING),
    (reasons.FILE, Category.MANUAL_FOLLOWUP),
]
CLASSIC_CATEGORY = {
    reasons.C_METRICS: Category.MISSING_MAPPING,
    reasons.C_TILE: Category.UNSUPPORTED_FEATURE,
    reasons.C_ZONE: Category.UNSUPPORTED_FEATURE,
}
OTHER_FIXTURES = (
    ("cloudwatch", "cloudwatch"),
    ("cloudwatch", "cloudwatch-real"),
    ("gcp", "gcp"),
    ("dynatrace", "dynatrace"),
    ("grafana", "grafana"),
)


def other_reasons() -> list[str]:
    found: list[str] = []
    for source, folder in OTHER_FIXTURES:
        found += [r for m in REGISTRY[source].convert(FIXTURES / folder) for r in m.reasons]
    found += json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    found += inventory_reasons.ALL
    return sorted(set(found))


@pytest.fixture(scope="module")
def own_titles() -> set[str]:
    return {title for _, title, _ in remediation_rules.RULES}


@pytest.mark.parametrize("reason", reasons.ALL)
def test_each_reason_is_answered_by_its_own_rule_and_no_other_rule_shadows_it(
    reason: str, own_titles: set[str]
) -> None:
    found = remedies_for([reason])
    assert len(found) == 1 and found[0].title in own_titles
    owners = [t for p, t, _ in remediation_rules.RULES if p.search(reason)]
    assert owners == [found[0].title]  # exactly one of our rules claims it, and it is the winner
    assert steps_for([reason])


def test_there_is_one_rule_per_reason_and_every_title_is_unique() -> None:
    titles = [title for _, title, _ in remediation_rules.RULES]
    assert len(titles) == len(set(titles)) == len(reasons.ALL)


def test_the_rule_titles_across_every_registered_rule_are_unique_per_pattern() -> None:
    keys = [(p.pattern, title) for p, title, _ in remediation._COMPILED]
    assert len(keys) == len(set(keys))


def test_our_rules_do_not_claim_reasons_of_other_sources(own_titles: set[str]) -> None:
    claimed = {
        reason: [r.title for r in remedies_for([reason])]
        for reason in other_reasons()
        if own_titles & {r.title for r in remedies_for([reason])}
    }
    assert not claimed


@pytest.mark.parametrize("reason", reasons.ALL)
def test_every_reason_has_the_expected_category(reason: str) -> None:
    expected = CLASSIC_CATEGORY.get(reason)
    expected = expected or next(c for prefix, c in CATEGORY_BY_PREFIX if reason.startswith(prefix))
    assert classify(reason) is expected


def test_the_categories_of_the_other_sources_are_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {r: classify(r) for r in other_reasons()}
    ours = [
        (c, p) for c, p in taxonomy._EXTRA if "dynatrace" in p.pattern and "dashboard" in p.pattern
    ]
    assert len(ours) == 9
    monkeypatch.setattr(taxonomy, "_EXTRA", [e for e in taxonomy._EXTRA if e not in ours])
    assert now == {r: classify(r) for r in other_reasons()}


@pytest.mark.parametrize(
    "text",
    [
        "see dynatrace dashboard query: in the middle",
        "a dynatrace dashboard note: is not at the start",
        "xdynatrace classic dashboard: a management zone",
    ],
)
def test_the_taxonomy_phrases_are_anchored(text: str) -> None:
    assert classify(text) in (Category.OTHER, Category.UNSUPPORTED_QUERY)
    assert classify(text) is not Category.APPROXIMATION


def test_the_rules_register_once_even_when_the_module_is_reloaded() -> None:
    before = len(remediation._COMPILED)
    importlib.reload(remediation_rules)
    assert len(remediation._COMPILED) == before


def test_every_rule_pattern_is_anchored_to_its_whole_reason() -> None:
    assert all(p.pattern.startswith("^dynatrace") for p, _, _ in remediation_rules.RULES)
