"""Grafana rules to IR: what converts, what stays manual, and the reason for each."""

from pathlib import Path
from typing import Any

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator, SignalKind
from importer.core.remediation import steps_for
from importer.sources import REGISTRY
from importer.sources.grafana.models import GrafanaRule, load_rules
from importer.sources.grafana.translate import translate

FIXTURES = Path(__file__).parent / "fixtures" / "grafana"
CONVERTED = {
    "ckg-prom-cpu-gauge",
    "ckg-prom-regex-unagg",
    "ckg-loki-errors",
    "ckg-expr-keeplast-nodata",
}


def by_title() -> dict[str, IRMonitor]:
    return {m.name: m for m in translate(load_rules(FIXTURES / "export_alert_rules.json"))}


def text(m: IRMonitor) -> str:
    return " | ".join(m.reasons).lower()


def make(
    expr: str,
    *,
    dtype: str | None = "prometheus",
    reducer: str | None = "last",
    kind: str = "gt",
    params: list[Any] | None = None,
    for_: int = 300,
    interval: int | None = 60,
    uid: str = "ds-1",
    **rule: Any,
) -> GrafanaRule:
    """A rule with the pipeline query -> reduce -> threshold."""
    model: dict[str, Any] = {"refId": "A", "expr": expr}
    if dtype:
        model["datasource"] = {"type": dtype, "uid": uid}
    nodes: list[dict[str, Any]] = [
        {"refId": "A", "datasourceUid": uid, "model": model, "relativeTimeRange": {"from": 600}}
    ]
    source = "A"
    if reducer:
        nodes.append(
            {
                "refId": "B",
                "datasourceUid": "__expr__",
                "model": {"type": "reduce", "expression": "A", "reducer": reducer},
            }
        )
        source = "B"
    evaluator = {"type": kind, "params": [80] if params is None else params}
    nodes.append(
        {
            "refId": "C",
            "datasourceUid": "__expr__",
            "model": {
                "type": "threshold",
                "expression": source,
                "conditions": [{"evaluator": evaluator}],
            },
        }
    )
    return GrafanaRule.model_validate(
        {
            "title": "r",
            "uid": "u1",
            "condition": "C",
            "data": nodes,
            "for_seconds": for_,
            "interval_seconds": interval,
            "no_data_state": "NoData",
            "exec_err_state": "OK",
            **rule,
        }
    )


def one(rule: GrafanaRule) -> IRMonitor:
    (monitor,) = translate([rule])
    return monitor


# real fixtures


def test_the_source_is_registered_and_loads_the_fixture_folder() -> None:
    monitors = REGISTRY["grafana"].convert(FIXTURES)
    assert len(monitors) == 21


def test_exactly_the_simple_sample_rules_convert() -> None:
    monitors = by_title()
    converted = {t for t, m in monitors.items() if m.confidence is Confidence.APPROXIMATE}
    assert converted == CONVERTED
    assert len(monitors) == 17


def test_every_unconverted_sample_rule_has_one_specific_reason_and_steps() -> None:
    for title, m in by_title().items():
        if title in CONVERTED:
            continue
        assert m.confidence is Confidence.MANUAL, title
        assert steps_for(m.reasons), (title, m.reasons)


@pytest.mark.parametrize(
    ("title", "fragment"),
    [
        ("ckg-expr-flatten", "math expression"),
        ("ckg-expr-multi-ds-manual", "more than one data source"),
        ("ckg-expr-within-range", "threshold range"),
        ("ckg-loki-latency", "parser stage or unwrap"),
        ("ckg-prom-5xx-rate", "promql uses rate"),
        ("ckg-prom-absent", "promql uses absent"),
        ("ckg-prom-disk-fill", "promql uses rate"),
        ("ckg-prom-join-manual", "promql uses vector matching"),
        ("ckg-prom-mem-ratio", "promql uses an _over_time"),
        ("ckg-prom-offset-manual", "promql uses offset"),
        ("ckg-prom-p95", "promql uses histogram_quantile"),
        ("ckg-prom-topk-manual", "promql uses topk"),
        ("ckg-prom-up-zero", "promql uses a binary operator"),
    ],
)
def test_each_unconverted_sample_rule_says_why(title: str, fragment: str) -> None:
    assert fragment in text(by_title()[title])


