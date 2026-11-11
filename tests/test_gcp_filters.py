"""Cloud Monitoring and Cloud Logging filter expressions: grammar, scope and NRQL."""

from typing import Any

import pytest

from importer.core.dashboard_ir import IRWidget
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, TagFilter
from importer.sources.gcp.dashboard_models import GcpDashboard
from importer.sources.gcp.dashboards import translate_dashboard
from importer.sources.gcp.filter import (
    FilterError,
    Group,
    Term,
    conjuncts,
    parse_expression,
    parse_filter,
    unquote_field,
)
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.scope import ScopeFail, label_name, metric_filter, regex_to_match
from importer.sources.gcp.translate import translate_policy

METRIC = 'metric.type="compute.googleapis.com/instance/cpu/utilization"'


def condition(filter_text: str, kind: str = "conditionThreshold") -> IRMonitor:
    body: dict[str, Any] = {"filter": filter_text}
    if kind == "conditionThreshold":
        body |= {
            "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
            "comparison": "COMPARISON_GT",
            "thresholdValue": 0.8,
            "duration": "600s",
        }
    policy = GcpAlertPolicy.model_validate(
        {"displayName": "p", "combiner": "OR", "conditions": [{"displayName": "c", kind: body}]}
    )
    (monitor,) = translate_policy(policy)
    return monitor


def chart(filter_text: str, aggregation: dict[str, Any] | None = None) -> IRWidget:
    series: dict[str, Any] = {"filter": filter_text}
    if aggregation is not None:
        series["aggregation"] = aggregation
    raw = {
        "displayName": "d",
        "gridLayout": {
            "widgets": [
                {
                    "title": "w",
                    "xyChart": {"dataSets": [{"timeSeriesQuery": {"timeSeriesFilter": series}}]},
                }
            ]
        },
    }
    return translate_dashboard(GcpDashboard.model_validate(raw)).widgets[0]


# ----- the quoted label name bug ---------------------------------------------------------------


def test_a_quoted_label_name_is_the_same_label_as_the_unquoted_one() -> None:
    assert label_name('resource.label."project_id"') == "gcp.projectId"
    assert label_name('metric.label."type"') == "metric.type"
    assert label_name('resource.labels."zone"') == "gcp.zone"


def test_group_by_with_a_quoted_label_name_converts() -> None:
    widget = chart(
        METRIC,
        {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_MEAN",
            "crossSeriesReducer": "REDUCE_MEAN",
            "groupByFields": ['resource.label."project_id"', 'metric.label."type"'],
        },
    )
    assert widget.confidence is Confidence.APPROXIMATE
    assert widget.series[0].signal.group_by == ["gcp.projectId", "metric.type"]


def test_filter_with_a_quoted_label_name_converts() -> None:
    widget = chart(f'{METRIC} metric.label."type"="search"')
    assert widget.confidence is Confidence.APPROXIMATE
    assert widget.series[0].signal.scope == [TagFilter(key="metric.type", value="search")]


def test_a_quoted_label_name_that_is_not_a_valid_attribute_stays_manual() -> None:
    m = condition(f'{METRIC} AND metadata.user_labels."jobset.sigs.k8s.io/name"="x"')
    assert m.confidence is Confidence.MANUAL
    m = condition(f'{METRIC} AND metric.label."a/b"="x"')
    assert m.confidence is Confidence.MANUAL and "valid New Relic attribute name" in m.reasons[0]


def test_a_quote_inside_a_quoted_label_cannot_reach_nrql() -> None:
    m = condition(f"""{METRIC} AND metric.label."a'b"="x" """)
    assert m.confidence is Confidence.MANUAL


# ----- the grammar -----------------------------------------------------------------------------


def test_unquote_field_reads_quoted_segments_and_nothing_else() -> None:
    assert unquote_field('labels."a.b/c"') == "labels.a.b/c"
    assert unquote_field("jsonPayload.@type") == "jsonPayload.@type"
    with pytest.raises(FilterError):
        unquote_field("a b")


