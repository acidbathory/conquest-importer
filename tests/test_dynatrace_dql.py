"""The DQL log-count pattern accepts a few more filter forms, only where the meaning is certain."""

import contextlib
import time

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.dynatrace.dql import CONTAINS_NOTE, NOT_NARROW, DqlError, parse_filter
from importer.sources.dynatrace.models import DtWorkflow
from importer.sources.dynatrace.translate import translate_workflow

DQL = "dynatrace.automations:execute-dql-query"
CONDITION = '{{ result("count")["records"][0]["n"] > 100 }}'


def convert(filter_text: str, tail: str = "summarize n = count()") -> IRMonitor:
    query = f"fetch logs, from:now() - 15m | filter {filter_text} | {tail}"
    wf = DtWorkflow.model_validate(
        {
            "title": "w",
            "id": "w1",
            "tasks": {
                "count": {"action": DQL, "input": {"query": query}},
                "notify": {
                    "action": "dynatrace.email:send-email",
                    "conditions": {"custom": CONDITION},
                    "input": {"to": ["a@example.com"]},
                },
            },
            "trigger": {
                "schedule": {
                    "isActive": True,
                    "trigger": {"type": "interval", "intervalMinutes": 15},
                }
            },
        }
    )
    return translate_workflow(wf)


@pytest.mark.parametrize(
    ("filter_text", "where"),
    [
        ('contains(content, "timeout")', "content LIKE '%timeout%'"),
        ('contains( content , "a b" )', "content LIKE '%a b%'"),
        ('contains(content, "it\'s | x")', "content LIKE '%it\\'s | x%'"),
        ('matchesValue(loglevel, "ERROR")', "loglevel = 'ERROR'"),
        ('matchesValue(loglevel, {"ERROR", "WARN"})', "loglevel IN ('ERROR', 'WARN')"),
        ('in(loglevel, "ERROR", "WARN")', "loglevel IN ('ERROR', 'WARN')"),
        ('in(loglevel, "ERROR")', "loglevel IN ('ERROR')"),
        ('in(loglevel, array("ERROR","WARN"))', "loglevel IN ('ERROR', 'WARN')"),
        ('in(path, "a*b", "c_d")', "path IN ('a*b', 'c_d')"),
        (
            'app == "x" and contains(content, "boom") AND in(loglevel, "ERROR", "WARN")',
            "app = 'x' AND content LIKE '%boom%' AND loglevel IN ('ERROR', 'WARN')",
        ),
    ],
)
def test_accepted_filter_forms_become_exact_nrql(filter_text: str, where: str) -> None:
    m = convert(filter_text)
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT count(*) FROM Log WHERE " + where  # noqa: S608 - expected text


@pytest.mark.parametrize(
    "filter_text",
    [
        'contains(content, "50%")',  # % would be a wildcard in LIKE
        'contains(content, "a*b")',
        'contains(content, "a_b")',
        'contains(content, "")',
        'contains(content, "x", caseSensitive: false)',
        'contains(content, "x", false)',
        "contains(content)",
        'contains(lower(content), "x")',
        "contains(content, 'x')",
        "contains(content, x)",
        'not contains(content, "x")',
        '!contains(content, "x")',
        'contains(content, "x") or contains(content, "y")',
        'contains(content, "x") junk',
        'contains(content, "bad\\nescape")',
        'matchesValue(loglevel, "ERR*")',
        'matchesValue(loglevel, {"ERR*", "WARN"})',
        "matchesValue(loglevel, {})",
        'matchesValue(loglevel, {"A",})',
        'matchesValue(loglevel, "A", "B")',
        "in(loglevel)",
        "in(loglevel, ERROR)",
        'in(loglevel, "A",)',
        "in(loglevel, array())",
        'in(loglevel, array("A")',
        'in(lower(loglevel), "a")',
        "in(loglevel, " + ", ".join(f'"v{i}"' for i in range(60)) + ")",
        'loglevel in ("A")',
        'matchesPhrase(content, "x")',
    ],
)
def test_other_filter_forms_stay_manual_without_echoing_the_query(filter_text: str) -> None:
    m = convert(filter_text)
    assert m.confidence is Confidence.MANUAL
    reasons = m.reasons
    assert reasons[0].startswith(NOT_NARROW)
    assert filter_text not in " ".join(reasons) and steps_for(reasons)


def test_a_contains_condition_carries_a_case_note_with_a_category() -> None:
    m = convert('contains(content, "x")')
    assert CONTAINS_NOTE in m.reasons
    assert classify(CONTAINS_NOTE) is Category.APPROXIMATION
    plain = convert('content == "x"')
    assert CONTAINS_NOTE not in plain.reasons


@pytest.mark.parametrize(
    "tail",
    [
        "summarize n = count(), by:{loglevel}",
        "summarize n = count(), by: {loglevel, host}",
        "summarize n = count(), m = count()",
        "summarize n = count(), m = sum(x)",
        "summarize n = avg(x)",
    ],
)
def test_grouping_and_several_aggregations_stay_manual(tail: str) -> None:
    m = convert('loglevel == "ERROR"', tail)
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith(NOT_NARROW)


def test_a_by_clause_reason_says_why_it_is_not_a_facet() -> None:
    m = convert('loglevel == "ERROR"', "summarize n = count(), by:{loglevel}")
    assert "by:" in m.reasons[0] and "first record" in m.reasons[0]


def test_the_precedence_of_the_specific_by_reason_over_the_generic_one() -> None:
    generic = convert('loglevel == "ERROR"', "summarize n = avg(x)")
    assert "first record" not in generic.reasons[0]


ATTACKS = [
    'contains(content, "' + "a" * 100_000,
    'contains(content, "' + '\\"' * 50_000 + '")',
    "contains(" + " " * 100_000 + ")",
    "in(a, " + '"a", ' * 50_000,
    "in(a, " + '"a",' * 50_000 + ")",
    "matchesValue(a, {" + '"a", ' * 50_000 + "}",
    "matchesValue(a, {" + '"a" ,' * 50_000 + "}",
    'a == "x" and ' * 20_000,
    "x" * 100_000,
    "(" * 100_000,
    'contains(a, "x") and ' * 20_000 + "junk",
    "in(a, array(" + '"a",' * 50_000,
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_filter_parsing_is_fast_on_adversarial_input(attack: str) -> None:
    start = time.perf_counter()
    m = convert(attack)
    assert time.perf_counter() - start < 5.0
    assert m.confidence is Confidence.MANUAL


def test_many_terms_are_bounded() -> None:
    m = convert(" and ".join('a == "x"' for _ in range(500)))
    assert m.confidence is Confidence.MANUAL


@pytest.mark.parametrize("attack", ATTACKS)
def test_the_filter_parser_alone_is_fast_on_adversarial_input(attack: str) -> None:
    """Without the query length cap in front of it."""
    start = time.perf_counter()
    with contextlib.suppress(DqlError):  # one attack is a valid, very long contains() text
        parse_filter("filter " + attack)
    assert time.perf_counter() - start < 5.0


@pytest.mark.parametrize(
    "filter_text",
    ['contains(content, "50%")', 'matchesValue(a, "x*")', "in(a)", 'a == "x" junk'],
)
def test_the_new_manual_reasons_are_unsupported_query(filter_text: str) -> None:
    assert classify(convert(filter_text).reasons[0]) is Category.UNSUPPORTED_QUERY


def test_the_by_reason_is_unsupported_query_and_has_steps() -> None:
    m = convert('a == "x"', "summarize n = count(), by:{a}")
    assert classify(m.reasons[0]) is Category.UNSUPPORTED_QUERY and steps_for(m.reasons)