def test_the_cpu_gauge_becomes_an_average_by_instance() -> None:
    m = by_title()["ckg-prom-cpu-gauge"]
    assert m.signal.kind is SignalKind.METRIC and m.source_type == "alert rule"
    assert (
        nrql_for(m)
        == "SELECT average(node_cpu_busy) FROM Metric WHERE job = 'node' AND env != 'dev' "
        "FACET instance"
    )
    assert (m.thresholds[0].operator, m.thresholds[0].value) == (Operator.ABOVE, 80)
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (300, 60)
    assert m.unmapped["enabled"] is False  # the rule was paused
    assert "paused" in text(m)


def test_a_regex_of_literals_becomes_in_and_the_pending_period_is_whole_windows() -> None:
    m = by_title()["ckg-prom-regex-unagg"]
    assert (
        nrql_for(m)
        == "SELECT average(queue_depth) FROM Metric WHERE queue IN ('orders', 'billing')"
    )
    assert m.signal.window_seconds == 120
    assert "each series" in text(m)  # Grafana alerts per series; the query does not


def test_the_loki_count_has_filters_a_facet_and_the_range_as_window() -> None:
    m = by_title()["ckg-loki-errors"]
    assert m.signal.kind is SignalKind.LOG
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE env = 'prod' AND app LIKE 'api%' "
        "AND message LIKE '%error%' AND message NOT LIKE '%healthcheck%' FACET app"
    )
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (300, 300)
    assert "forwarded" in text(m)


def test_keep_last_on_no_data_is_noted_not_dropped_silently() -> None:
    m = by_title()["ckg-expr-keeplast-nodata"]
    assert "keeps the last state" in text(m)
    assert nrql_for(m) == "SELECT average(queue_depth) FROM Metric"


def test_the_hand_made_shapes_convert_or_stay_manual() -> None:
    monitors = {m.name: m for m in translate(load_rules(FIXTURES))}
    loki = monitors["hm-ruler-loki-errors"]
    assert loki.confidence is Confidence.APPROXIMATE
    assert (
        nrql_for(loki)
        == "SELECT count(*) FROM Log WHERE app = 'checkout' AND message LIKE '%timeout%'"
    )
    assert (loki.signal.window_seconds, loki.signal.aggregation_window_seconds) == (600, 300)
    assert loki.no_data.notify and "keep firing" in text(loki)
    queue = monitors["hm-apis-queue-depth"]
    assert nrql_for(queue) == "SELECT max(queue_depth) FROM Metric WHERE env = 'prod' FACET queue"
    assert queue.thresholds[0].operator is Operator.ABOVE_OR_EQUALS
    assert "recovery threshold" in text(queue)
    assert "unsupported" in text(monitors["hm-ruler-cloudwatch"]) or "not supported" in text(
        monitors["hm-ruler-cloudwatch"]
    )
    assert "recording rule" in text(monitors["hm-ruler-recording"])


# conversion details


