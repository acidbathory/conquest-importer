"""Hostile input to the new forms: nothing raises, no rule text leaks, NRQL is escaped."""

import random

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from test_grafana import make, one

PIECES = [
    "rate(",
    "increase(",
    "absent(",
    "absent_over_time(",
    "x_total",
    "[5m]",
    "[0s]",
    "{",
    "}",
    'a="b"',
    ",",
    " ",
    "sum",
    "avg",
    "(",
    ")",
    " by (a) ",
    "/",
    "*",
    "100",
    '"',
    "\\",
    "1e-3",
    "-",
]


def test_random_expressions_never_raise_and_are_manual_or_converted() -> None:
    rng = random.Random(7)  # noqa: S311 - seeded, so the test is repeatable
    for _ in range(600):
        expr = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 40)))
        m = one(make(expr, instant=True))
        assert m.confidence in (Confidence.APPROXIMATE, Confidence.MANUAL)


@pytest.mark.parametrize(
    "expr",
    [
        'sum(rate(SENTINEL_total{SENTINEL="SENTINEL"}[5m]))',
        'sum(rate(SENTINEL{a="SENTINEL"}[5m]))',
        'sum(rate(SENTINEL_total{a=~"SENTINEL.+"}[5m]))',
        "sum(rate(SENTINEL_total[5m])) / sum(rate(SENTINEL_other[5m]))",
        'sum(rate(SENTINEL_total{a="1"}[5m])) / sum(rate(SENTINEL_total[5m]))',
        'avg(SENTINEL_a) / sum(SENTINEL_b{x="SENTINEL"})',
        'absent(SENTINEL{a=~"SENTINEL(x"})',
        'absent(SENTINEL{a="SENTINEL"}) == SENTINEL',
    ],
)
def test_no_reason_repeats_text_from_the_expression(expr: str) -> None:
    m = one(make(expr, instant=True, kind="lt", params=[0]))
    assert not any("SENTINEL" in r for r in m.reasons)


@pytest.mark.parametrize("value", ["a'b", "a\\b", "a\\'; DROP", "x' OR '1'='1"])
def test_matcher_values_are_escaped_in_the_new_forms(value: str) -> None:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    # a quote or backslash inside a PromQL string is written with a backslash
    promql = value.replace("\\", "\\\\").replace('"', '\\"')
    absent = one(make(f'absent(up{{job="{promql}"}})', instant=True, params=[0]))
    rate = one(make(f'sum(rate(x_total{{job="{promql}"}}[5m]))', instant=True))
    for m in (absent, rate):
        assert m.confidence is Confidence.APPROXIMATE, m.reasons
        assert f"job = '{escaped}'" in nrql_for(m)


@pytest.mark.parametrize("name", ["a-b_total", "a b_total", "a`_total", "a;b_total", "é_total"])
def test_a_metric_name_nrql_would_reject_is_manual_in_the_new_forms(name: str) -> None:
    for expr in (f"sum(rate({name}[5m]))", f"absent({name})"):
        assert one(make(expr, instant=True, params=[0])).confidence is Confidence.MANUAL


def test_a_long_expression_is_refused_before_any_parsing() -> None:
    m = one(make("sum(rate(x_total[5m]))" + " " * 10_001, instant=True))
    assert m.confidence is Confidence.MANUAL
