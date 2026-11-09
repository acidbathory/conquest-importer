"""Metric events, Davis detectors and the inventory rows, converted to IR."""

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest

from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, IRMonitor, Operator, Severity, SignalKind
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.models import SettingsObject, load_objects, parse_object
from importer.sources.dynatrace.classic.translate import translate

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-classic"
CPU = "builtin:host.cpu.usage"


def event(
    *,
    query: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    enabled: bool = True,
    summary: str = "CPU high",
    object_id: str = "ev-1",
) -> SettingsObject:
    value = {
        "enabled": enabled,
        "summary": summary,
        "queryDefinition": {
            "type": "METRIC_KEY",
            "metricKey": CPU,
            "aggregation": "AVG",
            "entityFilter": {"dimensionKey": "dt.entity.host", "conditions": []},
            "dimensionFilter": [],
            **(query or {}),
        },
        "modelProperties": {
            "type": "STATIC_THRESHOLD",
            "threshold": 90,
            "alertCondition": "ABOVE",
            "alertOnNoData": False,
            "violatingSamples": 3,
            "samples": 3,
            "dealertingSamples": 3,
            **(model or {}),
        },
        "eventTemplate": {"title": "t", "description": "d", "eventType": "RESOURCE"},
    }
    return parse_object(
        {
            "objectId": object_id,
            "schemaId": models.METRIC_EVENTS,
            "scope": "environment",
            "value": value,
        }
    )


def one(obj: SettingsObject) -> IRMonitor:
    (monitor,) = translate([obj])
    return monitor


def text(monitor: IRMonitor) -> str:
    return " | ".join(monitor.reasons)


# ----- metric events: static ------------------------------------------------


def test_a_static_threshold_on_a_mapped_key_becomes_a_condition() -> None:
    m = one(event())
    assert m.confidence is Confidence.APPROXIMATE and m.source_system == "dynatrace-classic"
    assert m.signal.kind is SignalKind.METRIC and m.name == "CPU high"
    assert (m.signal.window_seconds, m.signal.aggregation_window_seconds) == (180, 60)
    assert m.signal.occurrences.value == "all"
    (threshold,) = m.thresholds
    assert (threshold.severity, threshold.operator, threshold.value) == (
        Severity.CRITICAL,
        Operator.ABOVE,
        90.0,
    )
    assert nrql_for(m) == "SELECT average(cpuPercent) FROM SystemSample"
    assert "metric name mapping is unverified" in text(m)
    assert "data prerequisite:" in text(m) and "infrastructure agent" in text(m)
    assert "dealertingSamples" in text(m)


def test_all_consecutive_samples_need_no_approximation_note() -> None:
    assert "stricter" not in text(one(event()))


def test_n_of_m_samples_becomes_n_consecutive_and_says_it_is_stricter() -> None:
    m = one(event(model={"violatingSamples": 3, "samples": 5}))
    assert m.signal.window_seconds == 180 and m.signal.occurrences.value == "all"
    assert "3 of 5" in text(m) and "stricter" in text(m)


def test_below_maps_and_outside_needs_a_baseline() -> None:
    assert one(event(model={"alertCondition": "BELOW"})).thresholds[0].operator is Operator.BELOW
    m = one(event(model={"alertCondition": "OUTSIDE"}))
    assert m.confidence is Confidence.MANUAL and "OUTSIDE" in text(m)


def test_no_data_alerting_becomes_loss_of_signal() -> None:
    m = one(event(model={"alertOnNoData": True, "violatingSamples": 4, "samples": 4}))
    assert m.no_data.notify and m.no_data.timeout_minutes == 4
    assert "loss of signal" in text(m)
    assert not one(event()).no_data.notify


def test_query_offset_only_moves_the_aggregation_delay() -> None:
    m = one(event(query={"queryOffset": 5}))
    assert m.signal.aggregation_delay_seconds == 300 and m.signal.window_seconds == 180
    assert one(event(query={"queryOffset": 1})).signal.aggregation_delay_seconds == 120
    assert one(event()).signal.aggregation_delay_seconds == 120
    assert one(event(query={"queryOffset": 100})).confidence is Confidence.MANUAL


