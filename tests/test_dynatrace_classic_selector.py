"""The metric selector grammar: a strict parser, ReDoS-safe, and the meaning of each function."""

import time

import pytest

from importer.sources.dynatrace.classic import selector as sel
from importer.sources.dynatrace.classic.selector import Binary, Number, SelectorError, Series
from importer.sources.dynatrace.classic.selector_query import (
    DimFilter,
    ENum,
    EOp,
    ERef,
    to_query,
)

KEY = "builtin:host.cpu.usage"


def names(series: Series) -> list[str]:
    return [t.name for t in series.transforms]


def test_a_plain_key_and_a_chain_parse_into_an_ast() -> None:
    parsed = sel.parse_selector(f'{KEY}:filter(eq("host","a~"b~~")):splitBy("host","disk"):avg')
    assert isinstance(parsed, Series) and parsed.key == KEY
    assert names(parsed) == ["filter", "splitBy", "avg"]
    split = parsed.transforms[1]
    assert [a.value for a in split.args] == ["host", "disk"]  # type: ignore[union-attr]
    inner = parsed.transforms[0].args[0]
    assert isinstance(inner, sel.Call) and inner.args[1].value == 'a"b~'  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "key",
    ["builtin:host.cpu.usage", "ext:cloud.aws.ec2.cpu", "calc:service.mine", "my.custom-metric_1"],
)
def test_metric_key_forms(key: str) -> None:
    parsed = sel.parse_selector(f"{key}:avg")
    assert isinstance(parsed, Series) and parsed.key == key


def test_arithmetic_has_the_usual_precedence_and_parentheses() -> None:
    parsed = sel.parse_selector(f"({KEY}:avg) / (a.b:sum) * 100 + 1")
    assert isinstance(parsed, Binary) and parsed.op == "+"
    left = parsed.left
    assert isinstance(left, Binary) and left.op == "*"
    assert isinstance(left.left, Binary) and left.left.op == "/"
    assert isinstance(parsed.right, Number) and parsed.right.value == 1


def test_transformations_can_follow_a_parenthesised_series() -> None:
    parsed = sel.parse_selector(f"({KEY}:value):default(0)")
    assert isinstance(parsed, Series) and names(parsed) == ["value", "default"]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        f"{KEY}:",
        f"{KEY}:avg(",
        f'{KEY}:splitBy("a)',
        f'{KEY}:splitBy("a~x")',  # an unknown escape
        f'{KEY}:filter(eq("a","b")',
        f"{KEY}:avg,{KEY}:max",  # several metrics in one selector
        f"{KEY}:avg )",
        f"{KEY}:avg:",
        "(" + KEY,
        f"{KEY} {KEY}",
        f"{KEY};drop",
        f"{KEY}:avg\x00",
        f'{KEY}:splitBy("a\nb")',
        "1e999",
        "..:avg:" + "x" * 200,
        "$(touch x)",
        f"{KEY}:avg -",
        "unknown:key:avg",  # a colon after an unknown namespace is read as a transformation
    ],
)
def test_malformed_input_is_a_selector_error_with_no_input_echoed(text: str) -> None:
    with pytest.raises(SelectorError) as err:
        to_query(text)
    assert text.strip() == "" or text.strip() not in str(err.value)


def test_a_selector_with_several_metrics_says_so() -> None:
    with pytest.raises(SelectorError, match="several metrics"):
        to_query(f"{KEY}:avg,{KEY}:max")


ADVERSARIAL = [
    "(" * 100_000,
    ")" * 100_000,
    "a" * 100_000,
    ":x" * 50_000,
    '"' + "~" * 100_000,
    "a:avg" + ':splitBy("' * 20_000,
    "1" * 100_000,
    "-" * 100_000,
    "(" * 3_000 + "a" + ")" * 3_000,
    f"{KEY}:filter(" + "and(" * 3_000 + 'eq("a","b")' + ")" * 3_000 + ")",
    f"{KEY}" + ":avg" * 1_000,
    "a+" * 50_000,
    "a:filter(" + 'eq("a","' + "\\" * 90_000,
    f"{KEY}:splitBy(" + '"a",' * 20_000 + ")",
]