@pytest.mark.parametrize(
    ("expr", "nrql"),
    [
        ("up_total_x", "SELECT average(up_total_x) FROM Metric"),
        ("avg(cpu)", "SELECT average(cpu) FROM Metric"),
        ("sum(cpu)", "SELECT sum(cpu) FROM Metric"),
        ("min(cpu)", "SELECT min(cpu) FROM Metric"),
        ("max(cpu)", "SELECT max(cpu) FROM Metric"),
        ("sum by (a, b) (cpu)", "SELECT sum(cpu) FROM Metric FACET a, b"),
        ("sum(cpu) by (a)", "SELECT sum(cpu) FROM Metric FACET a"),
        ('cpu{a="x"}', "SELECT average(cpu) FROM Metric WHERE a = 'x'"),
        ('cpu{a="x",}', "SELECT average(cpu) FROM Metric WHERE a = 'x'"),
        ('cpu{a!="x"}', "SELECT average(cpu) FROM Metric WHERE a != 'x'"),
        ('cpu{a=~"x|y"}', "SELECT average(cpu) FROM Metric WHERE a IN ('x', 'y')"),
        ('cpu{a=~"x"}', "SELECT average(cpu) FROM Metric WHERE a = 'x'"),
        ('cpu{a!~"x|y"}', "SELECT average(cpu) FROM Metric WHERE a NOT IN ('x', 'y')"),
        ('cpu{a=~"x.*"}', "SELECT average(cpu) FROM Metric WHERE a LIKE 'x%'"),
        ('cpu{a=~".*x"}', "SELECT average(cpu) FROM Metric WHERE a LIKE '%x'"),
        ('cpu{a=~".*x.*"}', "SELECT average(cpu) FROM Metric WHERE a LIKE '%x%'"),
        ('cpu{a!~"x.*"}', "SELECT average(cpu) FROM Metric WHERE a NOT LIKE 'x%'"),
        ('cpu{a=""}', "SELECT average(cpu) FROM Metric WHERE a IS NULL"),
        ('cpu{a="it\'s"}', "SELECT average(cpu) FROM Metric WHERE a = 'it\\'s'"),
        ('  avg( cpu{ a = "x" } )  ', "SELECT average(cpu) FROM Metric WHERE a = 'x'"),
    ],
)
def test_simple_promql_converts(expr: str, nrql: str) -> None:
    m = one(make(expr, reducer="last"))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == nrql


@pytest.mark.parametrize(
    ("reducer", "word"),
    [("last", "average"), ("mean", "average"), ("min", "min"), ("max", "max"), ("sum", "sum")],
)
def test_a_bare_selector_takes_its_aggregation_from_the_reducer(reducer: str, word: str) -> None:
    assert nrql_for(one(make("cpu", reducer=reducer))) == f"SELECT {word}(cpu) FROM Metric"  # noqa: S608 - expected text, not SQL


def test_a_promql_aggregation_wins_over_the_reducer() -> None:
    assert nrql_for(one(make("sum(cpu)", reducer="mean"))) == "SELECT sum(cpu) FROM Metric"


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ("rate(x[5m])", "promql uses rate"),
        ("increase(x[5m])", "promql uses rate"),
        ("avg_over_time(x[5m])", "promql uses an _over_time"),
        ("x offset 5m", "promql uses offset"),
        ("max_over_time(x[1h:5m])", "promql uses a subquery"),
        ("a * on(p) group_left(t) b", "promql uses vector matching"),
        ("a / ignoring(x) group_right b", "promql uses vector matching"),
        ("topk(3, x)", "promql uses topk"),
        ("bottomk(3, x)", "promql uses topk"),
        ("histogram_quantile(0.9, x)", "promql uses histogram_quantile"),
        ("absent(x)", "promql uses absent"),
        ("absent_over_time(x[5m])", "promql uses absent"),
        ("x + y", "promql uses a binary operator"),
        ("x == 0", "promql uses a binary operator"),
        ("x > bool 1", "promql uses a binary operator"),
        ("x and y", "promql uses a binary operator"),
        ("sum without (a) (x)", "promql uses without"),
        ("count(x)", "promql expression is not a simple"),
        ("avg(x) by (a) by (b)", "promql expression is not a simple"),
        ("sum by (a) (sum by (b) (x))", "promql expression is not a simple"),
        ("x[5m]", "promql expression is not a simple"),
        ("", "promql expression is not a simple"),
        ('{__name__="x"}', "promql expression is not a simple"),
        ("(x)", "promql expression is not a simple"),
        ('x{a="\\n"}', "promql label matcher"),
        ('x{a=~"a.+"}', "promql label matcher"),
        ('x{a=~"(a|b)"}', "promql label matcher"),
        ('x{a!=""}', "promql label matcher"),
        ('x{__name__="y"}', "promql label matcher"),
        ('x{a="1" b="2"}', "promql expression is not a simple"),
        ('x{a=~"a%"}', "promql label matcher"),
        ('x{a=~".*"}', "promql label matcher"),
        ("requests_total", "looks like a counter"),
        ("sum(x_bucket)", "looks like a counter"),
        ("a:b:c", "cannot be an nrql attribute name"),
        ('x{a.b="1"}', "promql expression is not a simple"),
        ("sum by (a-b) (x)", "promql expression is not a simple"),
    ],
)
def test_other_promql_is_manual_with_a_specific_reason(expr: str, fragment: str) -> None:
    m = one(make(expr))
    assert m.confidence is Confidence.MANUAL
    assert fragment in text(m), m.reasons
    assert steps_for(m.reasons)


