"""Rules and taxonomy for the conversion reasons: each wins for its own reason, none is shadowed."""

import importlib
import json
import re
from pathlib import Path

import pytest

from importer.core import remediation
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notify_convert import reasons as C
from importer.sources.grafana.notify_convert import remediation_rules as rules_module

FIXTURES = Path(__file__).parent / "fixtures"
K = Category

REASONS = [
    (C.R_SECRET, "Grafana contact point needs a secret at apply time", K.MANUAL_FOLLOWUP),
    (C.R_NO_ADDRESS, "Grafana email contact point without an address", K.MANUAL_FOLLOWUP),
    (
        C.R_UNKNOWN_RECEIVER,
        "Grafana policy names a contact point that is not in the export",
        K.MANUAL_FOLLOWUP,
    ),
    (
        C.R_UNKNOWN_TIMING,
        "Grafana policy names a mute timing that is not in the export",
        K.MANUAL_FOLLOWUP,
    ),
    (
        C.R_NO_DESTINATION,
        "Grafana route without a destination New Relic can create",
        K.MANUAL_FOLLOWUP,
    ),
    (C.R_MAP_MISMATCH, "Grafana destinations map entry does not fit", K.MANUAL_FOLLOWUP),
    (C.R_VENDOR_UNVERIFIED, "Grafana vendor webhook payload not tested", K.APPROXIMATION),
    (C.R_MUTE_SCOPE, "Grafana mute timing as a muting rule", K.APPROXIMATION),
    (C.R_ORPHAN_MUTE, "Grafana mute timing no route uses", K.MANUAL_FOLLOWUP),
]
OURS = {v for k, v in vars(C).items() if k.startswith("R_") and isinstance(v, str)}
READ_SIDE = {v for k, v in vars(R).items() if k.startswith("R_") and isinstance(v, str)}


@pytest.mark.parametrize(("reason", "title", "_category"), REASONS)
def test_each_reason_is_answered_by_its_own_rule(reason: str, title: str, _category: K) -> None:
    assert [r.title for r in remedies_for([reason])] == [title]


@pytest.mark.parametrize(("reason", "_title", "category"), REASONS)
def test_each_reason_has_its_category(reason: str, _title: str, category: K) -> None:
    assert classify(reason) is category


def test_every_reason_constant_has_a_rule_and_steps() -> None:
    assert {r for r, _, _ in REASONS} == OURS
    assert all(steps_for([r]) for r in OURS)


def test_the_read_side_reasons_are_still_answered_by_the_read_side_rules() -> None:
    from importer.sources.grafana.notifications import remediation_rules as read_rules

    read_titles = {t for _, t, _ in read_rules.RULES}
    for reason in READ_SIDE:
        assert next(r.title for r in remedies_for([reason])) in read_titles, reason


def test_no_other_registered_rule_comes_before_ours_for_our_reasons() -> None:
    ours = {t for _, t, _ in rules_module.RULES}
    for reason in OURS:
        matching = [t for p, t, _ in remediation._COMPILED if p.search(reason)]
        assert matching and matching[0] in ours, (reason, matching[:3])


def test_our_rules_claim_no_reason_of_another_source() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    ours = {t for _, t, _ in rules_module.RULES}
    claimed = {r for r in [*before, *READ_SIDE] if ours & {x.title for x in remedies_for([r])}}
    assert not claimed


def test_our_taxonomy_moves_no_category_of_another_source() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    assert {r: classify(r).value for r in before} == before


def test_our_patterns_are_anchored_and_name_exactly_one_reason() -> None:
    assert all(p.pattern.startswith("^") for p, _, _ in rules_module.RULES)
    assert all(pattern.startswith("^") for _, pattern in rules_module.TAXONOMY)
    for pattern, _, _ in rules_module.RULES:
        matched = [r for r in OURS if re.search(pattern.pattern, r, re.IGNORECASE)]
        assert len(matched) == 1, (pattern.pattern, matched)
    for _, text in rules_module.TAXONOMY:
        assert not any(re.search(text, "see " + r) for r in OURS)


def test_no_reason_starts_with_a_phrase_the_read_side_taxonomy_would_claim_wrongly() -> None:
    """The read side registered its patterns first; a clash would move our category."""
    assert classify(C.R_MUTE_SCOPE) is K.APPROXIMATION  # not "grafana mute timing" -> unsupported
    assert classify(C.R_UNKNOWN_TIMING) is K.MANUAL_FOLLOWUP


def test_registering_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED)
    importlib.reload(rules_module)
    assert len(remediation._COMPILED) == before


def test_the_steps_carry_no_secret_shaped_text() -> None:
    from importer.core.notification_ir import looks_like_secret

    for _, _, steps in rules_module.RULES:
        assert not any(looks_like_secret(s, strict=False) for s in steps)
