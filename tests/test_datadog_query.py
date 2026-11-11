"""D1: the metric query parser, the event chains, the composite expression and their caps."""

import json
from pathlib import Path

import pytest

from importer.sources.datadog.query import (
    Bin,
    BoolOp,
    Brace,
    Call,
    InSet,
    Kw,
    MonitorRef,
    Not,
    Num,
    Series,
    Star,
    Tag,
    Word,
    event_window_seconds,
    parse_chain,
    parse_composite,
    parse_metric_query,
    parse_scope,
)

REAL = Path(__file__).parent / "fixtures" / "datadog" / "real" / "monitors"


def _series(q: str) -> Series:
    parsed = parse_metric_query(q)
    assert parsed.ok and isinstance(parsed.expr, Series), parsed.diagnostics
    return parsed.expr


def test_plain_threshold() -> None:
    q = parse_metric_query("avg(last_5m):avg:system.cpu.user{env:prod,!host:a*} by {host,az} >= 90")
    assert q.ok and q.lossless and q.diagnostics == ()
    assert (q.time_aggr, q.window.seconds if q.window else 0) == ("avg", 300)
    assert (q.op, q.threshold) == (">=", 90.0)
    s = q.expr
    assert isinstance(s, Series)
    assert (s.aggr, s.metric, s.group_by) == ("avg", "system.cpu.user", ("host", "az"))
    assert s.scope == BoolOp("and", (Tag("env", "prod"), Tag("host", "a*", negate=True)))


def test_windows_and_units() -> None:
    assert parse_metric_query("max(current_1h):avg:m{*} > 1").window.kind == "current"  # type: ignore[union-attr]
    assert parse_metric_query("avg(last_1d):avg:m{*} > 1").window.seconds == 86400  # type: ignore[union-attr]
    assert parse_metric_query("max(next_12h):avg:m{*} > 1").window.seconds == 43200  # type: ignore[union-attr]


def test_metric_without_aggregator_defaults_to_avg() -> None:
    s = _series("avg(last_5m):rabbitmq.queue.consumers{*} by {host} < 1")
    assert (s.aggr, s.explicit_aggr) == ("avg", False)


def test_function_chain_and_modifiers() -> None:
    s = _series("sum(last_5m):sum:m.count{*} by {k}.as_count().rollup(sum, 60).fill(zero, 600) > 0")
    assert [c.name for c in s.functions] == ["as_count", "rollup", "fill"]
    assert s.functions[1].args == (Word("sum"), Num(60.0))


def test_nested_calls_and_brace_argument() -> None:
    q = parse_metric_query("max(last_5m):sum(max:s.x{a:b} by {r}.rollup(max, 60), { g }) > 1")
    assert q.ok and q.lossless
    call = q.expr
    assert isinstance(call, Call) and call.name == "sum"
    assert isinstance(call.args[0], Series) and call.args[1] == Brace(("g",))


def test_formula_of_series_with_arithmetic() -> None:
    q = parse_metric_query("avg(last_5m):(avg:a{*} by {h} / avg:b{*} by {h}) * 100 > 90")
    assert q.ok and q.lossless
    assert isinstance(q.expr, Bin) and q.expr.op == "*"
    assert q.metrics == ["a", "b"]
    q2 = parse_metric_query("avg(last_5m):100 - (avg:a{*} * 100 / avg:b{*}) >= 90")
    assert isinstance(q2.expr, Bin) and q2.expr.op == "-" and q2.expr.left == Num(100.0)


def test_negative_numbers() -> None:
    q = parse_metric_query("avg(last_5m):cutoff_min(avg:m{*}, -0.1) < -5")
    assert q.ok and q.threshold == -5.0
    assert isinstance(q.expr, Call) and q.expr.args[1] == Num(-0.1)


def test_change_alert_head() -> None:
    q = parse_metric_query("pct_change(avg(last_5m),last_30m):avg:m{*} > -50")
    assert q.ok and q.change is not None
    assert (q.change.kind, q.change.inner_aggr, q.change.shift_seconds) == (
        "pct_change",
        "avg",
        1800,
    )
    q2 = parse_metric_query("change(sum(last_5m),last_10m):sum:m{*}.as_count() >= 1")
    assert q2.change is not None and q2.change.shift_seconds == 600
    q3 = parse_metric_query("change(avg(last_5m),1h_ago):avg:m{*} >= 1")
    assert q3.change is not None and q3.change.shift_seconds == 3600


