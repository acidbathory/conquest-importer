"""Alertmanager matcher semantics, with regular expressions that cannot run away.

`=` and `!=` compare exact strings. `=~` and `!~` match the whole value (the pattern is
anchored at both ends, as `^(?:pattern)$`). A missing label is the empty string. Alertmanager
uses RE2, which cannot backtrack badly; Python's `re` can, so a pattern is run only when it is
in a safe subset (see `is_safe_pattern`). Anything else returns None, which callers report as
undecidable instead of guessing.
"""

import re
from functools import lru_cache

MAX_PATTERN = 256
MAX_VALUE = 512  # longest label value a pattern is run against
MAX_UNBOUNDED = 2  # `*`, `+` and `{n,}` in one pattern: more can be polynomial in the value
MAX_BOUND = 1000  # RE2's own limit on a repeat count
_QUANTIFIERS = "*+{"
_META = set(".^$*+?{}[]|()\\")
_BOUND = re.compile(r"\{(\d+)(?:,(\d*))?\}")
_LOOKAROUND = ("(?=", "(?!", "(?<", "(?P=", "(?P>")


@lru_cache(maxsize=2048)
def is_safe_pattern(pattern: str) -> bool:
    """True for a pattern that is valid, bounded and cannot backtrack exponentially.

    Refused: backreferences, lookaround, repeat counts over 1000, more than two unbounded
    repeats, and a repeated group that holds a repeat or an alternation (`(a+)+`, `(a|b)*`).
    """
    if len(pattern) > MAX_PATTERN:
        return False
    try:
        re.compile(pattern, re.ASCII)
    except (re.error, RecursionError, OverflowError):
        return False
    return _scan(pattern)


def _mark(groups: list[bool]) -> None:
    if groups:
        groups[-1] = True


def _count_ok(count: re.Match[str]) -> tuple[bool, bool]:
    """(within the repeat limit, has no upper bound) for a `{n}`, `{n,}` or `{n,m}` repeat."""
    low, high = count.group(1), count.group(2)
    top = low if high is None and "," not in count.group(0) else high
    within = int(low) <= MAX_BOUND and (not top or int(top) <= MAX_BOUND)
    return within, not top


def _scan(pattern: str) -> bool:
    groups: list[bool] = []  # per open group: does it hold a repeat or an alternation?
    unbounded = 0
    in_class = False
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        step = 1
        if ch == "\\":
            if pattern[i + 1 : i + 2] in tuple("123456789"):
                return False  # backreference
            step = 2
        elif in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            if pattern.startswith(_LOOKAROUND, i):
                return False
            groups.append(False)
        elif ch == ")":
            risky = groups.pop() if groups else False
            if risky and pattern[i + 1 : i + 2] and pattern[i + 1] in _QUANTIFIERS:
                return False  # a repeated group holding a repeat or an alternation
            if risky:
                _mark(groups)
        elif ch == "|":
            _mark(groups)
        elif ch in "*+":
            unbounded += 1
            _mark(groups)
        elif ch == "{" and (count := _BOUND.match(pattern, i)):
            within, open_ended = _count_ok(count)
            if not within:
                return False
            unbounded += open_ended
            _mark(groups)
            step = count.end() - i
        i += step
    return unbounded <= MAX_UNBOUNDED


@lru_cache(maxsize=2048)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.ASCII)


def matches(op: str, value: str, label: str | None) -> bool | None:
    """Does the matcher hold for a label value (None means the label is missing)?

    None when a pattern is outside the safe subset or the value is too long to test.
    """
    actual = label or ""
    if op == "=":
        return actual == value
    if op == "!=":
        return actual != value
    if not is_safe_pattern(value) or len(actual) > MAX_VALUE:
        return None
    found = _compiled(value).fullmatch(actual) is not None
    return found if op == "=~" else not found


# --- patterns that a New Relic workflow filter can express ---

_ESCAPABLE = set(".^$*+?{}[]|()\\-/ ,:@#=%!~<>\"'")


def _literal(text: str) -> str | None:
    """The text a regex part matches when it has no operators; None if it has any."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            if i + 1 >= len(text) or text[i + 1] not in _ESCAPABLE:
                return None
            out.append(text[i + 1])
            i += 2
            continue
        if ch in _META:
            return None
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_group(pattern: str) -> str:
    """`(a|b)` or `(?:a|b)` as `a|b` when one group wraps the whole pattern."""
    for opener in ("(?:", "("):
        if pattern.startswith(opener) and pattern.endswith(")"):
            depth = 0
            for i, ch in enumerate(pattern):
                if pattern[i - 1 : i] == "\\":
                    continue
                depth += (ch == "(") - (ch == ")")
                if depth == 0 and i < len(pattern) - 1:
                    return pattern
            return pattern[len(opener) : -1]
    return pattern


def express(pattern: str) -> tuple[str, tuple[str, ...]] | None:
    """('any'|'prefix'|'suffix'|'literals', values) for a pattern a filter can say, else None."""
    if not is_safe_pattern(pattern):
        return None
    body = _strip_group(pattern)
    if body == ".*":
        return "any", ()
    if body.endswith(".*") and (text := _literal(body[:-2])) is not None and text:
        return "prefix", (text,)
    if body.startswith(".*") and (text := _literal(body[2:])) is not None and text:
        return "suffix", (text,)
    parts = [_literal(part) for part in body.split("|")]
    if all(p is not None for p in parts):
        return "literals", tuple(p for p in parts if p is not None)
    return None
