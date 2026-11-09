"""Checks on NRQL that a translator hands to the dashboard IR, and on {{variable}} references.

These are pure validators. They never build NRQL: a query string passed in here was already built
by the translator, with every untrusted value escaped through quote_string or identifier. What the
checks add is structure: one SELECT statement, none of the clauses the dashboard owns, and
variables only where New Relic substitutes them.

Keywords are found in a copy of the query where string literals, backtick names and variable
references are blanked out. A keyword that is followed by a comparison operator (or IS, IN, LIKE,
...) or preceded by a dot is a column name, not a clause: `WHERE limit > {{v}}` is fine. A column
that has a keyword's name and is followed by something else (`SELECT as FROM Log`) is still read as
the clause; quote such a name with backticks.
"""

import re
from bisect import bisect_left

MAX_NRQL_LENGTH = 4096  # repository cap; New Relic's own limit is UNVERIFIED

# A dashboard's time picker owns the time window, so a widget query may not set its own.
# LIMIT is allowed (FACET ... LIMIT 20 is how a top-N chart is written) and so is TIMESERIES.
RAW_FORBIDDEN_CLAUSES = ("SINCE", "UNTIL", "COMPARE WITH")

# New Relic does not substitute a {{variable}} placed in these clauses (research note
# newrelic-notifications-and-nrql-reference.md, dashboard template variables).
VARIABLE_FORBIDDEN_CLAUSES = (
    "LIMIT",
    "SINCE",
    "TIMESERIES",
    "COMPARE WITH",
    "AS",
    "OFFSET",
    "SLIDE BY",
    "UNTIL",
    "WITH",
)

VARIABLE_NAME = r"[A-Za-z_][A-Za-z0-9_]{0,63}"
_VARIABLE = re.compile(r"\{\{\s*(" + VARIABLE_NAME + r")\s*\}\}")
# A string literal or a backtick name stops at a line break, so a stray quote is found.
_QUOTED = re.compile(r"'(?:[^'\\\n\r]|\\[^\n\r])*'|`[^`\n\r]*`")
_CLAUSE = re.compile(
    r"\b(COMPARE\s+WITH|SLIDE\s+BY|ORDER\s+BY|SELECT|FROM|WHERE|FACET|LIMIT|SINCE|UNTIL"
    r"|TIMESERIES|AS|OFFSET|WITH)\b",
    re.IGNORECASE,
)
_COLUMN_FOLLOWER = re.compile(
    r"\s*(?:=|!=|<>|<=|>=|<|>|,|\)|(?:IS|IN|NOT|LIKE|RLIKE|AND|OR)\b)", re.IGNORECASE
)
# Controls other than tab, line feed and carriage return; line and paragraph separators, NEL;
# bidirectional overrides and isolates; zero-width characters and the byte order mark.
_HIDDEN = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x85\u2028\u2029\u200b-\u200d\u2060\ufeff"
    r"\u202a-\u202e\u2066-\u2069]"
)
_SEMICOLONS = (";", "\uff1b")


def _blank(match: re.Match[str]) -> str:
    return "_" * len(match.group(0))


def _masked(nrql: str) -> str:
    """The query with string literals, quoted names and variable references blanked out.

    Positions are kept, so a variable found in the original text can be placed in its clause.
    """
    return _VARIABLE.sub(_blank, _QUOTED.sub(_blank, nrql))


def _clause_name(text: str) -> str:
    return " ".join(text.upper().split())


def _reject_hidden(nrql: str) -> None:
    if _HIDDEN.search(nrql):
        raise ValueError("control, bidirectional or zero-width characters are not allowed in NRQL")


def _clauses(masked: str) -> list[tuple[int, str]]:
    """(position, clause name) for every real clause keyword, in order."""
    found = []
    for m in _CLAUSE.finditer(masked):
        if m.start() > 0 and masked[m.start() - 1] == ".":
            continue  # attributes.limit
        if _COLUMN_FOLLOWER.match(masked, m.end()):
            continue  # a column that is named like a keyword
        found.append((m.start(), _clause_name(m.group(1))))
    return found


def check_variable_usage(nrql: str) -> list[str]:
    """Return the variable names used, once each in order; raise when one is in a bad clause."""
    _reject_hidden(nrql)
    masked = _masked(nrql)
    if "{" in masked or "}" in masked:
        raise ValueError("malformed {{variable}} reference")
    clauses = _clauses(masked)
    starts = [start for start, _ in clauses]
    # A variable inside a string belongs to the clause around the string.
    names: list[str] = []
    for ref in _VARIABLE.finditer(nrql):
        at = bisect_left(starts, ref.start()) - 1
        current = clauses[at][1] if at >= 0 else ""
        if current in VARIABLE_FORBIDDEN_CLAUSES:
            raise ValueError(f"a variable is not allowed in the {current} clause")
        if ref.group(1) not in names:
            names.append(ref.group(1))
    return names


def check_variables_defined(queries: list[str], defined: list[str]) -> None:
    """Raise when a query uses a variable that the dashboard does not define."""
    known = set(defined)
    for query in queries:
        for name in check_variable_usage(query):
            if name not in known:
                raise ValueError(f"variable '{name}' is used in a query but not defined")


def check_raw_nrql(nrql: str) -> None:
    """Raise ValueError unless this is one SELECT statement fit for a dashboard widget.

    Line breaks and tabs are allowed so that a pretty-printed query passes; they are only legal
    between tokens, because a string literal cannot contain a line break.
    """
    if not nrql.strip():
        raise ValueError("NRQL is empty")
    if len(nrql) > MAX_NRQL_LENGTH:
        raise ValueError(f"NRQL is longer than {MAX_NRQL_LENGTH} characters")
    _reject_hidden(nrql)
    masked = _masked(nrql)
    if "'" in masked or "`" in masked:
        raise ValueError("NRQL has an unbalanced quote")
    if any(s in masked for s in _SEMICOLONS):
        raise ValueError("a semicolon is not allowed in NRQL")
    first = _CLAUSE.match(masked.lstrip())
    if first is None or _clause_name(first.group(1)) not in ("SELECT", "FROM"):
        raise ValueError("NRQL must start with SELECT or FROM")
    found = {name for _, name in _clauses(masked)}
    for clause in RAW_FORBIDDEN_CLAUSES:
        if clause in found:
            raise ValueError(f"{clause} is not allowed: the dashboard time picker sets the window")
    check_variable_usage(nrql)