def test_functions_after_an_equals_sign_are_read() -> None:
    (one,) = parse_filter('zone = one_of("a", "b")')
    assert one == Term("zone", "in", values=("a", "b"))
    (neg,) = parse_filter('zone != one_of("a")')
    assert neg.negate and neg.op == "in"
    (start,) = parse_filter('name = starts_with("prod-")')
    assert (start.op, start.value) == ("starts_with", "prod-")
    (sub,) = parse_filter('name=has_substring("x")')
    assert sub.op == "has_substring"
    (regex,) = parse_filter('name = monitoring.regex.full_match("a.*")')
    assert (regex.op, regex.value) == ("regex_full", "a.*")


def test_or_and_parentheses_build_a_tree() -> None:
    node = parse_expression('(a="1" OR a="2") AND b="3"')
    first, second = conjuncts(node)
    assert isinstance(first, Group) and first.op == "OR" and len(first.items) == 2
    assert second == Term("b", "=", "3")


def test_not_is_pushed_down_with_de_morgan() -> None:
    node = parse_expression('NOT (a="1" OR b="2")')
    assert node == Group("AND", (Term("a", "=", "1", True), Term("b", "=", "2", True)))
    assert parse_filter('-a="1"') == [Term("a", "=", "1", True)]
    assert parse_filter('NOT NOT a="1"') == [Term("a", "=", "1")]


def test_a_value_list_in_parentheses_repeats_the_field() -> None:
    node = parse_expression('resource.type=("a" OR "b")')
    assert node == Group("OR", (Term("resource.type", "=", "a"), Term("resource.type", "=", "b")))


def test_mixing_and_with_or_without_parentheses_is_refused() -> None:
    with pytest.raises(FilterError, match="mixes AND and OR"):
        parse_expression('a="1" OR b="2" c="3"')
    with pytest.raises(FilterError, match="mixes AND and OR"):
        parse_expression('a="1" AND b="2" OR c="3"')


@pytest.mark.parametrize(
    "text",
    [
        'a="1" AND',
        'a="1" OR OR b="2"',
        '(a="1"',
        'a="1")',
        "a=one_of()",
        'a=unknown_fn("x")',
        'a>one_of("x")',
        'a=starts_with("x","y")',
        "f(x)",
        'select_slo_health("x")',
        'a="unterminated',
        "(" * 100 + 'a="1"' + ")" * 100,
        "a=" + '"x"' * 3 + "(",
    ],
)
def test_malformed_filters_are_rejected_not_guessed(text: str) -> None:
    with pytest.raises(FilterError):
        parse_expression(text)


def test_a_very_long_filter_is_rejected() -> None:
    with pytest.raises(FilterError, match="too long"):
        parse_expression('a="1" ' * 10_000)


def test_a_slo_selector_is_named_in_the_reason() -> None:
    m = condition('select_slo_burn_rate("projects/p/services/s/serviceLevelObjectives/o", "3600s")')
    assert m.confidence is Confidence.MANUAL
    assert "select_slo_burn_rate" in m.reasons[0] and "service level" in m.reasons[0]


def test_escaped_quotes_and_backslashes_in_values_are_read() -> None:
    (term,) = parse_filter(r'a="x\"y\\z"')
    assert term.value == 'x"y\\z'


# ----- scope: what a metric filter means -------------------------------------------------------


def scope(text: str) -> list[TagFilter]:
    return metric_filter(f"{METRIC} AND {text}").filters


def test_one_of_becomes_an_in_list() -> None:
    assert scope('resource.label.zone = one_of("a","b")') == [
        TagFilter(key="gcp.zone", values=["a", "b"])
    ]
    assert scope('resource.label.zone != one_of("a","b")') == [
        TagFilter(key="gcp.zone", values=["a", "b"], negate=True)
    ]


def test_an_or_of_one_label_becomes_an_in_list() -> None:
    assert scope('(resource.labels.zone = "a" OR resource.labels.zone = "b")') == [
        TagFilter(key="gcp.zone", values=["a", "b"])
    ]
    assert scope('resource.labels.zone = ("a" OR "b" OR "a")') == [
        TagFilter(key="gcp.zone", values=["a", "b"])
    ]


def test_parenthesised_ands_and_not_are_flattened() -> None:
    found = scope('(metric.labels.a = "1" AND metric.labels.b = "2") AND NOT metric.labels.c = "3"')
    assert found == [
        TagFilter(key="metric.a", value="1"),
        TagFilter(key="metric.b", value="2"),
        TagFilter(key="metric.c", value="3", negate=True),
    ]