def test_a_disabled_event_is_still_converted_and_says_so() -> None:
    m = one(event(enabled=False))
    assert m.confidence is Confidence.APPROXIMATE and m.unmapped["enabled"] is False
    assert "was disabled" in text(m)


def test_units_are_scaled_into_new_relic_units() -> None:
    m = one(
        event(
            query={"metricKey": "builtin:service.response.time"},
            model={"threshold": 2_000_000},
        )
    )
    assert m.thresholds[0].value == pytest.approx(2.0)
    assert nrql_for(m) == "SELECT average(duration) FROM Transaction"


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        ("MAX", "SELECT max(cpuPercent) FROM SystemSample"),
        ("MIN", "SELECT min(cpuPercent) FROM SystemSample"),
        ("SUM", "SELECT sum(cpuPercent) FROM SystemSample"),
        ("COUNT", "SELECT count(cpuPercent) FROM SystemSample"),
        ("MEDIAN", "SELECT percentile(cpuPercent, 50) FROM SystemSample"),
        ("PERCENTILE90", "SELECT percentile(cpuPercent, 90) FROM SystemSample"),
        ("VALUE", "SELECT latest(cpuPercent) FROM SystemSample"),
    ],
)
def test_key_mode_aggregations(aggregation: str, expected: str) -> None:
    assert nrql_for(one(event(query={"aggregation": aggregation}))) == expected


def test_an_unknown_aggregation_is_manual() -> None:
    m = one(event(query={"aggregation": "STDDEV"}))
    assert m.confidence is Confidence.MANUAL and "aggregation" in text(m)


@pytest.mark.parametrize(
    "model",
    [
        {"violatingSamples": 0},
        {"violatingSamples": 6, "samples": 5},
        {"violatingSamples": 5000, "samples": 5000},
        {"samples": 0, "violatingSamples": 0},
        {"type": "MAGIC"},
        {"threshold": None},
    ],
)
def test_impossible_sample_counts_and_types_are_manual(model: dict[str, Any]) -> None:
    assert one(event(model=model)).confidence is Confidence.MANUAL


def test_every_converted_threshold_is_finite() -> None:
    m = one(event(query={"metricKey": "builtin:service.response.time"}, model={"threshold": 1e308}))
    assert math.isfinite(m.thresholds[0].value)


# ----- the metric map ---------------------------------------------------------


def test_a_key_with_no_map_row_is_manual_and_says_what_to_install() -> None:
    m = one(event(query={"metricKey": "builtin:host.disk.free"}))
    assert m.confidence is Confidence.MANUAL
    assert "no New Relic signal is known for Dynatrace metric" in text(m)
    assert "infrastructure agent" in text(m)
    m = one(event(query={"metricKey": "my.custom.metric"}))
    assert "OpenTelemetry" in text(m)


def test_a_hostile_metric_key_is_quoted_in_the_reason() -> None:
    m = one(event(query={"metricKey": 'x"\nDROP‮\x1b[31m'}))
    reason = text(m)
    assert m.confidence is Confidence.MANUAL
    assert "\n" not in reason and "\x1b" not in reason and "‮" not in reason


# ----- filters ---------------------------------------------------------------


def dimension(op: str, value: str, key: str = "dt.entity.host") -> dict[str, Any]:
    return {"dimensionFilter": [{"dimensionKey": key, "operator": op, "dimensionValue": value}]}


@pytest.mark.parametrize(
    ("op", "value", "clause"),
    [
        ("EQUALS", "web-1", "hostname = 'web-1'"),
        ("DOES_NOT_EQUAL", "web-1", "hostname != 'web-1'"),
        ("STARTS_WITH", "web", "hostname LIKE 'web%'"),
        ("DOES_NOT_START_WITH", "web", "hostname NOT LIKE 'web%'"),
        ("CONTAINS_CASE_SENSITIVE", "eb", "hostname LIKE '%eb%'"),
        ("DOES_NOT_CONTAIN_CASE_SENSITIVE", "eb", "hostname NOT LIKE '%eb%'"),
        ("EQUALS", "it's", "hostname = 'it\\'s'"),
    ],
)
def test_dimension_filters_become_where_clauses(op: str, value: str, clause: str) -> None:
    m = one(event(query=dimension(op, value)))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m).endswith("WHERE " + clause)


