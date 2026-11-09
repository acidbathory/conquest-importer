"""Shared builders for the Dynatrace classic tests."""

from typing import Any

from importer.core.ir import IRMonitor
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.models import SettingsObject, parse_object
from importer.sources.dynatrace.classic.translate import translate

APPS = "https://abc12345.apps.dynatrace.com"
LIVE = "https://abc12345.live.dynatrace.com"
CPU = "builtin:host.cpu.usage"
RT = "builtin:service.response.time"  # unit factor 1e-6
TOKEN = "dt0s16.ABCDEFGH.SECRETSECRETSECRETSECRET"  # noqa: S105 - made-up value


def event(
    query: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    template: dict[str, Any] | None = None,
    summary: str = "e",
    object_id: str = "ev-1",
) -> SettingsObject:
    return parse_object(
        {
            "objectId": object_id,
            "schemaId": models.METRIC_EVENTS,
            "value": {
                "summary": summary,
                "queryDefinition": {
                    "type": "METRIC_KEY",
                    "metricKey": CPU,
                    "aggregation": "AVG",
                    **(query or {}),
                },
                "modelProperties": {
                    "type": "STATIC_THRESHOLD",
                    "threshold": 90,
                    "alertCondition": "ABOVE",
                    "violatingSamples": 3,
                    "samples": 3,
                    **(model or {}),
                },
                "eventTemplate": template or {"eventType": "RESOURCE"},
            },
        }
    )


def sel(text: str, **model: Any) -> SettingsObject:
    return event({"type": "METRIC_SELECTOR", "metricSelector": text}, model)


GOOD = [
    {"key": "query", "value": "timeseries avg(dt.host.cpu.usage), by:{dt.entity.host}"},
    {"key": "threshold", "value": "90"},
    {"key": "alertCondition", "value": "ABOVE"},
    {"key": "alertOnMissingData", "value": "false"},
    {"key": "violatingSamples", "value": "3"},
    {"key": "slidingWindow", "value": "5"},
    {"key": "dealertingSamples", "value": "5"},
]


def detector(inputs: list[dict[str, Any]]) -> SettingsObject:
    return parse_object(
        {
            "objectId": "det-1",
            "schemaId": models.DETECTORS,
            "value": {
                "title": "d",
                "analyzer": {
                    "name": "dt.statistics.ui.anomaly_detection."
                    + "StaticThresholdAnomalyDetectionAnalyzer",
                    "input": inputs,
                },
            },
        }
    )


def one(obj: SettingsObject) -> IRMonitor:
    (m,) = translate([obj])
    return m
