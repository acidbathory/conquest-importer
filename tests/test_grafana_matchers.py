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
        # Prometheus has no empty-valued labels, so `!=""` means the label is set, as `=~".+"`
        ('cpu{a!=""}', " WHERE a IS NOT NULL"),
        ('cpu{a!="", b="x"}', " WHERE a IS NOT NULL AND b = 'x'"),
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


def test_a_set_label_inside_a_counter_rate_converts() -> None:
    m = one(make('sum by (pod) (rate(cpu_total{container!=""}[5m]))', instant=True, params=[1]))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == (
        "SELECT sum(cpu_total) / 300 FROM Metric WHERE container IS NOT NULL FACET pod"
    )


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
    ],
)
def test_forms_that_would_change_which_series_match_stay_refused(expr: str) -> None:
    m = one(make(expr))
    assert m.confidence is Confidence.MANUAL
    assert any("promql label matcher" in r for r in m.reasons)


@pytest.mark.parametrize(
    ("expr", "where"),
    [
        ('cpu{a=~"x[0-9]"}', " WHERE a RLIKE 'x[0-9]'"),
        ('cpu{a=~"(x|y)"}', " WHERE a RLIKE '(x|y)'"),
        ('cpu{a=~"^x$"}', " WHERE a RLIKE '^x$'"),
        ('cpu{a=~"x+"}', " WHERE a RLIKE 'x+'"),
        ('cpu{a=~"a.+"}', " WHERE a RLIKE 'a.+'"),
        ('cpu{a=~"x.y.*z"}', " WHERE a RLIKE 'x.y.*z'"),
        ('cpu{a=~"."}', " WHERE a RLIKE '.'"),
        ('cpu{a=~".."}', " WHERE a RLIKE '..'"),
        ('cpu{a!~"x[0-9]"}', " WHERE a NOT RLIKE 'x[0-9]'"),
        # a bare dot is a regular expression dot (any character): exact with RLIKE
        ('cpu{a=~"5.."}', " WHERE a RLIKE '5..'"),
        ('cpu{a=~"a."}', " WHERE a RLIKE 'a.'"),
        ('cpu{a=~".a"}', " WHERE a RLIKE '.a'"),
        ('cpu{a=~"a..b"}', " WHERE a RLIKE 'a..b'"),
        ('cpu{a=~"5.\\\\."}', " WHERE a RLIKE '5.\\\\.'"),
        ('cpu{a=~"5.|x"}', " WHERE a RLIKE '5.|x'"),
    ],
)
def test_real_regular_expressions_convert_with_rlike(expr: str, where: str) -> None:
    m = one(make(expr))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == SELECT + where
    assert any("RLIKE" in r and "missing label" in r for r in m.reasons)
