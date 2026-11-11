"""Which tier a dashboard query reaches, from the shapes in the research note (section 5.2)."""

import time

import pytest

from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.query_tier import (
    alert_reach,
    classify_log_lines,
    classify_query,
)

PROM_TIERS = [
    # T1: the alert translator's reach, plus label matchers that RLIKE can express
    ("up", 1),
    ('node_load1{job="a"}', 1),
    ('avg(node_load1{job=~"$job"})', 1),
    ('sum by (instance) (node_memory_MemAvailable_bytes{job=~"$job"})', 1),
    ('max(node_filesystem_avail_bytes{mountpoint!="/boot"})', 1),
    ('node_memory_MemTotal_bytes{instance=~"$node:$port"}', 1),
    ('min by (a, b) (go_goroutines{job=~"api|web"})', 1),
    ('go_goroutines{job=~"api.*"}', 1),
    ('up{job=~".*api"}', 1),
    ('up{job=~".*api.*"}', 1),
    ('up{job!~"a.*"}', 1),
    ('up{job=~".+"}', 1),  # present, written as IS NOT NULL
    ('node_filesystem_size_bytes{fstype=~"ext.*|xfs"}', 1),  # RLIKE, anchored like PromQL
    ('count{a="b"}', 1),  # a metric called count is not the aggregator
    ('up{job="rate(x_total[5m])"}', 1),  # text inside a string is not a call
    ("up * .5", 3),
    ("up * 1e3", 3),
    ("up * 1e-3", 3),
    ("up * 2.", 3),
    ("rate(x_total[1h30m])", 2),
    ("sum by (a, b) (x) / sum by (b, a) (y)", 3),
    ("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", 3),
    # T2: rate, increase, histogram_quantile and topk
    ('rate(node_network_receive_bytes_total{device!="lo"}[5m])', 2),
    ('sum by (instance) (irate(node_cpu_seconds_total{mode="idle"}[$__rate_interval]))', 2),
    ("sum(rate(node_cpu_seconds_total[$interval])) by (mode)", 2),
    ('increase(retries_total{service=~"$service"}[$__interval])', 2),
    (
        "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket"
        '{service=~"$service"}[$__rate_interval])))',
        2,
    ),
    ("histogram_quantile(0.99, sum(rate(x_bucket[5m])) by (le, handler))", 2),
    ("histogram_quantile(0.9, rate(x_bucket[5m]))", 2),
    ("topk(10, sum by (handler) (rate(http_requests_total[5m])))", 2),
    (
        "topk(10, histogram_quantile(0.95, sum by (le, handler) "
        "(rate(http_request_duration_seconds_bucket[$__range]))))",
        2,
    ),
    ("sum(rate(a_total[5m]))\n  by (x)", 2),
    # T3: arithmetic between convertible terms and numbers, and count
    ('1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))', 3),
    ("sum(rate(a_total[5m])) / sum(rate(b_total[5m]))", 3),
    ("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", 3),
    ("(1 - (node_filesystem_avail_bytes / node_filesystem_size_bytes)) * 100", 3),
    ("node_memory_MemTotal_bytes * 8", 3),
    ("-node_load1", 3),
    ("node_load1 * $factor", 3),
    ("count(up)", 3),
    ('count by (job) (up{job=~"x"})', 3),
]