def test_anomalies_keyword_arguments() -> None:
    q = parse_metric_query(
        "avg(last_4h):anomalies(avg:m{*} by {a}.as_count(), 'agile', 2, direction='both', "
        "alert_window='last_15m', interval=60, seasonality='hourly') >= 1"
    )
    assert q.ok and q.lossless
    call = q.expr
    assert isinstance(call, Call) and call.name == "anomalies"
    assert call.args[1] == Word("agile")
    assert Kw("direction", Word("both")) in call.args
    assert Kw("interval", Num(60.0)) in call.args


def test_forecast_and_next_window() -> None:
    q = parse_metric_query(
        "max(next_12h):forecast(avg:m{*} by {a,b}, 'linear', 1, model='reactive', "
        "interval='30m') >= 500"
    )
    assert q.ok and q.window is not None and q.window.kind == "next"


def test_scope_forms() -> None:
    assert parse_scope("*") == Star()
    assert parse_scope("a:b AND (c:d OR c:e)") == BoolOp(
        "and", (Tag("a", "b"), BoolOp("or", (Tag("c", "d"), Tag("c", "e"))))
    )
    assert parse_scope("env IN (prod, stage)") == InSet("env", ("prod", "stage"))
    assert parse_scope("env NOT IN (a)") == InSet("env", ("a",), negate=True)
    assert parse_scope("NOT a:b") == Tag("a", "b", negate=True)
    assert parse_scope("code:4* OR code:5*") == BoolOp("or", (Tag("code", "4*"), Tag("code", "5*")))
    assert parse_scope("path:/mnt/data, measure:percent ,func:*ctr*") == BoolOp(
        "and", (Tag("path", "/mnt/data"), Tag("measure", "percent"), Tag("func", "*ctr*"))
    )
    assert parse_scope("production") == Tag(None, "production")
    assert parse_scope("!a:b") == Tag("a", "b", negate=True)
    assert parse_scope("NOT (a:b OR c:d)") == Not(BoolOp("or", (Tag("a", "b"), Tag("c", "d"))))


def test_scope_garbage_does_not_raise() -> None:
    for text in ("((((", "))))", "a:b AND", "IN (", ", , ,", "OR OR", "NOT"):
        parse_scope(text)


def test_template_variable_in_scope_is_a_tag_value() -> None:
    assert parse_scope("$env") == Tag(None, "$env")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("avg(last_5m):avg:m{*}", "no_comparator"),
        ("avg(last_1h):avg:a{*} / avg:b{*} > threshold", "threshold_not_number"),
        ("whatever > 1", "bad_head"),
        ("entangle(last_5m):foo{*} > 1", "bad_head"),
        ("avg(last_5m):avg:m{*} by {a > 1", "syntax"),
        ("avg(last_5m):avg:m{*} avg:n{*} > 1", "trailing_text"),
        ("avg(last_5m):abs( > 1", "syntax"),
        ("avg(last_5m):'unclosed > 1", "syntax"),
        ("", "no_comparator"),
    ],
)
def test_bad_queries_give_a_diagnostic_not_an_exception(text: str, code: str) -> None:
    q = parse_metric_query(text)
    assert not q.ok and not q.lossless
    assert q.diagnostics[0].code == code


def test_lossy_scope_clears_the_lossless_flag() -> None:
    q = parse_metric_query("avg(last_5m):avg:m{a:b AND (c:d} > 1")
    assert q.ok and not q.lossless
    q2 = parse_metric_query("avg(last_5m):avg:m > 1")
    assert q2.ok and not q2.lossless and q2.diagnostics[0].code == "no_scope"


def test_depth_and_length_caps() -> None:
    deep = "avg(last_5m):" + "abs(" * 1500 + "avg:m{*}" + ")" * 1500 + " > 1"
    q = parse_metric_query(deep)
    assert not q.ok and q.diagnostics[0].code == "too_deep"
    long_scope = "avg(last_5m):avg:m{" + ",".join(f"host:h{i}" for i in range(5000)) + "} > 1"
    assert parse_metric_query(long_scope).diagnostics[0].code == "too_long"
    parens = "avg(last_5m):avg:m{" + "(" * 100 + "a:b" + ")" * 100 + "} > 1"
    assert parse_metric_query(parens).diagnostics[0].code == "too_deep"


