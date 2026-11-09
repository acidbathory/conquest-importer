"""The one DQL shape that converts: a log count.

    fetch logs[, from:now() - N(m|h)] | filter <test> [and <test> ...] | summarize <name> = count()

A test is one of: `field == "v"`, `contains(field, "v")`, `matchesValue(field, "v")`,
`matchesValue(field, {"a", "b"})`, `in(field, "a", "b")` or `in(field, array("a", "b"))`.
Anything else raises DqlError with a fixed reason (never the query text), so the workflow stays
manual. Every pattern is length-bounded or unambiguous, so hostile text cannot make it backtrack.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from importer.core.ir import CountFilter

NOT_NARROW = "DQL query is not the supported pattern (fetch logs, equality filters, one count)"
CONTAINS_NOTE = (
    "contains() is case-sensitive in Dynatrace and maps to a LIKE match; how LIKE treats "
    "case in New Relic was not checked, so matches may differ"
)
MEMBER_NOTE = (
    "matchesValue() and in() may ignore case or look inside arrays in Dynatrace, while = and IN "
    "in New Relic are exact, so matches may differ"
)
CONTRADICTION_NOTE = (
    "the same field is tested against two different values joined by and, so nothing can "
    "match; the converted condition may differ from what was intended"
)
FILTER_FORM = (
    f'{NOT_NARROW}: the filter has a test other than field == "value", contains(), '
    "matchesValue() or in() joined by and, or a value with a control character or an escape "
    "other than a quote or backslash"
)
CONTAINS_BAD = (
    f"{NOT_NARROW}: a contains() text that is empty or has %, * or _ is not mapped, because "
    "those would act as LIKE wildcards"
)
WILDCARD_MATCH = (
    f"{NOT_NARROW}: matchesValue() with a * is not mapped, because its wildcard rules were "
    "not checked"
)
BY_REASON = (
    f"{NOT_NARROW}: the summarize step has a by: grouping; the workflow reads only the first "
    "record, which a New Relic FACET condition does not reproduce"
)
TOO_LONG = f"{NOT_NARROW}: the query is longer than {{limit}} characters"
MAX_QUERY = 20_000
MAX_TERMS = 20
MAX_VALUES = 50
SECONDS_PER_UNIT = {"m": 60, "h": 3600}

_STR = r'"(?:[^"\\\x00-\x1f\x7f]|\\["\\])*"'  # a value: no control characters, escapes \" and \\
_FIELD = r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)"  # no empty part: not a. or a..b
_LIST = rf"({_STR}(?:\s*,\s*{_STR})*)"
_STRINGS = re.compile(_STR)
FETCH = re.compile(r"fetch\s+logs(?:\s*,\s*from:\s*now\(\)\s*-\s*(\d+)\s*([mh]))?")
SUMMARIZE = re.compile(r"summarize\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*count\(\)")
SUMMARIZE_BY = re.compile(r"summarize\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*count\(\)\s*,\s*by\s*:")
_HEAD = re.compile(r"filter\s+")
_AND = re.compile(r"\s+(?:and|AND)\s+")


@dataclass(frozen=True)
class CountQuery:
    """What the pattern means: log filters, the summarize name, the range, and notes."""

    filters: list[CountFilter]
    name: str
    lookback_seconds: int
    notes: list[str] = field(default_factory=list)


class DqlError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _strings(raw: str) -> list[str]:
    """The values of a matched string or list. Only the escapes \\" and \\\\ can occur."""
    found = [re.sub(r'\\(["\\])', r"\1", m[1:-1]) for m in _STRINGS.findall(raw)]
    if len(found) > MAX_VALUES:
        raise DqlError(f"{NOT_NARROW}: a list has more than {MAX_VALUES} values")
    return found


def _eq(name: str, raw: str) -> CountFilter:
    return CountFilter(key=name, value=_strings(raw)[0])


def _contains(name: str, raw: str) -> CountFilter:
    (text,) = _strings(raw)
    if not text or any(c in text for c in "%*_"):
        raise DqlError(CONTAINS_BAD)
    return CountFilter(key=name, value=f"*{text}*", wildcard=True)


def _match(name: str, raw: str) -> CountFilter:
    (text,) = _strings(raw)
    if "*" in text:
        raise DqlError(WILDCARD_MATCH)
    return CountFilter(key=name, value=text)


