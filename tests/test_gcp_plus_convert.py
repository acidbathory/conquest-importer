"""Conditions on log-based metrics, SLOs and uptime checks, with the export's definitions."""

import json
from pathlib import Path
from typing import Any

from importer.core.emit.nrql import build_nrql
from importer.core.ir import Confidence, IRMonitor
from importer.sources.gcp.extract_plus import log_metric_summary, slo_summary, uptime_details
from importer.sources.gcp.extras import GcpExtras
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy

FIX = Path(__file__).parent / "fixtures" / "gcp-extract-plus"
P = "projects/example-project"


def fixture(name: str, key: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIX / name).read_text())[key]
    return data


def extras() -> GcpExtras:
    return GcpExtras(
        log_metrics={
            m["name"]: m
            for m in map(log_metric_summary, fixture("api-log-metrics.json", "metrics"))
        },
        slos={
            "/".join(s["name"].split("/")[-4:]): s
            for s in map(slo_summary, fixture("api-slos-checkout.json", "serviceLevelObjectives"))
        },
        uptime={
            c["name"].rsplit("/", 1)[-1]: {**c, "details": uptime_details(c)}
            for c in fixture("api-uptime-check-configs.json", "uptimeCheckConfigs")
        },
    )


def policy(filter_text: str, **body: Any) -> GcpAlertPolicy:
    cond = {
        "comparison": "COMPARISON_GT",
        "duration": "0s",
        "thresholdValue": 5,
        "filter": filter_text,
        **body,
    }
    return GcpAlertPolicy.model_validate(
        {"displayName": "p", "conditions": [{"displayName": "c", "conditionThreshold": cond}]}
    )


def run(pol: GcpAlertPolicy, with_extras: bool = True) -> IRMonitor:
    if with_extras:
        pol.attach_extras(extras())
    (monitor,) = translate_policy(pol)
    return monitor


def agg(aligner: str, reducer: str = "REDUCE_NONE", period: str = "300s", **kw: Any) -> list[Any]:
    return [
        {
            "alignmentPeriod": period,
            "perSeriesAligner": aligner,
            "crossSeriesReducer": reducer,
            **kw,
        }
    ]


def logm(name: str) -> str:
    return f'resource.type = "global" AND metric.type = "logging.googleapis.com/user/{name}"'


def test_a_log_based_counter_becomes_a_count_of_log_events_with_the_metric_filter() -> None:
    m = run(policy(logm("checkout_errors"), aggregations=agg("ALIGN_DELTA")))
    assert m.confidence == Confidence.APPROXIMATE and m.data_path == "needs-log-forwarding"
    nrql = build_nrql(m)
    assert nrql.startswith("SELECT count(*) FROM Log WHERE ")
    assert "jsonPayload.service = 'checkout'" in nrql and "severity IN (" in nrql
    assert m.thresholds[0].value == 5
    assert not any(r.startswith("data path: unknown") for r in m.reasons)


def test_a_rate_threshold_is_multiplied_by_the_window() -> None:
    m = run(policy(logm("checkout_errors"), aggregations=agg("ALIGN_RATE", "REDUCE_SUM")))
    assert m.thresholds[0].value == 5 * 300


def test_the_unextracted_label_filter_uses_the_extract_field() -> None:
    f = logm("checkout_errors") + ' AND metric.label.order_region = "eu"'
    m = run(policy(f, aggregations=agg("ALIGN_DELTA")))
    assert "jsonPayload.region = 'eu'" in build_nrql(m)


def test_a_regex_label_extractor_stays_manual_with_the_workaround() -> None:
    f = logm("regex_label_errors") + ' AND metric.label.code = "500"'
    m = run(policy(f, aggregations=agg("ALIGN_DELTA")))
    assert m.confidence == Confidence.MANUAL and "parsing rule" in m.reasons[0]


def test_distribution_and_value_extractor_metrics_stay_manual_with_nrql() -> None:
    for name in ("checkout_latency_ms", "bytes_sent"):
        m = run(policy(logm(name), aggregations=agg("ALIGN_DELTA")))
        assert m.confidence == Confidence.MANUAL and "percentile(<attribute>" in m.reasons[0]


def test_unknown_or_disabled_log_metric_is_manual_and_names_the_reason() -> None:
    gone = run(policy(logm("nope"), aggregations=agg("ALIGN_DELTA")))
    off = run(policy(logm("disabled_metric"), aggregations=agg("ALIGN_DELTA")))
    assert "not in the export" in gone.reasons[0] and "disabled" in off.reasons[0]


def test_an_export_without_log_metrics_keeps_the_old_reason() -> None:
    m = run(policy(logm("checkout_errors"), aggregations=agg("ALIGN_DELTA")), with_extras=False)
    assert m.confidence == Confidence.MANUAL and "log filter is part of the metric" in m.reasons[0]


