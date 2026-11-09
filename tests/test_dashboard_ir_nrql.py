"""Validators for NRQL that a translator hands to the dashboard IR, and for {{variables}}."""

import contextlib
import time
from typing import Any

import pytest

from importer.core.dashboard_nrql import (
    check_raw_nrql,
    check_variable_usage,
    check_variables_defined,
)


@pytest.mark.parametrize(
    "clause",
    ["LIMIT", "SINCE", "TIMESERIES", "COMPARE WITH", "AS", "OFFSET", "SLIDE BY", "UNTIL", "WITH"],
)
def test_a_variable_in_a_forbidden_clause_is_rejected(clause: str) -> None:
    nrql = f"SELECT count(*) FROM Transaction FACET host {clause} {{{{v}}}}"  # noqa: S608
    with pytest.raises(ValueError, match="not allowed"):
        check_variable_usage(nrql)


@pytest.mark.parametrize(
    "nrql",
    [
        "SELECT average(cpu) FROM Metric WHERE host IN ({{host}}) FACET {{group}}",
        "FROM {{event}} SELECT count(*) ORDER BY {{group}}",
        "SELECT count(*) FROM Log WHERE message = '{{host}}'",
    ],
)
def test_a_variable_in_an_allowed_clause_is_accepted(nrql: str) -> None:
    assert check_variable_usage(nrql)


def test_used_variable_names_are_returned_once_in_order() -> None:
    nrql = "SELECT count(*) FROM Log WHERE a IN ({{b}}) AND c IN ({{ a }}) AND d IN ({{b}})"
    assert check_variable_usage(nrql) == ["b", "a"]


def test_keywords_inside_strings_and_names_do_not_count() -> None:
    nrql = "SELECT count(*) FROM Log WHERE msg = 'LIMIT SINCE AS' AND {{limit}} = 1"
    assert check_variable_usage(nrql) == ["limit"]


def test_a_variable_inside_a_quoted_string_follows_the_clause_around_it() -> None:
    with pytest.raises(ValueError, match="AS"):
        check_variable_usage("SELECT count(*) AS '{{v}}' FROM Log")


@pytest.mark.parametrize("bad", ["{{", "{{a b}}", "{{a-b}}", "x }}", "{{1a}}"])
def test_malformed_variable_references_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        check_variable_usage(f"SELECT count(*) FROM Log WHERE a = {bad}")  # noqa: S608


def test_variables_used_but_not_defined_are_rejected() -> None:
    check_variables_defined(["SELECT 1 FROM Log WHERE a IN ({{x}})"], ["x", "y"])
    with pytest.raises(ValueError, match="'z'"):
        check_variables_defined(["SELECT 1 FROM Log WHERE a IN ({{z}})"], ["x"])


@pytest.mark.parametrize(
    "nrql",
    [
        "SELECT average(cpu) FROM Metric WHERE host = 'a' FACET region TIMESERIES AUTO",
        "FROM Metric SELECT uniques(region)",
        "SELECT count(*) FROM Log FACET host LIMIT 20",
        "select count(*) from Log where msg = 'it\\'s; fine' limit max",
    ],
)
def test_dashboard_nrql_that_is_allowed(nrql: str) -> None:
    check_raw_nrql(nrql)