@pytest.mark.parametrize(
    "case",
    [
        dimension("EQUALS", "x", key="not.in.the.map"),
        dimension("STARTS_WITH", "100%"),
        dimension("STARTS_WITH", "a_b"),
        dimension("CONTAINS_CASE_SENSITIVE", "a*b"),
        dimension("EQUALS", "line\nbreak"),
        dimension("MATCHES_REGEX", "x"),
        {"managementZone": "-189204438944455158"},
        {
            "entityFilter": {
                "dimensionKey": "dt.entity.host",
                "conditions": [{"type": "TAG", "operator": "EQUALS", "value": "env:prod"}],
            }
        },
        {
            "entityFilter": {
                "dimensionKey": "dt.entity.host",
                "conditions": [{"type": "ENTITY_ID", "operator": "EQUALS", "value": "HOST-1"}],
            }
        },
        {
            "entityFilter": {
                "dimensionKey": "dt.entity.disk",
                "conditions": [{"type": "NAME", "operator": "EQUALS", "value": "d"}],
            }
        },
    ],
)
def test_a_filter_that_cannot_be_expressed_makes_the_condition_manual(
    case: dict[str, Any],
) -> None:
    assert one(event(query=case)).confidence is Confidence.MANUAL


def test_an_entity_name_filter_becomes_a_where_clause_on_the_name_attribute() -> None:
    conditions = [{"type": "NAME", "operator": "STARTS_WITH", "value": "gke-"}]
    m = one(
        event(query={"entityFilter": {"dimensionKey": "dt.entity.host", "conditions": conditions}})
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m).endswith("WHERE hostname LIKE 'gke-%'")
    assert "AND" in text(m) or "combine" in text(m)


# ----- selector mode ---------------------------------------------------------


def selector(text_: str, **model: Any) -> SettingsObject:
    return event(query={"type": "METRIC_SELECTOR", "metricSelector": text_}, model=model)


def test_a_selector_with_split_and_filter_becomes_facet_and_where() -> None:
    m = one(
        selector(
            f'{CPU}:filter(eq("dt.entity.host","h1")):splitBy("dt.entity.host"):max',
        )
    )
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == (
        "SELECT max(cpuPercent) FROM SystemSample WHERE hostname = 'h1' FACET hostname"
    )


def test_a_ratio_of_two_service_counts_becomes_one_expression() -> None:
    m = one(
        selector(
            '(builtin:service.errors.total.count:splitBy("dt.entity.service"):value)'
            ' / (builtin:service.requestCount.server:splitBy("dt.entity.service"):value) * 100',
        )
    )
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert nrql_for(m) == (
        "SELECT filter(count(*), WHERE error IS true) / count(*) * 100 "
        "FROM Transaction FACET appName"
    )


@pytest.mark.parametrize(
    "bad",
    [
        f"{CPU}:fold(avg)",
        f"{CPU}:avg,{CPU}:max",
        f"{CPU}:avg:avg",
        "(" * 5000,
        f'{CPU}:filter(in("dt.entity.host",entitySelector("type(HOST)")))',
    ],
)
def test_an_unsupported_selector_is_manual_with_the_reason(bad: str) -> None:
    m = one(selector(bad))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons and m.reasons[0].startswith("metric selector not converted")


@pytest.mark.parametrize(
    ("bad", "fragment"),
    [
        (f'{CPU}:splitBy("dt.entity.disk")', "dimension 'dt.entity.disk' is not in the metric map"),
        (f"({CPU}:avg) / (builtin:service.response.time:avg)", "different New Relic events"),
        (f"({CPU}:avg) / (builtin:service.response.time:avg) * 2", "different New Relic events"),
        ("(builtin:service.response.time:avg) / (builtin:service.requestCount.server)", "unit"),
    ],
)
def test_a_selector_the_map_cannot_serve_is_manual(bad: str, fragment: str) -> None:
    m = one(selector(bad))
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_a_selector_over_two_metrics_of_one_event_converts() -> None:
    m = one(selector(f"({CPU}:avg) / (builtin:host.mem.usage:avg)"))
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == (
        "SELECT average(cpuPercent) / average(memoryUsedPercent) FROM SystemSample"
    )