@pytest.mark.parametrize(
    ("expr", "nrql"),
    [
        (
            'count_over_time({app="a"} |= "boom" [5m])',
            "SELECT count(*) FROM Log WHERE app = 'a' AND message LIKE '%boom%'",
        ),
        (
            'sum(count_over_time({app="a"} [5m]))',
            "SELECT count(*) FROM Log WHERE app = 'a'",
        ),
        (
            'sum by (app, env) (count_over_time({k="v"} != "ok" [5m]))',
            "SELECT count(*) FROM Log WHERE k = 'v' AND message NOT LIKE '%ok%' FACET app, env",
        ),
        (
            'sum(count_over_time({a!="b"} [5m])) by (app)',
            "SELECT count(*) FROM Log WHERE a != 'b' FACET app",
        ),
        (
            'sum(count_over_time({a=~"x|y"} [5m]))',
            "SELECT count(*) FROM Log WHERE a IN ('x', 'y')",
        ),
        (
            'sum(count_over_time({a!~"x.*"} [5m]))',
            "SELECT count(*) FROM Log WHERE a NOT LIKE 'x%'",
        ),
        (
            'sum(count_over_time({a="1"} |~ "timeout" [5m]))',
            "SELECT count(*) FROM Log WHERE a = '1' AND message LIKE '%timeout%'",
        ),
        (
            'sum(count_over_time({a="1"} |= "say \\"hi\\"" [5m]))',
            "SELECT count(*) FROM Log WHERE a = '1' AND message LIKE '%say \"hi\"%'",
        ),
        (
            'sum(count_over_time({a="1"} |= "it\'s" [5m]))',
            "SELECT count(*) FROM Log WHERE a = '1' AND message LIKE '%it\\'s%'",
        ),
    ],
)
def test_simple_logql_converts(expr: str, nrql: str) -> None:
    m = one(make(expr, dtype="loki", for_=0))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.signal.kind is SignalKind.LOG
    assert nrql_for(m) == nrql
    assert m.signal.aggregation_window_seconds == 300


@pytest.mark.parametrize(
    ("expr", "fragment"),
    [
        ('sum(count_over_time({a="1"} | json | x="y" [5m]))', "parser stage or unwrap"),
        ('sum(count_over_time({a="1"} | logfmt [5m]))', "parser stage or unwrap"),
        ('sum(count_over_time({a="1"} | pattern "<x>" [5m]))', "parser stage or unwrap"),
        ('quantile_over_time(0.9, {a="1"} | unwrap d [5m])', "parser stage or unwrap"),
        ('sum(rate({a="1"} [5m]))', "logql uses a function other than count_over_time"),
        ('bytes_over_time({a="1"} [5m])', "logql uses a function other than count_over_time"),
        ('sum(count_over_time({a="1"} |~ "t.*out" [5m]))', "regular-expression line filter"),
        ('sum(count_over_time({a="1"} |= "100%" [5m]))', "wildcard character"),
        ('sum(count_over_time({a="1"} |= "a*" [5m]))', "wildcard character"),
        ('sum(count_over_time({a=""} [5m]))', "logql label matcher"),
        ('sum(count_over_time({a=~".+"} [5m]))', "logql label matcher"),
        ("sum(count_over_time({} [5m]))", "logql expression is not a count_over_time"),
        ('count_over_time({a="1"} [5m]) > 3', "logql expression is not a count_over_time"),
        ('{a="1"}', "logql expression is not a count_over_time"),
        ('sum(count_over_time({a="1"}[5x]))', "logql expression is not a count_over_time"),
        ('sum(count_over_time({a="1"}[0m]))', "logql expression is not a count_over_time"),
    ],
)
def test_other_logql_is_manual_with_a_specific_reason(expr: str, fragment: str) -> None:
    m = one(make(expr, dtype="loki"))
    assert m.confidence is Confidence.MANUAL
    assert fragment in text(m), m.reasons
    assert steps_for(m.reasons)


