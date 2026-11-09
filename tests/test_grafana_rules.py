"""Remediation rules and taxonomy wording for Grafana reasons; other sources stay unchanged."""

import importlib
import json
from pathlib import Path

import pytest

from importer.core import remediation
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.grafana import remediation_rules

# the package re-exports the function `translate`, which hides the module of the same name
t = importlib.import_module("importer.sources.grafana.translate")
C = Category
FIXTURES = Path(__file__).parent / "fixtures"

# every manual reason with the rule that must answer it (first match wins across all sources)
MANUAL_REASONS = [
    (t.R_UNREADABLE, "Grafana rule not readable"),
    (t.R_RANGE_MISSING, "Grafana query time range"),
    (t.R_AGG_MIX.format(agg="sum", reducer="mean"), "PromQL aggregation across series"),
    (t.R_LOKI_REDUCER, "LogQL range query reducer"),
    (t.R_REDUCE_MODE, "Grafana reduce mode"),
    (t.R_COUNTER_NAME, "PromQL counter name"),
    (t.R_COUNTER_AGG.format(agg="avg"), "PromQL counter rate aggregation"),
    (t.R_COUNTER_REDUCER.format(reducer="max"), "PromQL counter rate reducer"),
    (t.R_COUNTER_RANGE.format(seconds=90), "PromQL counter range"),
    (t.R_RATIO_SHAPE, "PromQL ratio not converted"),
    (t.R_RATIO_AGG.format(agg="max"), "PromQL ratio not converted"),
    (t.R_ABSENT_THRESHOLD, "PromQL absent() threshold"),
    (t.R_COMPARE_AND_NODE, "PromQL comparison inside the query"),
    (t.R_NO_COMPARISON, "Rule-file rule without a comparison"),
    (t.R_RECORD, "Grafana recording rule"),
    (t.R_DS_MANAGED, "Data source-managed rule"),
    (t.R_NO_NODE, "Grafana pipeline not converted"),
    (t.R_SHAPE, "Grafana pipeline not converted"),
    (t.R_MATH, "Grafana math expression"),
    (t.R_CLASSIC, "Grafana classic condition"),
    (t.R_SQL, "Grafana SQL expression"),
    (t.R_RESAMPLE, "Grafana resample expression"),
    (t.R_MIXED, "Grafana rule with several queries"),
    (t.R_MULTI, "Grafana rule with several queries"),
    (t.R_RANGE, "Grafana range threshold"),
    (t.R_OPERATOR, "Grafana threshold not converted"),
    (t.R_VALUE, "Grafana threshold not converted"),
    (t.R_REDUCER, "Grafana reducer not converted"),
    (t.R_DATASOURCE.format(label="cloudwatch"), "Data source not supported"),
    (t.R_AGG_WINDOW.format(seconds=7200, limit=3600), "Grafana window too long"),
    (t.R_PENDING.format(seconds=90000, limit=86400), "Grafana pending period too long"),
    ("promql uses rate, increase or another range function", "PromQL rate or over-time function"),
    ("promql uses an _over_time function", "PromQL rate or over-time function"),
    ("promql uses absent()", "PromQL absent()"),
    ("promql uses offset", "PromQL feature with no alert-condition equivalent"),
    ("promql uses a subquery", "PromQL feature with no alert-condition equivalent"),
    (
        "promql uses vector matching (on, ignoring, group_left or group_right)",
        "PromQL feature with no alert-condition equivalent",
    ),
    ("promql uses topk or bottomk", "PromQL feature with no alert-condition equivalent"),
    ("promql uses histogram_quantile", "PromQL feature with no alert-condition equivalent"),
    ("promql uses without()", "PromQL feature with no alert-condition equivalent"),
    ("promql uses a binary operator or comparison", "PromQL operator or comparison"),
    (t.PROM_MATCHER, "PromQL label matcher not converted"),
    (t.PROM_COUNTER, "PromQL counter metric"),
    (t.PROM_NAME, "PromQL name not valid in NRQL"),
    (t.PROM_NOT_SIMPLE, "PromQL not converted"),
    (t.LOKI_PARSER, "LogQL parser stage or unwrap"),
    (t.LOKI_FUNCTION, "LogQL function not converted"),
    (t.LOKI_REGEX, "LogQL regular expression line filter"),
    (t.LOKI_WILDCARD, "LogQL filter text with a wildcard character"),
    (t.LOKI_MATCHER, "LogQL label matcher not converted"),
    (t.LOKI_NOT_SIMPLE, "LogQL not converted"),
]
NOTES = [
    (t.N_PAUSED, "Paused Grafana rule"),
    (t.N_ROUTING, "Grafana notifications"),
    (t.N_KEEP_FIRING, "Grafana keep firing or recovery threshold"),
    (t.N_RECOVERY, "Grafana keep firing or recovery threshold"),
    (t.N_NODATA_DEFAULT, "Grafana no-data and error states"),
    (t.N_NODATA_KEEP, "Grafana no-data and error states"),
    (t.N_NODATA_ALERTING.format(minutes=5), "Grafana no-data and error states"),
    (t.N_ERROR_STATE, "Grafana no-data and error states"),
    (t.N_METRIC, "Metric must reach New Relic"),
    (t.N_LOGS, "Logs must reach New Relic"),
]
EXPECTED_CATEGORY = {
    **{r: C.UNSUPPORTED_FEATURE for r, _ in MANUAL_REASONS},  # then the query ones, below
    t.PROM_MATCHER: C.UNSUPPORTED_QUERY,
    t.PROM_NAME: C.UNSUPPORTED_QUERY,
    t.PROM_NOT_SIMPLE: C.UNSUPPORTED_QUERY,
    t.LOKI_MATCHER: C.UNSUPPORTED_QUERY,
    t.LOKI_NOT_SIMPLE: C.UNSUPPORTED_QUERY,
    t.LOKI_WILDCARD: C.UNSUPPORTED_QUERY,
    t.PROM_COUNTER: C.UNSUPPORTED_QUERY,
    t.R_COUNTER_NAME: C.UNSUPPORTED_QUERY,
    t.R_COUNTER_AGG.format(agg="avg"): C.UNSUPPORTED_QUERY,
    t.R_COUNTER_REDUCER.format(reducer="max"): C.UNSUPPORTED_QUERY,
    t.R_COUNTER_RANGE.format(seconds=90): C.UNSUPPORTED_QUERY,
    t.R_RATIO_SHAPE: C.UNSUPPORTED_QUERY,
    t.R_RATIO_AGG.format(agg="max"): C.UNSUPPORTED_QUERY,
    t.R_ABSENT_THRESHOLD: C.UNSUPPORTED_QUERY,
    t.R_COMPARE_AND_NODE: C.UNSUPPORTED_QUERY,
    t.N_COUNTER: C.APPROXIMATION,
    t.N_RATE_DIVISION.format(seconds=300): C.APPROXIMATION,
    t.N_RATIO_ZERO: C.APPROXIMATION,
    t.N_ABSENT: C.APPROXIMATION,
    t.N_RULE_FILE: C.APPROXIMATION,
    t.LOKI_PARSER: C.UNSUPPORTED_FEATURE,
    t.LOKI_FUNCTION: C.UNSUPPORTED_FEATURE,
    t.LOKI_REGEX: C.UNSUPPORTED_FEATURE,
    t.N_RANGE_USED: C.APPROXIMATION,
    t.N_RANGE_IGNORED: C.APPROXIMATION,
    t.N_DROPNN: C.APPROXIMATION,
    t.N_PAUSED_UNKNOWN: C.MANUAL_FOLLOWUP,
    t.N_COPIES.format(count=2): C.APPROXIMATION,
    t.N_DUP_UID: C.APPROXIMATION,
    t.N_INFERRED: C.UNVERIFIED_NAMES,
    t.N_METRIC: C.UNVERIFIED_NAMES,
    t.N_LOGS: C.UNVERIFIED_NAMES,
    t.N_PER_SERIES: C.APPROXIMATION,
    t.N_NEGATION: C.APPROXIMATION,
    t.N_LAST: C.APPROXIMATION,
    t.N_PENDING: C.APPROXIMATION,
    t.N_ROUNDED_FOR.format(seconds=90, window=60, duration=120): C.APPROXIMATION,
    t.N_INTERVAL_UNKNOWN: C.APPROXIMATION,
    t.N_ROUNDED_WINDOW.format(seconds=90, window=120): C.APPROXIMATION,
    t.N_LOKI_WINDOW.format(range=300, window=300): C.APPROXIMATION,
    t.N_COUNT_EMPTY: C.APPROXIMATION,
    t.N_PAUSED: C.MANUAL_FOLLOWUP,
    t.N_KEEP_FIRING: C.APPROXIMATION,
    t.N_RECOVERY: C.APPROXIMATION,
    t.N_NODATA_ALERTING.format(minutes=5): C.APPROXIMATION,
    t.N_NODATA_DEFAULT: C.APPROXIMATION,
    t.N_NODATA_KEEP: C.APPROXIMATION,
    t.N_ERROR_STATE: C.APPROXIMATION,
    t.N_ROUTING: C.MANUAL_FOLLOWUP,
}