@pytest.mark.parametrize(
    ("nrql", "message"),
    [
        ("SELECT count(*) FROM Log SINCE 1 day ago", "SINCE"),
        ("SELECT count(*) FROM Log UNTIL now", "UNTIL"),
        ("SELECT count(*) FROM Log COMPARE WITH 1 week ago", "COMPARE WITH"),
        ("SELECT count(*) FROM Log; DROP", "semicolon"),
        ("SELECT count(*) FROM Log WHERE a = 'x", "quote"),
        ("SELECT count(*) FROM Log\nSINCE 1 day ago", "SINCE"),
        ("SELECT count(*) FROM Log\x07", "control"),
        ("DELETE FROM Log", "SELECT or FROM"),
        ("", "empty"),
        ("SELECT 1 FROM Log WHERE a = '" + "x" * 5000 + "'", "longer than"),
    ],
)
def test_dashboard_nrql_that_is_refused(nrql: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_raw_nrql(nrql)


def test_raw_nrql_also_checks_variable_placement() -> None:
    with pytest.raises(ValueError, match="LIMIT"):
        check_raw_nrql("SELECT count(*) FROM Log FACET host LIMIT {{n}}")


# --- round 2 ---------------------------------------------------------------------------------


def test_pretty_printed_queries_are_accepted() -> None:
    check_raw_nrql("SELECT count(*)\r\n\tFROM Log\n  WHERE a = 1\n  FACET host")


def test_a_newline_inside_a_string_literal_is_still_refused() -> None:
    with pytest.raises(ValueError, match="quote"):
        check_raw_nrql("SELECT count(*) FROM Log WHERE a = 'x\ny'")


def test_a_clause_split_over_lines_is_still_recognised() -> None:
    with pytest.raises(ValueError, match="COMPARE WITH"):
        check_raw_nrql("SELECT count(*) FROM Log COMPARE\n  WITH 1 day ago")


@pytest.mark.parametrize(
    "char",
    [
        "\x00", "\x07", "\x1b", "\x7f", "\x85", "\u2028", "\u2029", "\u202a",
        "\u202e", "\u2066", "\u2069", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
    ],
)  # fmt: skip
def test_control_bidi_and_zero_width_characters_are_refused(char: str) -> None:
    with pytest.raises(ValueError, match="control"):
        check_raw_nrql(f"SELECT count(*) FROM Log WHERE a = 'x{char}y'")  # noqa: S608


def test_a_zero_width_character_cannot_hide_a_keyword() -> None:
    with pytest.raises(ValueError, match="control"):
        check_raw_nrql("SELECT count(*) FROM Log SIN\u200bCE 1 day ago")
    with pytest.raises(ValueError, match="control"):
        check_variable_usage("SELECT count(*) FROM Log LIM\u200bIT {{v}}")


def test_a_fullwidth_semicolon_outside_a_string_is_refused() -> None:
    with pytest.raises(ValueError, match="semicolon"):
        check_raw_nrql("SELECT count(*) FROM Log\uff1b SELECT 1")
    check_raw_nrql("SELECT count(*) FROM Log WHERE a = 'x\uff1b'")


@pytest.mark.parametrize("bad", ["{{{v}}}", "{{v}}}", "{{{v}}", "{ {{v}}", "{{v}} }"])
def test_extra_braces_around_a_variable_are_malformed(bad: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        check_variable_usage(f"SELECT count(*) FROM Log WHERE a IN ({bad})")  # noqa: S608


@pytest.mark.parametrize(
    "nrql",
    [
        "SELECT count(*) FROM Log WHERE with = {{v}}",
        "SELECT count(*) FROM Log WHERE limit > {{v}}",
        "SELECT count(*) FROM Log WHERE as IN ({{v}})",
        "SELECT count(*) FROM Log WHERE as IS NOT NULL AND x = {{v}}",
        "SELECT count(*) FROM Log WHERE attributes.limit = {{v}}",
        "SELECT count(*) FROM Log WHERE `limit` = {{v}}",
    ],
)
def test_a_column_named_like_a_keyword_does_not_hide_the_real_clause(nrql: str) -> None:
    assert check_variable_usage(nrql) == ["v"]


def test_a_real_clause_after_a_keyword_named_column_is_still_found() -> None:
    with pytest.raises(ValueError, match="LIMIT"):
        check_variable_usage("SELECT count(*) FROM Log WHERE limit = 3 FACET host LIMIT {{v}}")


def test_the_length_limit_is_exactly_4096_characters() -> None:
    head = "SELECT count(*) FROM Log WHERE a = '"
    ok = head + "x" * (4096 - len(head) - 1) + "'"
    assert len(ok) == 4096
    check_raw_nrql(ok)
    with pytest.raises(ValueError, match="longer than"):
        check_raw_nrql(ok + " ")


@pytest.mark.parametrize(
    "run",
    [
        lambda: check_raw_nrql("SELECT 1 FROM Log WHERE a = '" + "x" * 100_000),
        lambda: check_raw_nrql("'" * 100_001),
        lambda: check_variable_usage("SELECT 1 FROM Log WHERE a IN (" + "{{v}}, " * 15_000 + ")"),
        lambda: check_variable_usage("SELECT " * 15_000 + "{{v}}"),
        lambda: check_variable_usage("{{" * 50_000),
        lambda: check_variables_defined(["SELECT {{v}}"] * 20_000, ["v"]),
    ],
)
def test_validators_stay_fast_on_100k_characters(run: Any) -> None:
    start = time.perf_counter()
    with contextlib.suppress(ValueError):
        run()
    assert time.perf_counter() - start < 2.0