def _members(name: str, raw: str, *, wildcard_check: bool) -> CountFilter:
    values = _strings(raw)
    if wildcard_check and any("*" in v for v in values):
        raise DqlError(WILDCARD_MATCH)
    return CountFilter(key=name, value=values[0], values=values)


def _match_set(name: str, raw: str) -> CountFilter:
    return _members(name, raw, wildcard_check=True)


def _in(name: str, raw: str) -> CountFilter:
    return _members(name, raw, wildcard_check=False)


# first match wins; the function forms come before `field == "v"`. The flag marks the
# membership tests that carry MEMBER_NOTE.
TERMS: list[tuple[re.Pattern[str], Callable[[str, str], CountFilter], bool]] = [
    (re.compile(rf"contains\(\s*{_FIELD}\s*,\s*({_STR})\s*\)"), _contains, False),
    (re.compile(rf"matchesValue\(\s*{_FIELD}\s*,\s*({_STR})\s*\)"), _match, True),
    (re.compile(rf"matchesValue\(\s*{_FIELD}\s*,\s*\{{\s*{_LIST}\s*\}}\s*\)"), _match_set, True),
    (re.compile(rf"in\(\s*{_FIELD}\s*,\s*array\(\s*{_LIST}\s*\)\s*\)"), _in, True),
    (re.compile(rf"in\(\s*{_FIELD}\s*,\s*{_LIST}\s*\)"), _in, True),
    (re.compile(rf"{_FIELD}\s*==\s*({_STR})"), _eq, False),
]


def _term(text: str, pos: int) -> tuple[CountFilter, int, bool]:
    for pattern, build, member in TERMS:
        if match := pattern.match(text, pos):
            return build(match[1], match[2]), match.end(), member
    raise DqlError(FILTER_FORM)


def _contradiction(filters: list[CountFilter]) -> bool:
    """True when one field is tested for equality against two different values (and-joined)."""
    equal: dict[str, str] = {}
    for f in filters:
        if f.values or f.wildcard or f.negate:
            continue
        if equal.setdefault(f.key, f.value) != f.value:
            return True
    return False


def parse_filter(text: str) -> tuple[list[CountFilter], list[str]]:
    """The tests of a `filter ...` step, joined by and, and the notes they call for."""
    head = _HEAD.match(text)
    if head is None:
        raise DqlError(FILTER_FORM)
    filters: list[CountFilter] = []
    notes: list[str] = []
    pos = head.end()
    while len(filters) < MAX_TERMS:
        built, pos, member = _term(text, pos)
        filters.append(built)
        if member and MEMBER_NOTE not in notes:
            notes.append(MEMBER_NOTE)
        joint = _AND.match(text, pos)
        if joint is None:
            if pos != len(text):
                raise DqlError(FILTER_FORM)
            if _contradiction(filters):
                notes.append(CONTRADICTION_NOTE)
            return filters, notes
        pos = joint.end()
    raise DqlError(f"{NOT_NARROW}: the filter has more than {MAX_TERMS} tests")


def split_pipes(query: str) -> list[str]:
    """Split on `|` outside double-quoted strings."""
    parts: list[str] = []
    current: list[str] = []
    quoted = escaped = False
    for char in query:
        if char == "|" and not quoted:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
    parts.append("".join(current).strip())
    return parts


def parse_dql(query: str) -> CountQuery:
    """Parse the log-count pattern, or raise DqlError with a fixed reason."""
    if len(query) > MAX_QUERY:
        raise DqlError(TOO_LONG.format(limit=MAX_QUERY))
    parts = split_pipes(query)
    if len(parts) != 3:
        raise DqlError(f"{NOT_NARROW}: it needs exactly a fetch, a filter and a summarize step")
    fetch = FETCH.fullmatch(parts[0])
    if fetch is None or (fetch[1] is not None and int(fetch[1]) == 0):
        raise DqlError(
            f"{NOT_NARROW}: the first step is not fetch logs with an hour or minute range"
        )
    filters, notes = parse_filter(parts[1])
    summarize = SUMMARIZE.fullmatch(parts[2])
    if summarize is None:
        if SUMMARIZE_BY.match(parts[2]):
            raise DqlError(BY_REASON)
        raise DqlError(f"{NOT_NARROW}: the last step is not a single summarize name = count()")
    amount, unit = fetch.groups()
    seconds = int(amount) * SECONDS_PER_UNIT[unit] if amount else 0
    if any(f.wildcard for f in filters):
        notes.insert(0, CONTAINS_NOTE)
    return CountQuery(filters, summarize[1], seconds, notes)
