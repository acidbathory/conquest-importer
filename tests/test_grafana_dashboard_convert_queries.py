"""PromQL and LogQL to NRQL for dashboard widgets: exact text for each shape of the research."""

import pytest

from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.legend import Legend, read_legend
from importer.sources.grafana.dashboard_convert.name_map import NameEntry, NameMap
from importer.sources.grafana.dashboard_convert.nrql import cond_sql
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Cond, Unconvertible

LINE = QueryOptions(variables={"job": None, "env": "prod"}, name_map=NameMap(), timeseries=True)
STAT = QueryOptions(variables={"job": None, "env": "prod"}, name_map=NameMap(), timeseries=False)


def nrql(expr: str, opts: QueryOptions = LINE, family: str = "prometheus") -> str:
    return convert_query(expr, family, opts).nrql


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        ('up{job="api"}', "SELECT average(up) FROM Metric WHERE job = 'api' TIMESERIES AUTO"),
        (
            "sum by (instance) (node_load1)",
            "SELECT sum(node_load1) FROM Metric FACET instance LIMIT MAX TIMESERIES AUTO",
        ),
        (
            "max(node_load1) by (instance, job)",
            "SELECT max(node_load1) FROM Metric FACET instance, job LIMIT MAX TIMESERIES AUTO",
        ),
        (
            'avg(node_load1{instance=~"a|b", job!="x"})',
            "SELECT average(node_load1) FROM Metric WHERE instance IN ('a', 'b') "
            "AND job != 'x' TIMESERIES AUTO",
        ),
        (
            'node_load1{instance=~"web.*"}',
            "SELECT average(node_load1) FROM Metric WHERE instance LIKE 'web%' TIMESERIES AUTO",
        ),
        (
            'node_load1{zone=""}',
            "SELECT average(node_load1) FROM Metric WHERE zone IS NULL TIMESERIES AUTO",
        ),
    ],
)
def test_selectors_and_aggregations_tier_one(expr: str, expected: str) -> None:
    assert nrql(expr) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            'sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))',
            "SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric "
            "WHERE mode != 'idle' FACET mode LIMIT MAX TIMESERIES AUTO",
        ),
        (
            "sum(irate(http_requests_total[$__rate_interval]))",
            "SELECT rate(sum(http_requests_total), 1 second) FROM Metric TIMESERIES AUTO",
        ),
        (
            "sum(increase(http_requests_total[5m]))",
            "SELECT rate(sum(http_requests_total), 5 minutes) FROM Metric TIMESERIES AUTO",
        ),
        (
            "sum(increase(http_requests_total[1h]))",
            "SELECT rate(sum(http_requests_total), 1 hour) FROM Metric TIMESERIES AUTO",
        ),
        (
            "increase(http_requests_total[$__range])",
            "SELECT sum(http_requests_total) FROM Metric TIMESERIES AUTO",
        ),
        (
            "increase(http_requests_total[$__interval])",
            "SELECT sum(http_requests_total) FROM Metric TIMESERIES AUTO",
        ),
        (
            "histogram_quantile(0.95, sum by (le, handler) "
            "(rate(http_request_duration_seconds_bucket[5m])))",
            "SELECT bucketPercentile(http_request_duration_seconds_bucket, 95) FROM Metric "
            "FACET handler LIMIT MAX TIMESERIES AUTO",
        ),
        (
            "histogram_quantile(0.999, sum(rate(lat_bucket[5m])) by (le))",
            "SELECT bucketPercentile(lat_bucket, 99.9) FROM Metric TIMESERIES AUTO",
        ),
        (
            "topk(5, sum by (pod) (rate(container_cpu_usage_seconds_total[5m])))",
            "SELECT rate(sum(container_cpu_usage_seconds_total), 1 second) FROM Metric "
            "FACET pod LIMIT 5 TIMESERIES AUTO",
        ),
    ],
)
def test_rate_increase_histogram_topk_tier_two(expr: str, expected: str) -> None:
    assert nrql(expr) == expected


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))',
            "SELECT 1 - rate(average(node_cpu_seconds_total), 1 second) FROM Metric "
            "WHERE mode = 'idle' TIMESERIES AUTO",
        ),
        (
            "sum(rate(a_total[5m])) / sum(rate(b_total[5m])) * 100",
            "SELECT (rate(sum(a_total), 1 second) / rate(sum(b_total), 1 second)) * 100 "
            "FROM Metric TIMESERIES AUTO",
        ),
        (
            'sum(rate(a_total{code="500"}[5m])) / sum(rate(a_total[5m]))',
            "SELECT rate(filter(sum(a_total), WHERE code = '500'), 1 second) "
            "/ rate(sum(a_total), 1 second) FROM Metric TIMESERIES AUTO",
        ),
        (
            "(a_free + b_free) / 2",
            "SELECT (average(a_free) + average(b_free)) / 2 FROM Metric TIMESERIES AUTO",
        ),
        (
            "count by (job) (up)",
            "SELECT count(up) FROM Metric FACET job LIMIT MAX TIMESERIES AUTO",
        ),
    ],
)
def test_arithmetic_and_count_tier_three(expr: str, expected: str) -> None:
    assert nrql(expr) == expected


