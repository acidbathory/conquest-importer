"""Whole-item evaluation with fake sources: the verdict cases the owner asked for."""

import pytest

from importer.parity.model import Fetched, Pair, ParityError, Series, Verdict
from importer.parity.runner import Settings, evaluate, run_once, watch

NOW = 1_700_000_100
STEP = 60
SETTINGS = Settings(hours=1, step=STEP)
START = NOW // STEP * STEP - 3600
PQ = "sum by (code) (rate(http_requests_total[5m]))"
NQ = "SELECT rate(sum(http_requests_total), 1 second) FROM Metric FACET code TIMESERIES AUTO"


def pair(original: str = PQ, nrql: str = NQ, **kw: object) -> Pair:
    return Pair("widget", "dash", "d1", "Requests", "prometheus", original, nrql, True, (), **kw)  # type: ignore[arg-type]


def src_series(values: list[float], labels: dict[str, str]) -> Series:
    return Series(labels, {START + (i + 1) * STEP: v for i, v in enumerate(values)})


def nr_series(values: list[float], labels: dict[str, str]) -> Series:
    return Series(labels, {START + i * STEP: v for i, v in enumerate(values)})


def fakes(prom: list[Series], nr: list[Series], truncated: bool = False):  # type: ignore[no-untyped-def]
    seen: dict[str, str] = {}

    def source(family: str, query: str, start: int, end: int, step: int) -> Fetched:
        seen["promql"] = query
        assert start == START + STEP and end == START + 3600 and step == STEP
        return Fetched(prom, truncated)

    def new_relic(nrql: str, start: int, end: int, step: int) -> Fetched:
        seen["nrql"] = nrql
        assert start == START and end == START + 3600
        return Fetched(nr, False)

    return {"prometheus": source}, new_relic, seen


def run(prom: list[Series], nr: list[Series], p: Pair | None = None, **kw: object):  # type: ignore[no-untyped-def]
    source, new_relic, _ = fakes(prom, nr, **kw)  # type: ignore[arg-type]
    return evaluate(p or pair(), NOW, SETTINGS, source, new_relic)


def ramp() -> list[float]:
    return [1.0 + (i % 7) for i in range(60)]


def test_exact_match_is_verified() -> None:
    lab = {"code": "200"}
    item = run([src_series(ramp(), lab)], [nr_series(ramp(), lab)])
    assert item.outcome.verdict is Verdict.VERIFIED and item.outcome.points == 58


def test_within_tolerance() -> None:
    lab = {"code": "200"}
    near = [v * 1.02 for v in ramp()]
    assert (
        run([src_series(ramp(), lab)], [nr_series(near, lab)]).outcome.verdict is Verdict.VERIFIED
    )


def test_bucket_misalignment_is_named() -> None:
    lab = {"code": "200"}
    late = [ramp()[0], *ramp()[:-1]]
    item = run([src_series(ramp(), lab)], [nr_series(late, lab)])
    assert item.outcome.verdict is Verdict.MISMATCH and "bucket alignment" in item.outcome.cause


def test_counter_reset_in_one_bucket_still_verifies() -> None:
    lab = {"code": "200"}
    broken = ramp()
    broken[20] = 0.0
    assert (
        run([src_series(ramp(), lab)], [nr_series(broken, lab)]).outcome.verdict is Verdict.VERIFIED
    )


def test_missing_series() -> None:
    a, b = {"code": "200"}, {"code": "500"}
    item = run([src_series(ramp(), a), src_series(ramp(), b)], [nr_series(ramp(), a)])
    assert item.outcome.verdict is Verdict.CLOSE and "missing series" in item.outcome.cause


def test_label_rename_via_metricname_rule() -> None:
    item = run([src_series(ramp(), {"metricName": "x"})], [nr_series(ramp(), {"metric_name": "x"})])
    assert item.outcome.verdict is Verdict.VERIFIED


def test_no_data_on_either_side_is_said_plainly() -> None:
    assert run([], [nr_series(ramp(), {})]).outcome.cause == "no data from the source"
    assert run([src_series(ramp(), {})], []).outcome.cause == "no data from New Relic"


def test_rate_window_is_named_when_values_differ_by_window() -> None:
    lab = {"code": "200"}
    noisy = [v * (1.6 if i % 2 else 0.4) for i, v in enumerate(ramp())]
    item = run([src_series(ramp(), lab)], [nr_series(noisy, lab)])
    assert item.outcome.verdict is Verdict.MISMATCH and "rate window" in item.outcome.cause


def test_huge_answer_is_capped_and_never_verified() -> None:
    lab = {"code": "200"}
    item = run([src_series(ramp(), lab)], [nr_series(ramp(), lab)], truncated=True)
    assert item.outcome.verdict is Verdict.CLOSE and "cap" in item.outcome.cause


def test_hostile_label_values_do_not_reach_the_queries() -> None:
    lab = {"code": "200"}
    evil = {"code": "x'; DROP"}
    source, nr, seen = fakes([src_series(ramp(), evil)], [nr_series(ramp(), lab)])
    item = evaluate(pair(), NOW, SETTINGS, source, nr)
    assert item.outcome.verdict is Verdict.MISMATCH
    assert "DROP" not in seen["promql"] + seen["nrql"]


def test_unfilled_variable_is_not_comparable() -> None:
    p = pair(
        original='up{job=~"$job"}',
        nrql="SELECT latest(up) FROM Metric WHERE job IN ({{job}}) TIMESERIES AUTO",
    )
    item = run([], [], p)
    assert item.outcome.verdict is Verdict.NOT_COMPARABLE and "--var job" in item.outcome.cause
    given = Settings(hours=1, step=STEP, variables={"job": ("api",)})
    source, nr, seen = fakes([], [])
    evaluate(p, NOW, given, source, nr)
    assert 'job=~"api"' in seen["promql"] and "IN ('api')" in seen["nrql"]


def test_skip_and_missing_loki_source() -> None:
    assert run([], [], pair(skip="because")).outcome.cause == "because"
    loki = Pair(
        "alert",
        "a",
        "k",
        "a",
        "loki",
        'count_over_time({a="b"}[5m])',
        "SELECT count(*) FROM Log",
        True,
        (),
    )
    item = run([], [], loki)
    assert item.outcome.verdict is Verdict.NOT_COMPARABLE and "--source-loki" in item.outcome.cause


def test_repeated_read_failures_stop_the_run() -> None:
    def broken(family: str, query: str, start: int, end: int, step: int) -> Fetched:
        raise ParityError("boom")

    with pytest.raises(ParityError, match="in a row"):
        run_once([pair()] * 6, NOW, SETTINGS, {"prometheus": broken}, lambda *_: Fetched([]))
    one = run_once([pair()], NOW, SETTINGS, {"prometheus": broken}, lambda *_: Fetched([]))
    assert "a read failed: boom" in one[0].outcome.cause


def test_watch_runs_until_interrupted() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def cycle(now: int) -> None:
        calls.append(now)
        if len(calls) == 3:
            raise KeyboardInterrupt

    done = watch(cycle, 600, sleep=sleeps.append, clock=lambda: 1000.0)
    assert done == 2 and sleeps == [600, 600] and calls == [1000, 1000, 1000]
