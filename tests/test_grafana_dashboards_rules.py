"""Manual steps and categories for dashboard reasons; no shadowing, no claims on other sources."""

import importlib
import json
from pathlib import Path

import pytest

from importer.core import remediation, taxonomy
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards import remediation_rules
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.panels import TABLE
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import build_report
from importer.sources.grafana.translate import OTHER_DATASOURCES

C = Category
FIXTURES = Path(__file__).parent / "fixtures"
LABELS = ("logs", "state-timeline", "alertlist", "gauge", "cloudwatch", "sql", "other")

# every reason, formatted, with the rule that must answer it and its category
EXAMPLES: list[tuple[str, str, Category]] = [
    (
        r.P_NO_WIDGET.format(label="alertlist"),
        "Dashboard panel type with no New Relic widget",
        C.UNSUPPORTED_FEATURE,
    ),
    (
        r.P_NO_WIDGET.format(label="other"),
        "Dashboard panel type with no New Relic widget",
        C.UNSUPPORTED_FEATURE,
    ),
    (
        r.P_WIDGET_GAP.format(label="gauge"),
        "Dashboard panel type the importer cannot create yet",
        C.UNSUPPORTED_FEATURE,
    ),
    (r.P_TRANSFORM, "Dashboard panel with transformations", C.UNSUPPORTED_FEATURE),
    (r.P_VARIABLE, "Dashboard panel using a variable that stays manual", C.UNSUPPORTED_FEATURE),
    (r.P_TIME_OVERRIDE, "Dashboard panel time override", C.UNSUPPORTED_FEATURE),
    (r.P_OPTIONS, "Dashboard panel options", C.UNSUPPORTED_FEATURE),
    (r.Q_REGEX, "Dashboard query label matcher or name", C.UNSUPPORTED_QUERY),
    (r.Q_BINARY_LABELS, "Dashboard query grouping", C.UNSUPPORTED_QUERY),
    (r.Q_GROUP_VARIABLE, "Dashboard query grouping", C.UNSUPPORTED_QUERY),
    (r.P_LIBRARY, "Dashboard library panel not in the export", C.MANUAL_FOLLOWUP),
    (r.P_NO_QUERY, "Dashboard panel without a usable query", C.UNSUPPORTED_QUERY),
    (r.P_EMPTY_QUERY, "Dashboard panel without a usable query", C.UNSUPPORTED_QUERY),
    (r.P_EXPRESSION, "Dashboard server-side expression", C.UNSUPPORTED_QUERY),
    (r.P_REUSE, "Dashboard panel that reuses another panel's result", C.UNSUPPORTED_QUERY),
    (
        r.P_DS_UNSUPPORTED.format(label="cloudwatch"),
        "Dashboard data source not supported",
        C.UNSUPPORTED_FEATURE,
    ),
    (r.P_DS_UNRESOLVED, "Dashboard data source not known", C.UNSUPPORTED_FEATURE),
    (r.Q_TOO_LONG, "Dashboard query too long or with a variable format", C.UNSUPPORTED_QUERY),
    (r.Q_VAR_FORMAT, "Dashboard query too long or with a variable format", C.UNSUPPORTED_QUERY),
    (r.Q_COMPARE, "Dashboard query comparison or vector matching", C.UNSUPPORTED_QUERY),
    (r.Q_VECTOR, "Dashboard query comparison or vector matching", C.UNSUPPORTED_QUERY),
    (r.Q_OFFSET, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_SUBQUERY, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_ABSENT, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_OVER_TIME, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_RANGE_FN, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_DELTA, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_WITHOUT, "Dashboard query time or range function", C.UNSUPPORTED_QUERY),
    (r.Q_BOTTOMK, "Dashboard query bottomk or topk with a variable", C.UNSUPPORTED_QUERY),
    (r.Q_TOPK_ARG, "Dashboard query bottomk or topk with a variable", C.UNSUPPORTED_QUERY),
    (r.Q_LABEL_FN, "Dashboard query function", C.UNSUPPORTED_QUERY),
    (r.Q_FUNCTION, "Dashboard query function", C.UNSUPPORTED_QUERY),
    (r.Q_TIME, "Dashboard query function", C.UNSUPPORTED_QUERY),
    (r.Q_ROUND_STEP, "Dashboard query function", C.UNSUPPORTED_QUERY),
    (r.Q_NESTED, "Dashboard query nested aggregation", C.UNSUPPORTED_QUERY),
    (r.Q_COUNTER, "Dashboard query on a raw counter", C.UNSUPPORTED_QUERY),
    (r.Q_MATCHER, "Dashboard query label matcher or name", C.UNSUPPORTED_QUERY),
    (r.Q_NAME, "Dashboard query label matcher or name", C.UNSUPPORTED_QUERY),
    (r.Q_HISTOGRAM, "Dashboard histogram_quantile shape", C.UNSUPPORTED_QUERY),
    (r.Q_RATE_SHAPE, "Dashboard query not converted", C.UNSUPPORTED_QUERY),
    (r.Q_NOT_SIMPLE, "Dashboard query not converted", C.UNSUPPORTED_QUERY),
    (r.QL_PARSER, "Dashboard LogQL parser stage", C.UNSUPPORTED_QUERY),
    (r.QL_FUNCTION, "Dashboard LogQL function", C.UNSUPPORTED_QUERY),
    (r.QL_REGEX, "Dashboard LogQL line filter", C.UNSUPPORTED_QUERY),
    (r.QL_WILDCARD, "Dashboard LogQL line filter", C.UNSUPPORTED_QUERY),
    (r.QL_MATCHER, "Dashboard LogQL not converted", C.UNSUPPORTED_QUERY),
    (r.QL_NOT_SIMPLE, "Dashboard LogQL not converted", C.UNSUPPORTED_QUERY),
    (r.V_QUERY_REGEX, "Dashboard variable regex", C.UNSUPPORTED_FEATURE),
    (r.V_QUERY_SHAPE, "Dashboard variable query", C.UNSUPPORTED_FEATURE),
    (r.V_QUERY_DS, "Dashboard variable query", C.UNSUPPORTED_FEATURE),
    (r.V_QUERY_RESULT, "Dashboard variable query", C.UNSUPPORTED_FEATURE),
    (r.V_NESTED, "Dashboard nested variable", C.UNSUPPORTED_FEATURE),
    (r.V_CYCLE, "Dashboard nested variable", C.UNSUPPORTED_FEATURE),
    (r.V_ADHOC, "Dashboard ad hoc variable", C.UNSUPPORTED_FEATURE),
    (r.V_NAME, "Dashboard variable name", C.UNSUPPORTED_FEATURE),
    (r.V_DUPLICATE, "Dashboard variable name", C.UNSUPPORTED_FEATURE),
    (r.V_KIND, "Dashboard variable type", C.UNSUPPORTED_FEATURE),
    (r.A_DROPPED, "Dashboard annotations", C.MANUAL_FOLLOWUP),
]