# thresholds and windows


@pytest.mark.parametrize(
    ("kind", "operator"),
    [
        ("gt", Operator.ABOVE),
        ("gte", Operator.ABOVE_OR_EQUALS),
        ("lt", Operator.BELOW),
        ("lte", Operator.BELOW_OR_EQUALS),
        ("eq", Operator.EQUALS),
        ("ne", Operator.NOT_EQUALS),
    ],
)
def test_comparison_operators(kind: str, operator: Operator) -> None:
    m = one(make("cpu", kind=kind, params=[2.5]))
    assert (m.thresholds[0].operator, m.thresholds[0].value) == (operator, 2.5)


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("within_range", [1, 2]),
        ("outside_range", [1, 2]),
        ("within_range_included", [1, 2]),
        ("outside_range_included", [1, 2]),
    ],
)
def test_range_thresholds_are_manual(kind: str, params: list[int]) -> None:
    m = one(make("cpu", kind=kind, params=params))
    assert m.confidence is Confidence.MANUAL and "threshold range" in text(m)


@pytest.mark.parametrize(
    ("kind", "params"),
    [
        ("gt", []),
        ("gt", [1, 2]),
        ("gt", ["x"]),
        ("gt", [True]),
        ("gt", [float("inf")]),
        ("gt", [float("nan")]),
        ("weird", [1]),
        ("", [1]),
    ],
)
def test_an_unusable_threshold_is_manual(kind: str, params: list[Any]) -> None:
    m = one(make("cpu", kind=kind, params=params))
    assert m.confidence is Confidence.MANUAL
    assert "grafana threshold" in text(m) and steps_for(m.reasons)


@pytest.mark.parametrize("reducer", ["count", "median", "weird", ""])
def test_a_reducer_without_a_new_relic_aggregation_is_manual(reducer: str) -> None:
    m = one(make("cpu", reducer=reducer or "median"))
    assert m.confidence is Confidence.MANUAL and "grafana reduce function" in text(m)


def test_a_threshold_straight_over_the_query_converts() -> None:
    m = one(make("avg(cpu)", reducer=None))
    assert m.confidence is Confidence.APPROXIMATE


@pytest.mark.parametrize(
    ("interval", "for_", "window", "duration"),
    [
        (60, 0, 60, 60),
        (60, 300, 60, 300),
        (120, 300, 120, 360),
        (90, 0, 120, 120),
        (None, 300, 60, 300),
        (45, 0, 60, 60),
        (3600, 3600, 3600, 3600),
    ],
)
def test_windows_are_whole_minutes_and_the_pending_period_whole_windows(
    interval: int | None, for_: int, window: int, duration: int
) -> None:
    m = one(make("cpu", interval=interval, for_=for_))
    assert (m.signal.aggregation_window_seconds, m.signal.window_seconds) == (window, duration)
    assert (m.signal.window_seconds or 0) % (m.signal.aggregation_window_seconds or 1) == 0


def test_an_unknown_interval_and_a_rounded_duration_are_noted() -> None:
    assert "evaluation interval" in text(one(make("cpu", interval=None)))
    assert "rounded" in text(one(make("cpu", interval=120, for_=300)))


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"interval": 3660}, "grafana aggregation window"),
        ({"for_": 86401}, "grafana pending period"),
    ],
)
def test_a_window_over_the_new_relic_limit_is_manual(kwargs: dict[str, Any], fragment: str) -> None:
    m = one(make("cpu", **kwargs))
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_a_loki_range_over_an_hour_is_manual() -> None:
    m = one(make('sum(count_over_time({a="1"}[2h]))', dtype="loki"))
    assert m.confidence is Confidence.MANUAL and "grafana aggregation window" in text(m)


def test_a_loki_range_shorter_than_a_minute_is_one_minute() -> None:
    m = one(make('sum(count_over_time({a="1"}[30s]))', dtype="loki", for_=0))
    assert m.signal.aggregation_window_seconds == 60


# states, labels and notes


