"""The PromQL census script: construct extraction, calibration against the converter, projection."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "analysis"
FIXTURES = ROOT / "tests" / "fixtures"


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


constructs = load("promql_constructs")
verdicts = load("promql_verdicts")
census = load("promql_census")

OPTS = QueryOptions(variables={"job": None}, name_map=NameMap(), timeseries=True)


def ids(expr: str) -> set[str]:
    from importer.sources.grafana.promql_ast import parse

    found: set[str] = constructs.constructs_of(parse(expr), expr)
    return found


@pytest.mark.parametrize(
    ("expr", "expected"),
    [
        (
            'up{job="a", env!="b", p=~"c", n!~"d"}',
            {"matcher.eq", "matcher.ne", "matcher.re", "matcher.nre"},
        ),
        ("sum without (a) (up)", {"agg.sum", "agg.without"}),
        ("sum by (a) (up)", {"agg.sum", "agg.by", "shape.sum_of_gauge"}),
        ("rate(x[$__rate_interval])", {"fn.rate", "window.rate_interval", "selector.range"}),
        ("increase(x[$__range])", {"window.range"}),
        ("x[5m]", {"window.fixed"}),
        ("up > bool 0", {"cmp.bool.vector_scalar"}),
        ("up > 0", {"cmp.filter.vector_scalar"}),
        ("a > b", {"cmp.filter.vector_vector"}),
        ("a and b", {"set.and"}),
        ("a / on (x) group_left (y) b", {"match.on", "match.group_left", "match.explicit"}),
        ("a / ignoring (x) b", {"match.ignoring"}),
        ("a / b", {"match.implicit", "arith.vector_vector"}),
        ("a * 2", {"arith.vector_scalar", "binop.*"}),
        ("x offset 1h", {"mod.offset"}),
        ("x @ 100", {"mod.at"}),
        ("max_over_time(rate(x[1m])[1h:5m])", {"subquery", "fn.max_over_time"}),
        ("node:cpu:rate5m", {"name.recording_rule"}),
        ("up or vector(0)", {"set.or", "idiom.or_vector"}),
        ("avg(rate(x[5m]))", {"shape.avg_min_max_of_rate"}),
        ("x_total", {"shape.counter_suffix_bare"}),
        ("topk(3, up)", {"agg.topk"}),
        ('up{job=~".*"}', {"matcher.re.matches_empty"}),
        ("sum(up{a='b'})", {"style.single_quoted_string"}),
        ("SUM(up)", {"style.uppercase_aggregate"}),
        ('up{job=~"$job"}', {"matcher.value_variable"}),
        ("-up", {"unary.-"}),
    ],
)
def test_constructs(expr: str, expected: set[str]) -> None:
    assert expected <= ids(expr)


def test_rate_inside_is_not_a_bare_counter() -> None:
    assert "shape.counter_suffix_bare" not in ids("sum(rate(x_total[5m]))")


# One minimal expression per construct whose support is claimed or denied. The converter itself
# is the judge: SUPPORTED_TODAY must say exactly what convert_query does.
SAMPLES = {
    "selector.instant": 'up{job="a"}',
    "matcher.re": 'up{job=~"a|b"}',
    "matcher.nre": 'up{job!~"a|b"}',
    "fn.rate": "sum(rate(x_total[5m]))",
    "fn.irate": "sum(irate(x_total[5m]))",
    "fn.increase": "sum(increase(x_total[5m]))",
    "fn.histogram_quantile": "histogram_quantile(0.9, sum by (le) (rate(x_bucket[5m])))",
    "agg.sum": "sum(rate(x_total[5m]))",
    "agg.by": "sum by (a) (rate(x_total[5m]))",
    "agg.topk": "topk(3, sum by (a) (rate(x_total[5m])))",
    "binop.+": "up + 1",
    "arith.vector_vector": "a + b",
    "window.rate_interval": "sum(rate(x_total[$__rate_interval]))",
    "name.recording_rule": "node:cpu:rate5m",
}
UNSUPPORTED_SAMPLES = {
    "agg.without": "sum without (a) (up)",
    "shape.sum_of_gauge": "sum(up)",
    "cmp.filter.vector_scalar": "up > 0",
    "set.or": "a or b",
    "match.on": "a / on (x) b",
    "mod.offset": "up offset 1h",
    "subquery": "max_over_time(up[1h:5m])",
    "fn.label_replace": 'label_replace(up, "a", "b", "c", "d")',
    "fn.delta": "delta(up[5m])",
    "shape.avg_min_max_of_rate": "avg(rate(x_total[5m]))",
    "style.uppercase_aggregate": "SUM(up)",
    "style.single_quoted_string": "sum(up{a='b'})",
    "shape.counter_suffix_bare": "x_total",
}


@pytest.mark.parametrize("construct", sorted(SAMPLES))
def test_supported_constructs_convert(construct: str) -> None:
    assert construct in census.SUPPORTED_TODAY
    assert construct in ids(SAMPLES[construct])
    convert_query(SAMPLES[construct], "prometheus", OPTS)


@pytest.mark.parametrize("construct", sorted(UNSUPPORTED_SAMPLES))
def test_unsupported_constructs_do_not_convert(construct: str) -> None:
    assert construct not in census.SUPPORTED_TODAY
    assert construct in ids(UNSUPPORTED_SAMPLES[construct])
    with pytest.raises(Unconvertible):
        convert_query(UNSUPPORTED_SAMPLES[construct], "prometheus", OPTS)


def test_every_supported_construct_has_a_verdict() -> None:
    for construct in census.SUPPORTED_TODAY:
        assert verdicts.verdict_for(construct).code in (1, 2), construct


def test_unknown_construct_is_not_expressible() -> None:
    assert verdicts.verdict_for("fn.no_such_function").code == 4


def test_verdict_codes_and_text() -> None:
    for name, verdict in verdicts.VERDICTS.items():
        assert verdict.code in (1, 2, 3, 4), name
        assert verdict.status in ("MEASURED", "DOCS", "UNVERIFIED", "n/a"), name
        assert verdict.note or verdict.code != 4, name
        if verdict.code in (1, 2, 3):
            assert verdict.nrql, name


def test_corpus_verdicts_cover_every_construct() -> None:
    found = census.build([FIXTURES / "grafana-dashboard-convert", FIXTURES / "grafana-dashboards"])
    seen = {c for e in found.expressions for c in e.constructs}
    assert seen
    assert not {c for c in seen if c not in verdicts.VERDICTS}


def test_census_agrees_with_the_converter() -> None:
    found = census.build([FIXTURES / "grafana-dashboard-convert", FIXTURES / "grafana-dashboards"])
    assert found.expressions
    for e in found.expressions:
        if e.converts:
            assert not e.unsupported, e.text
        elif not e.parse_error:
            assert e.unsupported, (e.text, e.reason)


def _expr(constructs: list[str], converts: bool) -> object:
    return census.Expression(
        "d", "0:0", "t", "timeseries", "A", "x", "prometheus", "", constructs, converts
    )


def _panel(key: str, indexes: list[int], other: list[str] | None = None) -> object:
    return census.PanelRow(key, "d", "t", "timeseries", False, [], other or [], indexes)


def test_projection_counts_panels_by_scenario() -> None:
    found = census.Census(
        expressions=[
            _expr(["selector.instant"], True),
            _expr(["selector.instant", "fn.round"], False),  # verdict 1
            _expr(["selector.instant", "agg.without"], False),  # verdict 4
            _expr(["selector.instant", "agg.nested"], False),  # verdict 3
        ],
        panels=[
            _panel("a", [0]),
            _panel("b", [0, 1]),
            _panel("c", [2], ["dashboard panel has transformations"]),
            _panel("d", [3]),
            _panel("e", [1], ["dashboard panel uses a variable that stays manual"]),
        ],
    )
    today = census.project(found, census.today())
    assert (today.panels, today.query_layer, today.end_to_end) == (5, 1, 1)
    low = census.project(found, census.expressible(2))
    assert (low.query_layer, low.end_to_end) == (3, 2)  # a, b and e; a and b have no other blocker
    high = census.project(found, census.expressible(3))
    assert (high.query_layer, high.end_to_end) == (4, 3)


def test_greedy_adds_the_construct_that_unlocks_most() -> None:
    found = census.Census(
        expressions=[
            _expr(["selector.instant", "fn.round"], False),
            _expr(["selector.instant", "fn.round"], False),
            _expr(["selector.instant", "fn.abs"], False),
            _expr(["selector.instant", "agg.without"], False),  # verdict 4: never a candidate
        ],
        panels=[_panel("a", [0]), _panel("b", [1]), _panel("c", [2]), _panel("d", [3])],
    )
    steps = census.greedy(found, 3)
    assert [s[0] for s in steps] == ["fn.round", "fn.abs"]
    assert [s[1] for s in steps] == [2, 3]