PROM_MANUAL = [
    ('up{job=~".*"}', r.Q_REGEX),
    ('up{job=~"a|"}', r.Q_REGEX),
    ('up{job=~"(?i)abc"}', r.Q_REGEX),
    ('node_filesystem_size_bytes{device!~"^(?:tmpfs|rootfs)$"}', r.Q_REGEX),
    ("sum by ($group) (up)", r.Q_GROUP_VARIABLE),
    ("sum(up) by ($group)", r.Q_GROUP_VARIABLE),
    ("sum by (a) (x) / sum by (b) (y)", r.Q_BINARY_LABELS),
    ("sum(x) / y", r.Q_BINARY_LABELS),
    ("sum by (a) (rate(x_total[5m])) - 3 * sum by (b) (y)", r.Q_BINARY_LABELS),
    (
        'histogram_quantile(0.9, sum by (le) (rate(x_total{a="y_bucket"}[5m])))',
        r.Q_HISTOGRAM,
    ),
    ("a" * 250, r.Q_NOT_SIMPLE),
    ("node_network_receive_bytes_total", r.Q_COUNTER),
    ("up or up", r.Q_COMPARE),
    ("up > sum(up)", r.Q_COMPARE),
    ("a and b", r.Q_COMPARE),
    ("a or b", r.Q_COMPARE),
    ("sum(a > 1)", r.Q_COMPARE),
    ("a % 3", r.Q_COMPARE),
    ("a / on(instance) group_left b", r.Q_VECTOR),
    ("a / ignoring(x) b", r.Q_VECTOR),
    ('label_replace(up, "a", "b", "c", "d")', r.Q_LABEL_FN),
    ("up offset 1d", r.Q_OFFSET),
    ("max_over_time(up[1h:5m])", r.Q_SUBQUERY),
    ("sum(max_over_time(up[1h]))", r.Q_OVER_TIME),
    ("max_over_time(rate(x_total[5m])[1h:1m])", r.Q_SUBQUERY),
    ("predict_linear(x[1h], 3600)", r.Q_RANGE_FN),
    ("delta(x[5m])", r.Q_DELTA),
    ("absent(up)", r.Q_ABSENT),
    ("sum without (a) (x)", r.Q_WITHOUT),
    ("bottomk(3, x)", r.Q_BOTTOMK),
    ("topk($n, up)", r.Q_TOPK_ARG),
    ("avg(sum by (i, c) (rate(a_total[5m]))) by (i)", r.Q_NESTED),
    ("time() - process_start_time_seconds", r.Q_TIME),
    ("clamp_min(x, y)", r.Q_FUNCTION),
    ("sqrt(x)", r.Q_FUNCTION),
    ("stddev(x)", r.Q_FUNCTION),
    ('node_cpu{__name__=~"x"}', r.Q_MATCHER),
    ('up{job=~"(?!canary).*"}', r.Q_MATCHER),
    ('up{job=~"(a)\\\\1"}', r.Q_MATCHER),
    ("histogram_quantile(0.9, sum(rate(x_total[5m])))", r.Q_HISTOGRAM),
    ("histogram_quantile(0.9, sum by (handler) (rate(x_bucket[5m])))", r.Q_HISTOGRAM),
    ("rate(sum(x_total)[5m])", r.Q_RATE_SHAPE),
    ("rate(x_total[5m]) by", r.Q_NOT_SIMPLE),
    ("${x:csv}", r.Q_VAR_FORMAT),
    ('up{job="${job:regex}"}', r.Q_VAR_FORMAT),
    ("1", r.Q_NOT_SIMPLE),
    ("not a query at all (", r.Q_NOT_SIMPLE),
    ("$undefined_metric", r.Q_NOT_SIMPLE),
    ("x" * 10_001, r.Q_TOO_LONG),
]


@pytest.mark.parametrize(("expr", "tier"), PROM_TIERS)
def test_a_promql_shape_reaches_its_tier(expr: str, tier: int) -> None:
    verdict = classify_query(expr, "prometheus")
    assert (verdict.tier, verdict.reason) == (tier, "")


@pytest.mark.parametrize(("expr", "reason"), PROM_MANUAL)
def test_a_promql_shape_that_stays_manual_gives_one_specific_reason(expr: str, reason: str) -> None:
    verdict = classify_query(expr, "prometheus", known=frozenset({"n", "x", "group"}))
    assert verdict.tier is None and verdict.reason == reason


LOKI = [
    ('count_over_time({app="x"} |= "error" [5m])', 1),
    ('sum by (level) (rate({app=~"$service"} |= "error" [$__interval]))', 2),
    ('count_over_time({app="x"} != "debug" [1h])', 1),
]
LOKI_MANUAL = [
    ('{app="x"} | json', r.QL_PARSER),
    ('count_over_time({app="x"} | logfmt [5m])', r.QL_PARSER),
    ('bytes_rate({app="x"}[5m])', r.QL_FUNCTION),
    ('count_over_time({app="x"} |~ "a.*b" [5m])', r.QL_REGEX),
    ('count_over_time({app="x"} |= "a%b" [5m])', r.QL_WILDCARD),
    ('{app="x"} |= "error"', r.QL_NOT_SIMPLE),
    ("count_over_time({} [5m])", r.QL_NOT_SIMPLE),
    ('count_over_time({__name__="x"} [5m])', r.QL_MATCHER),
]


@pytest.mark.parametrize(("expr", "tier"), LOKI)
def test_logql_counts_reach_tier_one_and_rates_tier_two(expr: str, tier: int) -> None:
    assert classify_query(expr, "loki").tier == tier


