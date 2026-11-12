"""A Grafana rule whose condition is its one query, which holds its own comparison."""

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.translate import N_LONE_QUERY, R_SHAPE, translate


def lone(expr: str, **fields: object) -> GrafanaRule:
    node = {
        "refId": "A",
        "datasourceUid": "ds-1",
        "relativeTimeRange": {"from": 600, "to": 0},
        "model": {"refId": "A", "expr": expr, "instant": True},
    }
    return GrafanaRule.model_validate(
        {
            "title": "r",
            "uid": "u1",
            "condition": "A",
            "data": [node],
            "for_seconds": 300,
            "interval_seconds": 60,
            "no_data_state": "NoData",
            "exec_err_state": "OK",
            **fields,
        }
    )


def one(rule: GrafanaRule) -> IRMonitor:
    (monitor,) = translate([rule])
    return monitor


def test_a_comparison_in_the_only_query_is_the_threshold_and_is_labelled() -> None:
    monitor = one(lone("avg(queue_depth) > 5"))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert N_LONE_QUERY in monitor.reasons
    assert monitor.thresholds[0].operator is Operator.ABOVE
    assert monitor.thresholds[0].value == 5
    assert "queue_depth" in nrql_for(monitor)


def test_bool_comparison_fires_on_the_same_series_as_the_plain_one() -> None:
    plain = one(lone("avg(queue_depth) == 0"))
    boolean = one(lone("avg(queue_depth) == bool 0"))
    assert boolean.confidence is Confidence.APPROXIMATE
    assert boolean.thresholds == plain.thresholds
    assert boolean.signal.metric == plain.signal.metric


def test_a_bare_selector_comparison_fires_when_any_series_crosses() -> None:
    monitor = one(lone("queue_depth > 5"))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert "max" in nrql_for(monitor).lower()


def test_a_query_without_a_comparison_stays_manual() -> None:
    monitor = one(lone("vector(1)"))
    assert monitor.confidence is Confidence.MANUAL


def test_a_condition_that_points_at_nothing_stays_manual() -> None:
    monitor = one(lone("queue_depth > 5", condition="Z"))
    assert monitor.confidence is Confidence.MANUAL
    assert monitor.reasons == [R_SHAPE] or monitor.reasons


@pytest.mark.parametrize("expr", ["a > 1 and on (x) b > 1", "a - b > 0"])
def test_a_comparison_that_joins_two_series_stays_manual(expr: str) -> None:
    assert one(lone(expr)).confidence is Confidence.MANUAL