def slo_filter(kind: str, slo: str = "availability-99-9", lookback: str = '"3600s"') -> str:
    name = f"{P}/services/checkout/serviceLevelObjectives/{slo}"
    extra = f", {lookback}" if kind == "burn_rate" else ""
    return f'select_slo_{kind}("{name}"{extra})'


def test_burn_rate_becomes_a_ratio_condition_scaled_by_the_error_budget() -> None:
    m = run(policy(slo_filter("burn_rate"), thresholdValue=10))
    assert m.confidence == Confidence.APPROXIMATE and m.data_path == "native"
    nrql = build_nrql(m)
    assert "request_count" in nrql and " / " in nrql
    # burn 10 x budget 0.001 = 0.01 bad share, so good share below 0.99
    assert m.thresholds[0].operator.value == "below"
    assert abs(m.thresholds[0].value - 0.99) < 1e-9
    assert m.signal.aggregation_window_seconds == 3600


def test_burn_rate_with_a_bad_filter_compares_the_bad_share() -> None:
    f = slo_filter("burn_rate", "availability-bad-total")
    m = run(policy(f, thresholdValue=2))
    assert m.confidence == Confidence.APPROXIMATE
    assert m.thresholds[0].operator.value == "above"
    assert abs(m.thresholds[0].value - 2 * 0.005) < 1e-9


def test_a_long_lookback_is_manual_and_names_the_service_level() -> None:
    m = run(policy(slo_filter("burn_rate", lookback='"21600s"'), thresholdValue=6))
    assert m.confidence == Confidence.MANUAL
    assert "3600s" in m.reasons[0] and "service level with target 99.9%" in m.reasons[0]
    assert "28 DAY" in m.reasons[0]


def test_health_and_budget_fraction_are_manual_with_the_service_level_to_create() -> None:
    for kind in ("health", "budget_fraction", "compliance"):
        m = run(policy(slo_filter(kind), comparison="COMPARISON_LT", thresholdValue=0.99))
        assert m.confidence == Confidence.MANUAL
        assert "whole SLO period" in m.reasons[0] and "service level" in m.reasons[0]


def test_other_sli_kinds_are_manual_with_their_own_workaround() -> None:
    for slo, word in (
        ("latency-cut", "Transaction duration"),
        ("windows-good-bad", "good windows"),
        ("basic-availability", "basic indicator"),
    ):
        m = run(policy(slo_filter("burn_rate", slo), thresholdValue=2))
        assert m.confidence == Confidence.MANUAL and word in m.reasons[0], slo


def test_an_unknown_slo_and_an_old_export_are_distinct() -> None:
    f = slo_filter("burn_rate", "missing")
    assert "not in the export" in run(policy(f, thresholdValue=2)).reasons[0]
    old = run(policy(slo_filter("burn_rate"), thresholdValue=2), with_extras=False)
    assert old.confidence == Confidence.MANUAL and "not read" in old.reasons[0]


def up(metric: str) -> str:
    return (
        'resource.type = "uptime_url" AND metric.type = '
        f'"monitoring.googleapis.com/uptime_check/{metric}" '
        'AND metric.label.check_id = "site-health"'
    )


def test_check_passed_counts_failed_synthetic_runs_for_the_monitor() -> None:
    a = agg(
        "ALIGN_NEXT_OLDER", "REDUCE_COUNT_FALSE", "1200s", groupByFields=["resource.label.host"]
    )
    m = run(policy(up("check_passed"), aggregations=a, thresholdValue=1, duration="60s"))
    assert m.confidence == Confidence.APPROXIMATE
    nrql = build_nrql(m)
    assert nrql.startswith("SELECT filter(count(*), WHERE result = 'FAILED') FROM SyntheticCheck")
    assert "monitorName = 'Site health'" in nrql and "FACET monitorName" in nrql


def test_request_latency_is_the_duration_of_the_run() -> None:
    a = agg("ALIGN_MEAN", "REDUCE_MEAN", "300s")
    m = run(policy(up("request_latency"), aggregations=a, thresholdValue=2000))
    assert build_nrql(m).startswith("SELECT average(duration) FROM SyntheticCheck WHERE")
    p95 = run(policy(up("request_latency"), aggregations=agg("ALIGN_PERCENTILE_95")))
    assert "percentile(duration, 95)" in build_nrql(p95)


def test_the_ssl_expiry_metric_names_the_certificate_check() -> None:
    f = up("time_until_ssl_cert_expires")
    m = run(policy(f, aggregations=agg("ALIGN_MEAN"), comparison="COMPARISON_LT"))
    assert m.confidence == Confidence.MANUAL and "certificate check" in m.reasons[0]


def test_an_unknown_check_and_an_old_export_for_uptime() -> None:
    f = up("check_passed").replace("site-health", "gone")
    a = agg("ALIGN_NEXT_OLDER", "REDUCE_COUNT_FALSE")
    assert "not in the export" in run(policy(f, aggregations=a)).reasons[0]
    old = run(policy(up("check_passed"), aggregations=a), with_extras=False)
    assert "depends on the check itself" in old.reasons[0]
