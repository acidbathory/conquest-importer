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


@pytest.mark.parametrize("name", ["ckg-mql"])
def test_unconverted_kinds_stay_manual_with_steps(name: str) -> None:
    m = by_name()[name]
    assert m.confidence is Confidence.MANUAL
    assert steps_for(m.reasons)


def test_a_log_severity_range_becomes_a_list_of_levels() -> None:
    m = by_name()["ckg-log-match"]
    assert m.confidence is Confidence.APPROXIMATE
    assert "severity IN ('ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY')" in nrql_for(m)


@pytest.mark.parametrize(
    ("expr", "levels"),
    [
        ("severity>WARNING", "'ERROR', 'CRITICAL', 'ALERT', 'EMERGENCY'"),
        ("severity<=INFO", "'DEFAULT', 'DEBUG', 'INFO'"),
        ("severity<NOTICE", "'DEFAULT', 'DEBUG', 'INFO'"),
    ],
)
def test_other_severity_comparisons(expr: str, levels: str) -> None:
    (m,) = translate_policy(policy({"conditionMatchedLog": {"filter": expr}}))
    assert nrql_for(m) == f"SELECT count(*) FROM Log WHERE severity IN ({levels})"  # noqa: S608


def test_a_comparison_on_another_field_stays_manual() -> None:
    (m,) = translate_policy(policy({"conditionMatchedLog": {"filter": "httpRequest.status>=500"}}))
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
        ({"forecastOptions": {"forecastHorizon": "60s"}}, "forecast"),
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


@pytest.mark.parametrize(
    "metric",
    [
        "custom.googleapis.com/orders/failed",
        "agent.googleapis.com/memory/percent_used",
        "workload.googleapis.com/jvm.memory.used",
        "prometheus.googleapis.com/up/gauge",
        "logging.googleapis.com/user/timeouts",
    ],
)
def test_metrics_new_relic_does_not_document_collecting_stay_manual_with_steps(metric: str) -> None:
    (m,) = translate_policy(policy(threshold(filter=f'metric.type="{metric}"')))
    assert m.confidence is Confidence.MANUAL
    assert "not one of the Google Cloud service metrics" in m.reasons[0]
    assert steps_for(m.reasons)


def test_promql_is_not_filed_under_the_mql_steps() -> None:
    from importer.core.remediation import remedies_for

    (m,) = translate_policy(
        policy({"conditionPrometheusQueryLanguage": {"query": "up == 0", "duration": "60s"}})
    )
    assert [r.title for r in remedies_for(m.reasons)] == ["PromQL condition"]


def test_an_uptime_check_alert_gets_synthetics_steps() -> None:
    from importer.core.remediation import remedies_for

    (m,) = translate_policy(
        policy(
            threshold(
                filter='metric.type="monitoring.googleapis.com/uptime_check/check_passed"',
                aggregations=[{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_NEXT_OLDER"}],
            )
        )
    )
    assert m.confidence is Confidence.MANUAL
    assert [r.title for r in remedies_for(m.reasons)] == ["Uptime check alert"]


def test_an_unsupported_log_comparison_gets_the_filter_steps_not_the_aggregation_steps() -> None:
    from importer.core.remediation import remedies_for

    (m,) = translate_policy(
        policy(
            {
                "conditionMatchedLog": {
                    "filter": 'httpRequest.status>=500 AND resource.type="gce_instance"'
                }
            }
        )
    )
    assert [r.title for r in remedies_for(m.reasons)] == ["Filter not supported"]


def _counter(aligner: str, value: float, period: str = "60s") -> IRMonitor:
    (m,) = translate_policy(
        policy(
            threshold(
                filter='metric.type="pubsub.googleapis.com/topic/send_request_count" '
                'AND resource.type="pubsub_topic"',
                aggregations=[{"alignmentPeriod": period, "perSeriesAligner": aligner}],
                thresholdValue=value,
                duration="0s",
            )
        )
    )
    return m


def test_a_rate_threshold_becomes_a_total_over_the_five_minute_window() -> None:
    m = _counter("ALIGN_RATE", 10)  # 10 per second
    assert m.signal.aggregation_window_seconds == 300
    assert m.thresholds[0].value == 3000
    assert any("rescaled" in r for r in m.reasons)


def test_a_delta_threshold_scales_with_the_window() -> None:
    m = _counter("ALIGN_DELTA", 5)  # 5 per 60 seconds
    assert m.signal.aggregation_window_seconds == 300
    assert m.thresholds[0].value == 25


def test_a_long_counter_window_is_kept() -> None:
    m = _counter("ALIGN_DELTA", 5, period="600s")
    assert m.signal.aggregation_window_seconds == 600
    assert m.thresholds[0].value == 5


def test_a_duration_that_is_not_a_whole_number_of_windows_is_rounded_up() -> None:
    (m,) = translate_policy(
        policy(
            threshold(
                filter='metric.type="pubsub.googleapis.com/topic/send_request_count" '
                'AND resource.type="pubsub_topic"',
                aggregations=[{"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_DELTA"}],
                duration="420s",
            )
        )
    )
    assert m.signal.window_seconds == 600 and m.signal.aggregation_window_seconds == 300
    assert any("rounded up to 600s" in r for r in m.reasons)
    assert nrql_for(m)  # emits instead of failing


ABSENT_METRIC = "pubsub.googleapis.com/subscription/ack_message_count"


def absence(**overrides: object) -> GcpAlertPolicy:
    body: dict[str, object] = {
        "filter": f'metric.type="{ABSENT_METRIC}" AND resource.type="pubsub_subscription" '
        'AND resource.labels.subscription_id="s"',
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}],
        "duration": "900s",
    }
    return policy({"conditionAbsent": {**body, **overrides}})


