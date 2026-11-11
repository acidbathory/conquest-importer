"""Rules and taxonomy for the Azure notification reasons: each wins for its own reason only."""

import importlib
import json
import re
from pathlib import Path

from importer.core import remediation
from importer.core.notification_ir import looks_like_secret
from importer.core.playbook import playbook_for
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications import remediation_rules as rules_module

FIXTURES = Path(__file__).parent / "fixtures"
OURS = {v for k, v in vars(R).items() if k.startswith("R_") and isinstance(v, str)}
TITLES = {t for _, t, _ in rules_module.RULES}
CATEGORY = {reason: category for reason, _, _, category in rules_module.TABLE}


def test_every_reason_constant_has_a_rule_a_category_and_steps() -> None:
    assert {reason for reason, _, _, _ in rules_module.TABLE} == OURS
    assert len(TITLES) == len(rules_module.TABLE)
    for reason in OURS:
        assert steps_for([reason]), reason
        assert classify(reason) is CATEGORY[reason], reason


def test_each_reason_is_answered_by_its_own_rule_and_no_earlier_one() -> None:
    for reason, title, _, _ in rules_module.TABLE:
        assert [r.title for r in remedies_for([reason])] == [title]
        matching = [t for p, t, _ in remediation._COMPILED if p.search(reason)]
        assert matching[0] == title, (reason, matching[:3])


def test_patterns_are_anchored_and_name_exactly_one_reason() -> None:
    for pattern, _, _ in rules_module.RULES:
        assert pattern.pattern.startswith("^")
        assert len([r for r in OURS if pattern.search(r)]) == 1, pattern.pattern


def test_every_reason_is_lower_case_fixed_text_with_the_azure_prefix() -> None:
    for reason in OURS:
        assert reason.startswith("azure ") and reason == reason.lower(), reason
        assert not re.search(r"[{}<>]", reason), reason


def test_our_rules_and_taxonomy_move_nothing_of_another_source() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    assert {r: classify(r).value for r in before} == before
    claimed = {r for r in before if TITLES & {x.title for x in remedies_for([r])}}
    assert not claimed


def test_registering_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED)
    importlib.reload(rules_module)
    assert len(remediation._COMPILED) == before


def test_the_steps_carry_no_secret_shaped_text() -> None:
    for _, _, steps in rules_module.RULES:
        assert not any(looks_like_secret(s, strict=False) for s in steps)


def test_the_hard_receivers_and_add_action_groups_have_a_cookbook_entry() -> None:
    for title in (
        "Azure SMS receiver",
        "Azure voice receiver",
        "Azure app push receiver",
        "Azure Event Hub receiver",
        "Azure ARM role receiver",
        "Azure alert processing rule that adds action groups",
    ):
        assert title in TITLES
        pb = playbook_for(title)
        assert pb is not None and pb.steps and pb.links, title
    sms = playbook_for("Azure SMS receiver")
    assert sms is not None and sms.no_equivalent and "PagerDuty" in sms.newrelic


def test_manual_reasons_are_manual_followup_or_unsupported() -> None:
    manual = (Category.MANUAL_FOLLOWUP, Category.UNSUPPORTED_FEATURE)
    for reason in (R.R_SMS, R.R_VOICE, R.R_EVENT_HUB, R.R_ARM_ROLE, R.R_APP_PUSH, R.R_RULE_ADD):
        assert classify(reason) in manual
