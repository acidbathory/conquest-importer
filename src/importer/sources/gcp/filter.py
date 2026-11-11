"""Parse the filter strings in Cloud Monitoring and Cloud Logging conditions.

Both languages join terms with AND; the converter accepts only `field OP value` terms (values in
double quotes or bare words) so a filter it cannot read stays manual instead of turning into a
query that means something else.
"""

import re
from dataclasses import dataclass

_TERM = re.compile(
    r"""\s*(?P<not>NOT\s+|-)?
        (?P<field>[A-Za-z_][\w.\-@]*(?:\."[^"\\]+")?)\s*
        (?P<op>>=|<=|!=|=~|!~|=|:|>|<)\s*
        (?P<value>one_of\((?:\s*"(?:[^"\\]|\\.)*"\s*,)*\s*"(?:[^"\\]|\\.)*"\s*\)
            |"(?:[^"\\]|\\.)*"|[^\s"()]+)""",
    re.VERBOSE,
)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_LOG_ID = re.compile(r'\s*log_id\(\s*"((?:[^"\\]|\\.)*)"\s*\)')
_AND = re.compile(r"\s+AND\s+|\s+")


class FilterError(ValueError):
    """The filter uses something the converter does not read."""


@dataclass(frozen=True)
class Term:
    field: str
    op: str
    value: str
    negate: bool = False
    values: tuple[str, ...] = ()  # one_of("a", "b"): the value is the first of these


def _unquote(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text[1:-1])


def parse_filter(text: str) -> list[Term]:
    """Terms joined by AND, optionally in parentheses (a group of ANDs only); log_id("x") and
    one_of("a", "b") are read. OR and NOT groups raise FilterError."""
    terms: list[Term] = []
    pos = 0
    depth = 0
    text = text.strip()
    while pos < len(text):
        while text[pos:].startswith("("):
            depth += 1
            pos = len(text) - len(text[pos + 1 :].lstrip())
        if (log_id := _LOG_ID.match(text, pos)) is not None:
            terms.append(Term("log_id", "()", _unquote(f'"{log_id[1]}"')))
            pos = log_id.end()
        else:
            match = _TERM.match(text, pos)
            if match is None:
                raise FilterError(f"cannot read {text[pos : pos + 40]!r}")
            terms.append(_term(match))
            pos = match.end()
        while text[pos:].lstrip().startswith(")"):
            depth -= 1
            if depth < 0:
                raise FilterError("the filter has an unmatched )")
            pos = len(text) - len(text[pos:].lstrip()[1:])
        sep = _AND.match(text, pos)
        if sep and pos < len(text):
            if text[sep.end() :].startswith(("OR ", "OR\t")):
                raise FilterError("the filter uses OR")
            pos = sep.end()
    if depth:
        raise FilterError("the filter has an unmatched (")
    if not terms:
        raise FilterError("the filter is empty")
    return terms


def _term(match: re.Match[str]) -> Term:
    value = match["value"]
    values: tuple[str, ...] = ()
    if value.startswith("one_of("):
        values = tuple(_unquote(m[0]) for m in _STRING.finditer(value))
        value = values[0]
    elif value.startswith('"'):
        value = _unquote(value)
    elif value.upper() in {"OR", "AND", "NOT"}:
        raise FilterError("the filter uses OR")
    field = match["field"].replace('."', ".").rstrip('"')
    return Term(field, match["op"], value, bool(match["not"]), values)