def test_an_absence_condition_becomes_a_loss_of_signal_check() -> None:
    m = by_name()["ckg-pubsub-absent"]
    assert m.confidence is Confidence.APPROXIMATE and m.signal.kind is SignalKind.HEARTBEAT
    assert nrql_for(m) == (
        "SELECT count(*) FROM Metric WHERE metricName = 'gcp.pubsub.subscription.ack_message_count'"
    )
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (900, 300)
    assert m.thresholds[0].operator is Operator.BELOW and m.thresholds[0].value == 1
    assert m.no_data.notify and m.no_data.timeout_minutes == 15
    assert any("no data points" in r for r in m.reasons)


def test_an_absence_condition_keeps_labels_and_grouping() -> None:
    (m,) = translate_policy(
        absence(
            aggregations=[
                {
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": "ALIGN_RATE",
                    "crossSeriesReducer": "REDUCE_SUM",
                    "groupByFields": ["resource.label.subscription_id"],
                }
            ]
        )
    )
    assert nrql_for(m) == (
        "SELECT count(*) FROM Metric WHERE metricName = "
        "'gcp.pubsub.subscription.ack_message_count' AND gcp.subscription_id = 's' "
        "FACET gcp.subscription_id"
    )


@pytest.mark.parametrize(
    ("duration", "period", "expected_window", "expected_period", "adjusted"),
    [
        ("900s", "300s", 900, 300, False),
        ("200s", "60s", 300, 300, True),
        ("700s", "300s", 900, 300, True),
        ("600s", "120s", 600, 300, True),
    ],
)
def test_absence_durations_are_whole_five_minute_windows(
    duration: str, period: str, expected_window: int, expected_period: int, adjusted: bool
) -> None:
    (m,) = translate_policy(
        absence(
            duration=duration,
            aggregations=[{"alignmentPeriod": period, "perSeriesAligner": "ALIGN_RATE"}],
        )
    )
    assert m.signal.window_seconds == expected_window
    assert m.signal.aggregation_window_seconds == expected_period
    assert m.signal.window_seconds % m.signal.aggregation_window_seconds == 0
    assert nrql_for(m)
    assert any("rounded up" in r or "raised to" in r for r in m.reasons) is adjusted


def test_absence_of_a_metric_that_never_arrives_says_so() -> None:
    (m,) = translate_policy(
        absence(filter='metric.type="compute.googleapis.com/instance/cpu/utilization"')
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert any("never arrived" in r and "fire" in r for r in m.reasons)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"filter": 'metric.type="custom.googleapis.com/x"'}, "not one of the google cloud"),
        ({"filter": 'resource.type="pubsub_subscription"'}, "no metric.type"),
        ({"filter": 'metric.type="a" OR metric.type="b"'}, "filter"),
        ({"filter": 'metric.type="pubsub.googleapis.com/subscription/x" AND zone=~"a"'}, "filter"),
        (
            {
                "aggregations": [
                    {"alignmentPeriod": "300s"},
                    {"alignmentPeriod": "300s"},
                ]
            },
            "second-stage",
        ),
        (
            {"aggregations": [{"alignmentPeriod": "300s", "crossSeriesReducer": "REDUCE_BOGUS"}]},
            "reducer",
        ),
        ({"aggregations": [{"alignmentPeriod": "30s"}]}, "alignment period"),
        ({"duration": "0s"}, "duration"),
    ],
)
def test_unmappable_absence_conditions_stay_manual_with_steps(
    overrides: dict[str, object], fragment: str
) -> None:
    (m,) = translate_policy(absence(**overrides))
    assert m.confidence is Confidence.MANUAL
    assert fragment in m.reasons[0].lower()
    assert steps_for(m.reasons)


