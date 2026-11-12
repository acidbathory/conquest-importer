"""Manual steps and categories for the converter's reasons: no shadowing, no claims elsewhere."""

import importlib
import json
from pathlib import Path

import pytest

from importer.core import remediation, taxonomy
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import DASHBOARD_REGISTRY, REGISTRY
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert import remediation_rules
from importer.sources.grafana.dashboards import reasons as planner_reasons

FIXTURES = Path(__file__).parent / "fixtures"


def constants(module: object, prefix: str) -> dict[str, str]:
    return {
        k: v
        for k, v in vars(module).items()
        if k.startswith(prefix) and isinstance(v, str) and v.startswith(r.PREFIX)
    }


REASONS = constants(r, "C_")
NOTES = constants(r, "N_")


def test_every_reason_constant_has_a_row_and_every_row_a_reason() -> None:
    rows = {text for text, _, _, _ in remediation_rules.ROWS}
    assert rows == set(REASONS.values())
    assert len({title for _, title, _, _ in remediation_rules.ROWS}) == len(remediation_rules.ROWS)


@pytest.mark.parametrize(("text", "title", "steps", "category"), remediation_rules.ROWS)
def test_each_reason_gets_its_own_rule_steps_and_category(
    text: str, title: str, steps: list[str], category: Category
) -> None:
    assert [x.title for x in remedies_for([text])] == [title]
    assert steps_for([text]) == steps
    assert classify(text) is category


@pytest.mark.parametrize("note", sorted(NOTES.values()))
def test_notes_are_approximations_and_need_no_manual_step(note: str) -> None:
    assert classify(note) is Category.APPROXIMATION
    assert remedies_for([note]) == []


def test_every_pattern_is_anchored_at_the_converter_wording() -> None:
    for pattern, _, _ in remediation_rules.RULES:
        assert pattern.pattern.startswith("^grafana\\ dashboard\\ convert"), pattern.pattern


def test_reasons_and_notes_are_short_fixed_sentences_with_no_placeholders() -> None:
    for text in [*REASONS.values(), *NOTES.values()]:
        # the approximation note for a sum over series is longer by design: it names all the facts
        limit = 330 if text == r.N_SUM_CARDINALITY else 160
        assert "{" not in text and text.isprintable() and len(text) < limit, text


def test_the_planner_keeps_its_own_wording_and_rules() -> None:
    for name, text in vars(planner_reasons).items():
        if name[:2] in ("P_", "Q_", "V_") and "{" not in text:
            assert not text.startswith(r.PREFIX)
            assert steps_for([text]), name
            assert next(x.title for x in remedies_for([text])).startswith("Dashboard"), name


def other_reasons() -> list[str]:
    saved = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    found = set(saved)
    for source, folder in (
        ("cloudwatch", "cloudwatch"),
        ("gcp", "gcp"),
        ("dynatrace", "dynatrace"),
        ("grafana", "grafana"),
    ):
        for monitor in REGISTRY[source].convert(FIXTURES / folder):
            found.update(monitor.reasons)
    found.update(v for v in vars(planner_reasons).values() if isinstance(v, str))
    return sorted(found)


def test_no_converter_rule_claims_a_reason_of_another_source() -> None:
    mine = {title for _, title, _, _ in remediation_rules.ROWS}
    claimed = [x for x in other_reasons() if mine & {y.title for y in remedies_for([x])}]
    assert claimed == []


def test_the_answers_for_other_sources_are_the_same_without_these_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reasons = other_reasons()
    with_mine = {x: (remedies_for([x]), classify(x)) for x in reasons}
    ours = {(p.pattern, title) for p, title, _ in remediation_rules.RULES}
    monkeypatch.setattr(
        remediation,
        "_COMPILED",
        [rule for rule in remediation._COMPILED if (rule[0].pattern, rule[1]) not in ours],
    )
    patterns = {
        remediation_rules._pattern(text).lower() for text, _, _, _ in remediation_rules.ROWS
    } | {"^" + r.NOTE.lower().replace(" ", "\\ ").replace(":", ":")}
    monkeypatch.setattr(
        taxonomy,
        "_EXTRA",
        [
            e
            for e in taxonomy._EXTRA
            if e[1].pattern not in patterns and r.PREFIX not in e[1].pattern
        ],
    )
    assert with_mine == {x: (remedies_for([x]), classify(x)) for x in reasons}


def test_registering_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED), len(taxonomy._EXTRA)
    importlib.reload(remediation_rules)
    assert (len(remediation._COMPILED), len(taxonomy._EXTRA)) == before


def test_the_first_reason_of_every_placeholder_on_the_fixtures_has_guidance() -> None:
    checked = 0
    for folder in ("grafana-dashboards", "grafana-dashboard-convert"):
        for d in DASHBOARD_REGISTRY["grafana"].convert(FIXTURES / folder):
            for w in d.widgets:
                if w.kind.value == "unsupported":
                    assert steps_for([w.reasons[0]]), w.reasons[0]
                    assert classify(w.reasons[0]) is not Category.OTHER, w.reasons[0]
                    checked += 1
    assert checked >= 20
