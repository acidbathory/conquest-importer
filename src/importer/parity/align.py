"""Align a source answer with a New Relic answer and judge how well they agree.

Bucket alignment rules (the reason a naive point-by-point comparison is wrong):

1. New Relic `TIMESERIES <step> SECONDS SINCE s UNTIL e` returns one value per bucket
   [b, b + step), labelled with its begin time `b`, aggregated over every sample in the bucket
   (gauges: the average or sum of samples; counters sent as deltas: the sum of deltas, which
   `rate(sum(x), 1 second)` divides by the bucket length).
2. Prometheus `query_range` with the same step returns, for each evaluation time t, an instant
   vector: the latest sample at or before t for a gauge, and for `rate(x[W])` the rate over
   (t - W, t]. The point at t therefore describes the interval that ENDS at t. It is moved to
   the bucket b = t - step before comparing.
3. The window start and end are whole multiples of the step, so both sides use the same grid.
4. The first bucket is dropped (the data, or a long rate window, may start inside it, so it is
   partial) and so is the last (it may still be filling when the answer was read).
5. A point missing on either side is skipped, not counted as zero.

Two series are the same series when their label sets are equal after Prometheus names are mapped
to New Relic attributes (`__name__` dropped, `metricName` renamed as the data path does it).
New Relic only has the labels in the FACET, so a New Relic label set that is a subset of exactly
one source series' labels also matches.
"""

import statistics
from dataclasses import dataclass

from importer.parity.model import Outcome, Series, SeriesResult, Verdict
from importer.sources.grafana.datapath.needs import label_attribute

VERIFIED_FRACTION = 0.95
CLOSE_FRACTION = 0.75
DEFAULT_TOLERANCE = 0.05
DEFAULT_ABS_TOLERANCE = 1e-9
MIN_POINTS = 10
MAX_SHIFT = 2  # buckets tried when looking for an offset


@dataclass(frozen=True)
class Window:
    start: int  # first bucket begin (a multiple of step)
    end: int  # end of the last bucket (a multiple of step)
    step: int


def window_for(now: int, hours: float, step: int) -> Window:
    end = now // step * step
    start = (end - int(hours * 3600)) // step * step
    return Window(start, end, step)


def prom_to_buckets(series: Series, step: int) -> Series:
    """The source series with each point moved to the bucket that ends at its time."""
    return Series(series.labels, {t - step: v for t, v in series.points.items()})


def map_labels(labels: dict[str, str]) -> dict[str, str]:
    """Source label names as New Relic attributes: `__name__` dropped, `metricName` renamed."""
    return {label_attribute(k)[0]: v for k, v in labels.items() if k != "__name__"}


def rel_error(a: float, b: float, abs_tol: float) -> float:
    diff = abs(a - b)
    if diff <= abs_tol:
        return 0.0
    return diff / max(abs(a), abs(b))


def _key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))


def _match(
    source: list[Series], nr: list[Series]
) -> tuple[list[tuple[Series, Series]], list[Series], list[Series]]:
    by_key = {_key(s.labels): s for s in source}
    pairs: list[tuple[Series, Series]] = []
    nr_left: list[Series] = []
    for n in nr:
        hit = by_key.pop(_key(n.labels), None)
        if hit is not None:
            pairs.append((hit, n))
        else:
            nr_left.append(n)
    still: list[Series] = []
    for n in nr_left:
        wanted = set(n.labels.items())
        covers = [k for k, s in by_key.items() if wanted <= set(k) and s is not None]
        if len(covers) == 1 and n.labels:
            pairs.append((by_key.pop(covers[0]), n))
        else:
            still.append(n)
    return pairs, list(by_key.values()), still


def _errors(
    a: Series, b: Series, drop: set[int], abs_tol: float, shift: int = 0, step: int = 0
) -> list[tuple[float, float, float]]:
    """(source, new relic, relative error) for every bucket present on both sides."""
    out: list[tuple[float, float, float]] = []
    for t, x in a.points.items():
        if t in drop:
            continue
        y = b.points.get(t + shift * step)
        if y is not None:
            out.append((x, y, rel_error(x, y, abs_tol)))
    return out


