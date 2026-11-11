"""What a Cloud Logging filter (a log-match condition) means as a count of New Relic Log events.

Reads the same grammar as the metric filters (`filter.py`): AND, implicit AND, OR over one field
(`severity=ERROR OR severity=CRITICAL` becomes an IN list), NOT, parentheses, `:` (contains),
`=~` (search regex, when it is a literal, prefix, suffix or list), severity ranges and
`log_id("...")`. What a count of filters cannot say (an OR between different fields, a regular
expression with real syntax) stays manual with the reason.
"""

from importer.core.emit.nrql import identifier
from importer.core.ir import CountFilter
from importer.sources.gcp.filter import (
    FilterError,
    Group,
    Term,
    conjuncts,
    parse_expression,
)
from importer.sources.gcp.scope import ScopeFail, match_of

# Cloud Logging severity levels, lowest to highest
SEVERITIES = [
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
]
SEVERITY_OPS = {">=", ">", "<=", "<"}
LOG_ID_NOTE = (
    "log_id() is matched on the end of the logName attribute (Google URL-encodes the slash in a "
    "log id, and a LIKE pattern cannot hold the percent sign, so the match is on what follows "
    "it); the logName attribute on forwarded logs is not confirmed"
)


def _severity(term: Term) -> CountFilter | None:
    if term.op not in SEVERITY_OPS or term.value.upper() not in SEVERITIES:
        return None
    first = SEVERITIES.index(term.value.upper())
    levels = {
        ">=": SEVERITIES[first:],
        ">": SEVERITIES[first + 1 :],
        "<=": SEVERITIES[: first + 1],
        "<": SEVERITIES[:first],
    }[term.op]
    return CountFilter(key="severity", value="", values=levels, negate=term.negate)


def _log_id(term: Term) -> CountFilter:
    if len(term.values) != 1:
        raise ScopeFail("log filter comparison on 'log_id' is not supported (one id expected)")
    log_id = term.values[0]
    if "*" in log_id or "%" in log_id:
        raise ScopeFail("log filter comparison on 'log_id' is not supported (wildcard in the id)")
    tail = log_id.rsplit("/", 1)[-1]
    pattern = f"*2F{tail}" if "/" in log_id else f"*/logs/{log_id}"
    return CountFilter(key="logName", value=pattern, wildcard=True, negate=term.negate)


def _term(term: Term) -> CountFilter:
    if term.op == "func":
        return _log_id(term)
    if term.op == "variable":
        raise ScopeFail("log filter not supported: a dashboard variable in a log filter")
    if (found := _severity(term)) is not None:
        return found
    try:
        m = match_of(term)
    except ScopeFail as err:
        raise ScopeFail(str(err).replace("filter term on", "log filter comparison on", 1)) from err
    return CountFilter(
        key=_key(term.field),
        value=m.value,
        values=m.values,
        wildcard=m.wildcard,
        negate=m.negate,
    )


def _key(name: str) -> str:
    try:
        identifier(name)
    except ValueError:
        raise ScopeFail(
            f"log filter not supported: the field {name!r} does not make a valid New Relic "
            "attribute name"
        ) from None
    return name


def _or_group(group: Group) -> CountFilter:
    terms = [i for i in group.items if isinstance(i, Term)]
    fields = {t.field for t in terms}
    if len(terms) != len(group.items) or len(fields) != 1:
        raise ScopeFail(
            "log filter not supported: an OR between different fields; a count of Log events "
            "joins its filters with AND, so write the OR by hand in the WHERE clause"
        )
    if any(t.negate or t.op not in ("=", "in") for t in terms):
        raise ScopeFail(
            "log filter not supported: an OR that is not a list of equal values of one field"
        )
    values: list[str] = []
    for t in terms:
        for v in t.values if t.op == "in" else (t.value,):
            if v not in values:
                values.append(v)
    return CountFilter(key=_key(next(iter(fields))), value="", values=values)


def log_filters(text: str) -> tuple[list[CountFilter], list[str]]:
    """The filters of a log-match condition and notes; raises ScopeFail when unreadable."""
    try:
        parts = conjuncts(parse_expression(text))
    except FilterError as err:
        raise ScopeFail(f"log filter not supported: {err}") from err
    filters: list[CountFilter] = []
    notes: list[str] = []
    for part in parts:
        if isinstance(part, Group):
            filters.append(_or_group(part))
            continue
        filters.append(_term(part))
        if part.op == "func":
            notes.append(LOG_ID_NOTE)
    return filters, notes