def test_a_selector_event_with_a_missing_selector_is_manual() -> None:
    assert one(selector("")).confidence is Confidence.MANUAL
    assert one(event(query={"metricKey": ""})).confidence is Confidence.MANUAL
    assert one(event(query={"type": "WHATEVER"})).confidence is Confidence.MANUAL


# ----- baselines --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("AUTO_ADAPTIVE_THRESHOLD", {"signalFluctuation": 1}),
        ("SEASONAL_BASELINE", {"tolerance": 4}),
    ],
)
@pytest.mark.parametrize(
    ("condition", "direction"),
    [("ABOVE", "upper_only"), ("BELOW", "lower_only"), ("OUTSIDE", "upper_and_lower")],
)
def test_baseline_models_become_baseline_conditions(
    kind: str, extra: dict[str, Any], condition: str, direction: str
) -> None:
    m = one(selector(f"{CPU}:avg", type=kind, alertCondition=condition, threshold=None, **extra))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.signal.baseline is not None and m.signal.baseline.direction == direction
    assert m.thresholds[0].value == m.signal.baseline.deviations == 3.0
    assert "sensitivity" in text(m) and "no principled conversion" in text(m)
    assert "weeks" in text(m)
    payload = nerdgraph_payload(m)
    assert payload["variables"]["condition"]["baselineDirection"] == direction.upper()


def test_a_baseline_on_a_metric_key_converts_with_a_warning() -> None:
    m = one(event(model={"type": "SEASONAL_BASELINE", "alertCondition": "OUTSIDE", "tolerance": 4}))
    assert m.signal.baseline is not None and "metric key" in text(m)


# ----- identity, escaping, round trip ----------------------------------------


def test_duplicate_object_ids_get_a_suffix_and_distinct_ids() -> None:
    a, b, c = translate([event(), event(), event()])
    assert (a.source_key, b.source_key, c.source_key) == ("ev-1", "ev-1#2", "ev-1#3")
    assert len({a.source_id, b.source_id, c.source_id}) == 3


def test_an_object_without_an_id_still_gets_a_stable_key() -> None:
    obj = parse_object({"schemaId": models.PROFILE, "value": {"name": "p"}})
    first, second = translate([obj, obj])
    assert first.source_key.startswith(models.PROFILE) and first.source_id != second.source_id


def test_a_hostile_summary_never_reaches_a_reason_or_the_query() -> None:
    hostile = "x' OR 1=1 --\n${var.secret} %{ if } | col | <img src=x>"
    m = one(event(summary=hostile, query=dimension("EQUALS", "v'; DROP TABLE x; --")))
    assert hostile not in text(m) and "DROP" not in text(m)
    assert "\\'; DROP TABLE x; --'" in nrql_for(m)  # inside a quoted, escaped literal
    assert "DROP TABLE x" not in terraform_file([m]).replace("\\'; DROP TABLE x; --", "")


def test_a_bad_object_is_one_manual_item_and_the_rest_convert() -> None:
    bad = parse_object({"objectId": "bad", "schemaId": models.METRIC_EVENTS, "value": {"x": 1}})
    good, broken = translate([event(), bad])
    assert good.confidence is Confidence.APPROXIMATE and broken.confidence is Confidence.MANUAL
    assert broken.reasons[0].startswith("the settings object could not be read")


def test_a_converted_event_emits_to_nerdgraph_and_terraform() -> None:
    m = one(event(query=dimension("EQUALS", "web-1"), model={"alertOnNoData": True}))
    payload = nerdgraph_payload(m)["variables"]["condition"]
    assert payload["nrql"]["query"].endswith("hostname = 'web-1'")
    assert payload["terms"][0]["thresholdDuration"] == 180
    assert payload["expiration"]["expirationDuration"] == 180
    assert "newrelic_nrql_alert_condition" in terraform_file([m])


# ----- inventory ----------------------------------------------------------------


def settings(schema: str, value: dict[str, Any], object_id: str = "o1") -> SettingsObject:
    return parse_object({"objectId": object_id, "schemaId": schema, "value": value})


