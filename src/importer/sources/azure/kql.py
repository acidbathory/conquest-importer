"""The small part of KQL (Kusto) that has a New Relic form.

Accepted:  `Table | where <terms joined by and> [| summarize [x =] <agg>(...) [by bin(TimeGenerated,
W)[, column...]]]`, with terms `col == "text"`, `!=`, `contains`, `has`, `startswith`, `endswith`
(each also with a leading `!`) and `col in ("a", "b")` / `!in`, and `TimeGenerated > ago(...)`
(dropped: the rule's window is the time range). Anything else is refused with the reason, because a
condition query cannot hold joins, subqueries, `let`, `union`, regular expressions or several
aggregations (docs/research/azure-monitor-alerting.md section 4.5).

The query is untrusted text. Nothing from it is formatted into NRQL here: the parser returns plain
values and column names that match a strict pattern; the emitter quotes and validates them.
"""

import re
from dataclasses import dataclass, field

from importer.sources.azure.models import iso_seconds

MAX_QUERY = 4000
IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,100}"
STRING = r'"([^"\\\n]{0,500})"|\'([^\'\\\n]{0,500})\''
_TERM_CMP = re.compile(
    rf"({IDENT})\s*(==|!=|!contains|contains|!has|has|!startswith|startswith|!endswith|endswith"
    rf"|=~|!~)\s*(?:{STRING})\s*",
)
_TERM_IN = re.compile(
    rf"({IDENT})\s*(!in|in)\s*\(\s*((?:{STRING})(?:\s*,\s*(?:{STRING}))*)\s*\)\s*"
)
_TERM_TIME = re.compile(r"TimeGenerated\s*(>|>=)\s*ago\s*\(\s*\d+[smhd]\s*\)\s*", re.IGNORECASE)
_STRING_ONLY = re.compile(STRING)
_SUMMARIZE = re.compile(
    rf"summarize\s+(?:{IDENT}\s*=\s*)?(count)\s*\(\s*\)\s*(?:by\s+(.+))?$"
    rf"|summarize\s+(?:{IDENT}\s*=\s*)?(avg|sum|min|max)\s*\(\s*({IDENT})\s*\)\s*(?:by\s+(.+))?$",
    re.IGNORECASE,
)
_BIN = re.compile(r"bin\s*\(\s*TimeGenerated\s*,\s*(\w+)\s*\)", re.IGNORECASE)
_KQL_SPAN = re.compile(r"^(\d+)([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class KqlError(Exception):
    """The query has a form with no New Relic equivalent; the message says which."""


@dataclass(frozen=True)
class Term:
    column: str
    op: str  # eq, contains, startswith, endswith, in
    values: tuple[str, ...]
    negate: bool = False


@dataclass(frozen=True)
class Summarize:
    function: str  # count, avg, sum, min, max
    column: str  # the measured column, "" for count
    bin_seconds: int | None
    by: tuple[str, ...] = ()


@dataclass
class Query:
    table: str
    terms: list[Term] = field(default_factory=list)
    summarize: Summarize | None = None
    dropped_time_filter: bool = False


def _split_top(text: str, sep: str) -> list[str]:
    """Split on `sep` outside string literals (and, for a comma, outside parentheses)."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(text):
                current.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in "()" and sep != "|":
            depth += 1 if ch == "(" else -1
            current.append(ch)
        elif text.startswith(sep, i) and depth == 0:
            parts.append("".join(current))
            current = []
            i += len(sep) - 1
        else:
            current.append(ch)
        i += 1
    if quote:
        raise KqlError("a string in the query is not closed")
    parts.append("".join(current))
    return parts


def _literal(text: str) -> str:
    found = _STRING_ONLY.fullmatch(text.strip())
    if found is None:
        raise KqlError("a text value uses an escape or is too long")
    value = found[1] if found[1] is not None else found[2]
    if "*" in value or "%" in value:
        raise KqlError("a text value holds * or %, which are wildcards in New Relic")
    return value


def _terms(text: str) -> tuple[list[Term], bool]:
    terms: list[Term] = []
    dropped = False
    for piece in re.split(r"\s+and\s+", _strip_outer(text), flags=re.IGNORECASE):
        if re.search(r"\s+or\s+|[()]", re.sub(STRING, "", piece), re.IGNORECASE) and not (
            _TERM_IN.fullmatch(piece) or _TERM_TIME.fullmatch(piece)
        ):
            raise KqlError("the where clause uses or, or parentheses")
        if _TERM_TIME.fullmatch(piece):
            dropped = True
            continue
        if (m := _TERM_IN.fullmatch(piece)) is not None:
            raw = _split_top(m[3], ",")
            terms.append(Term(m[1], "in", tuple(_literal(v) for v in raw), m[2] == "!in"))
        elif (m := _TERM_CMP.fullmatch(piece)) is not None:
            op = m[2]
            if op in ("=~", "!~"):
                raise KqlError("a case-insensitive comparison (=~) has no New Relic form")
            value = _literal(piece[m.end(2) :])
            negate = op.startswith("!") or op == "!="
            name = {"==": "eq", "!=": "eq"}.get(op, op.lstrip("!"))
            terms.append(Term(m[1], name, (value,), negate))
        else:
            raise KqlError("a where term is not a comparison of a column with a text value")
    return terms, dropped


def _strip_outer(text: str) -> str:
    return text.strip()


def _span(token: str) -> int | None:
    token = token.strip()
    found = _KQL_SPAN.match(token)
    if found:
        return int(found[1]) * _UNITS[found[2]]
    return iso_seconds(token)


def _summarize(text: str) -> Summarize:
    found = _SUMMARIZE.fullmatch(text.strip())
    if found is None:
        raise KqlError("the summarize is not count() or avg, sum, min or max of one column")
    function = (found[1] or found[3]).lower()
    column = found[4] or ""
    by_text = found[2] or found[5] or ""
    bin_seconds: int | None = None
    by: list[str] = []
    for item in [i.strip() for i in _split_top(by_text, ",")] if by_text else []:
        binned = _BIN.fullmatch(item)
        if binned:
            bin_seconds = _span(binned[1])
            if bin_seconds is None:
                raise KqlError("the bin size is not a time span like 5m")
        elif re.fullmatch(IDENT, item):
            by.append(item)
        else:
            raise KqlError("the summarize groups by an expression, not a column")
    return Summarize(function, column, bin_seconds, tuple(by))


def parse(text: str) -> Query:
    """The accepted query, or KqlError saying what has no New Relic form."""
    if len(text) > MAX_QUERY:
        raise KqlError("the query is longer than the importer reads")
    if ";" in re.sub(STRING, "", text):
        raise KqlError("the query has several statements (let or a function)")
    stages = [s.strip() for s in _split_top(text, "|")]
    head = stages[0]
    if not re.fullmatch(IDENT, head):
        raise KqlError("the query does not start with a plain table name")
    query = Query(table=head)
    for index, stage in enumerate(stages[1:], start=1):
        word = stage.split(None, 1)[0].lower() if stage else ""
        if word == "where":
            if query.summarize is not None:
                raise KqlError("a where comes after the summarize")
            terms, dropped = _terms(stage[len("where") :])
            query.terms += terms
            query.dropped_time_filter = query.dropped_time_filter or dropped
        elif word == "summarize" and index == len(stages) - 1:
            query.summarize = _summarize(stage)
        else:
            raise KqlError(f"the operator {word or '(empty)'!r} has no New Relic form")
    return query