@pytest.mark.parametrize("text", ADVERSARIAL)
def test_adversarial_input_is_refused_fast(text: str) -> None:
    started = time.monotonic()
    with pytest.raises(SelectorError):
        to_query(text)
    assert time.monotonic() - started < 1.0


def test_a_long_but_valid_selector_inside_the_caps_parses_quickly() -> None:
    text = f"{KEY}:splitBy(" + ",".join(f'"d{n}"' for n in range(200)) + "):avg"
    started = time.monotonic()
    query = to_query(text)
    assert time.monotonic() - started < 1.0
    assert len(query.terms[0].group_by) == 200


def test_the_caps_are_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sel, "MAX_LENGTH", 20)
    with pytest.raises(SelectorError, match="longer than 20 characters"):
        sel.parse_selector("a" * 21)
    monkeypatch.setattr(sel, "MAX_LENGTH", 4000)
    monkeypatch.setattr(sel, "MAX_NESTING", 3)
    with pytest.raises(SelectorError, match="nested more than 3"):
        sel.parse_selector("((((a))))")
    monkeypatch.setattr(sel, "MAX_NESTING", 16)
    monkeypatch.setattr(sel, "MAX_TRANSFORMS", 2)
    with pytest.raises(SelectorError, match="more than 2 transformations"):
        sel.parse_selector("a:avg:avg:avg")


# ----- meaning --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "func", "percentile"),
    [
        (f"{KEY}:avg", "average", None),
        (f"{KEY}:min", "min", None),
        (f"{KEY}:max", "max", None),
        (f"{KEY}:sum", "sum", None),
        (f"{KEY}:count", "count", None),
        (f"{KEY}:median", "percentile", 50.0),
        (f"{KEY}:percentile(95)", "percentile", 95.0),
        (f"{KEY}:value", "latest", None),
        (f"{KEY}:last", "latest", None),
        (f"{KEY}:lastReal", "latest", None),
    ],
)
def test_aggregations(text: str, func: str, percentile: float | None) -> None:
    (term,) = to_query(text).terms
    assert term.aggregate is not None
    assert (term.aggregate.func, term.aggregate.percentile) == (func, percentile)


def test_no_aggregation_or_auto_leaves_the_metric_default() -> None:
    assert to_query(KEY).terms[0].aggregate is None
    assert to_query(f"{KEY}:auto").terms[0].aggregate is None


def test_split_and_merge_all() -> None:
    assert to_query(f'{KEY}:splitBy("a","b"):avg').terms[0].group_by == ["a", "b"]
    assert to_query(f"{KEY}:splitBy():avg").terms[0].group_by == []


@pytest.mark.parametrize(
    ("clause", "expected"),
    [
        ('eq("d","v")', [DimFilter("d", "eq", ("v",), False)]),
        ('ne("d","v")', [DimFilter("d", "eq", ("v",), True)]),
        ('prefix("d","v")', [DimFilter("d", "prefix", ("v",), False)]),
        ('suffix("d","v")', [DimFilter("d", "suffix", ("v",), False)]),
        ('contains("d","v")', [DimFilter("d", "contains", ("v",), False)]),
        ('existsKey("d")', [DimFilter("d", "exists", (), False)]),
        ('not(existsKey("d"))', [DimFilter("d", "exists", (), True)]),
        ('not(prefix("d","v"))', [DimFilter("d", "prefix", ("v",), True)]),
        ('not(eq("d","v"))', [DimFilter("d", "eq", ("v",), True)]),
        (
            'and(eq("d","v"),ne("e","w"))',
            [DimFilter("d", "eq", ("v",), False), DimFilter("e", "eq", ("w",), True)],
        ),
        ('or(eq("d","v"),eq("d","w"))', [DimFilter("d", "eq", ("v", "w"), False)]),
        ('not(or(eq("d","v"),eq("d","w")))', [DimFilter("d", "eq", ("v", "w"), True)]),
    ],
)
def test_filters(clause: str, expected: list[DimFilter]) -> None:
    assert to_query(f"{KEY}:filter({clause}):avg").terms[0].filters == expected


