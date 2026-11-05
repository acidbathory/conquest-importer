"""Google Cloud alert policies convert to IR, using policies exported from a real project."""

from pathlib import Path

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor, Operator, Severity, SignalKind
from importer.core.remediation import steps_for
from importer.sources import REGISTRY
from importer.sources.gcp.filter import FilterError, parse_filter
from importer.sources.gcp.models import GcpAlertPolicy, load_policies
from importer.sources.gcp.translate import nr_metric_name, translate, translate_policy

FIXTURE = Path(__file__).parent / "fixtures" / "gcp" / "policies.json"


def by_name() -> dict[str, IRMonitor]:
    return {m.name: m for m in translate(load_policies(FIXTURE))}


def policy(condition: dict[str, object], **extra: object) -> GcpAlertPolicy:
    return GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "combiner": "OR",
            "conditions": [{"displayName": "c", **condition}],
            **extra,
        }
    )


def threshold(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "filter": 'metric.type="compute.googleapis.com/instance/cpu/utilization" '
        'AND resource.type="gce_instance" AND resource.labels.zone="us-central1-a"',
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.8,
        "duration": "600s",
    }
    return {"conditionThreshold": {**body, **overrides}}


def test_the_source_is_registered_and_loads_the_real_export() -> None:
    assert "gcp" in REGISTRY
    assert len(by_name()) == 4


def test_a_threshold_policy_becomes_a_metric_condition() -> None:
    m = by_name()["ckg-gce-cpu-high"]
    assert m.confidence is Confidence.APPROXIMATE and m.signal.kind is SignalKind.METRIC
    assert m.thresholds[0].operator is Operator.ABOVE and m.thresholds[0].value == 0.8
    assert m.thresholds[0].severity is Severity.WARNING
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (600, 300)
    assert nrql_for(m) == (
        "SELECT average(gcp.compute.instance.cpu.utilization) FROM Metric FACET gcp.zone"
    )
    assert m.notification.message_template == "CPU is high"


@pytest.mark.parametrize("name", ["ckg-pubsub-absent", "ckg-mql"])
def test_unconverted_kinds_stay_manual_with_steps(name: str) -> None:
    m = by_name()[name]
    assert m.confidence is Confidence.MANUAL
    assert steps_for(m.reasons)


def test_a_log_match_with_a_severity_range_stays_manual() -> None:
    m = by_name()["ckg-log-match"]
    assert m.confidence is Confidence.MANUAL and "comparison" in m.reasons[0]


def test_a_plain_log_match_becomes_a_log_count() -> None:
    m = translate_policy(
        policy({"conditionMatchedLog": {"filter": 'severity="ERROR" AND textPayload:"time out"'}})
    )[0]
    assert m.confidence is Confidence.APPROXIMATE and m.signal.kind is SignalKind.LOG
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE severity = 'ERROR' AND textPayload LIKE '%time out%'"
    )


def test_label_filters_become_scope_and_counters_keep_google_name() -> None:
    m = translate_policy(
        policy(
            threshold(
                filter='metric.type="pubsub.googleapis.com/subscription/ack_message_count" '
                'AND resource.labels.subscription_id="s"',
                aggregations=[{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_RATE"}],
            )
        )
    )[0]
    assert nrql_for(m) == (
        "SELECT sum(gcp.pubsub.subscription.ack_message_count) FROM Metric "
        "WHERE gcp.subscription_id = 's'"
    )


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"denominatorFilter": 'metric.type="x.googleapis.com/y"'}, "ratio"),
        ({"forecastOptions": {"forecastHorizon": "3600s"}}, "forecast"),
        ({"trigger": {"percent": 50}}, "several time series"),
        ({"comparison": "COMPARISON_UNSPECIFIED"}, "comparison"),
        ({"aggregations": [{"alignmentPeriod": "30s"}]}, "alignment period"),
        (
            {
                "aggregations": [
                    {"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_PERCENTILE_5"}
                ]
            },
            "aligner",
        ),
        ({"filter": 'metric.type="a.googleapis.com/b" OR metric.type="a.googleapis.com/c"'}, "OR"),
        ({"filter": 'metric.type=starts_with("a")'}, "filter"),
    ],
)
def test_unsupported_shapes_are_manual_not_crashes(
    overrides: dict[str, object], needle: str
) -> None:
    (m,) = translate_policy(policy(threshold(**overrides)))
    assert m.confidence is Confidence.MANUAL and needle in m.reasons[0]