INVENTORY_CASES: list[tuple[str, dict[str, Any], str]] = [
    (
        models.PROFILE,
        {"name": "Default", "severityRules": [{}], "eventFilters": []},
        "alerting profile is not converted",
    ),
    (
        models.NOTIFICATIONS,
        {"type": "SLACK", "displayName": "ch", "enabled": True},
        "problem notification is not converted",
    ),
    (
        models.MAINTENANCE,
        {
            "generalProperties": {"name": "w", "suppression": "DONT_DETECT_PROBLEMS"},
            "schedule": {"scheduleType": "WEEKLY"},
        },
        "maintenance window is not converted",
    ),
    (models.MAINTENANCE_NEW, {"name": "n", "filter": "x"}, "maintenance window is not converted"),
    (models.ZONES, {"name": "z", "rules": [{}, {}]}, "management zone is not converted"),
    (
        "builtin:anomaly-detection.services",
        {"failureRate": {}},
        "built-in anomaly detection setting is not converted",
    ),
    ("builtin:something.else", {"a": 1}, "settings schema is not converted"),
]


@pytest.mark.parametrize(("schema", "value", "prefix"), INVENTORY_CASES)
def test_inventory_objects_become_manual_rows(
    schema: str, value: dict[str, Any], prefix: str
) -> None:
    m = one(settings(schema, value))
    assert m.confidence is Confidence.MANUAL and m.reasons[0].startswith(prefix)
    assert m.signal.kind is SignalKind.UNKNOWN and not m.thresholds


def test_inventory_rows_take_their_name_from_the_object() -> None:
    names = [one(settings(s, v)).name for s, v, _ in INVENTORY_CASES[:5]]
    assert names == ["Default", "ch", "w", "n", "z"]


def test_hostile_text_in_inventory_objects_stays_out_of_reasons() -> None:
    hostile = "<script>alert(1)</script>|`x`\n${y}"
    m = one(
        settings(
            models.NOTIFICATIONS,
            {"type": hostile, "displayName": hostile, "enabled": True},
        )
    )
    assert hostile not in text(m) and "script" not in text(m)
    m = one(settings("builtin:" + "x" * 500 + "\n", {"a": 1}))
    assert "\n" not in text(m) and len(text(m)) < 500


# ----- Davis detectors ---------------------------------------------------------------


def detector(
    inputs: list[dict[str, Any]], analyzer: str = "StaticThresholdAnomalyDetectionAnalyzer"
) -> SettingsObject:
    return parse_object(
        {
            "objectId": "det-1",
            "schemaId": models.DETECTORS,
            "scope": "environment",
            "value": {
                "enabled": True,
                "title": "CPU detector",
                "analyzer": {
                    "name": f"dt.statistics.ui.anomaly_detection.{analyzer}",
                    "input": inputs,
                },
                "executionSettings": {"queryOffset": 2},
            },
        }
    )


GOOD = [
    {"key": "query", "value": "timeseries avg(dt.host.cpu.usage), by:{dt.entity.host}"},
    {"key": "threshold", "value": "90"},
    {"key": "alertCondition", "value": "ABOVE"},
    {"key": "alertOnMissingData", "value": "false"},
    {"key": "violatingSamples", "value": "3"},
    {"key": "slidingWindow", "value": "5"},
    {"key": "dealertingSamples", "value": "5"},
]


def with_input(key: str, value: object) -> list[dict[str, Any]]:
    return [*(i for i in GOOD if i["key"] != key), {"key": key, "value": value}]


def test_a_static_detector_on_a_simple_timeseries_converts() -> None:
    m = one(detector(GOOD))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.source_type == "davis anomaly detector"
    assert nrql_for(m) == "SELECT average(cpuPercent) FROM SystemSample FACET hostname"
    assert m.signal.window_seconds == 180 and m.signal.aggregation_delay_seconds == 120
    assert "stricter" in text(m) and "unverified" in text(m)


def test_detector_numbers_may_be_json_numbers() -> None:
    m = one(detector(with_input("threshold", 80)))
    assert m.thresholds[0].value == 80.0