def test_every_real_metric_query_parses_losslessly() -> None:
    count = 0
    for path in sorted(REAL.glob("*.json")):
        data = json.loads(path.read_text())
        monitor = data.get("definition", data)
        if monitor["type"] == "query alert":
            q = parse_metric_query(monitor["query"])
            assert q.ok and q.lossless, (path.name, q.diagnostics)
            count += 1
    assert count == 58


def test_log_chain() -> None:
    q = parse_chain('logs("source:a @b:c").index("*").rollup("count").by("@x,@y").last("5m") > 3')
    assert q.ok and q.head == "logs" and q.head_args == ("source:a @b:c",)
    assert [c.name for c in q.calls] == ["index", "rollup", "by", "last"]
    assert q.call("by") is not None and q.call("by").args == ("@x,@y",)  # type: ignore[union-attr]
    assert (q.op, q.threshold) == (">", 3.0)


def test_log_chain_with_escaped_quotes() -> None:
    q = parse_chain(
        'logs("service:web @msg:\\"a\' OR 1=1 --\\" \\\\ ")'
        '.index("*").rollup("count").last("5m") > 0'
    )
    assert q.ok
    assert "OR 1=1" in str(q.head_args[0])


def test_events_formula_and_rollup_measure() -> None:
    assert parse_chain('events("source:x").rollup("count").by("host").last("10m") > 0').head == (
        "events"
    )
    assert parse_chain('formula("(query / query1) * 100").last("1d") > 10').head == "formula"
    q = parse_chain('logs("x").index("*").rollup("avg","@d").last("1h") > 1')
    assert q.call("rollup").args == ("avg", "@d")  # type: ignore[union-attr]


def test_service_check_chain_has_no_comparator() -> None:
    q = parse_chain(
        '"teamcity.build.status".over("*").by("build_config").last(3).count_by_status()',
        comparator=False,
    )
    assert q.ok and q.head == "" and q.head_args == ("teamcity.build.status",)
    assert q.call("last").args == (3.0,)  # type: ignore[union-attr]


def test_slo_chain() -> None:
    q = parse_chain('error_budget("abc").over("7d") > 75')
    assert q.ok and q.head == "error_budget" and q.call("over").args == ("7d",)  # type: ignore[union-attr]


def test_bad_chains() -> None:
    assert not parse_chain("logs(").ok
    assert not parse_chain('logs("x") > ').ok
    assert not parse_chain('logs("x").last > 1').ok
    assert not parse_chain('logs("x").a("b").b("c") junk > 1').ok
    assert not parse_chain("x" * 20000).ok


def test_event_window_seconds() -> None:
    assert event_window_seconds("5m") == 300
    assert event_window_seconds("2d") == 172800
    assert event_window_seconds("5x") is None
    assert event_window_seconds(5.0) is None


def test_composite_expression() -> None:
    c = parse_composite("!1234 || (5678 && 91011) && !(12 && 13)")
    assert c.ok and c.lossless
    assert c.monitor_ids == [1234, 5678, 91011, 12, 13]
    assert parse_composite("1 && 2").expr is not None
    assert parse_composite("7").expr == MonitorRef(7)


def test_composite_bad_input() -> None:
    for text in ("", "1 &&", "(1", "a && b", "1 2", "(" * 5000):
        assert not parse_composite(text).ok


def test_hostile_synthetic_queries_end_as_diagnostics() -> None:
    synth = REAL.parent.parent / "synthetic"
    for name in ("deep_nesting", "huge_query"):
        data = json.loads((synth / f"SYNTHETIC_monitor_{name}.json").read_text())
        q = parse_metric_query(data["query"])
        assert not q.ok and q.diagnostics[0].fatal


def test_seeded_fuzz_never_raises() -> None:
    import random

    from importer.sources.datadog.logsearch import parse_log_search

    rng = random.Random(1)  # noqa: S311 - a seeded test, not security
    alpha = [*"abc:@*?\"'()[]{}<>=!-,. \\", "AND", "OR", "NOT", "IN", "TO", "avg", "by", "&&"]
    for _ in range(3000):
        s = "".join(rng.choice(alpha) for _ in range(rng.randint(0, 40)))
        parse_scope(s)
        parse_log_search(s)
        parse_composite(s)
        parse_chain(s)
        parse_chain(s, comparator=False)
        parse_metric_query("avg(last_5m):" + s + " > 1")
        parse_metric_query(s)