def test_an_and_policy_with_two_conditions_warns_and_names_each_condition() -> None:
    p = GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "combiner": "AND",
            "conditions": [
                {"displayName": "a", **threshold()},
                {"displayName": "b", **threshold()},
            ],
        }
    )
    monitors = translate_policy(p)
    assert [m.name for m in monitors] == ["p / a", "p / b"]
    assert all(any("all 2 conditions" in r for r in m.reasons) for m in monitors)


def test_missing_data_active_becomes_a_no_data_alert() -> None:
    (m,) = translate_policy(
        policy(threshold(evaluationMissingData="EVALUATION_MISSING_DATA_ACTIVE"))
    )
    assert m.no_data.notify and m.no_data.timeout_minutes == 10


def test_metric_names_follow_the_documented_rule() -> None:
    assert nr_metric_name("compute.googleapis.com/instance/cpu/utilization") == (
        "gcp.compute.instance.cpu.utilization"
    )
    assert nr_metric_name("kubernetes.io/container/cpu/core_usage_time") == (
        "gcp.kubernetes.io.container.cpu.core_usage_time"
    )
    # Confirmed on live data: no suffix is added, Google's own counter names already end in _count.
    assert nr_metric_name("compute.googleapis.com/instance/disk/read_bytes_count") == (
        "gcp.compute.instance.disk.read_bytes_count"
    )


@pytest.mark.parametrize("text", ["a=1 OR b=2", 'a=~"x" AND', "f(x)", ""])
def test_filters_the_parser_cannot_read_are_rejected(text: str) -> None:
    with pytest.raises(FilterError):
        parse_filter(text)


def test_quoted_values_may_contain_spaces_and_the_word_and() -> None:
    terms = parse_filter('textPayload:"a AND b" AND severity=ERROR')
    assert [(t.field, t.value) for t in terms] == [
        ("textPayload", "a AND b"),
        ("severity", "ERROR"),
    ]


def test_resource_and_metric_labels_use_the_attribute_names_seen_on_live_data() -> None:
    (m,) = translate_policy(
        policy(
            threshold(
                filter='metric.type="pubsub.googleapis.com/topic/send_request_count" '
                'AND resource.labels.project_id="p" AND metric.labels.response_code="ok"',
                aggregations=[
                    {
                        "alignmentPeriod": "60s",
                        "perSeriesAligner": "ALIGN_RATE",
                        "crossSeriesReducer": "REDUCE_SUM",
                        "groupByFields": ["resource.label.topic_id"],
                    }
                ],
            )
        )
    )
    assert nrql_for(m) == (
        "SELECT sum(gcp.pubsub.topic.send_request_count) FROM Metric "
        "WHERE gcp.projectId = 'p' AND metric.response_code = 'ok' FACET gcp.topic_id"
    )


def test_a_percentile_aligner_uses_the_series_new_relic_publishes() -> None:
    (m,) = translate_policy(
        policy(
            threshold(
                filter='metric.type="pubsub.googleapis.com/topic/message_sizes"',
                aggregations=[
                    {"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_PERCENTILE_99"}
                ],
            )
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == "SELECT average(gcp.pubsub.topic.message_sizes.p99) FROM Metric"


def test_a_metric_confirmed_on_live_data_carries_no_naming_warning() -> None:
    (m,) = translate_policy(
        policy(threshold(filter='metric.type="pubsub.googleapis.com/topic/send_request_count"'))
    )
    assert not any("did not appear in our live test" in r for r in m.reasons)


def test_a_metric_never_received_in_the_live_test_says_so() -> None:
    (m,) = translate_policy(policy(threshold()))  # compute cpu/utilization
    assert any("never arrived" in r for r in m.reasons)


def test_an_unseen_metric_keeps_the_unconfirmed_name_warning() -> None:
    (m,) = translate_policy(
        policy(threshold(filter='metric.type="spanner.googleapis.com/instance/cpu/utilization"'))
    )
    assert any("did not appear in our live test" in r for r in m.reasons)