@pytest.mark.parametrize(
    ("state", "notify", "fragment"),
    [
        ("Alerting", True, "loss-of-signal"),
        ("NoData", False, "separate no-data alert"),
        ("OK", False, ""),
        ("KeepLast", False, "keeps the last state"),
    ],
)
def test_no_data_states(state: str, notify: bool, fragment: str) -> None:
    m = one(make("cpu", no_data_state=state))
    assert m.no_data.notify is notify
    if notify:
        assert m.no_data.timeout_minutes == 5
    assert fragment in text(m)


@pytest.mark.parametrize(
    ("state", "present"),
    [("Error", True), ("Alerting", True), ("KeepLast", True), ("OK", False)],
)
def test_error_states(state: str, present: bool) -> None:
    assert ("query-error state" in text(one(make("cpu", exec_err_state=state)))) is present


def test_keep_firing_for_and_recovery_thresholds_are_noted() -> None:
    assert "keep firing" in text(one(make("cpu", keep_firing_for_seconds=300)))
    assert "keep firing" not in text(one(make("cpu")))
    rule = make("cpu")
    rule.data[-1].model["conditions"][0]["unloadEvaluator"] = {"type": "lt", "params": [1]}
    assert "recovery threshold" in text(one(rule))


def test_a_paused_rule_is_disabled_and_a_running_one_is_not() -> None:
    assert one(make("cpu", is_paused=True)).unmapped == {"enabled": False}
    assert "enabled" not in one(make("cpu")).unmapped


def test_notification_routing_is_always_a_manual_follow_up() -> None:
    assert "configured manually" in text(one(make("cpu")))


def test_the_description_becomes_the_message() -> None:
    m = one(make("cpu", annotations={"summary": "short", "description": "long text"}))
    assert m.notification.message_template == "long text"
    assert (
        one(make("cpu", annotations={"summary": "short"})).notification.message_template == "short"
    )


def test_an_unconfirmed_metric_name_is_said_to_be_unconfirmed() -> None:
    assert "not confirmed" in text(one(make("cpu")))


# pipeline shapes


def test_a_recording_rule_and_a_datasource_managed_rule_are_manual() -> None:
    rule = make("cpu", record={"metric": "m", "from": "A"})
    assert "recording rule" in text(one(rule))
    assert "data source-managed" in text(
        one(GrafanaRule(title="t", datasource_managed=True, data=[]))
    )


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda r: setattr(r, "condition", "Z"), "does not point at a node"),
        (lambda r: setattr(r, "condition", ""), "does not point at a node"),
        (lambda r: setattr(r, "condition", "B"), "not one query, an optional reduce"),
        (lambda r: r.data.pop(0), "not one query, an optional reduce"),
        (lambda r: r.data.pop(1), "not one query, an optional reduce"),
    ],
)
def test_a_broken_pipeline_is_manual(mutate: Any, fragment: str) -> None:
    rule = make("cpu")
    mutate(rule)
    m = one(rule)
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_a_cycle_in_the_pipeline_terminates() -> None:
    rule = make("cpu")
    rule.data[1].model["expression"] = "B"
    rule.data[2].model["expression"] = "B"
    assert one(rule).confidence is Confidence.MANUAL


def test_an_unused_node_is_manual() -> None:
    rule = make("cpu")
    rule.data.append(rule.data[0].model_copy(update={"refId": "Q"}))
    assert one(rule).confidence is Confidence.MANUAL


@pytest.mark.parametrize(
    ("expr_type", "fragment"),
    [
        ("math", "math expression"),
        ("classic_conditions", "classic condition"),
        ("sql", "sql expression"),
        ("resample", "resample expression"),
    ],
)
def test_expression_types_that_do_not_convert(expr_type: str, fragment: str) -> None:
    rule = make("cpu")
    rule.data.append(
        rule.data[1].model_copy(
            update={"refId": "D", "model": {"type": expr_type, "expression": "A"}}
        )
    )
    m = one(rule)
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_two_queries_of_one_data_source_are_manual() -> None:
    rule = make("cpu")
    rule.data.append(rule.data[0].model_copy(update={"refId": "Q"}))
    assert "more than one query" in text(one(rule))


