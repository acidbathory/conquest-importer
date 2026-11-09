"""Review round 2: both sides of a ratio, 'any series' semantics, empty-window and sample notes."""

import importlib

import pytest

from grafana_rules2_eval import evaluate
from importer.core.emit.common import nrql_for
from importer.core.ir import Aggregation, Confidence, IRMonitor
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def prom(expr: str, **kw: object) -> IRMonitor:
    return one(make(expr, instant=True, **kw))  # type: ignore[arg-type]


def file_rule(expr: str, **fields: object) -> IRMonitor:
    return one(
        GrafanaRule.model_validate(
            {
                "title": "r",
                "datasource_managed": True,
                "expr": expr,
                "interval_seconds": 60,
                "for_seconds": 300,
                **fields,
            }
        )
    )


# 1 and 2: a ratio checks both operands, in either order


COUNTER_PAIRS = [
    ("a_total", "foo_seconds"),
    ("lat_count", "lat_sum"),
    ("a_total", "queue_depth"),
]


@pytest.mark.parametrize(("good", "bad"), COUNTER_PAIRS)
def test_a_counter_ratio_with_one_bad_name_is_manual_in_either_order(good: str, bad: str) -> None:
    for left, right in ((good, bad), (bad, good)):
        m = prom(f"sum(rate({left}[5m])) / sum(rate({right}[5m]))")
        assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COUNTER_NAME]


@pytest.mark.parametrize(
    ("left", "right"), [("a_total", "b"), ("a", "b_total"), ("a_sum", "b_sum")]
)
def test_a_gauge_ratio_of_counter_looking_names_is_manual_in_either_order(
    left: str, right: str
) -> None:
    for pair in ((left, right), (right, left)):
        m = prom(f"avg({pair[0]}) / avg({pair[1]})")
        assert m.confidence is Confidence.MANUAL and m.reasons == [t.PROM_COUNTER]


def test_a_single_gauge_selector_is_still_refused_for_a_counter_name() -> None:
    assert prom("avg(http_requests_total)").reasons == [t.PROM_COUNTER]


# 6: 'any series' semantics


def test_an_unaggregated_counter_rate_is_manual() -> None:
    m = prom("rate(errors_total[5m])", params=[1])
    assert m.reasons == [t.R_COUNTER_UNAGG]


def test_an_unaggregated_counter_rate_in_a_rule_file_is_manual_too() -> None:
    assert file_rule("rate(errors_total[5m]) > 2").reasons == [t.R_COUNTER_UNAGG]


SERIES = {"a": [50.0], "b": [150.0], "c": [60.0]}  # one of three crosses 100
LOW = {"a": [50.0], "b": [5.0], "c": [60.0]}  # one of three is below 10


def rows(series: dict[str, list[float]]) -> list[dict[str, object]]:
    return [{"queue_depth": v, "queue": q} for q, vs in series.items() for v in vs]


@pytest.mark.parametrize("operator", [">", ">="])
def test_a_rule_file_above_test_on_a_bare_selector_uses_the_maximum(operator: str) -> None:
    m = file_rule(f"queue_depth {operator} 100")
    assert m.signal.metric and m.signal.metric.aggregation is Aggregation.MAX
    assert nrql_for(m) == "SELECT max(queue_depth) FROM Metric"
    assert evaluate(nrql_for(m), rows(SERIES)) == {(): 150.0}  # one series crosses: it fires
    assert t.N_ANY_SERIES in m.reasons


@pytest.mark.parametrize("operator", ["<", "<="])
def test_a_rule_file_below_test_on_a_bare_selector_uses_the_minimum(operator: str) -> None:
    m = file_rule(f"queue_depth {operator} 10")
    assert m.signal.metric and m.signal.metric.aggregation is Aggregation.MIN
    assert nrql_for(m) == "SELECT min(queue_depth) FROM Metric"
    assert evaluate(nrql_for(m), rows(LOW)) == {(): 5.0}
    assert t.N_ANY_SERIES in m.reasons


def test_the_average_would_have_missed_the_series_that_crosses() -> None:
    average = evaluate("SELECT average(queue_depth) FROM Metric", rows(SERIES))[()]
    assert average < 100 <= evaluate("SELECT max(queue_depth) FROM Metric", rows(SERIES))[()]


@pytest.mark.parametrize("operator", ["==", "!="])
def test_equality_on_a_bare_selector_in_a_rule_file_is_manual(operator: str) -> None:
    assert file_rule(f"up {operator} 0").reasons == [t.R_PER_SERIES_TEST]


def test_an_aggregated_rule_file_query_keeps_its_aggregation() -> None:
    m = file_rule("avg(queue_depth) == 0")
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT average(queue_depth) FROM Metric"
    assert t.N_ANY_SERIES not in m.reasons


def test_a_grafana_rule_with_a_bare_selector_is_unchanged() -> None:
    m = prom("queue_depth", params=[100])
    assert nrql_for(m) == "SELECT average(queue_depth) FROM Metric"
    assert t.N_ANY_SERIES not in m.reasons


# 7: empty windows and scrape interval


@pytest.mark.parametrize(
    ("kind", "noted"), [("lt", True), ("lte", True), ("eq", True), ("gt", False)]
)
@pytest.mark.parametrize(
    "expr",
    [
        "sum(rate(x_total[5m]))",
        "sum(increase(x_total[5m]))",
        "sum(rate(a_total[5m])) / sum(rate(b_total[5m]))",
    ],
)
def test_a_low_threshold_on_a_counter_says_an_idle_counter_may_not_fire(
    expr: str, kind: str, noted: bool
) -> None:
    m = prom(expr, kind=kind, params=[1])
    assert (t.N_COUNTER_EMPTY in m.reasons) is noted


def test_the_empty_window_note_is_also_on_a_rule_file_counter() -> None:
    assert t.N_COUNTER_EMPTY in file_rule("sum(rate(x_total[5m])) < 1").reasons


@pytest.mark.parametrize("expr", ["sum(rate(x_total[5m]))", "sum(increase(x_total[5m]))"])
def test_a_range_function_says_prometheus_needs_two_samples(expr: str) -> None:
    assert t.N_RATE_SAMPLES in prom(expr).reasons


def test_a_gauge_ratio_has_neither_counter_note() -> None:
    m = prom("avg(a) / avg(b)", kind="lt")
    assert t.N_RATE_SAMPLES not in m.reasons and t.N_COUNTER_EMPTY not in m.reasons
