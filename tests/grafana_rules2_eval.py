"""A tiny evaluator, to show that a converted NRQL query means what the PromQL meant.

Data is a list of rows of the `Metric` event, each a dict of attributes. Counters are stored the
way New Relic stores them: the change in each interval (a delta), one number per row. The
evaluator reads only the NRQL shapes the Grafana converter emits: `SELECT <expr> FROM Metric
[WHERE a = 'x' AND b IN ('p', 'q') ...] [FACET a, b]`, where <expr> is made of sum, average, min,
max and count of one attribute, numbers, `+ - * /` and parentheses. It returns one number per facet
(the key is the tuple of facet values, empty without FACET). A window with no rows gives no key.
"""

import re
from collections.abc import Callable
from typing import Any

Row = dict[str, Any]
FUNCTIONS: dict[str, Callable[[list[float]], float]] = {
    "sum": sum,
    "average": lambda v: sum(v) / len(v),
    "min": min,
    "max": max,
    "count": sum,
}
QUERY = re.compile(
    r"SELECT (?P<select>.+?) FROM Metric(?: WHERE (?P<where>.+?))?(?: FACET (?P<facet>.+))?$"
)
CLAUSE = re.compile(r"(\w+) (=|!=|IN|NOT IN|IS NULL|IS NOT NULL)(?: (.+))?$")


def _matches(row: Row, clause: str) -> bool:
    found = CLAUSE.fullmatch(clause.strip())
    if found is None:
        raise ValueError(f"clause not understood: {clause}")
    key, op, rest = found.groups()
    value = row.get(key)
    if op == "IS NULL":
        return value is None
    if op == "IS NOT NULL":
        return value is not None
    items = re.findall(r"'((?:[^'\\]|\\.)*)'", rest)
    hit = value in items if op in ("IN", "NOT IN") else value == items[0]
    return hit if op in ("=", "IN") else (value is not None and not hit)


def _value(select: str, rows: list[Row]) -> float | None:
    """Evaluate the SELECT expression over the rows of one facet."""

    def call(match: re.Match[str]) -> str:
        name, attribute = match[1], match[2].strip("`")
        values = (
            [1.0] * len(rows)
            if attribute == "*"
            else [float(r[attribute]) for r in rows if attribute in r]
        )
        if not values:
            raise LookupError
        return repr(FUNCTIONS[name](values))

    try:
        text = re.sub(r"(sum|average|min|max|count)\(([^)]*)\)", call, select)
    except LookupError:
        return None
    if re.search(r"[^0-9eE+\-*/(). ]", text):
        raise ValueError(f"select not understood: {select}")
    try:
        return float(eval(text, {"__builtins__": {}}))  # noqa: S307 - digits and operators only
    except ZeroDivisionError:
        return None


def evaluate(nrql: str, rows: list[Row]) -> dict[tuple[str, ...], float]:
    found = QUERY.fullmatch(nrql)
    if found is None:
        raise ValueError(f"query not understood: {nrql}")
    clauses = re.split(r" AND ", found["where"]) if found["where"] else []
    kept = [r for r in rows if all(_matches(r, c) for c in clauses)]
    facets = [f.strip().strip("`") for f in found["facet"].split(",")] if found["facet"] else []
    groups: dict[tuple[str, ...], list[Row]] = {}
    for row in kept:
        if all(f in row for f in facets):
            groups.setdefault(tuple(str(row[f]) for f in facets), []).append(row)
    out: dict[tuple[str, ...], float] = {}
    for key, members in groups.items():
        value = _value(found["select"], members)
        if value is not None:
            out[key] = value
    return out


def rate(deltas: list[float], range_seconds: int) -> float:
    """PromQL rate() of one counter over a range, without extrapolation: the increase over the
    range divided by its length. A delta-stored counter has no resets to adjust for."""
    return sum(deltas) / range_seconds