@pytest.mark.parametrize(
    ("inputs", "fragment"),
    [
        (with_input("threshold", "high"), "threshold"),
        (with_input("threshold", "1e999"), "threshold"),
        (with_input("threshold", True), "threshold"),
        (with_input("alertCondition", "OUTSIDE"), "OUTSIDE"),
        (with_input("violatingSamples", "x"), "violatingSamples"),
        (with_input("query", "fetch logs | summarize count()"), "timeseries"),
        (
            with_input("query", "timeseries avg(dt.host.disk.free), by:{dt.entity.host}"),
            "no New Relic signal",
        ),
        (with_input("query", "timeseries p = percentile(dt.host.cpu.usage, 90)"), "timeseries"),
        (
            with_input("query", "timeseries avg(dt.host.cpu.usage), by:{dt.entity.disk}"),
            "dimension",
        ),
        (with_input("query", "timeseries avg(dt.host.cpu.usage), interval:1m"), "timeseries"),
        ([*GOOD, {"key": "threshold", "value": "81"}], "twice"),
        ([i for i in GOOD if i["key"] != "query"], "query"),
    ],
)
def test_a_detector_outside_the_narrow_pattern_is_manual(
    inputs: list[dict[str, Any]], fragment: str
) -> None:
    m = one(detector(inputs))
    assert m.confidence is Confidence.MANUAL
    assert fragment in text(m)


@pytest.mark.parametrize(
    ("analyzer", "fragment"),
    [
        ("AutoAdaptiveAnomalyDetectionAnalyzer", "baseline detectors"),
        ("SeasonalBaselineAnomalyDetectionAnalyzer", "baseline detectors"),
        ("RecordAnomalyDetectionAnalyzer", "records mode"),
        ("SomethingNewAnalyzer", "analyzer is not recognised"),
    ],
)
def test_other_analyzers_are_manual_with_their_own_reason(analyzer: str, fragment: str) -> None:
    m = one(detector(GOOD, analyzer))
    assert m.confidence is Confidence.MANUAL and fragment in text(m)


def test_a_detector_regex_is_safe_on_adversarial_queries() -> None:
    import time

    started = time.monotonic()
    for query in (
        "timeseries " + "avg(" * 20_000,
        "timeseries avg(a), by:{" + "a," * 50_000,
        "timeseries " + " " * 100_000 + "avg(a)",
    ):
        assert one(detector(with_input("query", query))).confidence is Confidence.MANUAL
    assert time.monotonic() - started < 2


# ----- fixtures --------------------------------------------------------------------


def all_fixture_objects() -> list[SettingsObject]:
    return [o for f in sorted(FIXTURES.glob("*.json")) for o in load_objects(f)]


def test_every_fixture_object_converts_without_raising_and_has_a_reason_if_manual() -> None:
    monitors = translate(all_fixture_objects())
    assert len(monitors) == len(all_fixture_objects()) >= 25
    for m in monitors:
        if m.confidence is Confidence.MANUAL:
            assert m.reasons, m.name
        assert m.source_system == "dynatrace-classic"


def test_fixture_monitors_have_unique_ids_and_survive_a_json_round_trip() -> None:
    monitors = translate(all_fixture_objects())
    assert len({m.source_id for m in monitors}) == len(monitors)
    for m in monitors:
        assert IRMonitor.model_validate_json(m.model_dump_json()) == m


def test_planted_secrets_and_hostile_text_appear_in_no_output() -> None:
    monitors = translate(all_fixture_objects())
    blob = " ".join(m.model_dump_json() + " ".join(m.reasons) for m in monitors)
    for planted in ("SHOULD-NEVER-BE-PRINTED", "MASKED", "jane@", "idp.example", "token="):
        assert planted not in blob
    for m in monitors:
        for reason in m.reasons:
            assert "\n" not in reason and "\r" not in reason and "\x00" not in reason
            assert "DROP TABLE" not in reason and "${var" not in reason


def test_converted_fixture_monitors_can_be_emitted() -> None:
    for m in translate(all_fixture_objects()):
        if m.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
            nrql_for(m)
            nerdgraph_payload(m)
            terraform_file([m])


def test_translation_does_not_change_its_input() -> None:
    objects = all_fixture_objects()
    before = copy.deepcopy([o.model_dump() for o in objects])
    translate(objects)
    assert [o.model_dump() for o in objects] == before
    json.dumps(before)