def _best_shift(
    pairs: list[tuple[Series, Series]], drop: set[int], w: Window, tol: float, abs_tol: float
) -> int:
    best, best_fraction = 0, 0.0
    for shift in range(-MAX_SHIFT, MAX_SHIFT + 1):
        if shift == 0:
            continue
        total = hit = 0
        for a, b in pairs:
            errs = _errors(a, b, drop, abs_tol, shift, w.step)
            total += len(errs)
            hit += sum(e <= tol for _, _, e in errs)
        fraction = hit / total if total >= MIN_POINTS else 0.0
        if fraction > best_fraction:
            best, best_fraction = shift, fraction
    return best if best_fraction >= VERIFIED_FRACTION else 0


def _ratio_cause(rows: list[tuple[float, float, float]]) -> str:
    ratios = [y / x for x, y, _ in rows if x and y and x * y > 0]
    if len(ratios) < MIN_POINTS:
        return ""
    middle = statistics.median(ratios)
    spread = [abs(r - middle) / middle for r in ratios]
    if statistics.median(spread) > 0.1 or abs(middle - 1) < 0.1:
        return ""
    return (
        f"New Relic is about {middle:.3g} times the source in every bucket (constant ratio): "
        "counter or gauge treated differently, a sum() of a gauge adding every sample, or "
        "a rate window or unit that differs"
    )


def judge(
    source: list[Series],
    nr: list[Series],
    w: Window,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    abs_tolerance: float = DEFAULT_ABS_TOLERANCE,
    min_points: int = MIN_POINTS,
    capped: bool = False,
) -> Outcome:
    """Compare the two answers. `source` points are timestamped as Prometheus returns them."""
    if not any(s.points for s in source) or not any(s.points for s in nr):
        side = "the source" if not any(s.points for s in source) else "New Relic"
        both = not any(s.points for s in source) and not any(s.points for s in nr)
        cause = "no data on either side" if both else f"no data from {side}"
        return Outcome(Verdict.NO_DATA, cause)
    moved = [Series(map_labels(s.labels), prom_to_buckets(s, w.step).points) for s in source]
    pairs, source_left, nr_left = _match(moved, nr)
    if not pairs:
        return Outcome(
            Verdict.MISMATCH,
            "label mismatch: no series on one side has the labels of a series on the other "
            f"(source {_names(source_left)}; New Relic {_names(nr_left)})",
            source_only=len(source_left),
            nr_only=len(nr_left),
        )
    drop = {w.start, w.end - w.step}
    results: list[SeriesResult] = []
    rows: list[tuple[float, float, float]] = []
    for a, b in pairs:
        errs = _errors(a, b, drop, abs_tolerance)
        rows += errs
        results.append(
            SeriesResult(
                b.labels,
                len(errs),
                sum(e <= tolerance for _, _, e in errs),
                statistics.median([e for _, _, e in errs]) if errs else None,
            )
        )
    points = len(rows)
    within = sum(e <= tolerance for _, _, e in rows)
    median = statistics.median([e for _, _, e in rows]) if rows else None
    out = Outcome(
        Verdict.MISMATCH,
        points=points,
        within=within,
        median_error=median,
        series=results,
        source_only=len(source_left),
        nr_only=len(nr_left),
    )
    if points < min_points:
        out.verdict = Verdict.NOT_COMPARABLE
        out.cause = f"only {points} aligned points (needs {min_points}); widen --hours"
        return out
    fraction = within / points
    extra = out.source_only + out.nr_only
    if fraction >= VERIFIED_FRACTION:
        out.verdict = Verdict.VERIFIED
    elif fraction >= CLOSE_FRACTION or (median is not None and median <= 2 * tolerance):
        out.verdict = Verdict.CLOSE
    if out.verdict is Verdict.MISMATCH:
        shift = _best_shift(pairs, drop, w, tolerance, abs_tolerance)
        if shift:
            out.cause = (
                f"bucket alignment: New Relic is offset by {shift:+d} bucket(s) of {w.step} s"
            )
        else:
            out.cause = _ratio_cause(rows) or "values differ and no single cause was found"
    if extra:
        if out.verdict is Verdict.VERIFIED:
            out.verdict = Verdict.CLOSE
        text = (
            f"missing series: {out.source_only} only on the source, {out.nr_only} only in New Relic"
        )
        out.cause = f"{out.cause}; {text}" if out.cause else text
    if capped:
        if out.verdict is Verdict.VERIFIED:
            out.verdict = Verdict.CLOSE
        text = "an answer was cut by a cap on series or points; the rest was not compared"
        out.cause = f"{out.cause}; {text}" if out.cause else text
    return out


def _names(series: list[Series]) -> str:
    names = sorted({k for s in series for k in s.labels})
    return ", ".join(names[:5]) or "no labels"
