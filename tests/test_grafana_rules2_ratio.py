"""A ratio of two aggregates of different metrics with the same labels is one NRQL query.

Two aggregates in one SELECT combined by arithmetic is a single value, which a condition can
use. A ratio of one metric under two different label filters would need filter(), which the IR
cannot express, so it stays manual.
"""

import importlib

import pytest

from grafana_rules2_eval import evaluate, rate
from importer.core.emit.common import nrql_for
from importer.core.ir import BinOp, Confidence, IRMonitor
from test_grafana import make, one, text

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def prom(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def test_a_ratio_of_counter_rates_is_the_ratio_of_the_sums() -> None:
    m = prom("sum(rate(errors_total[5m])) / sum(rate(requests_total[5m]))", params=[0.05])
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT sum(errors_total) / sum(requests_total) FROM Metric"
    errors, requests = [3.0, 4.0, 5.0], [100.0, 120.0, 80.0]
    rows = [{"errors_total": e, "requests_total": r} for e, r in zip(errors, requests, strict=True)]
    got = evaluate(nrql_for(m), rows)[()]
    # the division by the window cancels: the ratio of the rates is the ratio of the increases
    assert got == pytest.approx(rate(errors, 300) / rate(requests, 300)) == pytest.approx(0.04)
    assert m.signal.aggregation_window_seconds == 300


def test_a_percentage_of_gauge_averages_keeps_the_factor() -> None:
    m = prom("avg(mem_used) / avg(mem_total) * 100", params=[90])
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT average(mem_used) / average(mem_total) * 100 FROM Metric"
    rows = [{"mem_used": u, "mem_total": 200.0} for u in (170.0, 190.0)]
    assert evaluate(nrql_for(m), rows) == {(): pytest.approx(90.0)}
    assert m.signal.metric and isinstance(m.signal.metric.expression, BinOp)


def test_a_leading_factor_is_the_same_percentage() -> None:
    m = prom("100 * avg(mem_used) / avg(mem_total)", params=[90])
    assert nrql_for(m) == "SELECT average(mem_used) / average(mem_total) * 100 FROM Metric"


def test_grouping_and_labels_are_shared_by_both_sides() -> None:
    m = prom(
        'sum by (svc) (rate(a_total{env="p"}[5m])) / sum by (svc) (rate(b_total{env="p"}[5m]))'
    )
    assert nrql_for(m) == (
        "SELECT sum(a_total) / sum(b_total) FROM Metric WHERE env = 'p' FACET svc"
    )


@pytest.mark.parametrize(
    "expr",
    [
        'sum(rate(r_total{code="500"}[5m])) / sum(rate(r_total[5m]))',
        'sum(rate(a_total{env="p"}[5m])) / sum(rate(b_total{env="q"}[5m]))',
        "sum by (a) (rate(x_total[5m])) / sum by (b) (rate(y_total[5m]))",
        "sum(rate(x_total[5m])) / sum by (b) (rate(y_total[5m]))",
        "sum(rate(x_total[5m])) / sum(rate(y_total[10m]))",
    ],
)
def test_a_ratio_needing_different_filters_or_grouping_is_manual(expr: str) -> None:
    m = prom(expr)
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_RATIO_SHAPE]


@pytest.mark.parametrize(
    ("expr", "reason"),
    [
        ("max(a) / max(b)", t.R_RATIO_AGG.format(agg="max")),
        ("sum(a) / sum(b)", t.R_AGG_MIX.format(agg="sum", reducer="last")),
        ("avg(a) / sum(b)", t.R_RATIO_SHAPE),
        ("a / b", t.R_RATIO_SHAPE),
        ("sum(rate(a_total[5m])) / sum(b)", t.R_RATIO_SHAPE),
        ("avg(rate(a_total[5m])) / avg(rate(b_total[5m]))", t.R_COUNTER_AGG.format(agg="avg")),
    ],
)
def test_a_ratio_that_is_not_exact_has_a_precise_reason(expr: str, reason: str) -> None:
    assert prom(expr).reasons == [reason]


@pytest.mark.parametrize(
    "expr",
    [
        "avg(a) / avg(b) + 1",
        "avg(a) / avg(b) * avg(c)",
        "(avg(a) + avg(b)) / avg(c)",
        "avg(a) / 0",
        "avg(a) / avg(b) / avg(c)",
    ],
)
def test_other_arithmetic_keeps_the_binary_operator_reason(expr: str) -> None:
    m = prom(expr)
    assert m.confidence is Confidence.MANUAL and "binary operator" in text(m)


def test_the_division_by_zero_is_noted() -> None:
    assert t.N_RATIO_ZERO in prom("avg(a) / avg(b)").reasons
