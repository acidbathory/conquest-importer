"""The one place that borrows from the alert translator (`sources/grafana/translate.py`).

The alert translator has no public function for "read this selector". The coverage planner
(`dashboards/query_tier.py`) borrows its private context and failure types; so does this
module, and only this module, so that a public function there can replace these few lines
(see the proposal). Everything here returns plain data and raises `Unconvertible`.
"""

from dataclasses import dataclass

from importer.sources.grafana.translate import (
    BODY,
    COUNTER_SUFFIXES,
    HANDLERS,
    MAX_EXPRESSION,
    PROM_MATCHER,
    PROM_NOT_SIMPLE,
    PROM_SELECTOR,
    QUOTED,
    _Ctx,
    _Fail,
    _matchers,
    _outer,
)

NAME = r"[A-Za-z_:][A-Za-z0-9_:]*"
__all__ = [
    "BODY",
    "COUNTER_SUFFIXES",
    "MAX_EXPRESSION",
    "NAME",
    "QUOTED",
    "Cond",
    "LokiQuery",
    "Unconvertible",
    "loki_query",
    "outer",
    "prom_conditions",
    "selector",
]


class Unconvertible(Exception):
    """The query cannot be written; `reason` is one of the fixed texts of `reasons.py`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Cond:
    """One label condition: `kind` is eq, in, like or absent."""

    key: str
    kind: str
    values: tuple[str, ...] = ()
    negate: bool = False


def outer(text: str, aggregators: str, fail: str) -> tuple[str | None, list[str], str]:
    """`agg [by (..)] (inner) [by (..)]` split into aggregator, labels and inner text."""
    try:
        return _outer(text, aggregators)
    except _Fail:
        raise Unconvertible(fail) from None


def selector(inner: str, fail: str) -> tuple[str, str]:
    """Metric name and the text between the braces."""
    found = PROM_SELECTOR.fullmatch(inner)
    if found is None:
        raise Unconvertible(fail)
    return found[1], found[2] or ""


def prom_conditions(body: str, fail: str) -> list[Cond]:
    """Label matchers of a metric selector, in the forms the alert translator reads."""
    try:
        found = _matchers(body, PROM_MATCHER, PROM_NOT_SIMPLE, absent_ok=True)
    except _Fail:
        raise Unconvertible(fail) from None
    return [Cond(m.key, m.kind, m.values, m.negate) for m in found]


@dataclass(frozen=True)
class LokiQuery:
    conditions: list[Cond]
    group_by: list[str]
    seconds: int


def loki_query(text: str, fail: str) -> LokiQuery:
    """A `count_over_time` of a stream selector with line filters (what the alert reader reads)."""
    ctx = _Ctx(reducer="last", instant=True, range_seconds=None)
    try:
        built = HANDLERS["loki"](text, ctx)
    except _Fail:
        raise Unconvertible(fail) from None
    signal = built.count
    if signal is None:
        raise Unconvertible(fail)
    conditions = [
        Cond(
            f.key,
            "in" if f.values else ("like" if f.wildcard else "eq"),
            tuple(f.values) if f.values else (f.value,),
            f.negate,
        )
        for f in signal.filters
    ]
    return LokiQuery(conditions, list(signal.group_by), built.window or 0)
