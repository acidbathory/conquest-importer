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
    kw = {"kind": "gt", "params": [0], "for_": 0, **kw}
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
    assert m.no_data.notify and m.no_data.timeout_minutes == 5
    assert nrql_for(m) == "SELECT count(*) FROM Metric WHERE metricName = 'up' AND job = 'payments'"
    assert t.N_ABSENT in m.reasons


def test_the_condition_means_what_absent_means() -> None:
    nrql = nrql_for(prom('absent(up{job="payments"})'))
    present = [{"metricName": "up", "job": "payments"}] * 3
    other = [{"metricName": "up", "job": "billing"}, {"metricName": "cpu", "job": "payments"}]
    # while a matching series exists the count is positive, so nothing is below 1
    assert evaluate(nrql, present)[()] == 3
    # with no matching series the query returns no row, which the expiration turns into a
    # violation
    assert evaluate(nrql, other) == {}
    assert evaluate(nrql, []) == {}


LOOKBACK = 300  # Prometheus drops a series from instant queries five minutes after its last sample


def prometheus_fires_at(last_sample: int, pending: int) -> int:
    """absent() is 1 from the moment the last sample is older than the lookback; the alert
    fires once it has been pending for `for`."""
    return last_sample + LOOKBACK + pending


def new_relic_fires_at(last_data: int, monitor: IRMonitor) -> int:
    """Loss of signal opens a violation when no data has arrived for the expiration."""
    assert monitor.no_data.notify and monitor.no_data.timeout_minutes
    return last_data + monitor.no_data.timeout_minutes * 60


@pytest.mark.parametrize("pending", [0, 60, 300, 600, 3600])
def test_the_expiration_fires_when_prometheus_would_fire(pending: int) -> None:
    m = prom("absent(up)", for_=pending)
    for last in (0, 1234):
        assert new_relic_fires_at(last, m) == prometheus_fires_at(last, pending)


@pytest.mark.parametrize(("pending", "minutes"), [(0, 5), (60, 6), (300, 10), (600, 15)])
def test_the_expiration_is_the_pending_period_plus_the_staleness_lookback(
    pending: int, minutes: int
) -> None:
    assert prom("absent(up)", for_=pending).no_data.timeout_minutes == minutes


@pytest.mark.parametrize(
    ("range_", "pending", "minutes"),
    [("10m", 0, 10), ("5m", 900, 20), ("1m", 0, 5), ("2m", 60, 5), ("30m", 120, 32)],
)
def test_absent_over_time_waits_its_range_plus_the_pending_period_but_never_under_five_minutes(
    range_: str, pending: int, minutes: int
) -> None:
    m = prom(f"absent_over_time(up[{range_}])", for_=pending)
    assert m.no_data.timeout_minutes == minutes
    assert m.signal.aggregation_window_seconds == 60


@pytest.mark.parametrize("state", ["OK", "NoData", "Alerting", "KeepLast"])
def test_a_grafana_absent_rule_says_what_its_no_data_state_does_unless_ok(state: str) -> None:
    m = prom("absent(up)", no_data_state=state)
    assert (t.N_ABSENT_NODATA in m.reasons) is (state != "OK")
    assert m.confidence is Confidence.APPROXIMATE and m.no_data.notify


def test_a_rule_file_absent_rule_has_no_no_data_state_to_explain() -> None:
    assert t.N_ABSENT_NODATA not in file_rule("absent(up)").reasons


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
    assert m.signal.kind is SignalKind.HEARTBEAT and m.no_data.timeout_minutes == 10
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
    m = file_rule('avg(up{job="payments"}) == 0', for_seconds=60)
    assert m.signal.kind is SignalKind.METRIC
    assert m.thresholds[0].operator is Operator.EQUALS and m.thresholds[0].value == 0


def test_a_comparison_in_the_query_and_a_threshold_node_cannot_combine() -> None:
    m = prom('up{job="payments"} == 0')
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COMPARE_AND_NODE]
