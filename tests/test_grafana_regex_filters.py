"""Regex label matchers become RLIKE filters when they are safe (invented data only)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, CountFilter, TagFilter
from test_grafana import make, one, text


def _convert(expr: str, dtype: str = "prometheus"):  # type: ignore[no-untyped-def]
    return one(make(expr, reducer="last", dtype=dtype))


@pytest.mark.parametrize(
    ("expr", "nrql"),
    [
        (r'cpu{a=~"a.+"}', "SELECT average(cpu) FROM Metric WHERE a RLIKE 'a.+'"),
        (r'cpu{a=~"(a|b)c"}', "SELECT average(cpu) FROM Metric WHERE a RLIKE '(a|b)c'"),
        (r'cpu{a!~"web-[0-9]+"}', "SELECT average(cpu) FROM Metric WHERE a NOT RLIKE 'web-[0-9]+'"),
        (r'cpu{a=~"x{2,3}y"}', "SELECT average(cpu) FROM Metric WHERE a RLIKE 'x{2,3}y'"),
        (r'cpu{a=~"5.."}', "SELECT average(cpu) FROM Metric WHERE a RLIKE '5..'"),
        (r'cpu{a=~"\\d+\\.x"}', "SELECT average(cpu) FROM Metric WHERE a RLIKE '\\\\d+\\\\.x'"),
        (r'cpu{a=~"a.*b"}', "SELECT average(cpu) FROM Metric WHERE a RLIKE 'a.*b'"),
    ],
)
def test_a_safe_regex_becomes_rlike(expr: str, nrql: str) -> None:
    m = _convert(expr)
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == nrql
    assert "matched in full with rlike" in text(m) and "is null in new relic" in text(m)


@pytest.mark.parametrize(
    "pattern",
    [
        "a|",  # can match the empty string
        "(a)?",
        "a*",
        "(?=a)b",  # lookahead
        "(?!a)b",
        "(?<=a)b",
        "(?<!a)b",
        "(?i)abc",
        r"(a)\\1",  # backreference
        "a++b",  # possessive
        "a*+b",
        "a{,3}b",  # literal in RE2, a range in Python
        "[[:alpha:]]b",
        r"\\Zb",
        "a$job",  # grafana variable
        "a${job}b",
        "a[[job]]",
        "a%b+",
        "(a",  # does not compile
        "a" * 600 + "+",  # longer than 500
        "café+",
    ],
)
def test_an_unsafe_regex_stays_manual(pattern: str) -> None:
    m = _convert(f'cpu{{a=~"{pattern}"}}')
    assert m.confidence is Confidence.MANUAL, pattern
    assert "promql label matcher" in text(m)


def test_the_existing_exact_paths_do_not_use_rlike() -> None:
    for expr in ('cpu{a=~"x|y"}', 'cpu{a=~"x.*"}', 'cpu{a=~".*"}', 'cpu{a=~".+"}', 'cpu{a=~"1.2"}'):
        assert "RLIKE" not in nrql_for(_convert(expr)), expr


def test_a_logql_regex_matcher_becomes_a_log_rlike_filter() -> None:
    m = _convert(r'sum(count_over_time({app=~"a.+b"}[5m]))', dtype="loki")
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert "app RLIKE 'a.+b'" in nrql_for(m)


def test_a_quote_in_a_pattern_is_escaped_by_the_emitter() -> None:
    f = TagFilter(key="a", value="it's+", regex=True)
    from importer.core.emit.nrql import _match_clause

    assert _match_clause("a", f) == "a RLIKE 'it\\'s+'"
    assert _match_clause("a", f.model_copy(update={"negate": True})) == "a NOT RLIKE 'it\\'s+'"


@pytest.mark.parametrize("value", ["a" * 501, "a\x00b"])
def test_the_emitter_refuses_a_long_or_nul_pattern(value: str) -> None:
    from importer.core.emit.nrql import _match_clause

    with pytest.raises(ValueError, match="pattern"):
        _match_clause("a", CountFilter(key="a", value=value, regex=True))


def test_regex_excludes_wildcard_and_values() -> None:
    with pytest.raises(ValidationError):
        TagFilter(key="a", value="x", regex=True, wildcard=True)
    with pytest.raises(ValidationError):
        TagFilter(key="a", value="x", regex=True, values=["x"])
    with pytest.raises(ValidationError):
        CountFilter(key="a", value="x", regex=True, wildcard=True)


def test_saved_ir_is_unchanged_when_regex_is_false() -> None:
    assert "regex" not in TagFilter(key="a", value="x").model_dump()
    assert "regex" not in CountFilter(key="a", value="x").model_dump()
    assert "regex" not in CountFilter(key="a", value="x").model_dump_json()
    assert TagFilter(key="a", value="x.+", regex=True).model_dump()["regex"] is True
    assert CountFilter(key="a", value="x.+", regex=True).model_dump()["regex"] is True
