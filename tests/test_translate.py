from typing import Any

import pytest

from importer.core.emit.nrql import build_nrql, identifier, quote_string
from importer.core.ir import BinOp, Confidence, IRMonitor, Operator, Severity
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.queries import (
    UnsupportedQuery,
    parse_count_query,
    parse_metric_query,
    parse_service_check,
)
from importer.sources.datadog.translate import load_metric_map, translate_monitor

METRIC_MAP = load_metric_map()


def convert(query: str, type_: str = "metric alert", **kw: Any) -> IRMonitor:
    monitor = DatadogMonitor(id=1, name="m", type=type_, query=query, **kw)
    return translate_monitor(monitor, METRIC_MAP)


def nrql(query: str, type_: str = "metric alert", **kw: Any) -> str:
    return build_nrql(convert(query, type_, **kw))


# --- metric parser ----------------------------------------------------------------


def test_parse_basic() -> None:
    p = parse_metric_query("avg(last_5m):avg:system.cpu.user{*} > 90")
    assert p.window_seconds == 300
    assert p.operator == Operator.ABOVE
    assert p.threshold == 90
    assert p.expression is None
    assert p.terms["a"].scope == [] and p.terms["a"].group_by == []


def test_parse_scope_and_group_by() -> None:
    p = parse_metric_query("max(last_1h):max:system.cpu.user{env:prod,role:web} by {host,az} >= 5")
    term = p.terms["a"]
    assert p.window_seconds == 3600
    assert [(f.key, f.value) for f in term.scope] == [("env", "prod"), ("role", "web")]
    assert term.group_by == ["host", "az"]


def test_parse_formula() -> None:
    p = parse_metric_query(
        "avg(last_10m):avg:system.disk.used{*} / avg:system.disk.total{*} * 100 > 90"
    )
    assert list(p.terms) == ["a", "b"]
    assert isinstance(p.expression, BinOp) and p.expression.op == "*"


def test_parse_flags_as_count() -> None:
    p = parse_metric_query("sum(last_5m):sum:trace.http.request.hits{*}.as_count() > 1")
    assert p.terms["a"].rate_hint


@pytest.mark.parametrize(
    "query",
    [
        "avg(last_4h):anomalies(avg:system.cpu.user{*}, 'basic', 2) >= 1",
        "max(next_1w):forecast(avg:system.disk.in_use{*}, 'linear', 1) >= 0.9",
        "avg(last_30m):outliers(avg:system.cpu.user{*} by {host}, 'dbscan', 2) > 0",
        "avg(last_5m):ewma_5(avg:system.cpu.user{*}) > 90",
        "avg(last_5m):avg:system.cpu.user{!env:prod} > 90",
        "avg(last_5m):avg:system.cpu.user{env:a'b} > 90",
        "avg(last_5m):(avg:system.cpu.user{*} > 90",
        "avg(last_5m):avg:system.cpu.user{*} / > 90",
        "garbage",
        "avg(last_5m):" + " + ".join(f"avg:m.x{i}{{*}}" for i in range(7)) + " > 1",
    ],
)
def test_parse_rejects_unsupported(query: str) -> None:
    with pytest.raises(UnsupportedQuery):
        parse_metric_query(query)


# --- count and service check parsers -------------------------------------------------


def test_parse_count_query() -> None:
    p = parse_count_query(
        'logs("status:error -service:web* AND host:a").index("*").rollup("count")'
        '.by("service,host").last("15m") >= 100'
    )
    assert p.window_seconds == 900
    assert [(f.key, f.negate, f.wildcard) for f in p.filters] == [
        ("status", False, False),
        ("service", True, True),
        ("host", False, False),
    ]
    assert p.group_by == ["service", "host"]


@pytest.mark.parametrize(
    "query",
    [
        'logs("status:error").index("*").rollup("avg", "@duration").last("5m") > 1',
        'logs("status:error OR status:warn").index("*").rollup("count").last("5m") > 1',
        'logs("status:err\'or").index("*").rollup("count").last("5m") > 1',
        'logs("free text search").index("*").rollup("count").last("5m") > 1',
    ],
)
def test_parse_count_rejects(query: str) -> None:
    with pytest.raises(UnsupportedQuery):
        parse_count_query(query)


def test_parse_service_check() -> None:
    p = parse_service_check('"datadog.agent.up".over("*").by("host").last(2).count_by_status()')
    assert (p.check, p.group_by, p.last_n) == ("datadog.agent.up", ["host"], 2)


def test_parse_service_check_rejects_scoped_over() -> None:
    with pytest.raises(UnsupportedQuery):
        parse_service_check('"datadog.agent.up".over("env:prod").last(2).count_by_status()')


# --- metric translation --------------------------------------------------------------


