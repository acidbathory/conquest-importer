"""Remediation and taxonomy rules of the Dynatrace classic source.

Rules are first-match over every source's reasons. The classes of mistake: an older rule answers
a new reason (shadowing), a new rule answers an older source's reason (claiming), and a reason
nobody wrote a rule for. Each has a test here.
"""

import importlib
import re
from functools import cache
from pathlib import Path

import pytest

from importer.core import improve, remediation, taxonomy
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.base import EXTRA_FIXTURES
from importer.sources.dynatrace.classic import remediation_rules, taxonomy_rules
from importer.sources.dynatrace.classic.models import load_objects
from importer.sources.dynatrace.classic.translate import translate

ROOT = Path(__file__).parent.parent
MINE = "dynatrace-classic"

# one real reason for each of this source's rules, by title
EXAMPLES: dict[str, str] = {
    "Dynatrace object could not be read": "the settings object could not be read: "
    "queryDefinition is missing",
    "Metric selector not converted": "metric selector not converted: the fold transformation is "
    "not supported: it collapses the series to one point, and an alert watches a stream",
    "No New Relic data for this Dynatrace metric": "no New Relic signal is known for Dynatrace "
    "metric 'builtin:host.disk.avail'; to get the data: install the New Relic infrastructure agent",
    "Dimension not in the metric map": "dimension 'dt.entity.disk' is not in the metric map for "
    "the metric used",
    "Metrics cannot share one condition": "the metrics live on different New Relic events and "
    "cannot share one condition",
    "Filter that New Relic cannot express": "the event is limited to a management zone, which New "
    "Relic does not offer",
    "Samples or window out of range": "violating samples (6) are more than the sliding window "
    "(5 samples)",
    "Metric event with an unsupported definition": "alert condition 'OUTSIDE' needs a baseline "
    "model; a static threshold is ABOVE or BELOW",
    "Davis baseline detector": "baseline detectors (auto-adaptive and seasonal) are not converted "
    "yet: only a static-threshold detector over a simple timeseries is",
    "Davis records-mode detector": "records mode detectors are not converted: arbitrary DQL has "
    "no NRQL equivalent",
    "Davis detector outside the supported pattern": "the detector query is not a simple "
    "timeseries: only `timeseries [name =] avg|min|max|sum(metric)` converts",
    "Number in the export is not finite": "the converted threshold is not a finite number",
    "Alerting profile": "alerting profile is not converted (3 severity rules, 0 event filters): "
    "it decides which problems notify whom",
    "Problem notification": "problem notification is not converted (type SLACK, enabled): "
    "recreate it as a New Relic destination",
    "Maintenance window": "maintenance window is not converted (schedule WEEKLY, suppression "
    "DONT_DETECT_PROBLEMS): recreate it as a New Relic muting rule",
    "Management zone": "management zone is not converted (2 rules): a zone is a set of entities",
    "Built-in anomaly detection setting": "built-in detection setting is not converted: "
    "Davis detects these problems automatically",
    "Settings schema not read": "settings schema is not converted: 'builtin:x' is not one of the "
    "schemas this source reads",
    "Event type that opens no problem": "the event type INFO does not open a problem in Dynatrace "
    "(it is informational), so it is not a paging condition",
    "The data must reach New Relic": "data prerequisite: install the New Relic infrastructure "
    "agent on the hosts",
    "Metric name mapping not confirmed": "metric name mapping is unverified for "
    "'builtin:host.cpu.usage': the New Relic name comes from the research note",
    "Violating samples approximated": "3 of 5 violating samples (not necessarily in a row) "
    "becomes 3 consecutive one-minute breaches, which is stricter",
    "Timing differs from Dynatrace": "dealertingSamples 5 has no equivalent: New Relic closes "
    "the alert event when the data stops breaching",
    "Baseline model differs": "sensitivity (signalFluctuation or tolerance) has no principled "
    "conversion; the band is set to 3 standard deviations",
    "Selector detail not applied": "default(0) is not applied: the converted condition has no "
    "gap fill",
    "Disabled in Dynatrace": "the Dynatrace metric event was disabled; the New Relic condition "
    "is created disabled too",
}


@cache
def reasons_of_other_sources() -> tuple[str, ...]:
    found: set[str] = set()
    for name, source in REGISTRY.items():
        if name == MINE:
            continue
        for folder in [*improve.FIXTURES.get(name, []), *EXTRA_FIXTURES.get(name, [])]:
            if (ROOT / folder).exists():
                found |= {r for m in source.convert(ROOT / folder) for r in m.reasons}
    return tuple(sorted(found))