def test_the_parentheses_of_the_whole_expression_are_dropped_and_inner_ones_stay() -> None:
    assert nrql("a_free / b_free").startswith("SELECT average(a_free) / average(b_free) FROM")
    assert nrql("a_free - (b_free - c_free)").startswith(
        "SELECT average(a_free) - (average(b_free) - average(c_free)) FROM"
    )


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            'up{job=~"$job"}',
            "SELECT latest(up) FROM Metric WHERE job IN ({{job}})",
        ),
        (
            'up{job!~"$job"}',
            "SELECT latest(up) FROM Metric WHERE job NOT IN ({{job}})",
        ),
        (
            'up{job="$job", env="$env"}',
            "SELECT latest(up) FROM Metric WHERE job IN ({{job}}) AND env = 'prod'",
        ),
        (
            "sum(rate(x_total[5m])) * $job",
            "SELECT rate(sum(x_total), 1 second) * {{job}} FROM Metric",
        ),
    ],
)
def test_variables_become_new_relic_variables_and_constants_are_written_in(
    expr: str, expected: str
) -> None:
    assert nrql(expr, STAT) == expected


def test_a_stat_has_no_timeseries_and_an_unaggregated_gauge_is_the_latest_value() -> None:
    assert nrql('up{job="a"}', STAT) == "SELECT latest(up) FROM Metric WHERE job = 'a'"
    converted = convert_query("avg(up)", "prometheus", STAT)
    assert converted.nrql == "SELECT average(up) FROM Metric"
    assert r.N_INSTANT in converted.notes


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            'sum by (level) (count_over_time({app="x"} |= "error" [5m]))',
            "SELECT count(*) FROM Log WHERE app = 'x' AND message LIKE '%error%' "
            "FACET level LIMIT MAX TIMESERIES AUTO",
        ),
        (
            'rate({app="x"} != "debug" [1m])',
            "SELECT rate(count(*), 1 second) FROM Log WHERE app = 'x' "
            "AND message NOT LIKE '%debug%' TIMESERIES AUTO",
        ),
        (
            'sum(rate({app=~"a|b"}[$__rate_interval]))',
            "SELECT rate(count(*), 1 second) FROM Log WHERE app IN ('a', 'b') TIMESERIES AUTO",
        ),
    ],
)
def test_logql(expr: str, expected: str) -> None:
    assert nrql(expr, family="loki") == expected


@pytest.mark.parametrize(
    ("legend", "alias", "labels", "lost"),
    [
        ("", None, (), False),
        ("Total CPU", "Total CPU", (), False),
        ("{{instance}}", None, ("instance",), False),
        ("{{ instance }} / {{job}}", None, ("instance", "job"), True),
        ("{{instance}} cpu", None, ("instance",), True),
        ("$job total", "total", (), False),
        ("$job", None, (), False),
    ],
)
def test_legend_forms(legend: str, alias: str | None, labels: tuple[str, ...], lost: bool) -> None:
    assert read_legend(legend) == Legend(alias, labels, lost)


