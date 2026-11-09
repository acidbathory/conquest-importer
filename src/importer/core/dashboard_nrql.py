"""Checks on NRQL that a translator hands to the dashboard IR, and on {{variable}} references.

These are pure validators. They never build NRQL: a query string passed in here was already built
by the translator, with every untrusted value escaped through quote_string or identifier. What the
checks add is structure: one SELECT statement, none of the clauses the dashboard owns, and
variables only where New Relic substitutes them.
"""

import re

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
_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|`[^`]*`")
_CLAUSE = re.compile(
    r"\b(COMPARE\s+WITH|SLIDE\s+BY|ORDER\s+BY|SELECT|FROM|WHERE|FACET|LIMIT|SINCE|UNTIL"
    r"|TIMESERIES|AS|OFFSET|WITH)\b",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _blank(match: re.Match[str]) -> str:
    return "_" * len(match.group(0))


def _masked(nrql: str) -> str:
    """The query with string literals, quoted names and variable references blanked out.

    Positions are kept, so a variable found in the original text can be placed in its clause.
    """
    return _VARIABLE.sub(_blank, _QUOTED.sub(_blank, nrql))


def _clause_name(text: str) -> str:
    return " ".join(text.upper().split())


def check_variable_usage(nrql: str) -> list[str]:
    """Return the variable names used, once each in order; raise when one is in a bad clause."""
    masked = _masked(nrql)
    if "{{" in masked or "}}" in masked:
        raise ValueError("malformed {{variable}} reference")
    clauses = [(m.start(), _clause_name(m.group(1))) for m in _CLAUSE.finditer(masked)]
    # Variables inside a string are found in the original text, outside it in either.
    names: list[str] = []
    for ref in _VARIABLE.finditer(nrql):
        current = ""
        for start, name in clauses:
            if start >= ref.start():
                break
            current = name
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
    """Raise ValueError unless this is one SELECT statement fit for a dashboard widget."""
    if not nrql.strip():
        raise ValueError("NRQL is empty")
    if len(nrql) > MAX_NRQL_LENGTH:
        raise ValueError(f"NRQL is longer than {MAX_NRQL_LENGTH} characters")
    if _CONTROL.search(nrql):
        raise ValueError("control characters are not allowed in NRQL")
    masked = _masked(nrql)
    if "'" in masked or "`" in masked:
        raise ValueError("NRQL has an unbalanced quote")
    if ";" in masked:
        raise ValueError("a semicolon is not allowed in NRQL")
    first = _CLAUSE.match(masked.lstrip())
    if first is None or _clause_name(first.group(1)) not in ("SELECT", "FROM"):
        raise ValueError("NRQL must start with SELECT or FROM")
    found = {_clause_name(m.group(1)) for m in _CLAUSE.finditer(masked)}
    for clause in RAW_FORBIDDEN_CLAUSES:
        if clause in found:
            raise ValueError(f"{clause} is not allowed: the dashboard time picker sets the window")
    check_variable_usage(nrql)