@cache
def my_reasons() -> tuple[str, ...]:
    objects = load_objects(ROOT / "tests" / "fixtures" / "dynatrace-classic")
    return tuple(sorted({r for m in translate(objects) for r in m.reasons}))


def first_rule(reason: str, rules: list[remediation.Rule]) -> str:
    for pattern, title, _steps in rules:
        if pattern.search(reason):
            return title
    return ""


def registered() -> list[remediation.Rule]:
    import importer.sources  # noqa: F401

    return list(remediation._COMPILED)


def test_every_rule_has_exactly_one_example() -> None:
    titles = [title for _, title, _ in remediation_rules.RULES]
    assert sorted(titles) == sorted(EXAMPLES) and len(set(titles)) == len(titles)


@pytest.mark.parametrize(("title", "reason"), sorted(EXAMPLES.items()))
def test_each_rule_wins_for_its_own_example(title: str, reason: str) -> None:
    assert [r.title for r in remedies_for([reason])] == [title]


def test_every_pattern_is_anchored() -> None:
    for pattern, title, _ in remediation_rules.RULES:
        assert pattern.pattern.startswith(("^", "(?:^")), title


def test_this_sources_rules_do_not_claim_reasons_of_other_sources() -> None:
    mine = {p.pattern for p, _, _ in remediation_rules.RULES}
    rules = registered()
    others = [r for r in rules if r[0].pattern not in mine]
    claimed = [
        (reason, first_rule(reason, rules), first_rule(reason, others))
        for reason in reasons_of_other_sources()
        if first_rule(reason, rules) != first_rule(reason, others)
    ]
    assert not claimed, claimed
    assert len(reasons_of_other_sources()) > 50  # the check had material to work on


def test_every_reason_of_the_fixtures_has_manual_steps() -> None:
    assert len(my_reasons()) > 30
    missing = [r for r in my_reasons() if not steps_for([r])]
    assert not missing, missing


def test_every_reason_of_the_fixtures_is_answered_by_one_of_these_rules() -> None:
    mine = {title for _, title, _ in remediation_rules.RULES}
    wrong = [
        (r, first_rule(r, registered()))
        for r in my_reasons()
        if first_rule(r, registered()) not in mine
    ]
    assert not wrong, wrong


def test_registering_twice_adds_nothing() -> None:
    before = len(registered())
    importlib.reload(remediation_rules)
    assert len(registered()) == before
    extra = len(taxonomy._EXTRA)
    importlib.reload(taxonomy_rules)
    assert len(taxonomy._EXTRA) == extra


# ----- taxonomy ---------------------------------------------------------------


def test_every_taxonomy_pattern_is_anchored_and_lower_case() -> None:
    for _category, pattern in taxonomy_rules.RULES:
        assert pattern.startswith("^") and pattern == pattern.lower()
        re.compile(pattern)


@pytest.mark.parametrize(("title", "reason"), sorted(EXAMPLES.items()))
def test_every_example_reason_is_categorised(title: str, reason: str) -> None:
    assert classify(reason) is not Category.OTHER, title


def test_every_reason_of_the_fixtures_is_categorised() -> None:
    other = [r for r in my_reasons() if classify(r) is Category.OTHER]
    assert not other, other


@pytest.mark.parametrize(
    ("reason", "category"),
    [
        ("no New Relic signal is known for Dynatrace metric 'x'", Category.MISSING_MAPPING),
        ("metric name mapping is unverified for 'x': from the note", Category.UNVERIFIED_NAMES),
        ("alerting profile is not converted (1 severity rules)", Category.UNSUPPORTED_FEATURE),
        ("metric selector not converted: unknown transformation 'x'", Category.UNSUPPORTED_QUERY),
        ("3 of 5 violating samples (not necessarily in a row)", Category.APPROXIMATION),
        ("data prerequisite: install the agent", Category.MANUAL_FOLLOWUP),
    ],
)
def test_taxonomy_examples(reason: str, category: Category) -> None:
    assert classify(reason) is category


def test_the_categories_of_the_other_sources_are_unchanged() -> None:
    with_mine = {r: classify(r) for r in reasons_of_other_sources()}
    saved = list(taxonomy._EXTRA)
    mine = {re.compile(p) for _, p in taxonomy_rules.RULES}
    try:
        taxonomy._EXTRA[:] = [e for e in saved if e[1] not in mine]
        without_mine = {r: classify(r) for r in reasons_of_other_sources()}
    finally:
        taxonomy._EXTRA[:] = saved
    assert with_mine == without_mine