@pytest.mark.parametrize(
    ("reason", "title", "category"), EXAMPLES, ids=[e[0][:50] for e in EXAMPLES]
)
def test_each_rule_wins_for_its_own_example_over_every_registered_rule(
    reason: str, title: str, category: Category
) -> None:
    assert [x.title for x in remedies_for([reason])] == [title]
    assert steps_for([reason])
    assert classify(reason) is category


def test_every_reason_constant_has_an_example_and_every_rule_has_a_reason() -> None:
    constants = {
        v
        for k, v in vars(r).items()
        if k.isupper() and isinstance(v, str) and v.startswith("dashboard ")
    }
    covered = {reason for reason, _, _ in EXAMPLES}
    templates = {c for c in constants if "{label}" in c}
    plain = constants - templates
    assert plain <= covered, plain - covered
    for template in templates:
        assert any(template.replace("{label}", lab) in covered for lab in LABELS), template
    assert {title for _, title, _ in remediation_rules.RULES} == {t for _, t, _ in EXAMPLES}


def test_all_rules_of_this_package_are_anchored_at_the_dashboard_wording() -> None:
    for rule_pattern, _, _ in remediation_rules.RULES:
        assert rule_pattern.pattern.startswith("^dashboard "), rule_pattern.pattern
    for _, text in remediation_rules.TAXONOMY:
        assert text.startswith("^dashboard "), text


def other_reasons() -> list[str]:
    """Every reason the other sources give today: the saved list and a fresh conversion."""
    saved = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    found = set(saved)
    for source, folder in (
        ("cloudwatch", "cloudwatch"),
        ("cloudwatch", "cloudwatch-real"),
        ("gcp", "gcp"),
        ("dynatrace", "dynatrace"),
        ("grafana", "grafana"),
    ):
        for monitor in REGISTRY[source].convert(FIXTURES / folder):
            found.update(monitor.reasons)
    t = importlib.import_module("importer.sources.grafana.translate")
    found.update(
        v for k, v in vars(t).items() if isinstance(v, str) and k[:2] in ("R_", "N_", "PR", "LO")
    )
    return sorted(found)


def test_no_dashboard_rule_claims_a_reason_of_another_source() -> None:
    mine = {title for _, title, _ in remediation_rules.RULES}
    claimed = {
        reason: [x.title for x in remedies_for([reason])]
        for reason in other_reasons()
        if mine & {x.title for x in remedies_for([reason])}
    }
    assert not claimed


def test_the_answers_for_other_sources_are_the_same_with_and_without_these_rules(
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
    mine_cats = {pattern for _, pattern in remediation_rules.TAXONOMY}
    monkeypatch.setattr(
        taxonomy, "_EXTRA", [e for e in taxonomy._EXTRA if e[1].pattern not in mine_cats]
    )
    without = {x: (remedies_for([x]), classify(x)) for x in reasons}
    assert with_mine == without


def test_registering_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED), len(taxonomy._EXTRA)
    importlib.reload(remediation_rules)
    assert (len(remediation._COMPILED), len(taxonomy._EXTRA)) == before


def test_the_plan_report_names_guidance_for_every_reason_on_the_fixtures() -> None:
    plan = build_plan(load_estate(FIXTURES / "grafana-dashboards"))
    assert "no guidance yet" not in build_report(plan)
    for reason in plan.reasons:
        assert steps_for([reason]), reason
        assert classify(reason) is not C.OTHER, reason


def test_the_table_of_other_data_sources_is_covered_by_one_rule() -> None:
    for label in set(OTHER_DATASOURCES.values()):
        reason = r.P_DS_UNSUPPORTED.format(label=label)
        assert [x.title for x in remedies_for([reason])] == ["Dashboard data source not supported"]


@pytest.mark.parametrize("label", [*sorted(TABLE), "other"])
def test_every_panel_type_label_gets_its_rule(label: str) -> None:
    group = TABLE.get(label, ("none", ""))[0]
    reason = (
        r.P_WIDGET_GAP.format(label=label)
        if group == "extra"
        else r.P_NO_WIDGET.format(label=label)
    )
    if group == "core":
        return  # a core type never gives a panel-type reason
    title = [x.title for x in remedies_for([reason])]
    assert title and classify(reason) is C.UNSUPPORTED_FEATURE
