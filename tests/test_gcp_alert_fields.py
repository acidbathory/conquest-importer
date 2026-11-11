"""Google alert policy fields beyond the query: severity, strategy, documentation, labels,
missing data, forecasts, triggers and the aggregation enums."""

import pytest

from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.ir import Confidence, IRMonitor, Occurrences, Severity
from importer.sources.gcp.translate import translate_policy
from test_gcp import policy, threshold

LOG: dict[str, object] = {"conditionMatchedLog": {"filter": 'severity="ERROR"'}}
CPU = "compute.googleapis.com/instance/cpu/utilization"
REDUCED = {
    "alignmentPeriod": "300s",
    "perSeriesAligner": "ALIGN_MEAN",
    "crossSeriesReducer": "REDUCE_MEAN",
}


def one(condition: dict[str, object], **extra: object) -> IRMonitor:
    (m,) = translate_policy(policy(condition, **extra))
    return m


def reasons(m: IRMonitor) -> str:
    return " | ".join(m.reasons)


def description(m: IRMonitor) -> str:
    assert m.emit is not None and m.emit.description_note is not None
    return m.emit.description_note


# --- severity ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("google", "expected", "noted"),
    [
        ("CRITICAL", Severity.CRITICAL, False),
        ("WARNING", Severity.WARNING, False),
        ("ERROR", Severity.CRITICAL, True),
        ("", Severity.CRITICAL, False),
        ("SEVERITY_UNSPECIFIED", Severity.CRITICAL, False),
    ],
)
def test_severity(google: str, expected: Severity, noted: bool) -> None:
    m = one(threshold(), severity=google)
    assert m.thresholds[0].severity is expected
    assert ("only critical and warning" in reasons(m)) is noted


def test_google_severity_is_kept_as_a_tag_and_in_the_description() -> None:
    m = one(threshold(), severity="ERROR")
    assert "gcp-severity:ERROR" in m.tags
    assert "Google severity: ERROR" in description(m)


# --- alert strategy ---------------------------------------------------------


def test_a_log_alert_carries_its_rate_limit_as_the_violation_time_limit() -> None:
    m = one(LOG, alertStrategy={"notificationRateLimit": {"period": "3600s"}})
    assert m.emit is not None and m.emit.violation_time_limit_seconds == 3600
    assert "at most once every 3600s" in reasons(m)
    cond = nerdgraph_payload(m)["variables"]["condition"]
    assert cond["violationTimeLimitSeconds"] == 3600


def test_a_log_alert_without_a_rate_limit_says_google_requires_one() -> None:
    m = one(LOG)
    assert "which Google requires" in reasons(m)
    assert m.emit is None or m.emit.violation_time_limit_seconds is None


def test_log_rate_limit_wins_over_auto_close() -> None:
    m = one(
        LOG,
        alertStrategy={"notificationRateLimit": {"period": "600s"}, "autoClose": "604800s"},
    )
    assert m.emit is not None and m.emit.violation_time_limit_seconds == 600
    assert "autoClose of 604800s is not mapped" in reasons(m)


def test_auto_close_becomes_the_violation_time_limit() -> None:
    m = one(threshold(), alertStrategy={"autoClose": "86400s"})
    assert m.emit is not None and m.emit.violation_time_limit_seconds == 86400


def test_auto_close_outside_new_relics_range_is_a_note_not_a_failed_emit() -> None:
    m = one(threshold(), alertStrategy={"autoClose": "60s"})
    assert m.emit is None or m.emit.violation_time_limit_seconds is None
    assert "outside the 5 minute to 30 day" in reasons(m)
    nerdgraph_payload(m)


def test_renotify_and_prompts_get_a_precise_note() -> None:
    m = one(
        threshold(),
        alertStrategy={
            "notificationPrompts": ["OPENED", "CLOSED"],
            "notificationChannelStrategy": [
                {"notificationChannelNames": ["c"], "renotifyInterval": "1800s"}
            ],
        },
    )
    text = reasons(m)
    assert "every 1800s" in text and "no repeat reminder" in text
    assert "CLOSED, OPENED" in text and "issue activated" in text
    assert "renotify 1800s" in description(m)


# --- documentation, labels and extractors -----------------------------------


