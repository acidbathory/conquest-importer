"""absent() and absent_over_time() become a loss-of-signal (heartbeat) condition.

The condition counts the rows of the metric and fires when none arrive for the duration. Like
Prometheus absent(), it fires only when no matching series reports at all (there is no FACET).
"""

import importlib

import pytest

from grafana_rules2_eval import evaluate
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator, SignalKind
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one, text

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def prom(expr: str, **kw: object) -> IRMonitor:
    kw = {"kind": "gt", "params": [0], "for_": 0, "no_data_state": "Alerting", **kw}
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def file_rule(expr: str, **fields: object) -> IRMonitor:
    rule = GrafanaRule(
        title="r",
        datasource_managed=True,
        expr=expr,
        interval_seconds=60,
        **fields,
    )
    return one(rule)


def test_absent_is_a_heartbeat_with_loss_of_signal() -> None:
    m = prom('absent(up{job="payments"})')
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.kind is SignalKind.HEARTBEAT and m.signal.heartbeat is not None
    assert m.thresholds[0].operator is Operator.BELOW and m.thresholds[0].value == 1
    assert m.no_data.notify and m.no_data.timeout_minutes == 1
    assert nrql_for(m) == "SELECT count(*) FROM Metric WHERE metricName = 'up' AND job = 'payments'"
    assert t.N_ABSENT in m.reasons


def test_the_condition_means_what_absent_means() -> None:
    nrql = nrql_for(prom('absent(up{job="payments"})'))
    present = [{"metricName": "up", "job": "payments"}] * 3
    other = [{"metricName": "up", "job": "billing"}, {"metricName": "cpu", "job": "payments"}]
    # absent() returns nothing while a matching series exists: the count is 3, not below 1
    assert evaluate(nrql, present)[()] == 3 and 3 >= 1
    # absent() returns 1 when none matches: the query returns no row, which is what the
    # expiration (loss of signal) turns into an open violation
    assert evaluate(nrql, other) == {}
    assert evaluate(nrql, []) == {}


def test_the_expiration_is_the_pending_period() -> None:
    m = prom("absent(up)", for_=600)
    assert m.signal.window_seconds == 600 and m.no_data.timeout_minutes == 10


def test_absent_over_time_uses_its_range_as_the_time_without_data() -> None:
    m = prom("absent_over_time(up[10m])")
    assert m.signal.window_seconds == 600 and m.no_data.timeout_minutes == 10
    assert m.signal.aggregation_window_seconds == 60


def test_the_longer_of_the_range_and_the_pending_period_wins() -> None:
    assert prom("absent_over_time(up[5m])", for_=900).no_data.timeout_minutes == 15


def test_regex_and_negative_matchers_become_the_scope() -> None:
    m = prom('absent(up{job=~"a|b",env!="dev"})')
    assert nrql_for(m) == (
        "SELECT count(*) FROM Metric WHERE metricName = 'up' AND job IN ('a', 'b') AND env != 'dev'"
    )


@pytest.mark.parametrize(
    ("kind", "params"),
    [("gt", [0]), ("gte", [1]), ("eq", [1]), ("ne", [0]), ("gt", [0.5])],
)
def test_a_threshold_that_fires_on_the_value_one_converts(kind: str, params: list[float]) -> None:
    assert prom("absent(up)", kind=kind, params=params).confidence is Confidence.APPROXIMATE


@pytest.mark.parametrize(
    ("kind", "params"), [("lt", [1]), ("gt", [1]), ("eq", [0]), ("gte", [2]), ("lte", [0])]
)
def test_a_threshold_that_does_not_fire_on_one_is_manual(kind: str, params: list[float]) -> None:
    m = prom("absent(up)", kind=kind, params=params)
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_ABSENT_THRESHOLD]


def test_a_rule_file_absent_rule_needs_no_threshold() -> None:
    m = file_rule("absent(up{job='x'})".replace("'", '"'), for_seconds=300)
    assert m.signal.kind is SignalKind.HEARTBEAT and m.no_data.timeout_minutes == 5
    assert t.N_RULE_FILE in m.reasons


def test_a_rule_file_absent_compared_with_one_converts_too() -> None:
    assert file_rule("absent(up) == 1").signal.kind is SignalKind.HEARTBEAT


@pytest.mark.parametrize(
    "expr",
    [
        "absent(sum(up))",
        "absent(up offset 1d)",
        "absent(up) and absent(down)",
        "absent(up[5m])",
        "absent_over_time(up)",
    ],
)
def test_other_absent_shapes_keep_the_absent_reason(expr: str) -> None:
    m = prom(expr)
    assert (m.confidence is Confidence.MANUAL and "absent" in text(m)) or "offset" in text(m)


def test_up_equal_to_zero_is_an_ordinary_metric_condition_not_absence() -> None:
    m = file_rule('up{job="payments"} == 0', for_seconds=60)
    assert m.signal.kind is SignalKind.METRIC
    assert m.thresholds[0].operator is Operator.EQUALS and m.thresholds[0].value == 0


def test_a_comparison_in_the_query_and_a_threshold_node_cannot_combine() -> None:
    m = prom('up{job="payments"} == 0')
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COMPARE_AND_NODE]
