"""Rule-file rules (Mimir and Loki ruler, Prometheus rule files) use the same handlers as the
Grafana-managed rules: the same query gives the same signal either way."""

from pathlib import Path

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator, SignalKind
from importer.sources.grafana.models import GrafanaRule, load_rules
from importer.sources.grafana.translate import N_RULE_FILE, R_NO_COMPARISON, translate
from test_grafana import make, one, text

RULES2 = Path(__file__).parent / "fixtures" / "grafana-rules2"


def rule_file(expr: str, **fields: object) -> GrafanaRule:
    return GrafanaRule.model_validate(
        {
            "title": "r",
            "uid": "u1",
            "expr": expr,
            "datasource_managed": True,
            "for_seconds": 300,
            "interval_seconds": 60,
            **fields,
        }
    )


def comparable(monitor: IRMonitor) -> tuple[object, ...]:
    return (
        monitor.confidence,
        monitor.signal.model_dump(mode="json"),
        [t.model_dump(mode="json") for t in monitor.thresholds],
    )


@pytest.mark.parametrize(
    ("expr", "operator", "evaluator", "value"),
    [
        ('avg by (queue) (queue_depth{queue=~"orders|billing"})', ">=", "gte", 1000),
        ("avg(queue_depth)", ">", "gt", 5),
        ('avg(temperature{room="a"})', "<", "lt", 2.5),
        ("avg(queue_depth)", "==", "eq", 0),
    ],
)
def test_the_same_query_gives_the_same_signal_in_a_rule_file_and_in_a_grafana_rule(
    expr: str, operator: str, evaluator: str, value: float
) -> None:
    from_file = one(rule_file(f"{expr} {operator} {value:g}"))
    grafana = one(make(expr, kind=evaluator, params=[value], reducer="last", instant=True))
    assert from_file.confidence is Confidence.APPROXIMATE
    assert comparable(from_file) == comparable(grafana)


def test_a_rule_file_rule_is_labelled_as_one_and_keeps_the_pending_period() -> None:
    monitor = one(rule_file("queue_depth > 5", for_seconds=600))
    assert N_RULE_FILE in monitor.reasons
    assert monitor.signal.window_seconds == 600
    assert monitor.signal.aggregation_window_seconds == 60
    assert monitor.thresholds[0].operator is Operator.ABOVE


def test_a_loki_rule_file_rule_counts_log_lines() -> None:
    expr = 'sum(count_over_time({app="api"} |= "error" [5m])) > 10'
    monitor = one(rule_file(expr))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert monitor.signal.kind is SignalKind.LOG
    assert monitor.signal.aggregation_window_seconds == 300
    assert "FROM Log" in nrql_for(monitor)


def test_a_rule_file_query_without_a_comparison_has_no_threshold() -> None:
    monitor = one(rule_file("avg(queue_depth)"))
    assert monitor.confidence is Confidence.MANUAL
    assert monitor.reasons == [R_NO_COMPARISON]


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("histogram_quantile(0.9, sum by (le) (x_bucket)) > 1", "histogram_quantile"),
        ("bottomk(3, queue_depth) > 1", "topk"),
        ("queue_depth offset 1d > 1", "offset"),
        ("queue_depth > queue_limit", "binary operator"),
        ("avg_over_time(queue_depth[5m]) > 1", "_over_time"),
    ],
)
def test_a_rule_file_query_that_cannot_convert_is_manual_for_the_same_reason(
    expr: str, fragment: str
) -> None:
    monitor = one(rule_file(expr))
    assert monitor.confidence is Confidence.MANUAL
    assert fragment in text(monitor)


def test_a_recording_rule_in_a_rule_file_is_still_a_recording_rule() -> None:
    monitors = {m.name: m for m in translate(load_rules(RULES2 / "prometheus_rule_file.json"))}
    assert "recording rule" in text(monitors["job:payments_errors:rate5m"])


def test_a_rule_file_rule_without_a_query_is_the_old_manual_reason() -> None:
    monitor = one(GrafanaRule(title="t", datasource_managed=True))
    assert "data source-managed rule" in text(monitor) and monitor.confidence is Confidence.MANUAL