def test_simple_metric_is_exact_and_builds_nrql() -> None:
    ir = convert(
        "avg(last_5m):avg:system.cpu.user{*} > 90",
        options={"thresholds": {"critical": 90, "warning": 80}},
    )
    assert ir.confidence == Confidence.EXACT
    assert [(t.severity, t.value) for t in ir.thresholds] == [
        (Severity.CRITICAL, 90),
        (Severity.WARNING, 80),
    ]
    assert build_nrql(ir) == "SELECT average(cpuUserPercent) FROM SystemSample"


def test_grouping_by_host_is_facet() -> None:
    ir = convert("avg(last_5m):avg:system.cpu.user{*} by {host} > 85")
    assert ir.confidence == Confidence.EXACT
    assert build_nrql(ir).endswith("FACET hostname")


def test_scale_is_applied_to_thresholds_and_flagged_approximate() -> None:
    ir = convert("avg(last_10m):avg:system.mem.pct_usable{*} < 0.1")
    assert ir.thresholds[0].value == pytest.approx(10.0)
    assert ir.confidence == Confidence.APPROXIMATE
    assert any("cache" in r for r in ir.reasons)


def test_scope_filter_builds_where_and_is_approximate() -> None:
    ir = convert("avg(last_5m):avg:system.cpu.user{env:prod} > 90")
    assert build_nrql(ir).endswith("WHERE env = 'prod'")
    assert ir.confidence == Confidence.APPROXIMATE


def test_recovery_threshold_dropped_with_reason() -> None:
    ir = convert(
        "avg(last_10m):avg:system.cpu.system{*} > 80",
        options={"thresholds": {"critical": 80, "critical_recovery": 60}},
    )
    assert ir.confidence == Confidence.APPROXIMATE
    assert any("recovery" in r for r in ir.reasons)


def test_unmapped_metric_is_manual() -> None:
    ir = convert("avg(last_5m):avg:custom.sample.metric{*} > 1")
    assert ir.confidence == Confidence.MANUAL
    assert any("custom.sample.metric" in r for r in ir.reasons)


# --- formulas ---------------------------------------------------------------------


def test_ratio_formula_builds_nrql() -> None:
    q = "avg(last_10m):avg:system.disk.used{*} / avg:system.disk.total{*} * 100 > 90"
    ir = convert(q)
    assert ir.confidence == Confidence.EXACT
    assert build_nrql(ir) == (
        "SELECT average(diskUsedBytes) / average(diskTotalBytes) * 100 FROM StorageSample"
    )
    assert ir.thresholds[0].value == 90


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            "(avg:system.mem.used{*} + avg:system.mem.total{*}) * 2",
            "(average(memoryUsedBytes) + average(memoryTotalBytes)) * 2",
        ),
        (
            "avg:system.mem.total{*} - (avg:system.mem.used{*} - 5)",
            "average(memoryTotalBytes) - (average(memoryUsedBytes) - 5)",
        ),
        (
            "avg:system.mem.total{*} / (avg:system.mem.used{*} * 2)",
            "average(memoryTotalBytes) / (average(memoryUsedBytes) * 2)",
        ),
        (
            "avg:system.mem.total{*} - avg:system.mem.used{*} - 5",
            "average(memoryTotalBytes) - average(memoryUsedBytes) - 5",
        ),
    ],
)
def test_formula_precedence_and_parentheses(body: str, expected: str) -> None:
    sql = nrql(f"avg(last_5m):{body} > 1")
    assert sql == f"SELECT {expected} FROM SystemSample"  # noqa: S608


def test_apm_error_ratio_uses_trusted_select_expressions() -> None:
    q = (
        "sum(last_5m):sum:trace.http.request.errors{env:prod} by {service}.as_count() / "
        "sum:trace.http.request.hits{env:prod} by {service}.as_count() > 0.05"
    )
    ir = convert(q, "query alert")
    assert build_nrql(ir) == (
        "SELECT filter(count(*), WHERE error IS true) / count(*) "
        "FROM Transaction WHERE env = 'prod' FACET appName"
    )
    assert ir.confidence == Confidence.APPROXIMATE
    assert any("as_count" in r for r in ir.reasons)


def test_formula_with_different_events_is_manual() -> None:
    ir = convert("avg(last_5m):avg:system.cpu.user{*} / avg:system.disk.total{*} > 1")
    assert ir.confidence == Confidence.MANUAL
    assert any("different New Relic events" in r for r in ir.reasons)


def test_formula_with_different_scopes_is_manual() -> None:
    ir = convert("avg(last_5m):avg:system.mem.used{*} / avg:system.mem.total{env:prod} > 1")
    assert ir.confidence == Confidence.MANUAL
    assert any("scopes" in r for r in ir.reasons)


def test_formula_with_unit_scaling_is_manual() -> None:
    ir = convert("avg(last_5m):avg:system.mem.pct_usable{*} * avg:system.mem.total{*} > 1")
    assert ir.confidence == Confidence.MANUAL
    assert any("unit conversion" in r for r in ir.reasons)


def test_anomaly_is_manual_with_hint() -> None:
    ir = convert(
        "avg(last_4h):anomalies(avg:system.cpu.user{*}, 'basic', 2) >= 1", type_="query alert"
    )
    assert ir.confidence == Confidence.MANUAL
    assert any("baseline" in r for r in ir.reasons)


