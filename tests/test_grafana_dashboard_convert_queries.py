"""PromQL and LogQL to NRQL for dashboard widgets: exact text for each shape of the research."""

import time

import pytest

from importer.core.dashboard_nrql import check_raw_nrql
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
            "avg by (instance) (node_load1)",
            "SELECT average(node_load1) FROM Metric FACET instance LIMIT MAX TIMESERIES AUTO",
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
    out = convert_query("avg(up)", "prometheus", LINE, read_legend("Up"))
    assert out.nrql == "SELECT average(up) AS 'Up' FROM Metric TIMESERIES AUTO"
    flat = convert_query("up", "prometheus", LINE, read_legend("{{instance}}"))
    assert flat.nrql == "SELECT average(up) FROM Metric FACET instance LIMIT MAX TIMESERIES AUTO"
    grouped = convert_query("avg by (a) (up)", "prometheus", LINE, read_legend("{{b}}"))
    assert "FACET a LIMIT" in grouped.nrql and r.N_LEGEND_LABEL in grouped.notes
    mixed = convert_query("avg by (a) (up)", "prometheus", LINE, read_legend("x {{a}}"))
    assert r.N_LEGEND_TEXT in mixed.notes


def test_a_hostile_alias_cannot_leave_its_quotes() -> None:
    legend = read_legend("a' ) FROM Log; DROP \\")
    out = convert_query("avg(up)", "prometheus", LINE, legend).nrql
    assert out == "SELECT average(up) AS 'a\\' ) FROM Log; DROP \\\\' FROM Metric TIMESERIES AUTO"


def test_more_than_five_labels_are_cut_with_a_note() -> None:
    out = convert_query("avg by (a,b,c,d,e,f) (up)", "prometheus", LINE)
    assert "FACET a, b, c, d, e LIMIT MAX" in out.nrql and r.N_FACET_LIMIT in out.notes


def test_a_fixed_bucket_replaces_auto() -> None:
    opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=True, bucket_seconds=60)
    assert nrql("avg(up)", opts).endswith("TIMESERIES 60 SECONDS")


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
    out = convert_query("avg(up)", "prometheus", LINE, read_legend("{{a}} {{b}}"))
    assert "{{" not in out.nrql.split("FROM")[0]


def test_a_like_pattern_with_a_percent_sign_is_refused() -> None:
    with pytest.raises(Unconvertible) as caught:
        cond_sql(Cond("job", "like", ("a%b*",)), [])
    assert caught.value.reason == r.C_LIKE_PERCENT


def test_a_flat_selector_says_series_are_merged_unless_a_label_keeps_them_apart() -> None:
    assert r.N_MERGED in convert_query("up", "prometheus", LINE).notes
    assert r.N_MERGED not in convert_query("up", "prometheus", LINE, read_legend("{{a}}")).notes
    assert r.N_MERGED not in convert_query("avg(up)", "prometheus", LINE).notes


@pytest.mark.parametrize("value", ["x" * 201, "a\nb", "a\x00b"])
def test_a_constant_that_is_long_or_has_a_line_break_is_refused(value: str) -> None:
    opts = QueryOptions(variables={"c": value}, name_map=NameMap(), timeseries=True)
    with pytest.raises(Unconvertible) as caught:
        nrql('up{job="$c"}', opts)
    assert caught.value.reason == r.C_SHAPE


def test_a_negated_absent_label_is_is_not_null() -> None:
    assert cond_sql(Cond("zone", "absent", negate=True), []) == "zone IS NOT NULL"
    assert cond_sql(Cond("zone", "absent"), []) == "zone IS NULL"


@pytest.mark.parametrize(
    ("window", "interval"),
    [
        ("1d", "1 day"),
        ("2w", "14 days"),
        ("90s", "90 seconds"),
        ("1h30m", "90 minutes"),
        ("1m", "1 minute"),
    ],
)
def test_the_increase_window_becomes_the_largest_whole_unit(window: str, interval: str) -> None:
    out = nrql(f"sum(increase(x_total[{window}]))")
    assert out.split(", ", 1)[1] == interval + ") FROM Metric TIMESERIES AUTO"


def test_a_legend_asks_for_at_most_five_labels() -> None:
    legend = read_legend(" ".join(f"{{{{l{i}}}}}" for i in range(8)))
    assert legend.labels == ("l0", "l1", "l2", "l3", "l4")


HOSTILE = {
    "operands": "a_free + " * 1100 + "a",
    "parentheses": "(" * 5000,
    "balanced": "(" * 2500 + ")" * 2500,
    "matchers": "up{" + 'job="x",' * 1200 + "}",
    "labels": "sum by (" + "a," * 3000 + "z) (up)",
    "long name": "sum(rate(" + "x" * 9000 + "[5m]))",
    "brackets": "[" * 9000,
    "backslashes": 'up{job="' + "\\" * 4000 + '"}',
    "nested sums": "histogram_quantile(0.9, "
    + "sum by (le) (" * 300
    + "x_bucket"
    + ")" * 300
    + ")",
    "nested topk": "topk(5, " * 300 + "up" + ")" * 300,
    "variables": "VARV0 " * 1000,
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_queries_are_refused_or_valid_and_quick(name: str) -> None:
    started = time.perf_counter()
    try:
        out = nrql(HOSTILE[name])
    except Unconvertible:
        out = ""
    assert time.perf_counter() - started < 1.0
    if out:
        check_raw_nrql(out)  # whatever was written passes the structural checks
        assert out.count("FACET") <= 1 and len(out) < 4096


def test_irate_note_says_the_bucket_average_replaces_the_last_two_samples() -> None:
    # live check (docs/research/live-grafana-counter-roundtrip.md): irate and rate give the same
    # NRQL, so a spike that irate shows is averaged over the bucket; the widget must say so
    converted = convert_query("irate(x_total[5m])", "prometheus", LINE)
    assert r.N_IRATE in converted.notes
    assert r.N_IRATE not in convert_query("rate(x_total[5m])", "prometheus", LINE).notes


def test_rate_chart_note_warns_when_the_bucket_may_not_match_the_scrape_interval() -> None:
    # live check: a bucket that is not a multiple of the scrape interval makes a steady rate swing
    # between 0 and twice its value (10 s data in 5 s buckets), so an automatic bucket is flagged
    for call in ("rate", "irate", "increase"):
        auto = convert_query(f"{call}(x_total[5m])", "prometheus", LINE)
        assert r.N_RATE_BUCKET in auto.notes
    fixed = QueryOptions(variables={}, name_map=NameMap(), timeseries=True, bucket_seconds=60)
    assert r.N_RATE_BUCKET not in convert_query("rate(x_total[5m])", "prometheus", fixed).notes
    assert r.N_RATE_BUCKET not in convert_query("rate(x_total[5m])", "prometheus", STAT).notes
