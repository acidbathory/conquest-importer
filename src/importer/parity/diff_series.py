"""Seeded synthetic series for the offline PromQL/NRQL differential harness.

Series are what a scrape would see: counters are cumulative, gauges are plain values, histogram
buckets are cumulative counters with an `le` label. Scenarios add the awkward cases: counter
resets, missing samples, volatile gauges. Every random choice comes from `random.Random` seeded
from the caller's seed and the series identity, so a run is reproducible.
"""

import math
import random
import zlib
from dataclasses import dataclass, field
from itertools import product

SCRAPE = 15
SPAN = 7200
LES = ("0.1", "0.5", "1", "+Inf")
SCENARIOS = ("clean", "resets", "gaps", "volatile", "misaligned")
RESET_AT = (6345, 6840)
GAP = (6450, 6800)

Labels = tuple[tuple[str, str], ...]
Vector = dict[Labels, float]


class Unmodelled(Exception):
    """The reference or the NRQL model does not read this form; the harness says so."""


@dataclass(frozen=True)
class Series:
    name: str
    labels: Labels
    samples: tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class DataSpec:
    """metrics: name -> counter | gauge | histogram. labels: label -> values (cartesian)."""

    metrics: dict[str, str]
    labels: dict[str, list[str]] = field(default_factory=dict)


def delta_stored(name: str) -> bool:
    """New Relic stores these suffixes as delta counts (docs/research/telemetry-rig-results.md)."""
    return name.endswith(("_total", "_count", "_bucket", "_sum"))


def _rng(seed: int, *parts: str) -> random.Random:
    # Synthetic test data, never a secret: a seeded generator is what makes a run reproducible.
    return random.Random(zlib.crc32("|".join(parts).encode()) ^ (seed * 2654435761 % 2**32))  # noqa: S311


def _times(gap: bool, rng: random.Random, first: bool) -> list[int]:
    keep = list(range(SCRAPE, SPAN + 1, SCRAPE))
    if not gap:
        return keep
    cut = [t for t in keep if not (GAP[0] <= t < GAP[1]) or not first]
    return [t for t in cut if rng.random() > 0.04]


def _counter(rng: random.Random, times: list[int], scenario: str) -> list[tuple[int, float]]:
    base = rng.uniform(1.0, 20.0)
    noise = 0.5 if scenario == "volatile" else 0.05
    out: list[tuple[int, float]] = []
    value = rng.uniform(100.0, 1000.0)
    last = 0
    for t in times:
        step = (t - last) / SCRAPE
        value += base * SCRAPE * step * (1 + rng.uniform(-noise, noise))
        if scenario == "resets" and any(last < r <= t for r in RESET_AT):
            value = base * SCRAPE * rng.uniform(0.5, 1.5)
        out.append((t, round(value, 6)))
        last = t
    return out


def _gauge(
    rng: random.Random, name: str, times: list[int], scenario: str
) -> list[tuple[int, float]]:
    binary = name == "up" or "status" in name or name.endswith("condition")
    base = 1.0 if binary else rng.uniform(1.0, 100.0)
    phase = rng.uniform(0, 6.28)
    out: list[tuple[int, float]] = []
    for t in times:
        if scenario == "volatile":
            value = float(rng.choice([0, 1])) if binary else base * rng.uniform(0.2, 3.0)
        else:
            wave = 0.001 * (1 + (t % 900) / 900) if binary else 0.03
            value = base if binary else base * (1 + wave * math.sin(t / 900 + phase))
        out.append((t, round(value, 6)))
    return out


def _histogram(rng: random.Random, times: list[int]) -> dict[str, list[tuple[int, float]]]:
    """Cumulative buckets: each `le` holds the observations at or below it."""
    shares = [rng.uniform(0.1, 0.4) for _ in LES]
    rate = rng.uniform(5.0, 20.0)
    exclusive = [0.0] * len(LES)
    out: dict[str, list[tuple[int, float]]] = {le: [] for le in LES}
    last = 0
    for t in times:
        inc = rate * (t - last) * rng.uniform(0.95, 1.05)
        for i, share in enumerate(shares):
            exclusive[i] += inc * share / sum(shares)
        for i, le in enumerate(LES):
            out[le].append((t, round(sum(exclusive[: i + 1]), 6)))
        last = t
    return out


def generate(spec: DataSpec, scenario: str, seed: int) -> list[Series]:
    """All series of the spec for one scenario."""
    names = sorted(spec.labels)
    columns = [spec.labels[n] for n in names]
    combos = [dict(zip(names, vals, strict=True)) for vals in product(*columns)]
    made: list[Series] = []
    for name, kind in sorted(spec.metrics.items()):
        for idx, combo in enumerate(combos):
            labels: Labels = tuple(sorted(combo.items()))
            rng = _rng(seed, name, repr(labels))
            times = _times(scenario == "gaps", rng, idx == 0)
            if kind == "histogram":
                for le, samples in _histogram(rng, times).items():
                    made.append(Series(name, tuple(sorted((*labels, ("le", le)))), tuple(samples)))
            elif kind == "counter":
                made.append(Series(name, labels, tuple(_counter(rng, times, scenario))))
            else:
                made.append(Series(name, labels, tuple(_gauge(rng, name, times, scenario))))
    return made
