"""Regex label matchers on dashboard tiles: carried by RLIKE only when that is exact."""

import pytest

from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboards import reasons as dr
from importer.sources.grafana.dashboards.query_tier import classify_query
from importer.sources.grafana.dashboards.regex_matchers import mask

OPTS = QueryOptions(variables={"ns": None}, name_map=NameMap(), timeseries=True)


def nrql(expr: str) -> str:
    return convert_query(expr, "prometheus", OPTS).nrql


@pytest.mark.parametrize(
    ("expr", "where"),
    [
        ('up{code=~"5.."}', "code RLIKE '5..'"),
        ('up{code!~"5.."}', "code NOT RLIKE '5..'"),
        ('up{device!~"lo|veth.*"}', "device NOT RLIKE 'lo|veth.*'"),
        ('up{mode=~"ext.*|xfs"}', "mode RLIKE 'ext.*|xfs'"),
        ('up{container!=""}', "container IS NOT NULL"),
        ('up{container=~".+"}', "container IS NOT NULL"),
        ('up{container!~".+"}', "container IS NULL"),
    ],
)
def test_regex_matchers_become_rlike_or_presence(expr: str, where: str) -> None:
    assert nrql(expr) == f"SELECT average(up) FROM Metric WHERE {where} TIMESERIES AUTO"  # noqa: S608
    assert classify_query(expr, "prometheus").tier == 1


def test_a_variable_matcher_next_to_a_regex_matcher_still_converts() -> None:
    out = nrql('avg(up{ns=~"$ns", code=~"5.."})')
    assert "ns IN ({{ns}}) AND code RLIKE '5..'" in out


@pytest.mark.parametrize(
    "pattern",
    [".*", "a|", "a?", "(?i)abc", "^a^bc$", "a\\\\.b", "(?:a|b)", "5..|$ns", "a{2}", "[a-"],
)
def test_a_pattern_rlike_cannot_carry_exactly_stays_manual(pattern: str) -> None:
    expr = 'up{code=~"' + pattern + '"}'
    assert classify_query(expr, "prometheus").tier is None
    with pytest.raises(Unconvertible):
        nrql(expr)


def test_the_regex_reason_names_the_empty_value_rule() -> None:
    assert "empty" in dr.Q_REGEX and "missing" in dr.Q_REGEX


def test_mask_leaves_plain_forms_and_strings_alone() -> None:
    text = 'up{job=~"a|b", x="5.."} and "{code=~\\"5..\\"}"'
    assert mask(text) == (text, {})


@pytest.mark.parametrize(
    ("matcher", "where"),
    [
        ('code=~"^5..$"', "code RLIKE '5..'"),
        ('job=~"^k8s_.*"', "job LIKE 'k8s_%'"),
        ('mode=~"^(ext.*|xfs)$"', "mode RLIKE 'ext.*|xfs'"),
        ('ns=~"^$ns$"', "ns IN ({{ns}})"),
        ('ns=~"^($ns)$"', "ns IN ({{ns}})"),
        ('code!~"^5..$"', "code NOT RLIKE '5..'"),
    ],
)
def test_the_anchors_of_a_whole_value_regex_are_dropped(matcher: str, where: str) -> None:
    out = nrql("up{" + matcher + "}")
    assert f"WHERE {where}" in out
