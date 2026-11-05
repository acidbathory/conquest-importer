"""Parse the filter strings in Cloud Monitoring and Cloud Logging conditions.

Both languages join terms with AND; the converter accepts only `field OP value` terms (values in
double quotes or bare words) so a filter it cannot read stays manual instead of turning into a
query that means something else.
"""

import re
from dataclasses import dataclass

_TERM = re.compile(
    r"""\s*(?P<not>NOT\s+|-)?
        (?P<field>[A-Za-z_][\w.\-]*)\s*
        (?P<op>>=|<=|!=|=~|!~|=|:|>|<)\s*
        (?P<value>"(?:[^"\\]|\\.)*"|[^\s"()]+)""",
    re.VERBOSE,
)
_AND = re.compile(r"\s+AND\s+|\s+")


class FilterError(ValueError):
    """The filter uses something the converter does not read."""


@dataclass(frozen=True)
class Term:
    field: str
    op: str
    value: str
    negate: bool = False


def parse_filter(text: str) -> list[Term]:
    terms: list[Term] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        match = _TERM.match(text, pos)
        if match is None:
            raise FilterError(f"cannot read {text[pos : pos + 40]!r}")
        value = match["value"]
        if value.startswith('"'):
            value = re.sub(r"\\(.)", r"\1", value[1:-1])
        elif value.upper() in {"OR", "AND", "NOT"}:
            raise FilterError("the filter uses OR")
        terms.append(Term(match["field"], match["op"], value, bool(match["not"])))
        pos = match.end()
        sep = _AND.match(text, pos)
        if sep and pos < len(text):
            if text[sep.end() :].startswith(("OR ", "OR\t")):
                raise FilterError("the filter uses OR")
            pos = sep.end()
    if not terms:
        raise FilterError("the filter is empty")
    return terms
