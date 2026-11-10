"""Evaluate every pair on both sides and judge it; one cycle, or a rolling watch."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from importer.parity.align import (
    DEFAULT_ABS_TOLERANCE,
    DEFAULT_TOLERANCE,
    MIN_POINTS,
    judge,
    window_for,
)
from importer.parity.model import Fetched, Item, Outcome, Pair, ParityError, Verdict
from importer.parity.queries import (
    Unfilled,
    fill_nrql,
    fill_promql,
    longest_window,
)

MAX_CONSECUTIVE_FAILURES = 5
RATE_WORDS = ("rate(", "irate(", "increase(", "_over_time(", "delta(", "deriv(")
SourceFetch = Callable[[str, str, int, int, int], Fetched]
NrFetch = Callable[[str, int, int, int], Fetched]


@dataclass
class Settings:
    hours: float = 6
    step: int = 60
    tolerance: float = DEFAULT_TOLERANCE
    abs_tolerance: float = DEFAULT_ABS_TOLERANCE
    min_points: int = MIN_POINTS
    variables: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _not_comparable(pair: Pair, why: str) -> Item:
    return Item(pair, Outcome(Verdict.NOT_COMPARABLE, why))


def _rate_window_cause(pair: Pair, step: int, outcome: Outcome) -> None:
    window = longest_window(pair.original)
    if (
        outcome.verdict in (Verdict.MISMATCH, Verdict.CLOSE)
        and window is not None
        and window != step
        and any(word in pair.original for word in RATE_WORDS)
    ):
        text = (
            f"rate window: the query looks back {window} s but each New Relic bucket is {step} s, "
            "so the source is smoother or choppier; compare with --step equal to the window"
        )
        generic = "values differ and no single cause was found"
        outcome.cause = text if outcome.cause in ("", generic) else f"{outcome.cause}; {text}"


def evaluate(
    pair: Pair,
    now: int,
    settings: Settings,
    source: dict[str, SourceFetch | None],
    nr: NrFetch,
) -> Item:
    if pair.skip:
        return _not_comparable(pair, pair.skip)
    fetch = source.get(pair.family)
    if fetch is None:
        flag = "--source-loki" if pair.family == "loki" else "--source-prom"
        return _not_comparable(pair, f"no {pair.family} source was given ({flag})")
    variables = {**dict(pair.variables), **settings.variables}
    try:
        original = fill_promql(pair.original, variables, settings.step, settings.hours)
        nrql = fill_nrql(pair.nrql, variables)
    except Unfilled as exc:
        return _not_comparable(pair, str(exc))
    w = window_for(now, settings.hours, settings.step)
    try:
        theirs = fetch(pair.family, original, w.start + w.step, w.end, w.step)
        ours = nr(nrql, w.start, w.end, w.step)
    except ParityError as exc:
        raise _Failed(pair, str(exc)) from None
    outcome = judge(
        theirs.series,
        ours.series,
        w,
        tolerance=settings.tolerance,
        abs_tolerance=settings.abs_tolerance,
        min_points=settings.min_points,
        capped=theirs.truncated or ours.truncated,
    )
    _rate_window_cause(pair, settings.step, outcome)
    return Item(pair, outcome, original, nrql)


class _Failed(Exception):
    def __init__(self, pair: Pair, why: str) -> None:
        super().__init__(why)
        self.pair, self.why = pair, why


def run_once(
    pairs: list[Pair],
    now: int,
    settings: Settings,
    source: dict[str, SourceFetch | None],
    nr: NrFetch,
    progress: Callable[[int, int], None] | None = None,
) -> list[Item]:
    """Every pair judged. A read that fails is reported on its item; repeated failures stop."""
    items: list[Item] = []
    failures = 0
    for index, pair in enumerate(pairs):
        try:
            items.append(evaluate(pair, now, settings, source, nr))
            failures = 0
        except _Failed as failed:
            failures += 1
            items.append(_not_comparable(failed.pair, f"a read failed: {failed.why}"))
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise ParityError(
                    f"{failures} reads failed in a row (last: {failed.why}); stopped"
                ) from None
        if progress:
            progress(index + 1, len(pairs))
    return items


def watch(
    cycle: Callable[[int], None],
    every_seconds: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    max_cycles: int | None = None,
) -> int:
    """Run `cycle(now)` again and again until Ctrl-C; returns how many cycles completed."""
    done = 0
    try:
        while max_cycles is None or done < max_cycles:
            cycle(int(clock()))
            done += 1
            if max_cycles is not None and done >= max_cycles:
                break
            sleep(every_seconds)
    except KeyboardInterrupt:
        pass
    return done
