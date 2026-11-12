"""Offline check of New Relic's `average(g) * cardinality(g)` against PromQL `sum(g) by (k)`.

Evidence only, and circular like the rest of the differential harness: the NRQL side is our model
of cardinality() (UNVERIFIED against a live account, assumptions in diff_nrql._cardinality).
Series are seeded; every number below is reproducible.
"""

import random
import statistics
from collections.abc import Callable
from dataclasses import dataclass

from importer.parity.diff_nrql import parse_nrql, run_nrql, run_nrql_buckets
from importer.parity.diff_promql import evaluate, parse_promql
from importer.parity.diff_series import Series, Vector

SEEDS = (1, 2, 3, 4, 5)
SPAN = 3600
TOLERANCE = 0.05
SUM_NRQL = "SELECT average(g) * cardinality(g) FROM Metric FACET k TIMESERIES {b} seconds"
SUM_ALL_NRQL = "SELECT average(g) * cardinality(g) FROM Metric TIMESERIES {b} seconds"
COUNT_NRQL = "SELECT cardinality(g) FROM Metric FACET k TIMESERIES {b} seconds"

Maker = Callable[[random.Random], list[Series]]


def rng_for(seed: int) -> random.Random:
    # Synthetic test data, never a secret: seeded for reproducibility.
    return random.Random(seed)  # noqa: S311


def gauge(
    rng: random.Random,
    k: str,
    pod: str,
    *,
    every: int = 15,
    base: float | None = None,
    trend: float = 0.0,
    noise: float = 0.0,
    live: tuple[int, int] = (0, SPAN),
    holes: float = 0.0,
) -> Series:
    base = rng.uniform(10.0, 100.0) if base is None else base
    pts: list[tuple[int, float]] = []
    for t in range(every, SPAN + 1, every):
        if not (live[0] < t <= live[1]) or rng.random() < holes:
            continue
        pts.append((t, round(base * (1 + trend * t / SPAN) * (1 + rng.uniform(-noise, noise)), 6)))
    return Series("g", (("k", k), ("pod", pod)), tuple(pts))


def steady(rng: random.Random) -> list[Series]:
    return [gauge(rng, k, f"{k}{i}", noise=0.001) for k in "ab" for i in range(3)]


def varying(rng: random.Random) -> list[Series]:
    return [gauge(rng, k, f"{k}{i}", trend=0.6, noise=0.2) for k in "ab" for i in range(3)]


def mixed_cadence(rng: random.Random) -> list[Series]:
    return [
        gauge(rng, k, f"{k}{i}", every=15 if i < 2 else 60, noise=0.001)
        for k in "ab"
        for i in range(3)
    ]


def gaps_and_stale(rng: random.Random) -> list[Series]:
    out = [gauge(rng, k, f"{k}{i}", noise=0.001, holes=0.1) for k in "ab" for i in range(3)]
    out.append(gauge(rng, "a", "dies", noise=0.001, live=(0, 2000)))
    out.append(gauge(rng, "b", "dies", noise=0.001, live=(0, 2110)))
    return out


def churn(rng: random.Random) -> list[Series]:
    out: list[Series] = []
    for k in "ab":
        for i in range(3):
            out.append(gauge(rng, k, f"{k}{i}old", noise=0.001, live=(0, 1800)))
            out.append(gauge(rng, k, f"{k}{i}new", noise=0.001, live=(1800, SPAN)))
    return out


def uneven_facets(rng: random.Random) -> list[Series]:
    counts = {"a": 1, "b": 3, "c": 8}
    return [gauge(rng, k, f"{k}{i}", noise=0.001) for k, n in counts.items() for i in range(n)]


@dataclass
class Stats:
    median: float
    p95: float
    worst: float
    missing: int  # buckets where only one side has the group
    n: int


def _compare(
    data: list[Series], nrql: str, promql: str, bucket: int, start: int = 900
) -> tuple[list[float], int]:
    query = parse_nrql(nrql.format(b=bucket))
    node = parse_promql(promql)
    got = run_nrql_buckets(query, data, start, SPAN, bucket)
    errs: list[float] = []
    missing = 0
    for t, vec in got.items():
        want: Vector = evaluate(node, data, t)
        if (want == {}) != (vec == {}) or vec.keys() != want.keys():
            missing += 1
            errs.append(1.0)
            continue
        errs.extend(abs(vec[k] - want[k]) / abs(want[k]) for k in want if want[k])
    return errs, missing


def measure(
    make: Maker, nrql: str, promql: str, bucket: int, seeds: tuple[int, ...] = SEEDS
) -> Stats:
    errs: list[float] = []
    missing = 0
    for seed in seeds:
        e, m = _compare(make(rng_for(seed)), nrql, promql, bucket)
        errs += e
        missing += m
    ordered = sorted(errs)
    return Stats(
        statistics.median(ordered),
        ordered[int(0.95 * (len(ordered) - 1))],
        ordered[-1],
        missing,
        len(ordered),
    )


