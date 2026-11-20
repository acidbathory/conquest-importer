"""max and min over the rule's range become threshold occurrences (at least once, or all)."""

import importlib

import pytest

from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, Occurrences
from test_grafana import make, one

t = importlib.import_module("importer.sources.grafana.translate")

RATE = "sum(rate(jobs_total[5m]))"
INCREASE = "sum(increase(jobs_total[5m]))"
RATIO = "sum(rate(fail_total[5m])) / sum(rate(all_total[5m]))"
HIST = "histogram_quantile(0.95, sum by (le) (rate(lat_seconds_bucket[5m])))"

CASES = [
    ("max", "gt", Occurrences.AT_LEAST_ONCE),
    ("min", "gt", Occurrences.ALL),
    ("max", "lt", Occurrences.ALL),
    ("min", "lt", Occurrences.AT_LEAST_ONCE),
]


@pytest.mark.parametrize("expr", [RATE, INCREASE, RATIO, HIST])
@pytest.mark.parametrize(("reducer", "kind", "expected"), CASES)
def test_extreme_reducer_sets_occurrences(
    expr: str, reducer: str, kind: str, expected: Occurrences
) -> None:
    m = one(make(expr, reducer=reducer, kind=kind, params=[2], for_=0, time_range=600))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.occurrences is expected
    assert m.signal.aggregation_window_seconds == 300 or "bucket" in expr or "lat_" in expr
    assert m.signal.window_seconds == 600
    word = "at_least_once" if expected is Occurrences.AT_LEAST_ONCE else "all"
    assert any(f"reduces the rate over its range with {reducer}" in r for r in m.reasons)
    assert any(f"occurrences {word.upper()}" in r for r in m.reasons)


def test_emitters_write_the_occurrence() -> None:
    m = one(make(RATE, reducer="max", params=[2], for_=0, time_range=600))
    term = nerdgraph_payload(m)["variables"]["condition"]["terms"][0]
    assert term["thresholdOccurrences"] == "AT_LEAST_ONCE"
    assert term["thresholdDuration"] == 600
    assert 'threshold_occurrences = "at_least_once"' in terraform_file([m])


@pytest.mark.parametrize("kind", ["eq", "ne"])
def test_equality_with_an_extreme_stays_refused(kind: str) -> None:
    m = one(make(RATE, reducer="max", kind=kind, params=[2], for_=0))
    assert m.confidence is Confidence.MANUAL


def test_at_least_once_with_a_longer_pending_period_is_refused() -> None:
    m = one(make(RATE, reducer="max", params=[2], for_=1200, time_range=600))
    assert m.confidence is Confidence.MANUAL
    m = one(make(RATE, reducer="min", params=[2], for_=1200, time_range=600))
    assert m.confidence is not Confidence.MANUAL  # ALL over the longer duration


def test_sum_and_count_stay_refused() -> None:
    for reducer in ("sum", "count"):
        m = one(make(RATE, reducer=reducer, params=[2]))
        assert m.confidence is Confidence.MANUAL


def test_mean_over_a_histogram_stays_refused() -> None:
    assert one(make(HIST, reducer="mean", params=[2])).confidence is Confidence.MANUAL


def test_a_range_longer_than_a_day_is_named_in_the_reason_not_the_pending_period() -> None:
    # max over a two-day range needs a two-day threshold duration; the pending period is 300 s
    m = one(make("sum(rate(x_total[1m]))", reducer="max", time_range=172800, for_=300, params=[1]))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons == [t.R_DURATION.format(seconds=172800, limit=86400)]
    assert "pending period 300s" not in " ".join(m.reasons)


def test_a_pending_period_longer_than_a_day_keeps_its_own_reason() -> None:
    m = one(make("cpu", for_=90000))
    assert m.reasons == [t.R_PENDING.format(seconds=90000, limit=86400)]
