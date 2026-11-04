"""CloudWatch anomaly-detection alarms become New Relic baseline conditions."""

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from importer.core.apply import NewRelicClient, Plan, PlanItem, apply_plan
from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import ACCOUNT_PLACEHOLDER, BASELINE_MUTATION, nerdgraph_payload
from importer.core.emit.terraform import resource_block
from importer.core.ir import Confidence, IRMonitor
from importer.core.remediation import steps_for
from importer.sources.cloudwatch.models import CloudWatchAlarm
from importer.sources.cloudwatch.translate import load_namespace_map, translate_alarm


def anomaly_alarm(
    operator: str = "GreaterThanUpperThreshold",
    expression: str = "ANOMALY_DETECTION_BAND(m1, 3)",
    stat: str = "Average",
    **extra: Any,
) -> CloudWatchAlarm:
    return CloudWatchAlarm.model_validate(
        {
            "AlarmName": "cpu-anomaly",
            "ComparisonOperator": operator,
            "EvaluationPeriods": 2,
            "ThresholdMetricId": "ad1",
            "Metrics": [
                {
                    "Id": "m1",
                    "ReturnData": True,
                    "MetricStat": {
                        "Metric": {
                            "Namespace": "AWS/EC2",
                            "MetricName": "CPUUtilization",
                            "Dimensions": [{"Name": "InstanceId", "Value": "i-0abc"}],
                        },
                        "Period": 300,
                        "Stat": stat,
                    },
                },
                {"Id": "ad1", "Expression": expression, "ReturnData": True},
            ],
            **extra,
        }
    )


def convert(alarm: CloudWatchAlarm) -> IRMonitor:
    return translate_alarm(alarm, load_namespace_map())


@pytest.mark.parametrize(
    ("operator", "direction"),
    [
        ("GreaterThanUpperThreshold", "upper_only"),
        ("LessThanLowerThreshold", "lower_only"),
        ("LessThanLowerOrGreaterThanUpperThreshold", "upper_and_lower"),
    ],
)
def test_the_band_direction_and_width_carry_over(operator: str, direction: str) -> None:
    monitor = convert(anomaly_alarm(operator))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert monitor.signal.baseline is not None
    assert monitor.signal.baseline.direction == direction
    assert monitor.signal.baseline.deviations == 3.0
    assert nrql_for(monitor).startswith("SELECT average(aws.ec2.CPUUtilization) FROM Metric WHERE")
    assert any("baseline condition" in r for r in monitor.reasons)


def test_the_default_band_is_two_deviations_wide() -> None:
    monitor = convert(anomaly_alarm(expression="ANOMALY_DETECTION_BAND(m1)"))
    assert monitor.signal.baseline is not None and monitor.signal.baseline.deviations == 2.0


def test_a_percentile_source_metric_still_converts() -> None:
    monitor = convert(anomaly_alarm(stat="p99"))
    assert monitor.confidence is Confidence.APPROXIMATE
    assert ".p99" in nrql_for(monitor) or "p99" in " ".join(monitor.reasons)


def test_the_nerdgraph_payload_is_a_baseline_condition() -> None:
    payload = nerdgraph_payload(convert(anomaly_alarm()))
    condition = payload["variables"]["condition"]
    assert payload["query"] == BASELINE_MUTATION
    assert "alertsNrqlConditionBaselineCreate" in payload["query"]
    assert condition["baselineDirection"] == "UPPER_ONLY" and "valueFunction" not in condition
    assert condition["enabled"] is False
    (term,) = condition["terms"]
    assert term["operator"] == "ABOVE" and term["threshold"] == 3.0
    assert term["thresholdOccurrences"] == "ALL" and term["thresholdDuration"] == 600


def test_the_terraform_is_a_baseline_condition() -> None:
    text = resource_block(convert(anomaly_alarm("LessThanLowerThreshold")))
    assert 'type      = "baseline"' in text
    assert 'baseline_direction = "lower_only"' in text
    assert 'operator              = "above"' in text and "threshold             = 3.0" in text
    assert text.index("baseline_direction") < text.index("nrql {")


@pytest.mark.parametrize(
    "expression",
    ["ANOMALY_DETECTION_BAND(m9, 2)", "m1 * 2", "ANOMALY_DETECTION_BAND(m1, 2) + 1"],
)
def test_bands_that_are_not_plain_stay_manual_with_steps(expression: str) -> None:
    monitor = convert(anomaly_alarm(expression=expression))
    assert monitor.confidence is Confidence.MANUAL
    assert "anomaly detection" in " ".join(monitor.reasons)
    assert steps_for(monitor.reasons)


def test_apply_reads_the_id_of_a_created_baseline_condition() -> None:
    payload = nerdgraph_payload(convert(anomaly_alarm()))
    assert payload["variables"]["accountId"] == ACCOUNT_PLACEHOLDER

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "policiesSearch" in query:
            data: dict[str, Any] = {
                "actor": {
                    "account": {"alerts": {"policiesSearch": {"policies": [], "nextCursor": None}}}
                }
            }
        elif "nrqlConditionsSearch" in query:
            data = {
                "actor": {
                    "account": {
                        "alerts": {
                            "nrqlConditionsSearch": {"nrqlConditions": [], "nextCursor": None}
                        }
                    }
                }
            }
        elif "alertsPolicyCreate" in query:
            data = {"alertsPolicyCreate": {"id": "55", "name": "P"}}
        else:
            data = {"alertsNrqlConditionBaselineCreate": {"id": "9", "name": "n"}}
        return httpx.Response(200, json={"data": data})

    plan = Plan(
        items=[PlanItem(source_id="1", name="cpu-anomaly", nrql="SELECT 1", payload=payload)],
        skipped=0,
    )
    client = NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(handler))
    with client:
        result = apply_plan(client, 7, "P", plan)
    assert result.created == ["cpu-anomaly"]
    assert [(o.kind, o.id) for o in result.objects] == [("policy", "55"), ("condition", "9")]