@pytest.mark.parametrize(
    ("dtype", "label"),
    [
        ("cloudwatch", "cloudwatch"),
        ("elasticsearch", "elasticsearch"),
        ("grafana-azure-monitor-datasource", "azure monitor"),
        ("mysql", "sql"),
        ("postgres", "sql"),
        ("influxdb", "influxdb"),
        ("tempo", "tempo"),
        ("something-new", "unknown"),
    ],
)
def test_other_data_source_types_are_manual_and_the_type_text_is_not_echoed(
    dtype: str, label: str
) -> None:
    m = one(make("whatever", dtype=dtype))
    assert m.confidence is Confidence.MANUAL
    assert label in text(m) and "data source type" in text(m)
    assert "something-new" not in text(m)


def test_the_data_source_type_is_read_from_the_query_inferred_from_the_uid_or_the_shape() -> None:
    inferred = "inferred"
    assert inferred in text(one(make("cpu", dtype=None, uid="grafanacloud-prom")))
    assert inferred in text(one(make('sum(count_over_time({a="1"}[5m]))', dtype=None, uid="x")))
    assert inferred in text(one(make('sum(count_over_time({a="1"}[5m]))', dtype=None, uid="logs")))
    assert inferred not in text(one(make("cpu", dtype="prometheus")))
    assert nrql_for(one(make("cpu", dtype=None, uid="mimir-1"))).startswith("SELECT average")


def test_a_data_source_with_no_hint_and_a_loki_shape_is_loki() -> None:
    m = one(make('sum(count_over_time({a="1"}[5m]))', dtype=None, uid="d9"))
    assert m.signal.kind is SignalKind.LOG


# untrusted text


HOSTILE = "x'\"`;\n--"


def test_hostile_text_reaches_nrql_only_escaped_and_never_the_reasons() -> None:
    expr = 'cpu{a="it\'s\\\\ done"}'
    m = one(make(expr, title=HOSTILE, annotations={"description": HOSTILE}))
    assert nrql_for(m) == "SELECT average(cpu) FROM Metric WHERE a = 'it\\'s\\\\ done'"
    assert HOSTILE not in " ".join(m.reasons)


@pytest.mark.parametrize("bad", ['cpu{a="\\x00"}', 'cpu{a="line\nbreak"}', 'cpu{a="\u0007"}'])
def test_control_characters_in_a_value_are_manual(bad: str) -> None:
    m = one(make(bad))
    assert m.confidence is Confidence.MANUAL
    assert "\n" not in " ".join(m.reasons)


def test_reasons_never_contain_text_from_the_rule() -> None:
    marker = "ZZ-secret-ZZ"
    for expr in (
        f"{marker}_total",
        f'cpu{{a=~"{marker}.+"}}',
        f"rate({marker}[5m])",
        f"{marker}:x",
        f"{marker} + 1",
    ):
        m = one(make(expr, title=marker, annotations={"summary": marker}))
        assert marker not in " ".join(m.reasons), expr
    for dtype in (marker, marker.lower()):
        assert marker.lower() not in text(one(make("cpu", dtype=dtype)))


def test_translate_never_raises_on_odd_input() -> None:
    odd = [
        GrafanaRule(title="a", data=[]),
        GrafanaRule.model_validate({"title": "b", "condition": "C", "data": [{"refId": "C"}]}),
        GrafanaRule.model_validate(
            {
                "title": "c",
                "condition": "C",
                "data": [{"refId": "C", "model": {"type": "threshold"}}],
            }
        ),
        GrafanaRule.model_validate(
            {
                "title": "d",
                "condition": "C",
                "data": [
                    {
                        "refId": "C",
                        "datasourceUid": "__expr__",
                        "model": {"type": "threshold", "expression": "C", "conditions": "nope"},
                    }
                ],
            }
        ),
    ]
    assert [m.confidence for m in translate(odd)] == [Confidence.MANUAL] * 4


def test_identical_rules_get_distinct_ids() -> None:
    monitors = translate([make("cpu"), make("cpu")])
    assert len({m.source_id for m in monitors}) == 2
    assert len({m.source_key for m in monitors}) == 2
