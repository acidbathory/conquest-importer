"""Validators for NRQL that a translator hands to the dashboard IR, and for {{variables}}."""

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
    nrql = f"SELECT count(*) FROM Transaction FACET host {clause} {{{{v}}}}"
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
        check_variable_usage(f"SELECT count(*) FROM Log WHERE a = {bad}")


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
        ("SELECT count(*) FROM Log\nSINCE 1 day ago", "control"),
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