@pytest.mark.parametrize(("expr", "reason"), LOKI_MANUAL)
def test_logql_that_stays_manual_gives_a_specific_reason(expr: str, reason: str) -> None:
    verdict = classify_query(expr, "loki")
    assert (verdict.tier, verdict.reason) == (None, reason)


@pytest.mark.parametrize(
    ("expr", "tier"),
    [
        ('{app="x"}', 1),
        ('{app=~"$service"} |= "error" != "debug"', 1),
        ('{app="x"} | json', None),
        ("rate(x[5m])", None),
        ("", None),
    ],
)
def test_raw_log_line_queries_are_a_separate_check_for_log_panels(
    expr: str, tier: int | None
) -> None:
    assert classify_log_lines(expr).tier == tier


def test_the_variables_a_query_uses_are_reported_and_builtins_are_not() -> None:
    expr = 'sum by (i) (rate(x_total{job=~"$job", i="${inst}"}[$__rate_interval])) / $k'
    verdict = classify_query(expr, "prometheus", known=frozenset({"job", "inst", "k"}))
    assert verdict.tier == 3 and verdict.variables == {"job", "inst", "k"}


def test_a_variable_the_dashboard_does_not_define_stays_literal_text() -> None:
    known = frozenset({"job"})
    quoted = classify_query('up{job="$ghost"}', "prometheus", known=known)
    assert quoted.tier == 1 and quoted.variables == frozenset()  # an ordinary string value
    name = classify_query('$ghost{job="a"}', "prometheus", known=known)
    assert name.tier is None and name.reason == r.Q_NOT_SIMPLE
    assert "ghost" not in name.reason


def test_the_alert_translator_is_reached_through_a_public_wrapper() -> None:
    assert alert_reach("up", "prometheus") == ""
    assert alert_reach("up == 1", "prometheus") != ""
    assert alert_reach('count_over_time({a="b"} |= "x" [5m])', "loki") == ""


@pytest.mark.parametrize(
    "expr",
    [
        " " * 10_000,
        "(" * 10_000,
        "rate(" * 2000,
        "sum by (" + "a," * 3000,
        "up{" + 'a="b",' * 1500,
        'up{a=~"' + "(" * 3000,
        "histogram_quantile(0.9," * 400,
        "a " + "- a " * 2000,
        "a" + " " * 9000 + "b",
        '"' * 10_000,
        "\\" * 10_000,
        "topk(1," * 1000,
        "up{a=" + '"x\\' * 2000,
        "sum without(" + "a" * 9000,
        "[" * 9000,
        "[a" * 4000,
        "a" * 9990,
        "x_over_time" * 800,
        "a_over_time(" * 800,
        "rate(x[5m]) " * 800,
    ],
    ids=lambda e: f"{len(e)}chars-{e[:8]!r}",
)
@pytest.mark.parametrize("language", ["prometheus", "loki"])
def test_hostile_expressions_finish_quickly_and_never_raise(expr: str, language: str) -> None:
    start = time.process_time()  # CPU time, so a busy CI runner does not fail the test
    verdict = classify_query(expr[:10_000], language)
    assert time.process_time() - start < 10.0  # catches catastrophic work, not a slow machine
    assert verdict.tier is None or verdict.tier in (1, 2, 3)


def _cpu_seconds(expr: str, language: str) -> float:
    best = float("inf")
    for _ in range(3):
        start = time.process_time()
        classify_query(expr, language)
        best = min(best, time.process_time() - start)
    return max(best, 1e-3)


@pytest.mark.parametrize("unit", ["rate(", "a ", "(", 'up{a="b",'])
@pytest.mark.parametrize("language", ["prometheus", "loki"])
def test_classifying_grows_no_faster_than_linearly_with_the_expression(
    unit: str, language: str
) -> None:
    """Four times the input may cost about four times the CPU; quadratic work would cost 16."""
    small = _cpu_seconds(unit * 500, language)
    large = _cpu_seconds(unit * 2000, language)
    assert large / small < 10


def test_a_reason_never_contains_text_from_the_expression() -> None:
    hostile = 'up{job="a\\"} or evil_marker_xyz"} and secret_marker_abc'
    verdict = classify_query(hostile, "prometheus")
    assert "marker" not in verdict.reason and "evil" not in verdict.reason