def test_a_static_legend_is_an_alias_and_a_label_legend_adds_a_facet_to_a_flat_selector() -> None:
    out = convert_query("sum(up)", "prometheus", LINE, read_legend("Up"))
    assert out.nrql == "SELECT sum(up) AS 'Up' FROM Metric TIMESERIES AUTO"
    flat = convert_query("up", "prometheus", LINE, read_legend("{{instance}}"))
    assert flat.nrql == "SELECT average(up) FROM Metric FACET instance LIMIT MAX TIMESERIES AUTO"
    grouped = convert_query("sum by (a) (up)", "prometheus", LINE, read_legend("{{b}}"))
    assert "FACET a LIMIT" in grouped.nrql and r.N_LEGEND_LABEL in grouped.notes
    mixed = convert_query("sum by (a) (up)", "prometheus", LINE, read_legend("x {{a}}"))
    assert r.N_LEGEND_TEXT in mixed.notes


def test_a_hostile_alias_cannot_leave_its_quotes() -> None:
    legend = read_legend("a' ) FROM Log; DROP \\")
    out = convert_query("sum(up)", "prometheus", LINE, legend).nrql
    assert out == "SELECT sum(up) AS 'a\\' ) FROM Log; DROP \\\\' FROM Metric TIMESERIES AUTO"


def test_more_than_five_labels_are_cut_with_a_note() -> None:
    out = convert_query("sum by (a,b,c,d,e,f) (up)", "prometheus", LINE)
    assert "FACET a, b, c, d, e LIMIT MAX" in out.nrql and r.N_FACET_LIMIT in out.notes


def test_a_fixed_bucket_replaces_auto() -> None:
    opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=True, bucket_seconds=60)
    assert nrql("sum(up)", opts).endswith("TIMESERIES 60 SECONDS")


def test_the_name_map_changes_event_and_attribute() -> None:
    names = NameMap(metrics={"node_load1": NameEntry(event="Metric", attribute="node.load.1m")})
    opts = QueryOptions(variables={}, name_map=names, timeseries=True)
    out = convert_query("avg(node_load1)", "prometheus", opts)
    assert out.nrql == "SELECT average(`node.load.1m`) FROM Metric TIMESERIES AUTO"
    assert r.N_NAME_MAP in out.notes and out.metrics == ["node_load1"]
    other = NameMap(metrics={"up": NameEntry(event="Sample", attribute="up")})
    opts = QueryOptions(variables={}, name_map=other, timeseries=True)
    assert nrql("avg(up)", opts) == "SELECT average(up) FROM Sample TIMESERIES AUTO"
    with pytest.raises(Unconvertible) as events:
        nrql("up / node_load1", opts)
    assert events.value.reason == r.C_EVENTS


@pytest.mark.parametrize(
    ("expr", "reason"),
    [
        ('up{job="x-$job"}', r.C_VAR_MATCHER),
        ('up{job=~"$job.*"}', r.C_VAR_MATCHER),
        ("sum(rate(x_total[$__rate_interval])) * $__interval", r.C_BUILTIN),
        ("node_$job", r.C_VAR_NAME),
        ("node_cpu_seconds_total", r.C_SHAPE),
        ("histogram_quantile(0.9, sum by (le) (x_bucket)) / on() y", r.C_SHAPE),
        ("histogram_quantile(0.9, sum by (le) (rate(x_bucket[5m]))) / sum(rate(y_total[5m]))", ""),
    ],
)
def test_shapes_the_converter_refuses_have_a_fixed_reason(expr: str, reason: str) -> None:
    if not reason:
        assert "bucketPercentile" in nrql(expr)  # a percentile may sit next to a plain term
        return
    with pytest.raises(Unconvertible) as caught:
        nrql(expr)
    assert caught.value.reason == reason


def test_a_percentile_cannot_be_next_to_a_term_with_other_filters() -> None:
    expr = (
        'histogram_quantile(0.9, sum by (le) (rate(x_bucket{a="1"}[5m]))) / sum(rate(y_total[5m]))'
    )
    with pytest.raises(Unconvertible) as caught:
        nrql(expr)
    assert caught.value.reason == r.C_FILTERS


def test_a_variable_inside_a_string_alias_is_not_a_variable_reference() -> None:
    out = convert_query("sum(up)", "prometheus", LINE, read_legend("{{a}} {{b}}"))
    assert "{{" not in out.nrql.split("FROM")[0]


def test_a_like_pattern_with_a_percent_sign_is_refused() -> None:
    with pytest.raises(Unconvertible) as caught:
        cond_sql(Cond("job", "like", ("a%b*",)), [])
    assert caught.value.reason == r.C_LIKE_PERCENT
