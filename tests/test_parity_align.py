"""Series matching, bucket alignment and the verdict rules of the parity verifier."""

from importer.parity.align import (
    Window,
    judge,
    map_labels,
    prom_to_buckets,
    window_for,
)
from importer.parity.model import Series, Verdict

STEP = 60
START = 1_700_000_040  # a multiple of 60
W = Window(start=START, end=START + 20 * STEP, step=STEP)


def nr(values: list[float], labels: dict[str, str] | None = None) -> Series:
    return Series(labels or {}, {START + i * STEP: v for i, v in enumerate(values)})


def prom(values: list[float], labels: dict[str, str] | None = None) -> Series:
    """A Prometheus series: the point at time t describes the bucket that ENDS at t."""
    return Series(labels or {}, {START + (i + 1) * STEP: v for i, v in enumerate(values)})


def test_window_is_a_whole_number_of_steps_and_excludes_the_open_bucket() -> None:
    w = window_for(now=1_700_000_123, hours=1, step=60)
    assert w.end % 60 == 0 and w.start % 60 == 0
    assert w.end == 1_700_000_100 and w.end - w.start == 3600


def test_prom_points_move_back_one_step_to_the_bucket_they_end() -> None:
    shifted = prom_to_buckets(prom([1.0, 2.0]), STEP)
    assert shifted.points == {START: 1.0, START + STEP: 2.0}


def test_labels_drop_the_name_and_rename_metricname() -> None:
    assert map_labels({"__name__": "up", "code": "200", "metricName": "x"}) == {
        "code": "200",
        "metric_name": "x",
    }


def test_exact_match_is_verified_and_edges_are_dropped() -> None:
    values = [float(i + 1) for i in range(20)]
    out = judge([prom(values)], [nr(values)], W, tolerance=0.05)
    assert out.verdict is Verdict.VERIFIED
    assert out.points == 18  # first and last bucket dropped
    assert out.median_error == 0.0


def test_within_tolerance_is_verified_and_outside_is_mismatch() -> None:
    values = [100.0] * 20
    near = [101.0] * 20
    assert judge([prom(values)], [nr(near)], W, tolerance=0.05).verdict is Verdict.VERIFIED
    far = judge([prom(values)], [nr([130.0] * 20)], W, tolerance=0.05)
    assert far.verdict is Verdict.MISMATCH


def test_a_one_bucket_shift_is_named() -> None:
    values = [float(i * i + 1) for i in range(20)]
    shifted = [values[0], *values[:-1]]
    out = judge([prom(values)], [nr(shifted)], W, tolerance=0.05)
    assert out.verdict is Verdict.MISMATCH
    assert "bucket alignment" in out.cause


def test_one_reset_bucket_in_a_counter_does_not_break_verification() -> None:
    long = Window(start=START, end=START + 60 * STEP, step=STEP)
    values = [5.0] * 60
    broken = list(values)
    broken[7] = 0.0
    out = judge([prom(values)], [nr(broken)], long, tolerance=0.05)
    assert out.verdict is Verdict.VERIFIED
    assert out.points - out.within == 1


def test_constant_ratio_points_at_counter_or_rate_window() -> None:
    values = [10.0] * 20
    out = judge([prom(values)], [nr([40.0] * 20)], W, tolerance=0.05)
    assert out.verdict is Verdict.MISMATCH
    assert "4" in out.cause and "ratio" in out.cause


def test_missing_series_on_one_side() -> None:
    a, b = {"code": "200"}, {"code": "500"}
    values = [3.0] * 20
    out = judge([prom(values, a), prom(values, b)], [nr(values, a)], W, tolerance=0.05)
    assert out.verdict is Verdict.CLOSE
    assert out.source_only == 1 and "missing series" in out.cause


def test_no_matching_series_at_all_is_a_label_mismatch() -> None:
    values = [3.0] * 20
    out = judge([prom(values, {"pod": "a"})], [nr(values, {"host": "a"})], W, tolerance=0.05)
    assert out.verdict is Verdict.MISMATCH
    assert "label" in out.cause


def test_renamed_label_matches() -> None:
    values = [3.0] * 20
    out = judge(
        [prom(values, {"metricName": "x"})], [nr(values, {"metric_name": "x"})], W, tolerance=0.05
    )
    assert out.verdict is Verdict.VERIFIED


def test_new_relic_facet_subset_matches_a_wider_source_series() -> None:
    values = [3.0] * 20
    src = prom(values, {"code": "200", "job": "api"})
    out = judge([src], [nr(values, {"code": "200"})], W, tolerance=0.05)
    assert out.verdict is Verdict.VERIFIED


def test_no_data_names_the_side() -> None:
    values = [3.0] * 20
    left = judge([], [nr(values)], W, tolerance=0.05)
    right = judge([prom(values)], [], W, tolerance=0.05)
    assert left.verdict is right.verdict is Verdict.NO_DATA
    assert "source" in left.cause and "New Relic" in right.cause


def test_too_few_points_is_not_comparable() -> None:
    out = judge([prom([1.0] * 3)], [nr([1.0] * 3)], W, tolerance=0.05, min_points=10)
    assert out.verdict is Verdict.NOT_COMPARABLE


def test_capped_answers_are_never_verified() -> None:
    values = [3.0] * 20
    out = judge([prom(values)], [nr(values)], W, tolerance=0.05, capped=True)
    assert out.verdict is Verdict.CLOSE and "cap" in out.cause


def test_absolute_tolerance_covers_values_near_zero() -> None:
    out = judge([prom([0.0] * 20)], [nr([1e-12] * 20)], W, tolerance=0.05)
    assert out.verdict is Verdict.VERIFIED
