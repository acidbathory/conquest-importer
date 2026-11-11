"""rate() and increase() of delta-stored counters become sum() over the range, as arithmetic.

rate() is not documented for alert conditions (docs/research/newrelic-notifications-and-nrql-
reference.md, section 2.3), so the condition uses sum() divided by the window, which is. Each case
that converts also runs the NRQL on sample rows and compares it with the PromQL value.
"""

import importlib
from typing import Any

import pytest

from grafana_rules2_eval import evaluate, rate
from importer.core.emit.common import nrql_for
from importer.core.ir import Aggregation, Confidence, IRMonitor
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one, text

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def prom(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def delta_rows(metric: str, series: dict[str, list[float]], **labels: str) -> list[dict[str, Any]]:
    """One row per interval and series, as New Relic stores a counter: the change since the last."""
    return [
        {metric: d, "service": name, **labels} for name, deltas in series.items() for d in deltas
    ]


def test_sum_of_a_rate_is_sum_over_the_window_divided_by_the_window() -> None:
    m = prom('sum by (service) (rate(http_requests_total{env="prod"}[5m]))', params=[2])
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.aggregation_window_seconds == 300
    assert m.signal.metric and m.signal.metric.aggregation is Aggregation.SUM
    assert nrql_for(m) == (
        "SELECT sum(http_requests_total) / 300 FROM Metric WHERE env = 'prod' FACET service"
    )
    series = {"api": [30.0, 60.0, 90.0, 120.0, 0.0], "web": [600.0, 600.0, 600.0, 600.0, 600.0]}
    rows = delta_rows("http_requests_total", series, env="prod")
    rows += delta_rows("http_requests_total", {"api": [9999.0]}, env="dev")  # other label value
    got = evaluate(nrql_for(m), rows)
    # the PromQL values worked by hand: api rose 300 in 300 s, web rose 3000 in 300 s
    assert got == {("api",): 1.0, ("web",): 10.0}
    assert got == {("api",): rate(series["api"], 300), ("web",): rate(series["web"], 300)}


def test_increase_is_the_sum_over_the_range_with_no_division() -> None:
    m = prom("sum(increase(orders_total[10m]))", params=[100])
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.aggregation_window_seconds == 600
    assert nrql_for(m) == "SELECT sum(orders_total) FROM Metric"
    rows = delta_rows("orders_total", {"a": [10.0, 20.0], "b": [5.0, 5.0, 60.0]})
    assert evaluate(nrql_for(m), rows) == {(): 100.0}  # the increase of both series together


def test_the_pending_period_and_the_range_are_both_kept() -> None:
    m = prom("sum(rate(errors_total[5m]))", params=[1], for_=900)
    assert (m.signal.aggregation_window_seconds, m.signal.window_seconds) == (300, 900)
    assert t.N_COUNTER in m.reasons and any("300s window" in r for r in m.reasons)


def test_a_rate_with_no_sum_is_manual_because_the_total_is_another_test() -> None:
    m = prom("rate(errors_total[5m])", params=[1])
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COUNTER_UNAGG]


@pytest.mark.parametrize("suffix", ["_total", "_count", "_bucket"])
def test_the_name_suffixes_new_relic_stores_as_counts_convert(suffix: str) -> None:
    assert prom(f"sum(rate(x{suffix}[5m]))").confidence is Confidence.APPROXIMATE


@pytest.mark.parametrize("name", ["x_sum", "queue_depth", "x_seconds"])
def test_a_name_new_relic_would_not_store_as_a_count_is_manual(name: str) -> None:
    m = prom(f"sum(rate({name}[5m]))")
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COUNTER_NAME]


@pytest.mark.parametrize("agg", ["avg", "min", "max"])
def test_only_sum_of_a_rate_converts(agg: str) -> None:
    m = prom(f"{agg}(rate(x_total[5m]))")
    assert m.reasons == [t.R_COUNTER_AGG.format(agg=agg)]


def test_a_reducer_that_looks_back_over_the_rate_series_is_manual() -> None:
    m = one(make("sum(rate(x_total[5m]))", reducer="max"))
    assert m.reasons == [t.R_COUNTER_REDUCER.format(reducer="max")]


@pytest.mark.parametrize("window", ["30s", "90s", "2m30s"])
def test_a_range_that_is_not_whole_minutes_is_manual(window: str) -> None:
    m = prom(f"sum(rate(x_total[{window}]))")
    assert m.confidence is Confidence.MANUAL
    assert "range" in text(m) or "promql" in text(m)


def test_a_range_over_the_longest_window_is_manual() -> None:
    m = prom("sum(increase(x_total[2h]))")
    assert m.confidence is Confidence.MANUAL and "longer than" in text(m)


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("sum(irate(x_total[5m]))", "rate, increase or another range function"),
        ("sum(rate(x_total[5m] offset 1d))", "offset"),
        ("bottomk(3, sum by (pod) (rate(x_total[5m])))", "topk"),
        ("sum by (a) (rate(x_total[5m])) by (b)", "rate, increase or another range function"),
    ],
)
def test_other_range_functions_keep_their_reason(expr: str, fragment: str) -> None:
    m = prom(expr)
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_a_rule_file_rate_comparison_converts_like_the_grafana_rule() -> None:
    expr = 'sum(rate(payments_errors_total{env="prod"}[5m]))'
    file_rule = GrafanaRule(
        title="r", datasource_managed=True, expr=f"{expr} > 2", for_seconds=600, interval_seconds=60
    )
    (from_file,) = t.translate([file_rule])
    grafana = prom(expr, params=[2], for_=600)
    assert from_file.signal == grafana.signal
    assert from_file.thresholds == grafana.thresholds