def test_documentation_links_labels_and_subject_are_preserved() -> None:
    m = one(
        threshold(),
        documentation={
            "content": "body",
            "mimeType": "text/markdown",
            "subject": "Disk full",
            "links": [
                {"displayName": "Runbook", "url": "https://example.com/rb"},
                {"displayName": "Console", "url": "https://x.example/${PROJECT_ID}"},
            ],
        },
        userLabels={"team": "sre", "tier": "1"},
    )
    text = description(m)
    assert "Notification subject: Disk full" in text
    assert "Documentation format: text/markdown" in text
    assert "Link: Runbook https://example.com/rb" in text
    assert "Link: Console https://x.example/${PROJECT_ID}" in text
    assert "Google labels: team=sre, tier=1" in text
    assert m.tags == ["team:sre", "tier:1"]
    assert m.emit is not None and m.emit.runbook_url == "https://example.com/rb"
    assert m.notification.message_template == "body"


def test_a_templated_link_is_not_used_as_the_runbook_url() -> None:
    m = one(
        threshold(),
        documentation={"links": [{"displayName": "c", "url": "https://x.example/${A}"}]},
    )
    assert m.emit is not None and m.emit.runbook_url is None


def test_hostile_text_is_cut_and_printable_in_the_description() -> None:
    m = one(
        threshold(),
        documentation={"subject": "a\x1b[31m" + "b" * 1000},
        userLabels={"k\n": "v\x00"},
    )
    text = description(m)
    assert "\x1b" not in text and "\x00" not in text
    assert len(text) < 700
    payload = nerdgraph_payload(m)
    assert "\x1b" not in str(payload)


def test_label_extractors_are_preserved_for_a_log_match() -> None:
    m = one(
        {
            "conditionMatchedLog": {
                "filter": 'severity="ERROR"',
                "labelExtractors": {"host": "EXTRACT(jsonPayload.host)"},
            }
        }
    )
    assert "Google log label extractors: host: EXTRACT(jsonPayload.host)" in description(m)
    assert "labelExtractors" in reasons(m)


# --- evaluationMissingData ---------------------------------------------------


def test_missing_data_active_opens_on_loss_of_signal() -> None:
    m = one(threshold(evaluationMissingData="EVALUATION_MISSING_DATA_ACTIVE"))
    assert m.no_data.notify and m.no_data.timeout_minutes == 10
    cond = nerdgraph_payload(m)["variables"]["condition"]
    assert cond["expiration"]["openViolationOnExpiration"] is True
    assert "ACTIVE becomes loss of signal" in reasons(m)


def test_missing_data_inactive_closes_on_loss_of_signal() -> None:
    m = one(threshold(evaluationMissingData="EVALUATION_MISSING_DATA_INACTIVE"))
    # the saved IR must not say "notify": nothing is opened on missing data, open violations close
    assert not m.no_data.notify and m.no_data.timeout_minutes == 10
    assert m.emit is not None and m.emit.open_violation_on_expiration is False
    cond = nerdgraph_payload(m)["variables"]["condition"]
    assert cond["expiration"] == {
        "expirationDuration": 600,
        "openViolationOnExpiration": False,
        "closeViolationsOnExpiration": True,
    }
    assert "closes open violations" in reasons(m)


def test_missing_data_no_op_is_noted_and_sets_nothing() -> None:
    m = one(threshold(evaluationMissingData="EVALUATION_MISSING_DATA_NO_OP"))
    assert not m.no_data.notify
    assert "NO_OP keeps the incident" in reasons(m)


# --- forecast ----------------------------------------------------------------


def test_forecast_becomes_the_predict_future_option() -> None:
    m = one(threshold(forecastOptions={"forecastHorizon": "14400s"}))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.emit is not None and m.emit.predict_by_seconds == 14400
    terms = nerdgraph_payload(m)["variables"]["condition"]["terms"]
    assert terms[0]["prediction"] == {"predictBy": 14400}
    assert "beta" in reasons(m)


def test_a_forecast_horizon_outside_googles_range_stays_manual() -> None:
    m = one(threshold(forecastOptions={"forecastHorizon": "60s"}))
    assert m.confidence is Confidence.MANUAL and "1 to 60 hours" in m.reasons[0]


# --- trigger -----------------------------------------------------------------


def test_threshold_duration_must_hold_for_every_window() -> None:
    assert one(threshold()).signal.occurrences is Occurrences.ALL
    assert one(LOG).signal.occurrences is Occurrences.AT_LEAST_ONCE


def test_a_trigger_is_moot_when_the_series_are_reduced_to_one() -> None:
    agg = {"filter": f'metric.type="{CPU}"', "aggregations": [REDUCED]}
    m = one(threshold(**agg, trigger={"percent": 50}))
    assert m.confidence is Confidence.APPROXIMATE
    assert "a single series" in reasons(m)