def verdict(s: Stats, steady_values: bool) -> str:
    """The harness ladder. WRONG is reserved for steady values (the harness's clean data): there
    the two sides must agree, so a median beyond tolerance is a wrong value, not an edge."""
    if s.worst < 1e-9:
        return "IDENTICAL"
    if s.worst <= TOLERANCE and not s.missing:
        return "EQUAL_WITHIN_TOLERANCE"
    if steady_values and s.median > TOLERANCE:
        return "WRONG"
    return "DIFFERS_AT_EDGES"


def test_cardinality_counts_distinct_series_per_facet_and_bucket() -> None:
    data = steady(rng_for(1))
    got = run_nrql(parse_nrql(COUNT_NRQL.format(b=300)), data, 900, 300)
    assert got == {(("k", "a"),): 3.0, (("k", "b"),): 3.0}
    got_all = run_nrql(parse_nrql("SELECT cardinality(g) FROM Metric"), data, 900, 300)
    assert got_all == {(): 6.0}


def test_cardinality_ignores_series_without_a_sample_in_the_bucket() -> None:
    data = churn(rng_for(1))
    before = run_nrql(parse_nrql(COUNT_NRQL.format(b=300)), data, 900, 300)
    assert before[(("k", "a"),)] == 3.0  # only the old pods report
    straddle = run_nrql(parse_nrql(COUNT_NRQL.format(b=300)), data, 1860, 300)
    assert straddle[(("k", "a"),)] == 6.0  # both generations reported inside the bucket


def test_buckets_are_deterministic_per_seed() -> None:
    a = measure(varying, SUM_NRQL, "sum(g) by (k)", 60, (7,))
    assert a == measure(varying, SUM_NRQL, "sum(g) by (k)", 60, (7,))


def test_latest_on_identity_labels_equals_the_per_series_value() -> None:
    """The cheap alternative: latest(g) FACET k, pod is exact per series, but the sum over pods
    needs a second aggregation level (a sub-query), which this model and our emitter refuse."""
    data = steady(rng_for(1))
    nr = run_nrql(parse_nrql("SELECT latest(g) FROM Metric FACET k, pod"), data, 900, 15)
    prom = evaluate(parse_promql("g"), data, 900)
    assert len(nr) == len(prom) == 6
    for key, value in prom.items():
        assert nr[tuple(sorted(key))] == value


SUM = "sum(g) by (k)"


def test_a_steady_uniform_cadence_is_within_tolerance() -> None:
    for nrql, prom in ((SUM_NRQL, SUM), (SUM_ALL_NRQL, "sum(g)")):
        s = measure(steady, nrql, prom, 60)
        assert verdict(s, True) == "EQUAL_WITHIN_TOLERANCE"
    assert verdict(measure(steady, COUNT_NRQL, "count(g) by (k)", 60), True) == "IDENTICAL"


def test_b_values_varying_inside_the_bucket_differ_from_the_instant_sum() -> None:
    s = measure(varying, SUM_NRQL, SUM, 60)
    assert verdict(s, False) == "DIFFERS_AT_EDGES"
    assert 0.10 < s.p95 < 0.15 and s.worst < 0.20


def test_c_different_scrape_intervals_bias_the_average_toward_fast_series() -> None:
    s = measure(mixed_cadence, SUM_NRQL, SUM, 60)
    assert verdict(s, True) == "WRONG"
    assert 0.05 < s.median < 0.12 and 0.13 < s.worst < 0.17
    # the bias does not shrink with a longer bucket: 5m has the same 4:1 sample ratio
    assert measure(mixed_cadence, SUM_NRQL, SUM, 300).median > TOLERANCE


def test_d_series_that_stop_mid_bucket_break_at_the_edges() -> None:
    s = measure(gaps_and_stale, SUM_NRQL, SUM, 60)
    assert verdict(s, False) == "DIFFERS_AT_EDGES"
    # PromQL keeps a stopped series for 5m of lookback; the bucket forgets it at once
    assert s.worst > 0.4
    assert measure(gaps_and_stale, SUM_NRQL, SUM, 300).worst < 0.15


def test_e_churn_matters_only_when_the_bucket_is_shorter_than_the_lookback() -> None:
    assert verdict(measure(churn, SUM_NRQL, SUM, 60), False) == "DIFFERS_AT_EDGES"
    assert measure(churn, SUM_NRQL, SUM, 60).worst > 0.5
    assert verdict(measure(churn, SUM_NRQL, SUM, 300), True) == "EQUAL_WITHIN_TOLERANCE"


def test_f_bucket_size_alone_does_not_matter_for_steady_values() -> None:
    for bucket in (60, 300):
        assert verdict(measure(steady, SUM_NRQL, SUM, bucket), True) == "EQUAL_WITHIN_TOLERANCE"
    # but with varying values the 5m bucket averages over more of the trend
    assert measure(varying, SUM_NRQL, SUM, 300).median > TOLERANCE


def test_g_unequal_series_counts_per_facet_are_handled_by_cardinality() -> None:
    s = measure(uneven_facets, SUM_NRQL, SUM, 60)
    assert verdict(s, True) == "EQUAL_WITHIN_TOLERANCE"
