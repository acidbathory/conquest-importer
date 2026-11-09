"""Remediation rules and taxonomy phrases for the inventory and synthetics reasons."""

import json
import re
from pathlib import Path

import pytest

from importer.core import taxonomy
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.dynatrace.dashboards import reasons as dashboard_reasons
from importer.sources.dynatrace.inventory import reasons, remediation_rules

FIXTURES = Path(__file__).parent / "fixtures"
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
    found += dashboard_reasons.ALL
    return sorted(set(found))


@pytest.mark.parametrize("reason", reasons.ALL)
def test_each_reason_is_answered_by_its_own_rule_and_no_other_rule_shadows_it(reason: str) -> None:
    titles = {title for _, title, _ in remediation_rules.RULES}
    found = remedies_for([reason])
    assert len(found) == 1 and found[0].title in titles
    assert [t for p, t, _ in remediation_rules.RULES if p.search(reason)] == [found[0].title]
    assert steps_for([reason])


def test_there_is_one_rule_per_reason() -> None:
    titles = [title for _, title, _ in remediation_rules.RULES]
    assert len(titles) == len(set(titles)) == len(reasons.ALL)


@pytest.mark.parametrize(("reason", "category"), sorted(reasons.CATEGORY.items()))
def test_each_reason_has_the_chosen_category(reason: str, category: Category) -> None:
    assert classify(reason) is category


def test_every_reason_starts_with_the_inventory_or_synthetic_prefix() -> None:
    assert all(text.startswith((reasons.INV, reasons.SYN)) for text in reasons.ALL)
    assert len(set(reasons.ALL)) == len(reasons.ALL)


def test_our_rules_do_not_claim_reasons_of_other_sources() -> None:
    ours = {title for _, title, _ in remediation_rules.RULES}
    claimed = [
        reason for reason in other_reasons() if ours & {r.title for r in remedies_for([reason])}
    ]
    assert not claimed


def test_our_taxonomy_phrases_do_not_move_other_reasons(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {r: classify(r) for r in other_reasons()}
    patterns = {"^" + re.escape(text.lower()) for text in reasons.ALL}
    ours = [e for e in taxonomy._EXTRA if e[1].pattern in patterns]
    assert len(ours) == len(reasons.ALL)
    monkeypatch.setattr(taxonomy, "_EXTRA", [e for e in taxonomy._EXTRA if e not in ours])
    assert now == {r: classify(r) for r in other_reasons()}
