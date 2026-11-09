"""The strict regex gate: patterns outside a small safe subset are undecidable, never run."""

import random
import re
import time

import pytest

from importer.sources.grafana.notifications import matchers as mt

HANGING = [
    "(a?){30}a{30}",
    "(a?a?)*b",
    "(a?a?)+b",
    "(a??a??)*b",
    "(?:a?){40}a{40}",
]


@pytest.mark.parametrize("pattern", HANGING)
def test_the_five_patterns_that_hang_python_re_are_undecidable_at_once(pattern: str) -> None:
    start = time.perf_counter()
    assert mt.is_safe_pattern(pattern) is False
    assert mt.matches("=~", pattern, "a" * 30) is None
    assert time.perf_counter() - start < 0.2


@pytest.mark.parametrize(
    "pattern",
    [
        "a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?a?aaaaaaaaaaaaaaaaaaaa",  # flattened, same blow-up
        "(a|a)(a|a)(a|a)(a|a)(a|a)(a|a)(a|a)(a|a)(a|a)(a|a)b",
        "(a|aa)+",
        "(ab?)*",
        "(a{1,2})+",
        "(a*)+",
        "(\\d{1,3}\\.){3}\\d{1,3}",  # a counted repeat inside a repeated group: refused on purpose
        "a{1,1001}",
        "a{1001}",
        "a*a*a*a*b",
    ],
)
def test_strictly_refused_patterns(pattern: str) -> None:
    assert mt.is_safe_pattern(pattern) is False


@pytest.mark.parametrize(
    "pattern", [".*foo.*bar.*", "a*b*c*", "x.*", "(a*)", "(ab){3}", "[a-z]+-[0-9]{2,4}", "^a$"]
)
def test_harmless_patterns_stay_decidable(pattern: str) -> None:
    assert mt.is_safe_pattern(pattern) is True


@pytest.mark.parametrize(
    "pattern",
    [
        "[[:digit:]]+",
        "[[:alpha:]]",
        "(?>a)",
        "a*+",
        "a++",
        "a?+",
        "a{2}+",
        r"\p{L}",
        r"\pL",
        r"\Qa.b\E",
        r"\z",
        r"\A",
        r"\Z",
        r"\1",
        "(?i)é",
        "(?i:a)",
        "(?m)a",
        "(?x) a",
        "(?a)a",
        "(?#comment)a",
        "(?P<n>a)(?P=n)",
        "(?(1)a|b)",
        "(?<=a)b",
        "(?=a)a",
        "(?!a)b",
        "é|(?i)x",
    ],
)
def test_forms_where_python_and_re2_differ_are_undecidable(pattern: str) -> None:
    assert mt.is_safe_pattern(pattern) is False
    assert mt.matches("=~", pattern, "123") is None


def test_the_reviewers_cases() -> None:
    assert mt.matches("=~", "[[:digit:]]+", "123") is None  # Python reads it as a class plus `]+`
    assert mt.matches("=~", "(?i)é", "É") is None


def test_case_insensitive_matching_is_refused_for_non_ascii_values() -> None:
    assert mt.matches("=~", "(?i)abc", "ABC") is True
    assert (
        mt.matches("=~", "(?i)k", "\N{KELVIN SIGN}") is None
    )  # the Kelvin sign folds to a plain k in RE2


def test_a_python_warning_while_compiling_makes_the_pattern_unsafe() -> None:
    assert mt.is_safe_pattern("[[a]") is False


def test_named_groups_and_plain_flags_are_allowed() -> None:
    assert mt.is_safe_pattern("(?P<n>ab)")
    assert mt.is_safe_pattern("(?s)a.b")


def test_the_parser_api_is_present_on_this_python() -> None:
    import sys

    assert sys.version_info >= (3, 12)
    assert mt.PARSER_AVAILABLE is True


def test_the_gate_fails_closed_when_the_parser_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mt, "PARSER_AVAILABLE", False)
    mt.is_safe_pattern.cache_clear()
    try:
        assert mt.is_safe_pattern("a") is False
    finally:
        mt.is_safe_pattern.cache_clear()


def test_any_pattern_the_gate_accepts_finishes_fast_on_adversarial_subjects() -> None:
    rng = random.Random(7)  # noqa: S311 - a fixed seed for a repeatable test
    atoms = [
        "a", "b", "a?", "a*", "b+", "(a|b)", "(ab)", "(ab)*", "(ab){2}", "a{2,5}", ".*", "[ab]",
        "[ab]*", "(a?)", "(a*)", "(a|a)", ".", "x", "a+", "(a?a?)", "(a+)", "b?", "(ab?)",
    ]  # fmt: skip
    subjects = ["a" * 40, "ab" * 20, "a" * 39 + "b", "b" * 40, ("a" * 10 + "c") * 3 + "ab"]
    accepted = 0
    for _ in range(1500):
        pattern = "".join(rng.choice(atoms) for _ in range(rng.randint(1, 9)))
        try:
            re.compile(pattern)
        except re.error:
            continue
        if not mt.is_safe_pattern(pattern):
            continue
        accepted += 1
        for subject in subjects:
            start = time.perf_counter()
            mt.matches("=~", pattern, subject)
            assert time.perf_counter() - start < 0.5, pattern
    assert accepted > 100  # the property is not vacuous


def test_express_accepts_only_escapes_it_knows() -> None:
    assert mt.express(r"a\.b") == ("literals", ("a.b",))
    assert mt.express(r"a\d") is None
    assert mt.express(r"a\q") is None
    assert mt.express("a|b|") == ("literals", ("a", "b", ""))
