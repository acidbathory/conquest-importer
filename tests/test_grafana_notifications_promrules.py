"""Prometheus rule groups to the rule objects the Grafana translator reads (not translated here)."""

import pytest

from importer.sources.grafana.notifications.promrules import split_comparison, to_grafana_rule
from importer.sources.grafana.notifications.rulefiles import load_rule_groups


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ("up == 0", ("up", "eq", 0.0)),
        ("sum(errors) > 5", ("sum(errors)", "gt", 5.0)),
        ("sum by (job) (errors) >= 0.5", ("sum by (job) (errors)", "gte", 0.5)),
        ("avg(x) < -3", ("avg(x)", "lt", -3.0)),
        ("avg(x) <= 1e3", ("avg(x)", "lte", 1000.0)),
        ("x != 2", ("x", "ne", 2.0)),
        ('up{job=~"a.*", env!="p"} == 0', ('up{job=~"a.*", env!="p"}', "eq", 0.0)),
        ('up{msg="a > 3"} == 1', ('up{msg="a > 3"}', "eq", 1.0)),
        ('up{a="x"} > 1', ('up{a="x"}', "gt", 1.0)),
    ],
)
def test_a_top_level_comparison_with_a_number_is_split(
    expr: str, expected: tuple[str, str, float]
) -> None:
    found = split_comparison(expr)
    assert found is not None
    assert (found.query, found.operator, found.value) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "sum(errors)",  # no comparison
        "5 < x",  # number on the left
        "a > b",  # not a number
        "a > 1 > 0",  # two comparisons
        "sum(a > 1)",  # nested, not top level
        "a > bool 1",
        "a > 1 and b > 2",
        "a >",
        "> 1",
        "",
        "a > NaN",
        "a > Inf",
        "a > 1e999",
    ],
)
def test_anything_else_is_not_split(expr: str) -> None:
    assert split_comparison(expr) is None


def test_a_regex_matcher_is_not_mistaken_for_a_comparison() -> None:
    assert split_comparison('up{a=~"x"}') is None
    assert split_comparison('up{a!~"x"} == 0') is not None


def test_an_unterminated_string_or_deep_nesting_does_not_hang() -> None:
    assert split_comparison('up{a="x} > 1') is None
    assert split_comparison("(" * 100_000 + "a" + ")" * 100_000 + " > 1") is not None
    assert split_comparison("(" * 100_000 + " > 1") is None


GROUP = {
    "ns": [
        {
            "name": "g",
            "interval": "30s",
            "rules": [
                {
                    "alert": "Hot",
                    "expr": "max(temp) > 90",
                    "for": "5m",
                    "labels": {"severity": "page"},
                    "annotations": {"summary": "hot"},
                },
                {"alert": "Complex", "expr": "a / b"},
                {"record": "job:x:rate5m", "expr": "sum(rate(x[5m]))"},
            ],
        }
    ]
}


def test_an_alert_with_a_comparison_becomes_a_grafana_pipeline() -> None:
    (group,) = load_rule_groups(GROUP, "prometheus")
    rule = to_grafana_rule(group.rules[0], group, "prom-uid")
    assert (rule.title, rule.folder, rule.group, rule.interval_seconds) == ("Hot", "ns", "g", 30)
    assert (rule.for_seconds, rule.labels, rule.annotations) == (
        300, {"severity": "page"}, {"summary": "hot"}
    )  # fmt: skip
    assert not rule.datasource_managed and rule.condition == "C"
    a, b, c = rule.data
    assert (a.refId, a.datasourceUid, a.model["expr"]) == ("A", "prom-uid", "max(temp)")
    assert a.model["datasource"] == {"type": "prometheus", "uid": "prom-uid"}
    assert (b.refId, b.model["type"], b.model["reducer"], b.model["expression"]) == (
        "B", "reduce", "last", "A"
    )  # fmt: skip
    assert c.model["conditions"] == [{"evaluator": {"type": "gt", "params": [90.0]}}]
    assert c.model["expression"] == "B"


def test_a_loki_group_uses_the_loki_data_source_type() -> None:
    (group,) = load_rule_groups(GROUP, "loki")
    rule = to_grafana_rule(group.rules[0], group, "loki-uid")
    assert rule.data[0].model["datasource"]["type"] == "loki"


def test_a_rule_without_a_simple_comparison_stays_data_source_managed() -> None:
    (group,) = load_rule_groups(GROUP)
    rule = to_grafana_rule(group.rules[1], group, "u")
    assert rule.datasource_managed and rule.data == [] and rule.title == "Complex"


def test_a_recording_rule_keeps_the_record_marker() -> None:
    (group,) = load_rule_groups(GROUP)
    rule = to_grafana_rule(group.rules[2], group, "u")
    assert rule.record is not None and rule.title == "job:x:rate5m"