def test_two_filter_transformations_are_both_applied() -> None:
    query = to_query(f'{KEY}:filter(eq("a","1")):filter(eq("b","2")):avg')
    assert [f.key for f in query.terms[0].filters] == ["a", "b"]


def test_ratio_becomes_an_expression_over_numbered_terms() -> None:
    query = to_query(f'({KEY}:splitBy("h"):sum) / (b.c:splitBy("h"):sum) * 100')
    assert [t.key for t in query.terms] == [KEY, "b.c"]
    assert query.expression == EOp("*", EOp("/", ERef(0), ERef(1)), ENum(100.0))


def test_the_same_metric_with_two_aggregations_is_two_terms() -> None:
    query = to_query(f"({KEY}:max) - ({KEY}:min)")
    assert [t.aggregate.func for t in query.terms if t.aggregate] == ["max", "min"]


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        (f"{KEY}:fold(avg)", "fold"),
        (f"{KEY}:delta", "delta"),
        (f"{KEY}:rollup(avg,5m)", "rollup"),
        (f"{KEY}:timeshift(-1d)", "timeshift"),
        (f'{KEY}:partition("p",value("a",eq("x","y")))', "partition"),
        (f"{KEY}:histogram", "histogram"),
        (f'{KEY}:merge("d")', "merge"),
        (f"{KEY}:toUnit(MilliSecond,Second)", "toUnit"),
        (f"{KEY}:rate(5m)", "rate"),
        (f"{KEY}:wat", "unknown transformation"),
        (f'{KEY}:filter(in("d",entitySelector("type(HOST)")))', "entity"),
        (f"{KEY}:filter(series(avg,gt(10)))", "series filter"),
        (f'{KEY}:filter(remainder("d"))', "remainder"),
        (f'{KEY}:filter(or(eq("a","1"),eq("b","2")))', "or"),
        (f'{KEY}:filter(not(and(eq("a","1"),eq("b","2"))))', "not"),
        (f'{KEY}:filter(contains("d","v",false))', "contains"),
        (f"{KEY}:avg:max", "two aggregations"),
        (f"{KEY}:percentile(150)", "percentile"),
        (f"{KEY}:percentile(x)", "percentile"),
        (f"{KEY}:splitBy(5)", "splitBy"),
        (f"{KEY}:default(x)", "default"),
        ('(a.b:avg) / (c.d:splitBy("x"):avg)', "same split"),
        ("1 + 2", "no metric"),
        (f"({KEY}:avg + 1):avg", "arithmetic"),
    ],
)
def test_unsupported_or_unsafe_constructs_make_the_selector_manual(
    text: str, fragment: str
) -> None:
    with pytest.raises(SelectorError, match=fragment):
        to_query(text)


def test_rate_per_minute_is_a_sum_with_a_note() -> None:
    (term,) = to_query(f"{KEY}:splitBy():rate(1m)").terms
    assert term.aggregate is not None and term.aggregate.func == "sum"
    assert any("rate" in n for n in term.notes)


def test_default_is_recorded_not_applied() -> None:
    (term,) = to_query(f"{KEY}:avg:default(0)").terms
    assert term.fill == 0.0 and any("default" in n for n in term.notes)


def test_default_always_is_noted_differently() -> None:
    (term,) = to_query(f"{KEY}:avg:default(0,always)").terms
    assert any("always" in n for n in term.notes)


@pytest.mark.parametrize("fn", ["sort(value(avg,descending))", "limit(10)", "names", "parents"])
def test_sort_limit_and_names_are_noted_not_applied(fn: str) -> None:
    (term,) = to_query(f"{KEY}:avg:{fn}").terms
    assert term.notes


def test_set_unit_and_smooth_are_noted() -> None:
    (term,) = to_query(f"{KEY}:avg:setUnit(Percent):smooth(skipfirst)").terms
    assert len(term.notes) == 2


def test_a_dimension_or_value_cannot_inject_through_the_parser() -> None:
    hostile = 'x\\"); DROP --'
    query = to_query(f'{KEY}:filter(eq("d","{hostile.replace(chr(34), "~" + chr(34))}")):avg')
    (flt,) = query.terms[0].filters
    assert flt.values == (hostile,)  # the value is data; quoting happens at the NRQL step
