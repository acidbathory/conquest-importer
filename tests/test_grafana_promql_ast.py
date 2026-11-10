"""The PromQL parser: every construct of the census becomes a node a rule can match on."""

import pytest

from importer.sources.grafana.promql_ast import (
    Aggregate,
    Binary,
    Call,
    Number,
    Paren,
    Placeholder,
    PromQLSyntaxError,
    Selector,
    StringLit,
    Subquery,
    Unary,
    format_expr,
    parse,
    walk,
)


def test_selector_with_the_four_matcher_operators() -> None:
    node = parse('up{job="a", env!="b", pod=~"c.*", ns!~"d"}')
    assert isinstance(node, Selector)
    assert node.name == "up"
    assert [(m.label, m.op, m.value) for m in node.matchers] == [
        ("job", "=", "a"),
        ("env", "!=", "b"),
        ("pod", "=~", "c.*"),
        ("ns", "!~", "d"),
    ]
    assert node.range is None


def test_range_vector_and_function_call() -> None:
    node = parse('rate(http_requests_total{code="200"}[5m])')
    assert isinstance(node, Call)
    assert node.func == "rate"
    (arg,) = node.args
    assert isinstance(arg, Selector)
    assert arg.range == "5m"


def test_aggregation_grouping_before_or_after_the_argument() -> None:
    after = parse("sum(rate(x[1m])) by (job, instance)")
    before = parse("sum by (job, instance) (rate(x[1m]))")
    assert isinstance(after, Aggregate)
    assert isinstance(before, Aggregate)
    assert (after.grouping, after.labels) == ("by", ("job", "instance"))
    assert after == before
    without = parse("max without (pod) (up)")
    assert isinstance(without, Aggregate)
    assert without.grouping == "without"


def test_aggregation_parameter() -> None:
    top = parse("topk(5, sum by (pod) (up))")
    assert isinstance(top, Aggregate)
    assert top.op == "topk"
    assert top.param == Number("5")
    values = parse('count_values("version", build_info)')
    assert isinstance(values, Aggregate)
    assert values.param == StringLit("version")
    quant = parse("quantile(0.9, up)")
    assert isinstance(quant, Aggregate)
    assert quant.param == Number("0.9")


def test_precedence_and_associativity() -> None:
    assert format_expr(parse("a + b * c")) == "a + (b * c)"
    assert format_expr(parse("a - b - c")) == "(a - b) - c"
    assert format_expr(parse("2 ^ 3 ^ 2")) == "2 ^ (3 ^ 2)"
    assert format_expr(parse("a > b + c")) == "a > (b + c)"
    assert format_expr(parse("a or b and c")) == "a or (b and c)"
    assert format_expr(parse("a and b unless c")) == "(a and b) unless c"
    neg = parse("-a ^ 2")
    assert isinstance(neg, Unary)
    assert isinstance(neg.expr, Binary)


def test_binary_modifiers() -> None:
    node = parse("a / on (job) group_left (team) b")
    assert isinstance(node, Binary)
    assert node.matching is not None
    assert (node.matching.kind, node.matching.labels) == ("on", ("job",))
    assert (node.matching.group, node.matching.group_labels) == ("group_left", ("team",))
    ign = parse("a * ignoring(x) group_right b")
    assert isinstance(ign, Binary)
    assert ign.matching is not None
    assert ign.matching.kind == "ignoring"
    assert ign.matching.group == "group_right"
    assert ign.matching.group_labels == ()


def test_comparison_with_bool_and_set_operators() -> None:
    cmp = parse("a > bool 0")
    assert isinstance(cmp, Binary)
    assert cmp.op == ">"
    assert cmp.bool_modifier
    plain = parse("a > 0")
    assert isinstance(plain, Binary)
    assert not plain.bool_modifier
    for op in ("and", "or", "unless"):
        node = parse(f"a {op} on (x) b")
        assert isinstance(node, Binary)
        assert node.op == op


def test_offset_at_and_subquery() -> None:
    node = parse("rate(x[5m] offset 1h @ 1609746000)")
    assert isinstance(node, Call)
    (arg,) = node.args
    assert isinstance(arg, Selector)
    assert (arg.offset, arg.at) == ("1h", "1609746000")
    assert parse("x @ end()").at == "end()"  # type: ignore[attr-defined]
    sub = parse("max_over_time(rate(x[1m])[1h:5m])")
    assert isinstance(sub, Call)
    (inner,) = sub.args
    assert isinstance(inner, Subquery)
    assert (inner.range, inner.step) == ("1h", "5m")
    default_step = parse("avg_over_time(f(x)[30m:])")
    assert isinstance(default_step, Call)
    assert isinstance(default_step.args[0], Subquery)
    assert default_step.args[0].step == ""


def test_grafana_variables_are_placeholders() -> None:
    node = parse('rate(x{job=~"$job", ns="${ns:regex}"}[$__rate_interval])')
    assert isinstance(node, Call)
    (arg,) = node.args
    assert isinstance(arg, Selector)
    assert arg.range == "$__rate_interval"
    assert arg.variables == ("job", "ns", "__rate_interval")
    assert isinstance(parse("$metric"), Placeholder)
    named = parse("node_$suffix{a=\"b\"}")
    assert isinstance(named, Selector)
    assert named.name == "node_$suffix"
    top = parse("topk($n, up)")
    assert isinstance(top, Aggregate)
    assert top.param == Placeholder("n")
    assert isinstance(parse("x[${__interval}]"), Selector)
    assert isinstance(parse("x[[[window]]]"), Selector)


def test_recording_rule_names_and_parentheses() -> None:
    node = parse("(instance:node_cpu:rate5m{job='n'})")
    assert isinstance(node, Paren)
    assert isinstance(node.expr, Selector)
    assert node.expr.name == "instance:node_cpu:rate5m"


def test_literals_and_functions_without_arguments() -> None:
    assert parse("1.5e3") == Number("1.5e3")
    assert parse("0x1F") == Number("0x1F")
    assert parse("Inf") == Number("Inf")
    assert isinstance(parse("time()"), Call)
    assert format_expr(parse("round(x, 0.001)")) == "round(x, 0.001)"
    label = parse('label_replace(up, "dst", "$1", "src", "(.*)")')
    assert isinstance(label, Call)
    assert len(label.args) == 5


def test_walk_visits_every_node() -> None:
    node = parse("sum by (a) (rate(x[1m])) / on (a) y")
    kinds = [type(n).__name__ for n in walk(node)]
    assert kinds == ["Binary", "Aggregate", "Call", "Selector", "Selector"]


@pytest.mark.parametrize("text", ["", "sum(", "a +", "x{job=}", "x[5m", "a b", "foo(1,)x", "x offset"])
def test_syntax_errors_raise(text: str) -> None:
    with pytest.raises(PromQLSyntaxError):
        parse(text)


def test_format_round_trips() -> None:
    text = 'sum by (a) (rate(x{b=~"c"}[5m] offset 1m)) / on (a) group_left () y > bool 3'
    assert parse(format_expr(parse(text))) == parse(text)
