"""Comments, quote styles and aggregate-name case are settled before any reader sees PromQL."""

import pytest

from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboards.query_tier import classify_query
from importer.sources.grafana.promql_normalize import normalize_promql

LINE = QueryOptions(variables={}, name_map=NameMap(), timeseries=True)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        # comments end at the line end and never start inside a string
        ("up # the comment", "up "),
        ("up # a\n+ up", "up \n+ up"),
        ('up{job="a#b"} # x', 'up{job="a#b"} '),
        ("up{job='a#b'}", 'up{job="a#b"}'),
        ("up{job=`a#b`}", 'up{job="a#b"}'),
        ('up{job="a\\"#b"} # x', 'up{job="a\\"#b"} '),
        ("up{job='a\\'#b'} # x", 'up{job="a\'#b"} '),
        # single quotes: \' loses its backslash, a bare " gains one, other escapes stay
        ("up{job='a'}", 'up{job="a"}'),
        ("up{job='it\\'s'}", 'up{job="it\'s"}'),
        ("up{job='say \"hi\"'}", 'up{job="say \\"hi\\""}'),
        ("up{job='a\\\\b\\n'}", 'up{job="a\\\\b\\n"}'),
        # a backtick string is raw: backslash and quote are escaped for the double-quoted form
        ('up{job=~`a\\d+"`}', 'up{job=~"a\\\\d+\\""}'),
        # aggregate names and by/without are keywords, which Prometheus reads in any case
        ("SUM(rate(x_total[5m]))", "sum(rate(x_total[5m]))"),
        ("Sum By (job) (up)", "sum by (job) (up)"),
        ("sum(up) BY (job)", "sum(up) by (job)"),
        ("avg(up) WITHOUT (job)", "avg(up) without (job)"),
        ("TOPK(3, up)", "topk(3, up)"),
        # functions are case sensitive in Prometheus, and names are names
        ("RATE(x_total[5m])", "RATE(x_total[5m])"),
        ("SUM_total", "SUM_total"),
        ("SUM:rate5m", "SUM:rate5m"),
        ('up{SUM="x"}', 'up{SUM="x"}'),
        ('up{job="SUM(x)"}', 'up{job="SUM(x)"}'),
        # untouched when there is nothing to do, or when a string is not closed
        ("sum(up)", "sum(up)"),
        ("up{job='a", "up{job='a"),
        ("", ""),
    ],
)
def test_normalize(raw: str, want: str) -> None:
    assert normalize_promql(raw) == want


def test_normalizing_twice_changes_nothing() -> None:
    once = normalize_promql("SUM by (j) (up{a='x#', b=`y`}) # c")
    assert normalize_promql(once) == once


@pytest.mark.parametrize(
    ("variant", "plain"),
    [
        ("SUM(rate(x_total[5m]))", "sum(rate(x_total[5m]))"),
        ("sum(rate(x_total{job='api'}[5m])) # why\n", 'sum(rate(x_total{job="api"}[5m]))'),
        (
            "sum(rate(x_total{job=`api`}[5m])) by (Job)",
            'sum(rate(x_total{job="api"}[5m])) by (Job)',
        ),
        ("SUM(rate(x_total[5m])) BY (job)", "sum(rate(x_total[5m])) by (job)"),
    ],
)
def test_dashboard_planner_and_converter_give_the_same_answer_for_every_spelling(
    variant: str, plain: str
) -> None:
    assert classify_query(variant, "prometheus") == classify_query(plain, "prometheus")
    assert classify_query(variant, "prometheus").tier is not None
    assert convert_query(variant, "prometheus", LINE) == convert_query(plain, "prometheus", LINE)


def test_a_function_name_in_the_wrong_case_stays_refused() -> None:
    assert classify_query("SUM(RATE(x_total[5m]))", "prometheus").tier is None


def test_logql_is_never_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    from importer.sources.grafana.dashboards import query_tier

    def boom(_text: str) -> str:
        raise AssertionError("LogQL must not be rewritten as PromQL")

    monkeypatch.setattr(query_tier, "normalize_promql", boom)
    classify_query('count_over_time({app="x"} |= `error` [5m])', "loki")
