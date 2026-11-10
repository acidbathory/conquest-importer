"""Read the converted NRQL as a timeseries from New Relic (read-only NerdGraph NRQL)."""

import math
from collections.abc import Callable
from typing import Any

from importer.core.text import printable
from importer.parity.model import Fetched, ParityError, Series
from importer.parity.queries import facet_names, timeseries_over

MAX_REQUESTS = 1000
MAX_SERIES = 200
MAX_ROWS = 400_000
MAX_LABEL = 200
MAX_BUCKETS = 360  # NRQL returns at most 366 buckets per timeseries
TIME_KEYS = {"beginTimeSeconds", "endTimeSeconds", "facet", "timestamp"}
Rows = Callable[[str], list[dict[str, Any]]]


def check_buckets(hours: float, step: int) -> None:
    buckets = hours * 3600 / step
    if buckets > MAX_BUCKETS:
        raise ParityError(
            f"{buckets:.0f} buckets is more than New Relic returns ({MAX_BUCKETS}); "
            f"raise --step to at least {math.ceil(hours * 3600 / MAX_BUCKETS)} or lower --hours"
        )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


class NewRelicReader:
    def __init__(self, rows: Rows) -> None:
        self._rows = rows
        self.requests = 0

    def __call__(self, nrql: str, start: int, end: int, step: int) -> Fetched:
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise ParityError(f"more than {MAX_REQUESTS} New Relic queries; stopped")
        rows = self._rows(timeseries_over(nrql, start, end, step))
        return parse_rows(rows, facet_names(nrql))


def parse_rows(rows: list[dict[str, Any]], facets: list[str]) -> Fetched:
    """Series from NRQL timeseries rows. One value per bucket; more than one is refused."""
    truncated = len(rows) > MAX_ROWS
    by_label: dict[tuple[str, ...], Series] = {}
    value_keys: set[str] = set()
    for row in rows[:MAX_ROWS]:
        begin = _number(row.get("beginTimeSeconds"))
        if begin is None:
            continue
        raw = row.get("facet")
        parts = [str(p) for p in raw] if isinstance(raw, list) else ([str(raw)] if raw else [])
        key = tuple(printable(p, MAX_LABEL) for p in parts)
        names = facets if len(facets) == len(key) else [f"facet{i + 1}" for i in range(len(key))]
        if key not in by_label:
            if len(by_label) >= MAX_SERIES:
                truncated = True
                continue
            by_label[key] = Series(
                {printable(n, MAX_LABEL): v for n, v in zip(names, key, strict=True)}
            )
        for name, value in row.items():
            if name in TIME_KEYS or name in facets:
                continue
            value_keys.add(name)
            number = _number(value)
            if number is not None:
                by_label[key].points[round(begin)] = number
    if len(value_keys) > 1:
        raise ParityError(
            "the converted query returns several values per bucket; only one can be compared"
        )
    return Fetched(list(by_label.values()), truncated)