def test_not_over_an_or_becomes_two_not_equals() -> None:
    found = scope('NOT (metric.labels.a = "1" OR metric.labels.a = "2")')
    assert found == [
        TagFilter(key="metric.a", value="1", negate=True),
        TagFilter(key="metric.a", value="2", negate=True),
    ]


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ('metadata_free = starts_with("prod-")', "prod-*"),
        ('metadata_free = ends_with("-prod")', "*-prod"),
        ('metadata_free = has_substring("prod")', "*prod*"),
        ('metadata_free : "prod"', "*prod*"),
    ],
)
def test_string_functions_become_like_patterns(text: str, pattern: str) -> None:
    (found,) = scope(text.replace("metadata_free", "metric.labels.name"))
    assert found == TagFilter(key="metric.name", value=pattern, wildcard=True)


@pytest.mark.parametrize("text", ['starts_with("a*")', 'has_substring("50%")'])
def test_like_patterns_with_wildcard_characters_stay_manual(text: str) -> None:
    with pytest.raises(ScopeFail, match="wildcard"):
        scope(f"metric.labels.name = {text}")


@pytest.mark.parametrize(
    ("pattern", "full", "expected"),
    [
        ("prod", True, ("eq", ["prod"])),
        ("a|b|c", True, ("in", ["a", "b", "c"])),
        ("prod-.*", True, ("like", ["prod-*"])),
        (".*-prod", True, ("like", ["*-prod"])),
        (".*mid.*", True, ("like", ["*mid*"])),
        (r"a\.b", True, ("eq", ["a.b"])),
        ("mid", False, ("like", ["*mid*"])),
        ("^start", False, ("like", ["start*"])),
        ("end$", False, ("like", ["*end"])),
        ("^exact$", False, ("eq", ["exact"])),
    ],
)
def test_simple_regexes_have_an_exact_equivalent(
    pattern: str, full: bool, expected: tuple[str, list[str]]
) -> None:
    assert regex_to_match(pattern, full=full) == expected


@pytest.mark.parametrize(
    "pattern", ["a.+b", "[0-9]+", "(a|b)c", r"\d+", "a*", "a?", "a|.*b", r"a\*", "50%", "a\\", ".*"]
)
def test_regexes_without_an_exact_equivalent_are_not_converted(pattern: str) -> None:
    assert regex_to_match(pattern, full=True) is None


def test_regex_full_match_converts_when_it_is_simple_and_is_manual_otherwise() -> None:
    assert scope('metric.labels.pod = monitoring.regex.full_match("api-.*")') == [
        TagFilter(key="metric.pod", value="api-*", wildcard=True)
    ]
    assert scope('metric.labels.pod != monitoring.regex.full_match("a|b")') == [
        TagFilter(key="metric.pod", values=["a", "b"], negate=True)
    ]
    m = condition(f'{METRIC} AND metric.labels.pod = monitoring.regex.full_match("a[0-9]+")')
    assert m.confidence is Confidence.MANUAL
    assert "RLIKE" in m.reasons[0] and "'a[0-9]+'" in m.reasons[0]


def test_an_or_between_different_labels_is_manual_with_the_reason() -> None:
    m = condition(f'{METRIC} AND (metric.labels.a = "1" OR metric.labels.b = "2")')
    assert m.confidence is Confidence.MANUAL
    assert "OR between different labels" in m.reasons[0] and "metric.labels.a" in m.reasons[0]


def test_an_or_of_not_equal_values_is_manual() -> None:
    m = condition(f'{METRIC} AND (metric.labels.a != "1" OR metric.labels.a != "2")')
    assert m.confidence is Confidence.MANUAL


def test_metric_type_functions_select_several_metrics_and_stay_manual() -> None:
    for text in (
        'metric.type = starts_with("compute.googleapis.com/instance/")',
        'metric.type = one_of("a.googleapis.com/b", "a.googleapis.com/c")',
        '(metric.type = "a.googleapis.com/b" OR metric.type = "a.googleapis.com/c")',
    ):
        m = condition(text)
        assert m.confidence is Confidence.MANUAL
        assert "selects several metrics" in m.reasons[0]


def test_resource_type_lists_are_notes_not_filters() -> None:
    found = metric_filter(f'{METRIC} AND resource.type = one_of("gce_instance", "gke_container")')
    assert found.filters == [] and "gce_instance" in found.notes[0]
    found = metric_filter(f'{METRIC} AND (resource.type = "a" OR resource.type = "b")')
    assert found.filters == [] and found.notes


