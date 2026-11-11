"""Names and scales of the metrics New Relic does not poll.

See docs/research/gcp-metric-name-mapping.md.
A VERIFIED row maps a Google metric to the name it has when the same host is monitored with the
OpenTelemetry Collector, and carries the scale between the two units so a threshold is rescaled.
"""

import json
from pathlib import Path

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import IRMonitor
from importer.sources.gcp import datapath
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy

AGENT = "agent.googleapis.com/"
VERIFIED_MARK = "metric name VERIFIED"


def convert(metric: str, filter_extra: str = "", **body: object) -> IRMonitor:
    condition = {
        "filter": f'metric.type="{metric}"{filter_extra}',
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 90,
        "duration": "600s",
        **body,
    }
    policy = GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "combiner": "OR",
            "conditions": [{"displayName": "c", "conditionThreshold": condition}],
        }
    )
    (m,) = translate_policy(policy)
    return m


def threshold(m: IRMonitor) -> float:
    return m.thresholds[0].value


@pytest.mark.parametrize(
    ("google", "otel", "value"),
    [
        ("cpu/utilization", "system.cpu.utilization", 0.9),
        ("memory/percent_used", "system.memory.utilization", 0.9),
        ("disk/percent_used", "system.filesystem.utilization", 0.9),
        ("swap/percent_used", "system.paging.utilization", 0.9),
        ("pagefile/percent_used", "system.paging.utilization", 0.9),
    ],
)
def test_percent_metrics_arrive_as_a_ratio_and_the_threshold_is_rescaled(
    google: str, otel: str, value: float
) -> None:
    m = convert(AGENT + google)
    assert f"average({otel})" in nrql_for(m)
    assert threshold(m) == value  # Google 90 percent is 0.9
    assert any(VERIFIED_MARK in r for r in m.reasons)
    assert not any("name UNVERIFIED" in r for r in m.reasons)


def test_rescaled_threshold_has_no_float_noise() -> None:
    assert threshold(convert(AGENT + "cpu/utilization", thresholdValue=95)) == 0.95


@pytest.mark.parametrize(
    ("google", "otel"),
    [
        ("cpu/load_1m", "system.cpu.load_average.1m"),
        ("cpu/load_5m", "system.cpu.load_average.5m"),
        ("cpu/load_15m", "system.cpu.load_average.15m"),
        ("memory/bytes_used", "system.memory.usage"),
        ("disk/bytes_used", "system.filesystem.usage"),
        ("processes/count_by_state", "system.processes.count"),
    ],
)
def test_unit_for_unit_metrics_keep_their_threshold(google: str, otel: str) -> None:
    m = convert(AGENT + google, thresholdValue=7)
    assert f"average({otel})" in nrql_for(m).replace("`", "")  # a name may be backtick-quoted
    assert threshold(m) == 7
    assert any(VERIFIED_MARK in r for r in m.reasons)


def test_cpu_labels_are_renamed_to_the_opentelemetry_attributes() -> None:
    m = convert(
        AGENT + "cpu/utilization",
        ' AND metric.labels.cpu_state = "user"',
        aggregations=[
            {
                "alignmentPeriod": "300s",
                "perSeriesAligner": "ALIGN_MEAN",
                "groupByFields": ["metric.label.cpu_number"],
            }
        ],
    )
    q = nrql_for(m)
    assert "state = 'user'" in q and "cpu_state" not in q
    assert "FACET cpu" in q and "cpu_number" not in q


def test_disk_used_filter_is_dropped_because_the_ratio_has_no_state() -> None:
    m = convert(AGENT + "disk/percent_used", ' AND metric.labels.state = "used"')
    q = nrql_for(m)
    assert "state" not in q
    assert any("state" in r and "dropped" in r for r in m.reasons)


@pytest.mark.parametrize(("google", "direction"), [("read", "read"), ("write", "write")])
def test_disk_bytes_counters_split_one_opentelemetry_metric_by_direction(
    google: str, direction: str
) -> None:
    m = convert(
        AGENT + f"disk/{google}_bytes_count",
        aggregations=[{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}],
        thresholdValue=1000,
    )
    q = nrql_for(m)
    assert "sum(system.disk.io)" in q and f"direction = '{direction}'" in q
    assert threshold(m) == 1000 * 300  # a rate over the 300 s window, bytes unchanged


def test_unmatched_agent_metric_keeps_the_google_name_and_says_unverified() -> None:
    m = convert(AGENT + "iis/request_count", thresholdValue=5)
    assert "agent.iis.request_count" in nrql_for(m)
    assert threshold(m) == 5
    text = " ".join(m.reasons)
    assert "name UNVERIFIED" in text and "iis.request.count" in text and VERIFIED_MARK not in text


def test_workload_metric_found_in_its_receiver_is_verified() -> None:
    m = convert("workload.googleapis.com/jvm.memory.heap.used", thresholdValue=5)
    assert "average(jvm.memory.heap.used)" in nrql_for(m)
    assert any(VERIFIED_MARK in r for r in m.reasons)


def test_workload_metric_not_found_in_a_receiver_stays_unverified() -> None:
    m = convert("workload.googleapis.com/flink.job.restart.count", thresholdValue=5)
    assert "flink.job.restart.count" in nrql_for(m)
    assert any("name UNVERIFIED" in r for r in m.reasons)
    assert not any(VERIFIED_MARK in r for r in m.reasons)


def test_prometheus_counter_without_a_counter_suffix_needs_a_type_label() -> None:
    m = convert(
        "prometheus.googleapis.com/Requests5XX/counter",
        aggregations=[{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}],
    )
    assert "Requests5XX" in nrql_for(m)
    assert any("newrelic_metric_type" in r and "counter" in r for r in m.reasons)


def test_prometheus_counter_with_a_total_suffix_needs_no_type_label() -> None:
    m = convert(
        "prometheus.googleapis.com/http_requests_total/counter",
        aggregations=[{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}],
    )
    assert not any("newrelic_metric_type" in r for r in m.reasons)
    assert any(VERIFIED_MARK in r for r in m.reasons)


def test_prometheus_gauge_with_a_counter_suffix_would_be_stored_as_a_count() -> None:
    m = convert("prometheus.googleapis.com/queue_items_count/gauge")
    assert any("newrelic_metric_type" in r and "gauge" in r for r in m.reasons)


def test_data_file_rows_are_complete_and_verified_rows_cite_sources() -> None:
    data = json.loads(Path(datapath.__file__).with_name("metric_names.json").read_text())
    rows = data["agent"]
    assert len(rows) >= 20
    for google, row in rows.items():
        assert google.startswith(AGENT)
        assert row["status"] in ("VERIFIED", "UNVERIFIED")
        assert row["factor"] > 0
        if row["status"] == "VERIFIED":
            assert row["name"] and all(s.startswith("https://") for s in row["sources"])
            assert len(row["sources"]) >= 2
        else:
            assert row["name"] is None and row["otel_hint"]
    assert all(isinstance(v, str) and v for v in data["workload_names"].values())
