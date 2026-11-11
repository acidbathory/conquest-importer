"""Cross-series reducers and second-stage aggregations, checked against values worked out by hand.

Google aligns each series on its own first and then reduces across series. New Relic polls a
Google service metric every 300s, so a window of 300s holds one row per series; there the
reducer's own function over the rows is exactly Google's answer. Every expected number below is
worked out on paper from two series.
"""

import pytest

from importer.core.emit.nrql import _metric_nrql
from importer.core.ir import Confidence
from importer.sources.gcp.translate import Fail, metric_series, translate_policy
from test_gcp import policy, threshold

CPU = "compute.googleapis.com/instance/cpu/utilization"
DISK = "compute.googleapis.com/instance/disk/read_bytes_count"
NAME = "gcp.compute.instance.cpu.utilization"


def query(metric: str, aligner: str, reducer: str, period: int) -> tuple[str, float, list[str]]:
    agg = {"crossSeriesReducer": reducer, "perSeriesAligner": aligner}
    signal, _window, scale, notes = metric_series(metric, [], agg, aligner, period)
    return _metric_nrql(signal), scale, notes


@pytest.mark.parametrize(
    ("reducer", "function", "expected"),
    [("REDUCE_SUM", "sum", 0.7), ("REDUCE_MAX", "max", 0.5), ("REDUCE_MIN", "min", 0.2)],
)
def test_a_reducer_over_one_poll_per_series_is_its_own_function(
    reducer: str, function: str, expected: float
) -> None:
    # two instances report 0.2 and 0.5 in the window: Google reduces the two aligned points
    rows = [0.2, 0.5]
    assert {"sum": sum(rows), "max": max(rows), "min": min(rows)}[function] == pytest.approx(
        expected
    )
    text, scale, _ = query(CPU, "ALIGN_MEAN", reducer, 300)
    assert text == f"SELECT {function}({NAME}) FROM Metric" and scale == 1.0


def test_a_mean_of_means_over_one_poll_stays_an_average() -> None:
    text, _, _ = query(CPU, "ALIGN_MEAN", "REDUCE_MEAN", 300)
    assert text == f"SELECT average({NAME}) FROM Metric"  # (0.2 + 0.5) / 2 = 0.35


def test_the_mean_of_per_series_changes_is_an_average_of_the_polled_changes() -> None:
    # counters: the series changed by 600 and 1500 bytes in the poll; mean across series = 1050
    text, scale, _ = query(DISK, "ALIGN_DELTA", "REDUCE_MEAN", 300)
    assert text == "SELECT average(gcp.compute.instance.disk.read_bytes_count) FROM Metric"
    assert scale == 1.0


def test_a_reducer_that_differs_from_the_aligner_over_several_polls_stays_manual() -> None:
    # max over series of the per-series mean of 3 polls: (0.2, 0.4, 0.6) -> 0.4 and (0.1, 0.1,
    # 0.1) -> 0.1 gives 0.4, but max(x) over all six rows is 0.6 and average(x) is 0.25
    with pytest.raises(Fail) as err:
        query(CPU, "ALIGN_MEAN", "REDUCE_MAX", 900)
    assert "several polls" in err.value.reason and "REDUCE_MAX" in err.value.reason


def test_the_same_function_over_several_polls_is_still_exact() -> None:
    # sum of per-series sums equals the sum of all rows; max of maxima equals the overall max
    assert query(CPU, "ALIGN_MAX", "REDUCE_MAX", 900)[0] == f"SELECT max({NAME}) FROM Metric"
    assert query(DISK, "ALIGN_DELTA", "REDUCE_SUM", 900)[0].startswith("SELECT sum(")


def test_a_condition_with_a_sum_across_instances_sums() -> None:
    body = threshold(
        aggregations=[
            {
                "alignmentPeriod": "300s",
                "perSeriesAligner": "ALIGN_MEAN",
                "crossSeriesReducer": "REDUCE_SUM",
            }
        ]
    )
    (monitor,) = translate_policy(policy(body))
    assert monitor.confidence is not Confidence.MANUAL
    assert monitor.signal.metric is not None and monitor.signal.metric.aggregation.value == "sum"


# --- second stage -------------------------------------------------------------------------

REDUCE = {"REDUCE_SUM": sum, "REDUCE_MAX": max, "REDUCE_MIN": min}


def google(rows: dict[tuple[str, str], float], stages: list[dict[str, object]]) -> dict[str, float]:
    """Reference evaluation of reducer stages over one point per series, grouped as Google does.

    `rows` maps (instance, disk) to the series' aligned value; each stage reduces across the
    series that share the stage's group-by labels (instance is label 0, disk label 1).
    """
    series = {k: v for k, v in rows.items()}
    keyed: dict[tuple[str, ...], float] = {k: v for k, v in series.items()}
    labels = ["instance", "disk"]
    for stage in stages:
        reducer = stage.get("crossSeriesReducer")
        if reducer in (None, "", "REDUCE_NONE"):
            continue
        by = [labels.index(str(g)) for g in stage.get("groupByFields", [])]  # type: ignore[attr-defined]
        groups: dict[tuple[str, ...], list[float]] = {}
        for key, value in keyed.items():
            groups.setdefault(tuple(key[i] for i in by), []).append(value)
        keyed = {k: REDUCE[str(reducer)](v) for k, v in groups.items()}
        labels = [labels[i] for i in by]
    return {"/".join(k): v for k, v in keyed.items()}