@pytest.mark.parametrize(("reason", "title"), [*MANUAL_REASONS, *NOTES])
def test_a_specific_rule_is_not_shadowed_by_a_broader_one(reason: str, title: str) -> None:
    assert [r.title for r in remedies_for([reason])] == [title]


@pytest.mark.parametrize(("reason", "category"), sorted(EXPECTED_CATEGORY.items()))
def test_every_grafana_reason_has_a_sensible_category(reason: str, category: Category) -> None:
    assert classify(reason) is category


def test_every_manual_reason_has_steps() -> None:
    assert all(steps_for([reason]) for reason, _ in MANUAL_REASONS)


def test_every_rule_in_the_module_has_an_example_in_these_tests() -> None:
    titles = {title for _, title in [*MANUAL_REASONS, *NOTES]}
    assert {title for _, title, _ in remediation_rules.RULES} == titles


def test_the_grafana_rules_do_not_claim_reasons_of_other_sources() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    grafana_titles = {title for _, title, _ in remediation_rules.RULES}
    claimed = {
        reason: [r.title for r in remedies_for([reason])]
        for reason in before
        if grafana_titles & {r.title for r in remedies_for([reason])}
    }
    assert not claimed


def test_the_categories_of_the_other_sources_are_unchanged() -> None:
    before = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
    monitors = []
    for source, folder in (
        ("cloudwatch", "cloudwatch"),
        ("cloudwatch", "cloudwatch-real"),
        ("gcp", "gcp"),
        ("dynatrace", "dynatrace"),
    ):
        monitors += REGISTRY[source].convert(FIXTURES / folder)
    now = {r: classify(r).value for m in monitors for r in m.reasons}
    assert now == before


@pytest.mark.parametrize(
    "reason",
    [
        "the promql expression is long",
        "a grafana pipeline is mentioned here",
        "this data source type text is not at the start",
        "my recording rule: is somewhere inside",
        "see logql uses elsewhere",
    ],
)
def test_the_grafana_taxonomy_phrases_are_anchored(reason: str) -> None:
    assert classify(reason) in (C.OTHER, C.UNSUPPORTED_QUERY)
    assert classify(reason) is not C.UNSUPPORTED_FEATURE


def test_every_reason_the_fixtures_produce_is_categorised() -> None:
    reasons = {r for m in REGISTRY["grafana"].convert(FIXTURES / "grafana") for r in m.reasons}
    assert reasons
    assert not sorted(r for r in reasons if classify(r) is C.OTHER)


def test_registering_the_grafana_rules_twice_adds_nothing() -> None:
    before = len(remediation._COMPILED)
    importlib.reload(remediation_rules)
    assert len(remediation._COMPILED) == before