def test_a_trigger_on_several_series_stays_manual_with_the_numbers() -> None:
    m = one(threshold(trigger={"count": 3}))
    assert m.confidence is Confidence.MANUAL
    assert "3 time series" in m.reasons[0]
    m = one(threshold(trigger={"percent": 50}))
    assert "50% of the time series" in m.reasons[0]


def absence(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "filter": f'metric.type="{CPU}"',
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
        "duration": "600s",
    }
    return {"conditionAbsent": {**body, **overrides}}


def test_absence_trigger_percent_is_stated() -> None:
    m = one(absence(trigger={"percent": 50}))
    assert m.confidence is Confidence.APPROXIMATE
    assert "50% of the time series" in reasons(m)


def test_absence_trigger_one_series_has_no_trigger_note() -> None:
    assert "of the time series" not in reasons(one(absence(trigger={"count": 1})))


@pytest.mark.parametrize(
    "aligner", ["ALIGN_COUNT", "ALIGN_STDDEV", "ALIGN_PERCENT_CHANGE", "ALIGN_FRACTION_TRUE"]
)
def test_any_aligner_is_fine_for_an_absence_check(aligner: str) -> None:
    m = one(
        absence(
            aggregations=[
                {
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": aligner,
                    "crossSeriesReducer": "REDUCE_PERCENTILE_95",
                }
            ]
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert "do not change what an absence check sees" in reasons(m)


# --- aligners and reducers ---------------------------------------------------


def agg(aligner: str, reducer: str = "REDUCE_NONE", period: str = "300s") -> dict[str, object]:
    return {
        "filter": f'metric.type="{CPU}"',
        "aggregations": [
            {
                "alignmentPeriod": period,
                "perSeriesAligner": aligner,
                "crossSeriesReducer": reducer,
                "groupByFields": ["resource.label.zone"],
            }
        ],
    }


def test_reducer_percentile_95_over_one_point_per_series_is_percentile() -> None:
    m = one(threshold(**agg("ALIGN_MEAN", "REDUCE_PERCENTILE_95")))
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == (
        "SELECT percentile(gcp.compute.instance.cpu.utilization, 95) FROM Metric FACET gcp.zone"
    )


@pytest.mark.parametrize("p", [50, 99])
def test_other_percentile_reducers(p: int) -> None:
    m = one(threshold(**agg("ALIGN_MAX", f"REDUCE_PERCENTILE_{p}")))
    assert f"percentile(gcp.compute.instance.cpu.utilization, {p})" in nrql_for(m)


def test_percentile_reducer_over_a_longer_period_stays_manual() -> None:
    m = one(threshold(**agg("ALIGN_MAX", "REDUCE_PERCENTILE_95", period="3600s")))
    assert m.confidence is Confidence.MANUAL
    assert "several polls" in m.reasons[0]


def test_reducer_stddev_over_one_point_per_series() -> None:
    m = one(threshold(**agg("ALIGN_MEAN", "REDUCE_STDDEV")))
    assert nrql_for(m).startswith("SELECT stddev(gcp.compute.instance.cpu.utilization)")


def test_aligner_stddev_needs_several_polls_in_the_period() -> None:
    m = one(threshold(**agg("ALIGN_STDDEV", period="900s")))
    assert nrql_for(m).startswith("SELECT stddev(gcp.compute.instance.cpu.utilization)")
    short = one(threshold(**agg("ALIGN_STDDEV", period="300s")))
    assert short.confidence is Confidence.MANUAL and "one point" in short.reasons[0]


@pytest.mark.parametrize(
    ("aligner", "needle"),
    [
        ("ALIGN_COUNT", "samples"),
        ("ALIGN_COUNT_TRUE", "boolean"),
        ("ALIGN_COUNT_FALSE", "boolean"),
        ("ALIGN_FRACTION_TRUE", "boolean"),
        ("ALIGN_PERCENT_CHANGE", "percent change"),
        ("ALIGN_PERCENTILE_05", "5th percentile"),
    ],
)
def test_aligners_without_a_correct_query_are_manual_with_a_reason(
    aligner: str, needle: str
) -> None:
    m = one(threshold(**agg(aligner)))
    assert m.confidence is Confidence.MANUAL and needle in m.reasons[0]


@pytest.mark.parametrize("reducer", ["REDUCE_COUNT", "REDUCE_COUNT_TRUE", "REDUCE_FRACTION_TRUE"])
def test_reducers_without_a_correct_query_are_manual_with_a_reason(reducer: str) -> None:
    m = one(threshold(**agg("ALIGN_MEAN", reducer)))
    assert m.confidence is Confidence.MANUAL and reducer in m.reasons[0]
