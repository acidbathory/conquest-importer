"""Remediation rules and taxonomy for the notification reasons, and the shadowing checks."""

import importlib
import json
import re
from pathlib import Path

import pytest

from importer.core import remediation
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications import remediation_rules as rules_module

FIXTURES = Path(__file__).parent / "fixtures"
C = Category

# every reason with the rule title that must answer it (first match wins across all sources)
REASONS = [
    (R.R_CP_NONE, "Grafana contact point has no New Relic destination", C.UNSUPPORTED_FEATURE),
    (R.R_CP_MANUAL, "Grafana contact point needs a manual step", C.MANUAL_FOLLOWUP),
    (R.R_CP_WEBHOOK_TEMPLATE, "Grafana contact point as a webhook template", C.MANUAL_FOLLOWUP),
    (R.R_CP_WEBHOOK_SETTINGS, "Grafana webhook settings not converted", C.APPROXIMATION),
    (R.R_CP_UNKNOWN, "Grafana contact point type not known", C.UNSUPPORTED_FEATURE),
    (R.R_CUSTOM_TEMPLATE, "Grafana custom notification template", C.MANUAL_FOLLOWUP),
    (
        R.R_POLICY_UNDECIDABLE,
        "Grafana notification policy matcher undecidable",
        C.UNSUPPORTED_QUERY,
    ),
    (R.R_POLICY_REGEX, "Grafana notification policy regular expression", C.UNSUPPORTED_QUERY),
    (R.R_POLICY_ORDER, "Grafana notification policy order", C.UNSUPPORTED_FEATURE),
    (R.R_POLICY_LIMIT, "Grafana notification policy too large", C.UNSUPPORTED_FEATURE),
    (
        R.R_POLICY_NO_RECEIVER,
        "Grafana notification policy rule without receiver",
        C.MANUAL_FOLLOWUP,
    ),
    (R.R_POLICY_TIMING, "Grafana notification policy grouping or timing", C.APPROXIMATION),
    (
        R.R_POLICY_BAD_MATCHER,
        "Grafana notification policy matcher not understood",
        C.UNSUPPORTED_QUERY,
    ),
    (R.R_POLICY_INCOMPLETE, "Grafana notification policy tree cut", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_DAYS, "Grafana mute timing days of the month", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_MONTHS, "Grafana mute timing months or years", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_LOCATION, "Grafana mute timing location", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_TIMES, "Grafana mute timing time range", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_WEEKDAYS, "Grafana mute timing weekdays", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_ACTIVE, "Grafana active time interval", C.UNSUPPORTED_FEATURE),
    (R.R_MUTE_COMBINED, "Grafana mute timing with combined fields", C.UNSUPPORTED_FEATURE),
    (
        R.R_DATASOURCE_PATH,
        "Grafana data source without a New Relic data path",
        C.UNSUPPORTED_FEATURE,
    ),
    (R.R_RULE_GROUP_UNREADABLE, "Grafana rule file group not readable", C.UNSUPPORTED_FEATURE),
]
ALL_CONSTANTS = {v for k, v in vars(R).items() if k.startswith("R_") and isinstance(v, str)}


@pytest.mark.parametrize(("reason", "title", "_category"), REASONS)
def test_each_reason_is_answered_by_its_own_rule_not_a_broader_one(
    reason: str, title: str, _category: Category
) -> None:
    assert [r.title for r in remedies_for([reason])] == [title]


@pytest.mark.parametrize(("reason", "_title", "category"), REASONS)
def test_each_reason_has_the_expected_category(
    reason: str, _title: str, category: Category
) -> None:
    assert classify(reason) is category


def test_every_reason_constant_has_a_rule_and_steps() -> None:
    assert {r for r, _, _ in REASONS} == ALL_CONSTANTS
    assert all(steps_for([r]) for r in ALL_CONSTANTS)


def test_every_rule_of_the_module_has_an_example_here() -> None:
    assert {t for _, t, _ in rules_module.RULES} == {t for _, t, _ in REASONS}


def test_every_pattern_is_anchored_and_the_titles_are_unique() -> None:
    assert all(p.pattern.startswith("^") for p, _, _ in rules_module.RULES)
    titles = [t for _, t, _ in rules_module.RULES]
    assert len(titles) == len(set(titles))


def test_no_other_registered_rule_comes_before_ours_for_our_reasons() -> None:
    """Across every rule of every source: for each reason, the first match is ours."""
    ours = {t for _, t, _ in rules_module.RULES}
    for reason in ALL_CONSTANTS:
        matching = [t for p, t, _ in remediation._COMPILED if p.search(reason)]
        assert matching and matching[0] in ours, (reason, matching[:3])


def test_our_rules_do_not_claim_reasons_of_other_sources() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    ours = {t for _, t, _ in rules_module.RULES}
    claimed = {
        r: [x.title for x in remedies_for([r])]
        for r in before
        if ours & {x.title for x in remedies_for([r])}
    }
    assert not claimed


def test_our_taxonomy_does_not_move_the_categories_of_other_sources() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    assert {r: classify(r).value for r in before} == before


def test_the_taxonomy_patterns_are_anchored_to_the_start_of_a_reason() -> None:
    assert all(pattern.startswith("^") for _, pattern in rules_module.TAXONOMY)
    for _, pattern in rules_module.TAXONOMY:
        assert not any(re.search(pattern, "see " + r) for r in ALL_CONSTANTS)


def test_registering_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED)
    importlib.reload(rules_module)
    assert len(remediation._COMPILED) == before


def test_our_patterns_name_only_the_fixed_reasons() -> None:
    for pattern, _, _ in rules_module.RULES:
        matched = [r for r in ALL_CONSTANTS if re.search(pattern.pattern, r, re.IGNORECASE)]
        assert len(matched) == 1, (pattern.pattern, matched)