# --- logs, APM and service checks ---------------------------------------------------


def test_log_monitor() -> None:
    ir = convert(
        'logs("status:error service:web").index("*").rollup("count").last("5m") > 50',
        "log alert",
    )
    assert build_nrql(ir) == "SELECT count(*) FROM Log WHERE level = 'error' AND service = 'web'"
    assert ir.signal.window_seconds == 300
    assert ir.confidence == Confidence.APPROXIMATE
    assert any("unverified" in r for r in ir.reasons)


def test_log_monitor_negation_wildcard_and_group() -> None:
    sql = nrql(
        'logs("-service:web* host:a").index("main").rollup("count").by("service").last("15m") > 1',
        "log alert",
    )
    assert sql == (
        "SELECT count(*) FROM Log WHERE service NOT LIKE 'web%' AND hostname = 'a' FACET service"
    )
    ir = convert('logs("service:web*").index("main").rollup("count").last("5m") > 1', "log alert")
    assert any("wildcards" in r for r in ir.reasons)
    assert any("index filter" in r for r in ir.reasons)


def test_unsupported_log_rollup_is_manual() -> None:
    ir = convert(
        'logs("status:error").index("*").rollup("avg", "@duration").last("5m") > 1', "log alert"
    )
    assert ir.confidence == Confidence.MANUAL


def test_apm_monitor() -> None:
    ir = convert(
        'trace-analytics("service:web status:error").index("trace-search")'
        '.rollup("count").last("5m") > 10',
        "trace-analytics alert",
    )
    assert build_nrql(ir) == (
        "SELECT count(*) FROM Transaction WHERE appName = 'web' AND error IS true"
    )
    assert ir.confidence == Confidence.APPROXIMATE


def test_service_check_becomes_heartbeat() -> None:
    ir = convert(
        '"datadog.agent.up".over("*").by("host").last(2).count_by_status()',
        "service check",
        options={"thresholds": {"critical": 1, "ok": 1}},
    )
    assert build_nrql(ir) == "SELECT count(*) FROM SystemSample FACET hostname"
    assert ir.signal.window_seconds == 120
    assert [(t.operator, t.value) for t in ir.thresholds] == [(Operator.BELOW, 1)]
    assert ir.no_data.notify and ir.no_data.timeout_minutes == 2
    assert ir.confidence == Confidence.APPROXIMATE


def test_unknown_service_check_is_manual() -> None:
    ir = convert('"http.can_connect".over("*").last(2).count_by_status()', "service check")
    assert ir.confidence == Confidence.MANUAL


def test_composite_reason_lists_source_monitors() -> None:
    ir = convert("123 && !456", "composite")
    assert ir.confidence == Confidence.UNSUPPORTED
    assert any("123" in r and "456" in r for r in ir.reasons)


def test_unknown_type_is_unsupported() -> None:
    ir = convert("whatever", "something new")
    assert ir.confidence == Confidence.UNSUPPORTED
    assert ir.reasons


# --- notifications and preservation -------------------------------------------------


def test_handles_and_templates_are_flagged() -> None:
    ir = convert(
        "avg(last_5m):avg:system.cpu.user{*} by {host} > 90",
        message="CPU on {{host.name}} @pagerduty-sample",
    )
    assert ir.notification.handles == ["@pagerduty-sample"]
    assert ir.confidence == Confidence.APPROXIMATE
    assert len(ir.reasons) == 2


def test_unknown_options_and_fields_are_preserved() -> None:
    m = DatadogMonitor.model_validate(
        {
            "id": 1,
            "name": "m",
            "type": "metric alert",
            "query": "avg(last_5m):avg:system.cpu.user{*} > 90",
            "options": {"renotify_interval": 60},
            "brand_new": 1,
        }
    )
    ir = translate_monitor(m, METRIC_MAP)
    assert ir.unmapped == {"renotify_interval": 60, "brand_new": 1}


def test_translate_never_raises_on_hostile_input() -> None:
    for query in ["", "'; DROP TABLE x; --", "avg(last_5m):" + "(" * 500, "\x00" * 10]:
        for type_ in ("metric alert", "log alert", "service check", "composite", "???"):
            assert convert(query, type_).confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED)


# --- NRQL safety ------------------------------------------------------------------


def test_quote_string_escapes() -> None:
    assert quote_string("a'b") == "'a\\'b'"
    assert quote_string("a\\b") == "'a\\\\b'"


def test_quote_string_rejects_newlines() -> None:
    with pytest.raises(ValueError, match="control characters"):
        quote_string("a\nb")


@pytest.mark.parametrize("bad", ["a b", "a;b", "a) FROM X", "", "1abc", "a-b"])
def test_identifier_rejects_bad_names(bad: str) -> None:
    with pytest.raises(ValueError, match="invalid NRQL identifier"):
        identifier(bad)
