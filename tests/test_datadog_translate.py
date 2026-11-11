"""D2: Datadog monitors to IR and NRQL, with hand-computed expected queries."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.ir import Confidence, IRMonitor, Operator, Severity
from importer.core.remediation import remedies_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.names import NameMap
from importer.sources.datadog.translate import translate, translate_monitor

FIX = Path(__file__).parent / "fixtures" / "datadog"


def mon(query: str, type_: str = "query alert", **fields: Any) -> IRMonitor:
    return translate_monitor(
        DatadogMonitor(type=type_, query=query, name="m", source_key="datadog/t", **fields)
    )


def nrql(query: str, type_: str = "query alert", **fields: Any) -> str:
    m = mon(query, type_, **fields)
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    return nrql_for(m)


def test_plain_threshold_nrql_window_terms_and_facet() -> None:
    m = mon(
        "avg(last_5m):avg:system.cpu.user{env:prod,!host:a*} by {host} > 90",
        options={"thresholds": {"critical": 90, "warning": 80}},
    )
    assert nrql_for(m) == (
        "SELECT average(system.cpu.user) FROM Metric WHERE env = 'prod' AND host NOT LIKE 'a%' "
        "FACET host"
    )
    assert m.signal.window_seconds == 300 and m.signal.aggregation_window_seconds == 300
    assert [(t.severity, t.operator, t.value) for t in m.thresholds] == [
        (Severity.CRITICAL, Operator.ABOVE, 90.0),
        (Severity.WARNING, Operator.ABOVE, 80.0),
    ]
    assert m.emit is not None and m.emit.slide_by_seconds == 60
    assert m.confidence is Confidence.APPROXIMATE
    assert m.data_path == "datadog-same-names"
    assert any("UNVERIFIED" in r for r in m.reasons)


def test_time_aggregator_picks_the_function() -> None:
    assert nrql("min(last_5m):max:m.x{*} <= 0") == "SELECT min(m.x) FROM Metric"
    assert nrql("max(last_15m):avg:m.x{*} > 1") == "SELECT max(m.x) FROM Metric"
    assert nrql("sum(last_5m):sum:falco.rules.matches{*}.as_count() > 0") == (
        "SELECT sum(falco.rules.matches) FROM Metric"
    )


def test_operators() -> None:
    for text, op in [
        (">=", Operator.ABOVE_OR_EQUALS),
        ("<", Operator.BELOW),
        ("<=", Operator.BELOW_OR_EQUALS),
        ("==", Operator.EQUALS),
        ("!=", Operator.NOT_EQUALS),
    ]:
        assert mon(f"avg(last_5m):avg:m{{*}} {text} 1").thresholds[0].operator is op


def test_ratio_of_two_series_with_a_number() -> None:
    q = "sum(last_5m):(sum:a.used{x:y} by {h} / sum:a.total{x:y} by {h}) * 100 > 90"
    assert nrql(q) == ("SELECT sum(a.used) / sum(a.total) * 100 FROM Metric WHERE x = 'y' FACET h")


def test_different_scopes_use_filter_per_term() -> None:
    q = "avg(last_5m):avg:m.h{measure:used} by {h} / avg:m.h{measure:max} by {h} > 1"
    assert nrql(q) == (
        "SELECT filter(average(m.h), WHERE measure = 'used') / "
        "filter(average(m.h), WHERE measure = 'max') FROM Metric FACET h"
    )


def test_scope_forms_in_and_exists() -> None:
    q = "avg(last_5m):avg:m{env IN (a,b) AND role:* AND (k:v OR k:w)} > 1"
    assert nrql(q) == (
        "SELECT average(m) FROM Metric WHERE env IN ('a', 'b') AND role IS NOT NULL "
        "AND k IN ('v', 'w')"
    )


def test_name_map_renames_and_scales() -> None:
    names = NameMap.model_validate(
        {
            "metrics": {"app.latency": {"attribute": "app.latency.ms", "unit_scale": 1000}},
            "attributes": {"env": "deployment.environment.name"},
        }
    )
    m = translate_monitor(
        DatadogMonitor(
            type="query alert",
            query="avg(last_5m):avg:app.latency{env:prod} > 0.5",
            name="n",
            source_key="datadog/n",
        ).bind(names)
    )
    assert nrql_for(m) == (
        "SELECT average(app.latency.ms) FROM Metric WHERE deployment.environment.name = 'prod'"
    )
    assert m.thresholds[0].value == 500.0
    assert m.data_path == "name-map"


def test_long_windows_and_limits() -> None:
    long = mon("avg(last_4h):avg:m{*} > 1")
    assert long.confidence is Confidence.APPROXIMATE
    assert long.signal.aggregation_window_seconds == 14400
    assert long.emit is not None and long.emit.slide_by_seconds is None
    assert nrql_for(long)  # six hours are accepted for datadog
    assert mon("avg(last_12h):avg:m{*} > 1").confidence is Confidence.MANUAL
    assert mon("avg(last_1d):avg:m{*} > 1").reasons[0].startswith("window is longer")
    one = mon("avg(last_1m):avg:m{*} > 1")
    assert one.emit is not None and one.emit.slide_by_seconds is None


def test_no_data_delay_and_unmapped_options() -> None:
    m = mon(
        "avg(last_5m):avg:m{*} > 1",
        options={
            "notify_no_data": True,
            "no_data_timeframe": 20,
            "evaluation_delay": 900,
            "require_full_window": True,
            "new_group_delay": 60,
            "renotify_interval": 30,
            "thresholds": {"critical": 1, "critical_recovery": 0},
        },
        priority=2,
    )
    assert m.no_data.notify and m.no_data.timeout_minutes == 20
    assert m.signal.aggregation_delay_seconds == 900
    assert {"require_full_window", "new_group_delay", "renotify_interval"} <= set(m.unmapped)
    assert "thresholds.critical_recovery" in m.unmapped
    assert m.unmapped["description_note"] == "Datadog priority P2"
    assert m.thresholds[0].severity is Severity.CRITICAL
    payload = nerdgraph_payload(m)["variables"]["condition"]
    assert payload["expiration"]["expirationDuration"] == 1200
    assert "Datadog priority P2" in payload["description"]


def test_default_no_data_timeframe_is_twice_the_window_and_is_clamped() -> None:
    m = mon("avg(last_5m):avg:m{*} > 1", options={"notify_no_data": True})
    assert m.no_data.timeout_minutes == 10
    big = mon(
        "avg(last_5m):avg:m{*} > 1", options={"notify_no_data": True, "no_data_timeframe": 99999}
    )
    assert big.no_data.timeout_minutes == 2880
    assert big.confidence is Confidence.APPROXIMATE


def test_low_priority_single_threshold_becomes_a_warning() -> None:
    m = mon("avg(last_5m):avg:m{*} > 1", priority=4)
    assert m.thresholds[0].severity is Severity.WARNING
    assert any("P4" in r for r in m.reasons)
    both = mon("avg(last_5m):avg:m{*} > 1", priority=4, options={"thresholds": {"warning": 0.5}})
    assert [t.severity for t in both.thresholds] == [Severity.CRITICAL, Severity.WARNING]


def test_log_count_nrql() -> None:
    q = 'logs("source:a @type:x.y -@id:*").index("*").rollup("count").by("@dom").last("10m") > 3'
    assert nrql(q, "log alert") == (
        "SELECT count(*) FROM Log WHERE source = 'a' AND type = 'x.y' AND id IS NULL FACET dom"
    )
    m = mon(q, "log alert")
    assert m.data_path == "needs-log-forwarding"
    assert m.signal.window_seconds == 600


def test_log_comparison_range_or_and_below() -> None:
    q = (
        'logs("@sev:>=40 @n:[1 TO 5] status:(error OR warn)")'
        '.index("*").rollup("count").last("5m") < 2'
    )
    m = mon(q, "log alert")
    assert nrql_for(m) == (
        "SELECT filter(count(*), WHERE sev >= 40 AND n >= 1 AND n <= 5 AND status IN "
        "('error', 'warn')) FROM Log"
    )
    assert any("wrapped in filter()" in r for r in m.reasons)


def test_log_free_text_and_index_notes() -> None:
    m = mon('logs("timeout").index("main").rollup("count").last("5m") > 0', "log alert")
    assert nrql_for(m) == "SELECT count(*) FROM Log WHERE message LIKE '%timeout%'"
    assert any("log index" in r for r in m.reasons)


@pytest.mark.parametrize(
    ("query", "type_", "start"),
    [
        ("avg(last_4h):anomalies(avg:m{*}, 'basic', 2) >= 1", "query alert", "anomaly monitor"),
        ("avg(last_30m):outliers(avg:m{*} by {h}, 'dbscan', 7) > 0", "query alert", "outlier"),
        ("max(next_1w):forecast(avg:m{*}, 'linear', 1) >= 5", "query alert", "forecast monitor"),
        ("pct_change(avg(last_5m),last_30m):avg:m{*} > -50", "query alert", "change alert"),
        ("max(current_1h):avg:m{*} > 3", "query alert", "cumulative window"),
        ("avg(last_5m):avg:m{*}.as_rate() > 3", "query alert", "query function: as_rate"),
        ("avg(last_5m):abs(avg:m{*}) > 3", "query alert", "query function: abs()"),
        ("avg(last_5m):avg:m{*}.fill(zero) > 3", "query alert", "query function: fill()"),
        ("avg(last_5m):avg:m{code:4* OR env:x} > 3", "query alert", "scope cannot"),
        ("avg(last_5m):avg:m{$env} > 3", "query alert", "scope uses a template"),
        ("avg(last_5m):avg:m{prod} > 3", "query alert", "scope cannot"),
        (
            "avg(last_5m):avg:a{*} by {x} / avg:b{*} by {y} > 3",
            "query alert",
            "queries in the formula group",
        ),
        ("avg(last_5m):avg:a{*} / avg:b{*} > threshold", "query alert", "query could not be read"),
        ("avg(last_5m):p95:m{*} > 3", "query alert", "query function: space aggregator"),
        ('logs("a").index("*").rollup("avg","@d").last("5m") > 1', "log alert", "log rollup"),
        ('logs("a OR b:c").index("*").rollup("count").last("5m") > 1', "log alert", "log search"),
        ('formula("q1 / q2").last("5m") > 1', "log alert", "multi-query formula monitor"),
        ('"c".over("*").by("h").last(3).count_by_status()', "service check", "service check"),
        ("1 && 2", "composite", "composite monitor"),
        ('events("x").rollup("count").last("5m") > 1', "event-v2 alert", "event monitor"),
        ('error_budget("x").over("7d") > 75', "slo alert", "slo alert"),
        ('rum("x").rollup("count").last("5m") > 1', "rum alert", "rum monitor"),
    ],
)
def test_everything_else_is_manual_with_a_specific_reason_and_remedy(
    query: str, type_: str, start: str
) -> None:
    m = mon(query, type_)
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith(start), m.reasons
    assert remedies_for(m.reasons), m.reasons
    assert all(classify(r) is not Category.OTHER for r in m.reasons)
    with pytest.raises(CannotEmit):
        nrql_for(m)


def test_milestones_are_named_where_later_work_is_planned() -> None:
    for query in (
        "avg(last_4h):anomalies(avg:m{*}, 'basic', 2) >= 1",
        "max(next_1w):forecast(avg:m{*}, 'linear', 1) >= 5",
        "avg(last_5m):avg:m{*}.as_rate() > 3",
    ):
        assert "D5" in mon(query).reasons[0]
    assert "D5" in mon("1 && 2", "composite").reasons[0]


def test_unknown_and_unreadable_are_unsupported() -> None:
    assert mon("x", "quantum-entanglement alert").confidence is Confidence.UNSUPPORTED
    assert translate_monitor(DatadogMonitor(source_key="k")).confidence is Confidence.UNSUPPORTED


def test_as_count_needs_the_right_metric_kind() -> None:
    names = NameMap.model_validate({"metrics": {"g": {"attribute": "g", "kind": "gauge"}}})
    m = translate_monitor(
        DatadogMonitor(
            type="query alert", query="sum(last_5m):sum:g{*}.as_count() > 1", source_key="k"
        ).bind(names)
    )
    assert m.confidence is Confidence.MANUAL and "as_count()" in m.reasons[0]


def test_hostile_synthetics_never_raise_and_every_reason_is_classified() -> None:
    monitors = REGISTRY["datadog"].convert(FIX / "synthetic")
    assert len(monitors) == 14
    for m in monitors:
        assert all(classify(r) is not Category.OTHER for r in m.reasons), m.reasons
        if m.confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED):
            assert remedies_for(m.reasons), (m.name, m.reasons)


def test_secrets_in_messages_and_handles_never_reach_any_output(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    out = tmp_path / "out"
    res = CliRunner().invoke(
        app, ["convert", "--source", "datadog", "--in", str(FIX / "synthetic"), "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    blob = ""
    for path in out.rglob("*"):
        if path.is_file():
            blob += path.read_text()
    assert "FAKE-NOT-A-SECRET" not in blob
    assert "hooks.example.invalid" not in blob
    assert "ops@example" not in blob
    assert json.loads(next((out / "ir").glob("*.json")).read_text())["source_system"] == "datadog"


def test_real_monitors_convert_and_every_converted_one_emits() -> None:
    monitors = REGISTRY["datadog"].convert(FIX / "real" / "monitors")
    assert len(monitors) == 70
    emitted = 0
    for m in monitors:
        if m.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
            nrql_for(m)
            nerdgraph_payload(m)
            emitted += 1
        else:
            assert remedies_for(m.reasons), m.reasons
    assert emitted >= 40


def test_translate_keeps_ids_distinct() -> None:
    a = DatadogMonitor(type="composite", query="1", source_key="same")
    out = translate([a, a])
    assert out[0].source_id != out[1].source_id
