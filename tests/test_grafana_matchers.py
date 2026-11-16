"""Label matchers that have an exact or noted New Relic form without RLIKE (invented data)."""

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from importer.sources.grafana.translate import N_DOT_LITERAL
from test_grafana import make, one

SELECT = "SELECT average(cpu) FROM Metric"


@pytest.mark.parametrize(
    ("expr", "where"),
    [
        ('cpu{a=~".*"}', ""),
        ('cpu{a=~".*", b="x"}', " WHERE b = 'x'"),
        ('cpu{a=~".+"}', " WHERE a IS NOT NULL"),
        ('cpu{a!~".+"}', " WHERE a IS NULL"),
        ('cpu{a!~"|x"}', " WHERE a != 'x' AND a IS NOT NULL"),
        ('cpu{a!~"x|y|"}', " WHERE a NOT IN ('x', 'y') AND a IS NOT NULL"),
        (
            'cpu{a=~"h1.example.test|h2.example.test"}',
            " WHERE a IN ('h1.example.test', 'h2.example.test')",
        ),
        ('cpu{a=~"h1\\\\.example"}', " WHERE a = 'h1.example'"),
        ('cpu{a=~"h1.example.*"}', " WHERE a LIKE 'h1.example%'"),
        ('cpu{a!~"10.0.0.1"}', " WHERE a != '10.0.0.1'"),
    ],
)
def test_matchers_with_a_plain_form_convert(expr: str, where: str) -> None:
    m = one(make(expr))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == SELECT + where


def test_a_bare_dot_is_read_as_a_literal_dot_and_says_so() -> None:
    m = one(make('cpu{a=~"h1.example"}'))
    assert N_DOT_LITERAL in m.reasons
    assert N_DOT_LITERAL not in one(make('cpu{a=~"h1\\\\.example"}')).reasons


@pytest.mark.parametrize(
    "expr",
    [
        'cpu{a!~".*"}',  # matches nothing
        'cpu{a=~"x|"}',  # matches a missing label too: an OR the filter list cannot hold
        'cpu{a!~"|"}',
        'cpu{a=~"x[0-9]"}',
        'cpu{a=~"(x|y)"}',
        'cpu{a=~"^x$"}',
        'cpu{a=~"x+"}',
        'cpu{a=~"x.y.*z"}',
        'cpu{a=~"."}',
        'cpu{a=~".."}',
    ],
)
def test_forms_that_would_change_which_series_match_stay_refused(expr: str) -> None:
    m = one(make(expr))
    assert m.confidence is Confidence.MANUAL
    assert any("promql label matcher" in r for r in m.reasons)


@pytest.mark.parametrize("value", ["5..", "a.", ".a", "a..b", "5.\\\\.", "5.|x"])
def test_a_dot_that_looks_like_a_wildcard_stays_refused(value: str) -> None:
    m = one(make(f'cpu{{a=~"{value}"}}'))
    assert m.confidence is Confidence.MANUAL
