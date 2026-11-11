"""Two aggregation stages as one.

Cloud Monitoring lets a query align and reduce twice: a `perSeriesAligner` and
`crossSeriesReducer` first, then a secondary stage (`secondaryAggregation` in a dashboard, a
second entry of `aggregations` in an alert condition). New Relic evaluates one aggregation per
query, and an alert condition cannot nest queries, so the two stages are merged where that gives
the same numbers and refused, with the reason, where it does not:

- The second stage does not re-align (`ALIGN_NONE` or none): the reducer simply runs over the
  series the first stage produced, at the first stage's timestamps.
- The second stage re-aligns over the same period as the first (periods below New Relic's 300s
  poll count as 300s): each series then has one point per period, which `ALIGN_MEAN`, `_MAX`,
  `_MIN` and `_SUM` leave as it is. `ALIGN_NEXT_OLDER` and `ALIGN_INTERPOLATE` only move that
  point onto the period boundary, so the value is the same and a note says the time shifts.
- The reducers compose: none after a reducer changes nothing; a reducer after none is the
  merged reducer; the same sum, max or min over coarser groups equals one over those groups.
"""

POLL_SECONDS = 300
IDENTITY_ALIGNERS = ("ALIGN_MEAN", "ALIGN_MAX", "ALIGN_MIN", "ALIGN_SUM")
BOUNDARY_ALIGNERS = ("ALIGN_NEXT_OLDER", "ALIGN_INTERPOLATE")
COMPOSABLE_REDUCERS = ("REDUCE_SUM", "REDUCE_MAX", "REDUCE_MIN")
NONE = ("", "REDUCE_NONE")


class StageError(Exception):
    """The two stages cannot be written as one query; `args[0]` says why."""


def _period(agg: dict[str, object]) -> int:
    raw = str(agg.get("alignmentPeriod") or "")
    digits = raw[:-1]
    seconds = int(digits) if raw.endswith("s") and digits.isascii() and digits.isdecimal() else 0
    return max(seconds, POLL_SECONDS)


def _groups(agg: dict[str, object]) -> list[str]:
    raw = agg.get("groupByFields")
    return [str(g) for g in raw] if isinstance(raw, list) else []


def merge_stages(
    first: dict[str, object], second: dict[str, object]
) -> tuple[dict[str, object], list[str]]:
    """The single stage that equals `first` followed by `second`, and notes; StageError if none."""
    aligner = str(second.get("perSeriesAligner") or "")
    notes: list[str] = []
    if aligner not in ("", "ALIGN_NONE"):
        if aligner not in (*IDENTITY_ALIGNERS, *BOUNDARY_ALIGNERS):
            raise StageError(
                f"second-stage aggregation re-aligns with {aligner}, which changes the values "
                "of the first stage; it needs a nested query, which a New Relic alert condition "
                "does not allow and a chart does only as a separate query"
            )
        if _period(second) != _period(first):
            raise StageError(
                f"second-stage aggregation re-aligns over {_period(second)}s after a first stage "
                f"over {_period(first)}s, so each second-stage point mixes several first-stage "
                "points; that needs a nested query, which a New Relic alert condition does not "
                "allow (in a chart, chart the first stage and apply the second to it by hand)"
            )
        if aligner in BOUNDARY_ALIGNERS:
            notes.append(
                f"the second stage {aligner} only moves each point onto the period boundary; New "
                "Relic charts the point at its own time"
            )
    reducer = str(second.get("crossSeriesReducer") or "")
    first_reducer = str(first.get("crossSeriesReducer") or "")
    merged: dict[str, object] = dict(first)
    if reducer in NONE:
        return merged, notes
    if first_reducer in NONE:
        merged["crossSeriesReducer"] = reducer
        merged["groupByFields"] = _groups(second)
        return merged, notes
    if reducer == first_reducer and reducer in COMPOSABLE_REDUCERS:
        if set(_groups(second)) <= set(_groups(first)):
            merged["groupByFields"] = _groups(second)
            return merged, notes
        raise StageError(
            f"second-stage {reducer} groups by labels the first stage already reduced away"
        )
    raise StageError(
        f"second-stage {reducer} after {first_reducer} is a reduction of a reduction that is not "
        "the same as one reduction (a mean of means, a sum of maxima); it needs a nested query, "
        "which a New Relic alert condition does not allow"
    )