ROWS = {("i1", "a"): 3.0, ("i1", "b"): 5.0, ("i2", "a"): 7.0}  # per-series rates in the period


def test_a_reducer_after_no_reducer_is_one_reducer() -> None:
    from importer.sources.gcp.stages import merge_stages

    first = {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}
    second = {
        "alignmentPeriod": "300s",
        "crossSeriesReducer": "REDUCE_SUM",
        "groupByFields": ["instance"],
        "perSeriesAligner": "ALIGN_SUM",
    }
    merged, notes = merge_stages(first, second)
    assert (merged["perSeriesAligner"], merged["crossSeriesReducer"]) == (
        "ALIGN_RATE",
        "REDUCE_SUM",
    )
    assert merged["groupByFields"] == ["instance"] and not notes
    # worked by hand: i1 = 3 + 5 = 8, i2 = 7
    assert google(ROWS, [first, second]) == google(ROWS, [merged]) == {"i1": 8.0, "i2": 7.0}


def test_the_same_reducer_over_coarser_groups_is_one_reducer() -> None:
    from importer.sources.gcp.stages import merge_stages

    first = {"crossSeriesReducer": "REDUCE_MAX", "groupByFields": ["instance", "disk"]}
    second = {"crossSeriesReducer": "REDUCE_MAX", "groupByFields": ["instance"]}
    merged, _ = merge_stages(first, second)
    assert merged["groupByFields"] == ["instance"]
    # max of maxima: i1 = max(3, 5) = 5, i2 = 7
    assert google(ROWS, [first, second]) == google(ROWS, [merged]) == {"i1": 5.0, "i2": 7.0}


def test_a_mean_of_means_cannot_be_merged() -> None:
    from importer.sources.gcp.stages import StageError, merge_stages

    first = {"crossSeriesReducer": "REDUCE_MEAN", "groupByFields": ["instance", "disk"]}
    second = {"crossSeriesReducer": "REDUCE_MEAN", "groupByFields": ["instance"]}
    with pytest.raises(StageError, match="nested query"):
        merge_stages(first, second)


def test_a_second_stage_over_another_period_cannot_be_merged() -> None:
    from importer.sources.gcp.stages import StageError, merge_stages

    first = {"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_RATE"}
    second = {
        "alignmentPeriod": "3600s",
        "perSeriesAligner": "ALIGN_MEAN",
        "crossSeriesReducer": "REDUCE_MAX",
    }
    with pytest.raises(StageError, match="3600s"):
        merge_stages(first, second)


def test_a_second_stage_that_does_not_realign_keeps_the_first_period() -> None:
    from importer.sources.gcp.stages import merge_stages

    first = {"alignmentPeriod": "86400s", "perSeriesAligner": "ALIGN_DELTA"}
    second = {"alignmentPeriod": "60s", "crossSeriesReducer": "REDUCE_SUM", "groupByFields": []}
    merged, _ = merge_stages(first, second)
    assert merged["alignmentPeriod"] == "86400s" and merged["crossSeriesReducer"] == "REDUCE_SUM"


def test_a_two_stage_alert_condition_converts_as_one_query() -> None:
    body = threshold(
        aggregations=[
            {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"},
            {
                "alignmentPeriod": "300s",
                "crossSeriesReducer": "REDUCE_SUM",
                "groupByFields": ["resource.label.instance_id"],
                "perSeriesAligner": "ALIGN_SUM",
            },
        ],
        filter='metric.type="compute.googleapis.com/instance/disk/read_bytes_count" '
        'AND resource.type="gce_instance"',
    )
    (monitor,) = translate_policy(policy(body))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert monitor.signal.metric is not None
    assert monitor.signal.metric.aggregation.value == "sum"
    assert monitor.signal.metric.group_by == ["gcp.instance_id"] or monitor.signal.metric.group_by


def test_a_two_stage_condition_that_needs_a_nested_query_says_so() -> None:
    body = threshold(
        aggregations=[
            {
                "alignmentPeriod": "60s",
                "crossSeriesReducer": "REDUCE_SUM",
                "groupByFields": ["resource.label.url_map_name"],
                "perSeriesAligner": "ALIGN_RATE",
            },
            {
                "alignmentPeriod": "900s",
                "crossSeriesReducer": "REDUCE_MAX",
                "perSeriesAligner": "ALIGN_MEAN",
            },
        ]
    )
    (monitor,) = translate_policy(policy(body))
    assert monitor.confidence is Confidence.MANUAL
    assert "nested query" in monitor.reasons[0] and "alert condition" in monitor.reasons[0]
