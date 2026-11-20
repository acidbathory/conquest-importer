"""max() across counter rates converts as sum() when the rule fires above 0, and only then.

A rate or increase of a counter is never negative, so in each group the largest value is above 0
exactly when the sum is. Each case that converts runs the NRQL on sample delta rows and compares
its firing decision with the PromQL max per group.
"""

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from grafana_rules2_eval import evaluate
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one
from test_grafana_rules2_pipeline import classic, cond

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")

INCREASE = "increase(restarts_total[10m])"
MAX_INCREASE = f"max by (service) ({INCREASE})"


def above_zero(expr: str) -> IRMonitor:
    return one(make(expr, params=[0]))


def rule_file(expr: str) -> IRMonitor:
    monitor: IRMonitor
    (monitor,) = t.translate([GrafanaRule(title="r", datasource_managed=True, expr=expr)])
    return monitor


@pytest.mark.parametrize(
    ("built", "nrql"),
    [
        (
            lambda: above_zero(f"max by (service) ({INCREASE})"),
            "SELECT sum(restarts_total) FROM Metric FACET service",
        ),
        (
            lambda: above_zero(f"max({INCREASE}) by (service)"),
            "SELECT sum(restarts_total) FROM Metric FACET service",
        ),
        (
            lambda: above_zero('max by (service) (rate(restarts_total{env="prod"}[5m]))'),
            "SELECT sum(restarts_total) / 300 FROM Metric WHERE env = 'prod' FACET service",
        ),
        (
            lambda: above_zero(f"max by (service) ({INCREASE}) * 60"),
            "SELECT sum(restarts_total) * 60 FROM Metric FACET service",
        ),
        (
            lambda: above_zero(f"max by (service) ({INCREASE} * 60)"),
            "SELECT sum(restarts_total) * 60 FROM Metric FACET service",
        ),
        (
            lambda: one(classic([cond("gt", [0], "last")], expr=f"max by (service) ({INCREASE})")),
            "SELECT sum(restarts_total) FROM Metric FACET service",
        ),
        (
            lambda: one(classic([cond("gt", [0, 0], "max")], expr=MAX_INCREASE)),
            "SELECT sum(restarts_total) FROM Metric FACET service",
        ),
        (
            lambda: rule_file(f"max by (service) ({INCREASE}) > 0"),
            "SELECT sum(restarts_total) FROM Metric FACET service",
        ),
    ],
)
def test_max_of_a_counter_rate_above_zero_is_written_as_the_sum(built: Any, nrql: str) -> None:
    m = built()
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == nrql
    assert t.N_MAX_AS_SUM in m.reasons
    assert (m.thresholds[0].operator, m.thresholds[0].value) == (Operator.ABOVE, 0)


@pytest.mark.parametrize(
    "expr",
    [f"max by (service) ({INCREASE})", f"max by (service) ({INCREASE}) * 60"],
)
def test_the_max_form_is_the_sum_form_plus_one_note(expr: str) -> None:
    from_max = above_zero(expr)
    from_sum = above_zero(expr.replace("max", "sum", 1))
    assert from_max.signal == from_sum.signal
    assert from_max.thresholds == from_sum.thresholds
    assert [r for r in from_max.reasons if r != t.N_MAX_AS_SUM] == from_sum.reasons


def test_the_sum_fires_on_the_same_groups_as_the_max() -> None:
    m = above_zero(f"max by (service) ({INCREASE})")
    series = {  # (service, pod): the change in each interval, as New Relic stores a counter
        ("api", "a"): [0.0, 0.0, 0.0],
        ("api", "b"): [0.0, 2.0, 0.0],
        ("web", "a"): [0.0, 0.0],
        ("web", "b"): [0.0, 0.0, 0.0],
        ("db", "a"): [5.0, 1.0],
        ("db", "b"): [3.0],
    }
    rows = [
        {"restarts_total": d, "service": service, "pod": pod}
        for (service, pod), deltas in series.items()
        for d in deltas
    ]
    promql_max: dict[tuple[str, ...], float] = {}
    for (service, _pod), deltas in series.items():
        key = (service,)
        promql_max[key] = max(promql_max.get(key, 0.0), sum(deltas))
    got = evaluate(nrql_for(m), rows)
    assert got == {("api",): 2.0, ("web",): 0.0, ("db",): 9.0}
    assert promql_max == {("api",): 2.0, ("web",): 0.0, ("db",): 6.0}
    assert {k: v > 0 for k, v in got.items()} == {k: v > 0 for k, v in promql_max.items()}


@pytest.mark.parametrize(
    ("kind", "params"),
    [("gt", [1]), ("gt", [0.1]), ("gte", [0]), ("lt", [0]), ("lt", [1]), ("eq", [0])],
)
def test_max_of_a_counter_rate_with_another_threshold_keeps_its_reason(
    kind: str, params: list[float]
) -> None:
    m = one(make(f"max by (service) ({INCREASE})", kind=kind, params=params))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons == [t.R_COUNTER_AGG.format(agg="max")]


@pytest.mark.parametrize("kind", ["within_range", "outside_range"])
def test_max_of_a_counter_rate_with_a_range_threshold_is_manual(kind: str) -> None:
    m = one(make(f"max by (service) ({INCREASE})", kind=kind, params=[0, 1]))
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_RANGE]


@pytest.mark.parametrize("agg", ["min", "avg"])
def test_other_aggregators_above_zero_stay_manual(agg: str) -> None:
    m = above_zero(f"{agg} by (service) ({INCREASE})")
    assert m.confidence is Confidence.MANUAL
    assert m.reasons == [t.R_COUNTER_AGG.format(agg=agg)]


@pytest.mark.parametrize(
    "expr",
    [
        f"max by (service) ({INCREASE}) - max by (service) (increase(crashes_total[10m]))",
        f"max by (service) ({INCREASE}) / max by (service) (increase(crashes_total[10m]))",
        f"max by (service) ({INCREASE}) * -1",
        f"max by (service) ({INCREASE}) * 0",
        f"60 / max by (service) ({INCREASE})",
        f"max by (service) ({INCREASE}) / 0",
        f"max by (service) ({INCREASE} / 0)",
    ],
)
def test_max_inside_other_arithmetic_stays_manual(expr: str) -> None:
    assert above_zero(expr).confidence is Confidence.MANUAL


def test_max_of_a_name_new_relic_would_not_store_as_a_count_stays_manual() -> None:
    m = above_zero("max by (service) (increase(queue_depth[10m]))")
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COUNTER_NAME]


def test_max_of_a_rate_of_a_hinted_gauge_stays_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"types": {"restarts_total": "gauge"}}))
    monkeypatch.setenv("IMPORTER_GRAFANA_NAME_MAP", str(path))
    m = above_zero(f"max by (service) ({INCREASE})")
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_RATE_OF_GAUGE]
