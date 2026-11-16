"""histogram_quantile over a summed _bucket rate becomes bucketPercentile(x_bucket, p).

The window of the rate is irrelevant to bucketPercentile; the percentile is q * 100. Invented
metric names only. Nothing here is checked against data in New Relic.
"""

import pytest
from pydantic import ValidationError

from importer.core.emit.common import nrql_for
from importer.core.ir import Aggregation, Confidence, IRMonitor, MetricTerm, Operator
from importer.sources.grafana.rules2 import promql as p
from test_grafana import make, one, text


def prom(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def test_the_plain_shape_converts() -> None:
    m = prom("histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (le))", params=[2])
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT bucketPercentile(demo_latency_bucket, 95) FROM Metric"
    assert m.signal.metric and m.signal.metric.aggregation is Aggregation.BUCKET_PERCENTILE
    for note in (p.N_HIST_APPROX, p.N_HIST_OTLP, p.N_HIST_UNCHECKED, p.N_HIST_WINDOW):
        assert note in m.reasons


@pytest.mark.parametrize("func", ["rate", "irate", "increase"])
@pytest.mark.parametrize("window", ["1m", "5m", "1h"])
def test_the_range_function_and_its_window_do_not_change_the_query(func: str, window: str) -> None:
    m = prom(f"histogram_quantile(0.9, sum by (le) ({func}(demo_latency_bucket[{window}])))")
    assert nrql_for(m) == "SELECT bucketPercentile(demo_latency_bucket, 90) FROM Metric"


@pytest.mark.parametrize(("q", "percent"), [("0.5", "50"), ("0.99", "99"), ("0.995", "99.5")])
def test_the_percentile_has_no_float_noise(q: str, percent: str) -> None:
    m = prom(f"histogram_quantile({q}, sum(rate(demo_latency_bucket[5m])) by (le))")
    assert f"bucketPercentile(demo_latency_bucket, {percent})" in nrql_for(m)


def test_labels_other_than_le_become_facets_and_matchers_are_kept() -> None:
    m = prom(
        'histogram_quantile(0.99, sum(rate(demo_latency_bucket{env="prod",job!="x"}[5m])) '
        "by (le, route, zone))"
    )
    assert nrql_for(m) == (
        "SELECT bucketPercentile(demo_latency_bucket, 99) FROM Metric "
        "WHERE env = 'prod' AND job != 'x' FACET route, zone"
    )


@pytest.mark.parametrize(
    ("expr", "kind", "value", "operator", "expected"),
    [
        ("* 1000", "gt", 500, Operator.ABOVE, 0.5),
        ("1000 *", "gt", 500, Operator.ABOVE, 0.5),
        ("* 1000", "lt", 250, Operator.BELOW, 0.25),
    ],
)
def test_a_multiplier_becomes_a_threshold_scale(
    expr: str, kind: str, value: float, operator: Operator, expected: float
) -> None:
    inner = "histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (le))"
    text_ = f"{inner} * 1000" if expr.startswith("*") else f"1000 * {inner}"
    m = prom(text_, kind=kind, params=[value])
    assert m.confidence is Confidence.APPROXIMATE
    assert m.thresholds[0].operator is operator and m.thresholds[0].value == expected
    assert nrql_for(m) == "SELECT bucketPercentile(demo_latency_bucket, 95) FROM Metric"


@pytest.mark.parametrize("factor", ["0", "-5"])
def test_a_multiplier_that_cannot_move_to_the_threshold_is_refused_with_its_reason(
    factor: str,
) -> None:
    m = prom(f"histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (le)) * {factor}")
    assert m.confidence is Confidence.MANUAL and p.R_HIST_MULTIPLIER in m.reasons


def test_a_multiplier_that_does_not_divide_exactly_is_refused() -> None:
    m = prom("histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (le)) * 3", params=[1])
    assert m.confidence is Confidence.MANUAL and p.R_HIST_MULTIPLIER in m.reasons


@pytest.mark.parametrize(
    "expr",
    [
        "histogram_quantile(0.95, rate(demo_latency_bucket[5m]))",  # no sum, per series
        "histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (route))",  # no le
        "histogram_quantile(0.95, sum(rate(demo_latency[5m])) by (le))",  # not a bucket metric
        "histogram_quantile(1.5, sum(rate(demo_latency_bucket[5m])) by (le))",
        "histogram_quantile(0, sum(rate(demo_latency_bucket[5m])) by (le))",
        "histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m] offset 1h)) by (le))",
        "histogram_quantile(0.95, sum(rate(a_bucket[5m])) by (le)) / "
        "histogram_quantile(0.5, sum(rate(b_bucket[5m])) by (le))",
        "histogram_quantile(0.95, sum(rate(a_bucket[5m])) by (le)) * on (x) group_left b",
        "histogram_quantile(0.95, sum(sum(rate(a_bucket[5m])) by (le, x)) by (le))",
        'histogram_quantile(0.95, sum(rate(a_bucket{le="1"}[5m])) by (le))',
        "histogram_quantile(0.95, avg(rate(a_bucket[5m])) by (le))",
    ],
)
def test_everything_else_stays_refused(expr: str) -> None:
    m = prom(expr)
    assert m.confidence is Confidence.MANUAL
    assert "interpolates" not in text(m)


def test_a_mean_of_a_percentile_is_refused() -> None:
    # a mean of per-step percentiles is not the percentile of the window; max and min convert
    m = one(
        make(
            "histogram_quantile(0.95, sum(rate(demo_latency_bucket[5m])) by (le))",
            reducer="mean",
        )
    )
    assert m.confidence is Confidence.MANUAL


def test_a_tiny_quantile_below_the_allowed_percentile_is_refused() -> None:
    m = prom("histogram_quantile(0.001, sum(rate(demo_latency_bucket[5m])) by (le))")
    assert m.confidence is Confidence.MANUAL and p.R_HIST_QUANTILE in m.reasons


def test_the_term_percentile_rules_and_the_saved_shape() -> None:
    plain = MetricTerm(metric="x", space_aggregation=Aggregation.SUM)
    assert "percentile" not in plain.model_dump()
    assert "percentile" not in plain.model_dump(mode="json")
    ok = MetricTerm(
        metric="x_bucket", space_aggregation=Aggregation.BUCKET_PERCENTILE, percentile=99
    )
    assert ok.model_dump()["percentile"] == 99
    for bad in (None, 0.5, 100.0):
        with pytest.raises(ValidationError):
            MetricTerm(
                metric="x_bucket", space_aggregation=Aggregation.BUCKET_PERCENTILE, percentile=bad
            )
    with pytest.raises(ValidationError):
        MetricTerm(metric="x", space_aggregation=Aggregation.SUM, percentile=50)
