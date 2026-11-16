"""Constant multipliers inside a sum, and the mean of a counter over the rule's range."""

import importlib

from grafana_rules2_eval import evaluate
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor
from test_grafana import make, one
from test_grafana_rules2_counters import delta_rows

t = importlib.import_module("importer.sources.grafana.translate")


def prom(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def test_a_constant_inside_sum_is_applied_to_the_sum() -> None:
    m = prom("sum(rate(orders_total[1m]) * 60)", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    rows = delta_rows("orders_total", {"a": [10.0], "b": [20.0]})
    # the PromQL value by hand: (10/60 + 20/60) * 60 = 30
    assert evaluate(nrql_for(m), rows) == {(): 30.0}


def test_a_constant_before_the_call_and_with_a_group() -> None:
    m = prom("sum by (code) (60 * rate(orders_total[2m]))", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.aggregation_window_seconds == 120
    rows = delta_rows("orders_total", {"a": [120.0]}, code="200")
    assert evaluate(nrql_for(m), rows) == {("200",): 60.0}


def test_a_division_by_a_constant_inside_sum() -> None:
    m = prom("sum(increase(orders_total[1m]) / 2)", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    assert evaluate(nrql_for(m), delta_rows("orders_total", {"a": [10.0]})) == {(): 5.0}


def test_two_constants_in_a_row() -> None:
    m = prom("sum(rate(orders_total[10m])*60*10)", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    rows = delta_rows("orders_total", {"a": [600.0]})
    assert evaluate(nrql_for(m), rows) == {(): 600.0}


def test_a_negative_constant_inside_max_stays_manual() -> None:
    m = prom("max(increase(orders_total[1m]) * -1)", params=[5])
    assert m.confidence is Confidence.MANUAL


def test_a_constant_on_a_gauge_counter_name_still_follows_the_counter_rule() -> None:
    m = prom("sum(rate(queue_depth[1m]) * 60)", params=[5])
    assert m.confidence is Confidence.MANUAL


def averaged(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, reducer="mean", time_range=600, **kw))  # type: ignore[arg-type]


def test_the_mean_of_a_rate_is_the_change_over_the_rules_range() -> None:
    m = averaged("sum(rate(orders_total[1m]))", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.aggregation_window_seconds == 600
    # 10 steps of 60 s, 60 and 120 and 0 ... in a 600 s range: 1200 in 600 s is 2 per second
    rows = delta_rows("orders_total", {"a": [60.0, 120.0, 0.0, 60.0], "b": [960.0]})
    assert evaluate(nrql_for(m), rows) == {(): 2.0}
    assert any("600s" in r for r in m.reasons)


def test_the_mean_of_an_increase_is_scaled_to_one_step() -> None:
    m = averaged("sum(increase(orders_total[1m]))", params=[5])
    assert m.confidence is Confidence.APPROXIMATE
    # 1200 over ten one-minute steps: a mean of 120 per step
    rows = delta_rows("orders_total", {"a": [1200.0]})
    assert evaluate(nrql_for(m), rows) == {(): 120.0}


def test_the_mean_of_a_ratio_of_counters_is_the_ratio_of_totals() -> None:
    m = averaged(
        "sum(increase(failed_total[1m])) * 100 / sum(increase(requests_total[1m]))", params=[5]
    )
    assert m.confidence is Confidence.APPROXIMATE
    rows = delta_rows("failed_total", {"a": [5.0]}) + delta_rows("requests_total", {"a": [100.0]})
    assert evaluate(nrql_for(m), rows) == {(): 5.0}


def test_max_and_min_of_a_counter_convert_and_sum_stays_manual() -> None:
    # max and min become threshold occurrences (see test_grafana_rules2_extremes.py)
    for reducer in ("max", "min"):
        m = one(make("sum(rate(orders_total[1m]))", reducer=reducer, time_range=600, params=[5]))
        assert m.confidence is Confidence.APPROXIMATE, reducer
    m = one(make("sum(rate(orders_total[1m]))", reducer="sum", time_range=600, params=[5]))
    assert m.confidence is Confidence.MANUAL


def test_a_mean_without_a_whole_minute_range_stays_manual() -> None:
    m = one(make("sum(rate(orders_total[1m]))", reducer="mean", time_range=90, params=[5]))
    assert m.confidence is Confidence.MANUAL


def test_the_mean_of_a_over_time_function_is_not_changed() -> None:
    m = averaged("sum(count_over_time(orders_total[1m]))", params=[5])
    assert m.confidence is Confidence.MANUAL