def test_a_dashboard_variable_term_is_noted_not_unreadable() -> None:
    widget = chart(f'{METRIC} resource.type="gce_instance" ${{job_id}}')
    assert widget.confidence is Confidence.APPROXIMATE
    assert any("job_id" in r and "not applied" in r for r in widget.reasons)


def test_ordering_on_a_label_is_manual() -> None:
    m = condition(f"{METRIC} AND metric.labels.code >= 500")
    assert m.confidence is Confidence.MANUAL and "compares with >=" in m.reasons[0]


# ----- the NRQL, with the shared escaping ------------------------------------------------------


def test_the_nrql_uses_in_like_and_not_in() -> None:
    m = condition(
        f'{METRIC} AND resource.labels.zone = one_of("a", "b") '
        'AND metric.labels.pod = starts_with("api-") AND metric.labels.env != one_of("x")'
    )
    assert nrql_for(m) == (
        "SELECT average(gcp.compute.instance.cpu.utilization) FROM Metric "
        "WHERE gcp.zone IN ('a', 'b') AND metric.pod LIKE 'api-%' AND metric.env NOT IN ('x')"
    )


def test_hostile_values_are_escaped_in_every_form() -> None:
    hostile = "x' OR 1=1 --"
    m = condition(
        f'{METRIC} AND metric.labels.a = one_of("{hostile}", "b") '
        f'AND metric.labels.b = starts_with("{hostile}") AND metric.labels.c = "{hostile}"'
    )
    assert m.confidence is Confidence.APPROXIMATE
    nrql = nrql_for(m)
    assert "IN ('x\\' OR 1=1 --', 'b')" in nrql
    assert "LIKE 'x\\' OR 1=1 --%'" in nrql
    assert "metric.c = 'x\\' OR 1=1 --'" in nrql


def test_a_newline_in_a_value_never_reaches_nrql() -> None:
    m = condition(f'{METRIC} AND metric.labels.a = "x\ny"')
    assert m.confidence is Confidence.MANUAL or "\n" not in nrql_for(m)


# ----- Cloud Logging ---------------------------------------------------------------------------


def logs(text: str) -> IRMonitor:
    return condition(text, kind="conditionMatchedLog")


def test_log_id_and_an_or_group_convert() -> None:
    m = logs(
        'log_id("container.googleapis.com/notifications") AND '
        '(severity=ERROR OR severity=CRITICAL) AND resource.type=("gke_cluster" OR "gke_nodepool")'
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE logName LIKE '%2Fnotifications' "
        "AND severity IN ('ERROR', 'CRITICAL') "
        "AND resource.type IN ('gke_cluster', 'gke_nodepool')"
    )
    assert any("log_id()" in r for r in m.reasons)


def test_a_plain_log_id_matches_the_end_of_the_log_name() -> None:
    assert "logName LIKE '%/logs/syslog'" in nrql_for(logs('log_id("syslog")'))


def test_not_and_search_regex_in_a_log_filter() -> None:
    m = logs('NOT textPayload:"healthcheck" AND textPayload=~"timeout" AND jsonPayload.code!="0"')
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE textPayload NOT LIKE '%healthcheck%' "
        "AND textPayload LIKE '%timeout%' AND jsonPayload.code != '0'"
    )


def test_a_log_regex_with_real_syntax_stays_manual_with_the_filter_comparison_reason() -> None:
    m = logs('textPayload=~"timeout after [0-9]+ms"')
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith("log filter comparison on 'textPayload'")


def test_a_log_or_between_different_fields_is_manual() -> None:
    m = logs('(severity=ERROR OR textPayload:"x")')
    assert m.confidence is Confidence.MANUAL and "OR between different fields" in m.reasons[0]


def test_a_log_field_nrql_cannot_name_is_manual_not_an_emit_failure() -> None:
    m = logs('jsonPayload.@type="type.googleapis.com/x"')
    assert m.confidence is Confidence.MANUAL
    assert "valid New Relic attribute name" in m.reasons[0]


def test_a_log_wildcard_character_in_a_contains_term_is_manual() -> None:
    assert logs('textPayload:"100%"').confidence is Confidence.MANUAL