def test_the_absence_remediation_rule_still_matches_the_old_reason() -> None:
    assert steps_for(["a metric-absence condition could not be converted: reason"])


@pytest.mark.parametrize("kind", ["threshold", "absence"])
@pytest.mark.parametrize(
    ("duration", "period", "fragment"),
    [
        ("99999999999s", "300s", "duration"),
        ("90000s", "300s", "duration"),
        ("600s", "7200s", "aggregation window"),
    ],
)
def test_out_of_range_windows_are_manual_at_translation(
    kind: str, duration: str, period: str, fragment: str
) -> None:
    aggs = [{"alignmentPeriod": period, "perSeriesAligner": "ALIGN_MEAN"}]
    if kind == "absence":
        (m,) = translate_policy(absence(duration=duration, aggregations=aggs))
    else:
        (m,) = translate_policy(policy(threshold(duration=duration, aggregations=aggs)))
    assert m.confidence is Confidence.MANUAL
    assert fragment in m.reasons[0].lower() and "limit" in m.reasons[0].lower()
    assert steps_for(m.reasons)


@pytest.mark.parametrize("kind", ["threshold", "absence"])
@pytest.mark.parametrize("label", ["resource.label.a-b", "metric.label.a b"])
def test_label_keys_new_relic_would_reject_are_manual(kind: str, label: str) -> None:
    filt = f'metric.type="pubsub.googleapis.com/subscription/backlog_bytes" AND {label}="x"'
    if kind == "absence":
        (m,) = translate_policy(absence(filter=filt))
    else:
        (m,) = translate_policy(policy(threshold(filter=filt)))
    assert m.confidence is Confidence.MANUAL
    assert "attribute name" in m.reasons[0] or "filter not supported" in m.reasons[0]
    assert steps_for(m.reasons)


def group_by_aggs(*fields: str) -> list[dict[str, object]]:
    return [
        {
            "alignmentPeriod": "300s",
            "perSeriesAligner": "ALIGN_RATE",
            "crossSeriesReducer": "REDUCE_SUM",
            "groupByFields": list(fields),
        }
    ]


@pytest.mark.parametrize("kind", ["threshold", "absence"])
def test_a_dropped_group_by_field_is_noted(kind: str) -> None:
    aggs = group_by_aggs("resource.label.subscription_id", "foo")
    if kind == "absence":
        (m,) = translate_policy(absence(aggregations=aggs))
        assert not any("fires only when no series reports" in r for r in m.reasons)
    else:
        (m,) = translate_policy(policy(threshold(aggregations=aggs)))
    assert any("group-by field 'foo' is not mapped and was dropped" in r for r in m.reasons)


def test_group_by_is_deduplicated_in_order() -> None:
    aggs = group_by_aggs("resource.label.zone", "resource.labels.zone", "resource.label.a")
    (m,) = translate_policy(absence(aggregations=aggs))
    assert m.signal.heartbeat is not None
    assert m.signal.heartbeat.group_by == ["gcp.zone", "gcp.a"]


def test_only_dropped_group_by_fields_still_say_no_group_by() -> None:
    (m,) = translate_policy(absence(aggregations=group_by_aggs("foo")))
    assert any("fires only when no series reports" in r for r in m.reasons)


def test_an_absence_condition_emits_to_nerdgraph_and_terraform() -> None:
    from importer.core.emit.nerdgraph import nerdgraph_payload
    from importer.core.emit.terraform import resource_block

    m = by_name()["ckg-pubsub-absent"]
    cond = nerdgraph_payload(m)["variables"]["condition"]
    assert cond["nrql"]["query"] == (
        "SELECT count(*) FROM Metric WHERE metricName = 'gcp.pubsub.subscription.ack_message_count'"
    )
    assert cond["expiration"] == {
        "expirationDuration": 900,
        "openViolationOnExpiration": True,
        "closeViolationsOnExpiration": False,
    }
    assert cond["terms"][0]["operator"] == "BELOW" and cond["terms"][0]["thresholdDuration"] == 900
    block = resource_block(m)
    assert "metricName = 'gcp.pubsub.subscription.ack_message_count'" in block
    assert "expiration_duration            = 900" in block
    assert "open_violation_on_expiration   = true" in block

    (scoped,) = translate_policy(absence())
    query = nerdgraph_payload(scoped)["variables"]["condition"]["nrql"]["query"]
    assert "gcp.subscription_id = 's'" in query


def test_a_single_policy_file_loads(tmp_path: Path) -> None:
    (tmp_path / "one.json").write_text(
        '{"displayName": "solo", "combiner": "OR", "conditions": []}'
    )
    assert [p.displayName for p in load_policies(tmp_path)] == ["solo"]
