"""Alertmanager matcher semantics, with regular expressions that cannot run away.

`=` and `!=` compare exact strings. `=~` and `!~` match the whole value (the pattern is
anchored at both ends, as `^(?:pattern)$`). A missing label is the empty string. Alertmanager
uses RE2, which cannot backtrack badly; Python's `re` can, and differs from RE2 in places. So a
pattern is run only when the stdlib parser shows it is in a small safe subset (see
`is_safe_pattern`). Anything else returns None, which callers report as undecidable instead of
guessing. The gate is strict on purpose: it refuses some harmless patterns, such as an
IPv4 address pattern with a counted group, because a repeated group that holds a repeat
cannot be told apart from `(a?a?)*b` by a cheap rule. There is no wall-clock guard on a
match (that needs a thread or a process); the gate and the length caps are the only protection.

The parser is `re._parser`, a private module of CPython 3.11 and later. If it cannot be
imported, every pattern is refused.
"""

import importlib
import re
import warnings
from functools import lru_cache
from typing import Any

try:
    _parser: Any = importlib.import_module("re._parser")
    _c: Any = importlib.import_module("re._constants")
    PARSER_AVAILABLE = True
except ImportError:  # pragma: no cover - a Python without the private modules
    PARSER_AVAILABLE = False

MAX_PATTERN = 256
MAX_VALUE = 256  # longest label value a pattern is run against
MAX_VARIABLE = (
    3  # repeats whose count is not fixed (`*`, `+`, `?`, `{n,m}`): polynomial in the value
)
MAX_BRANCHES = 3  # alternations in one pattern: sequential ones multiply the paths
MAX_BOUND = 1000  # RE2's own limit on a repeat count
ALLOWED_FLAGS = re.IGNORECASE | re.DOTALL | re.UNICODE
_ATOMS = {"LITERAL", "NOT_LITERAL", "IN", "ANY", "CATEGORY", "AT"}
_REPEATS = {"MAX_REPEAT", "MIN_REPEAT"}
_META = set(".^$*+?{}[]|()\\")
# RE2 reads these differently or not at all; Python would accept some of them silently
_DIFFERENT = ("[[:", "[:", "(?>", "*+", "++", "?+", "}+", "\\p", "\\P", "\\Q", "\\E", "\\z",
              "\\A", "\\Z", "\\C", "\\X")  # fmt: skip
_GROUP_OPENERS = re.compile(r"\(\?(?!:|P<)")


class _Stats:
    def __init__(self) -> None:
        self.variable = 0
        self.branches = 0


def _plain(items: Any) -> bool:
    """Only atoms and groups of atoms: nothing that repeats or branches."""
    for op, av in items:
        name = str(op)
        if name in _ATOMS:
            continue
        if name == "SUBPATTERN" and not av[1] and not av[2] and _plain(av[3]):
            continue
        return False
    return True


def _walk(items: Any, stats: _Stats) -> bool:
    for op, av in items:
        name = str(op)
        if name in _ATOMS:
            continue
        if name == "SUBPATTERN":
            _group, add_flags, del_flags, body = av
            if add_flags or del_flags or not _walk(body, stats):
                return False
        elif name == "BRANCH":
            stats.branches += 1
            if not all(_walk(alternative, stats) for alternative in av[1]):
                return False
        elif name in _REPEATS:
            low, high, body = av
            unbounded = high == _c.MAXREPEAT
            if low > MAX_BOUND or (not unbounded and high > MAX_BOUND):
                return False
            stats.variable += low != high
            if not (_plain(body) and body.getwidth()[0] > 0):
                return False  # a repeated group that holds a repeat, a branch, or can be empty
        else:
            return False  # backreference, lookaround, conditional, atomic or possessive form
    return True


def _raw_ok(pattern: str) -> bool:
    """Checks on the text: forms the parser reads like Python but RE2 does not."""
    if any(form in pattern for form in _DIFFERENT):
        return False
    if re.search(r"\\[0-9]", pattern):
        return False
    return not _GROUP_OPENERS.search(
        pattern.replace("(?i)", "", 1)
        .replace("(?s)", "", 1)
        .replace("(?is)", "", 1)
        .replace("(?si)", "", 1)
    )


@lru_cache(maxsize=2048)
def is_safe_pattern(pattern: str) -> bool:
    """True for a pattern that is valid, bounded and cannot backtrack badly.

    Refused: backreferences, lookaround, conditionals, atomic and possessive forms, scoped
    flags, flags other than i and s, `(?i)` with non-ASCII text, a repeated group that holds a
    repeat of any kind or a branch or can match nothing, a repeat count over 1000, more than
    three repeats with a variable count, more than three alternations, and the forms in
    `_DIFFERENT`.
    """
    if not PARSER_AVAILABLE or len(pattern) > MAX_PATTERN or not _raw_ok(pattern):
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            parsed = _parser.parse(pattern)
            re.compile(pattern, re.ASCII)
    except (re.error, RecursionError, OverflowError, Warning):
        return False
    flags = parsed.state.flags
    if flags & ~ALLOWED_FLAGS or (flags & re.IGNORECASE and not pattern.isascii()):
        return False
    stats = _Stats()
    return (
        _walk(parsed, stats) and stats.variable <= MAX_VARIABLE and stats.branches <= MAX_BRANCHES
    )


@lru_cache(maxsize=2048)
def _compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.ASCII)


def matches(op: str, value: str, label: str | None) -> bool | None:
    """Does the matcher hold for a label value (None means the label is missing)?

    None when a pattern is outside the safe subset or the value is too long to test, or when a
    case-insensitive pattern meets a non-ASCII value (RE2 folds case by Unicode rules).
    """
    actual = label or ""
    if op == "=":
        return actual == value
    if op == "!=":
        return actual != value
    if not is_safe_pattern(value) or len(actual) > MAX_VALUE:
        return None
    if not actual.isascii() and "(?i" in value:
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
