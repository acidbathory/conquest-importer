"""Azure metric alerts: several criteria in one query, dynamic thresholds, dimension operators.

Expected NRQL is written by hand from the rules in docs/research/azure-monitor-alerting.md and the
cookbook method 'AND of two tests with if()'.
"""

from typing import Any

import pytest

from azure_helpers import VM, criterion, metric_alert
from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.ir import Confidence, Operator
from importer.sources.azure.translate import translate_alert

WHERE = f"WHERE collector.name = 'azure-monitor' AND azure.resourceId = '{VM}'"


def one(alert: Any) -> Any:
    (monitor,) = translate_alert(alert)
    return monitor


def reasons(monitor: Any) -> str:
    return " | ".join(monitor.reasons)


NET = {"name": "net", "metricName": "Network In Total", "timeAggregation": "Total",
       "operator": "GreaterThanOrEqual", "threshold": 1000}  # fmt: skip


def test_two_criteria_become_one_query_with_an_if_indicator() -> None:
    m = one(metric_alert(criterion(), criterion(**NET)))
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m) == (
        "SELECT if(average(azure.compute.virtualmachines.PercentageCPU) > 80 AND "  # noqa: S608
        "sum(azure.compute.virtualmachines.NetworkInTotal) >= 1000, 1, 0) "
        f"FROM Metric {WHERE}"
    )
    assert [(t.operator, t.value) for t in m.thresholds] == [(Operator.ABOVE, 0.0)]
    assert "all 2 criteria" in reasons(m) and "if(" in reasons(m)
    assert m.data_path == "native"


def test_every_operator_has_an_if_form_and_big_numbers_have_no_exponent() -> None:
    crits = [
        criterion(name="a", operator="LessThan", threshold=1.5),
        criterion(name="b", operator="Equals", threshold=1e9, metricName="Network In Total"),
        criterion(
            name="c", operator="LessThanOrEqual", threshold=2, metricName="Network Out Total"
        ),
    ]
    m = one(metric_alert(*crits))
    query = nrql_for(m)
    assert "PercentageCPU) < 1.5 AND" in query
    assert "NetworkInTotal) = 1000000000 AND" in query
    assert "NetworkOutTotal) <= 2, 1, 0)" in query


def test_count_aggregation_inside_the_indicator() -> None:
    m = one(metric_alert(criterion(), criterion(**NET | {"timeAggregation": "Count"})))
    assert "count(azure.compute.virtualmachines.NetworkInTotal) >= 1000" in nrql_for(m)


def test_criteria_with_the_same_dimensions_share_the_facet() -> None:
    dim = [{"name": "Instance", "operator": "Include", "values": ["*"]}]
    m = one(metric_alert(criterion(dimensions=dim), criterion(dimensions=dim, **NET)))
    assert m.confidence is Confidence.APPROXIMATE
    assert nrql_for(m).endswith(f"{WHERE} FACET azure.compute.virtualmachines.Instance")


def test_criteria_with_different_dimensions_stay_manual_with_each_one_alone() -> None:
    dim = [{"name": "Instance", "operator": "Include", "values": ["a"]}]
    m = one(metric_alert(criterion(dimensions=dim), criterion(**NET)))
    assert m.confidence is Confidence.MANUAL
    assert "do not share the same dimensions" in reasons(m)
    assert any("alone" in s for s in m.remediation)


def test_a_criterion_that_cannot_convert_keeps_all_manual() -> None:
    m = one(metric_alert(criterion(), criterion(metricNamespace="Custom/ns", **NET)))
    assert m.confidence is Confidence.MANUAL and "several criteria" in reasons(m)


def test_the_multi_criteria_condition_emits() -> None:
    m = one(metric_alert(criterion(), criterion(**NET)))
    condition = nerdgraph_payload(m)["variables"]["condition"]
    assert condition["terms"][0]["threshold"] == 0.0
    assert condition["nrql"]["query"].startswith("SELECT if(")


@pytest.mark.parametrize("op", ["GreaterThan", "LessThan", "GreaterOrLessThan"])
def test_dynamic_operators_map_to_a_baseline_direction(op: str) -> None:
    direction = {
        "GreaterThan": "upper_only",
        "LessThan": "lower_only",
        "GreaterOrLessThan": "upper_and_lower",
    }[op]
    dyn = {
        "criterionType": "DynamicThresholdCriterion",
        "alertSensitivity": "Medium",
        "operator": op,
        "failingPeriods": {"numberOfEvaluationPeriods": 4, "minFailingPeriodsToAlert": 3},
        "ignoreDataBefore": "2026-11-01T00:00:00.000Z",
    }
    dyn_crit = criterion(**dyn)
    del dyn_crit["threshold"]
    m = one(metric_alert(dyn_crit, windowSize="PT15M", evaluationFrequency="PT5M"))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.baseline is not None
    assert m.signal.baseline.direction == direction
    assert m.signal.baseline.deviations == 3.0
    assert nrql_for(m) == (
        "SELECT average(azure.compute.virtualmachines.PercentageCPU) "  # noqa: S608
        f"FROM Metric {WHERE}"
    )
    assert m.signal.aggregation_window_seconds == 900
    assert m.signal.window_seconds == 2700  # 3 failing periods of 15 minutes
    text = reasons(m)
    assert "UNVERIFIED" in text and "not equivalent" in text and "ignoreDataBefore" in text
    assert "3 failing periods out of 4" in text


@pytest.mark.parametrize(("sens", "dev"), [("High", 2.0), ("Medium", 3.0), ("Low", 4.0)])
def test_sensitivity_maps_to_documented_default_deviations(sens: str, dev: float) -> None:
    crit = criterion(criterionType="DynamicThresholdCriterion", alertSensitivity=sens)
    crit.pop("threshold")
    m = one(metric_alert(crit))
    assert m.signal.baseline is not None and m.signal.baseline.deviations == dev
    condition = nerdgraph_payload(m)["variables"]["condition"]
    assert condition["baselineDirection"] == "UPPER_ONLY"
    assert condition["terms"][0]["threshold"] == dev


def test_a_dynamic_criterion_with_an_unknown_sensitivity_is_manual() -> None:
    crit = criterion(criterionType="DynamicThresholdCriterion", alertSensitivity="Extreme")
    crit.pop("threshold")
    m = one(metric_alert(crit))
    assert m.confidence is Confidence.MANUAL and "dynamic threshold" in reasons(m)


def test_dimension_operator_starts_with_becomes_a_like() -> None:
    dim = [{"name": "ApiName", "operator": "StartsWith", "values": ["Get", "Put"]}]
    m = one(metric_alert(criterion(dimensions=dim)))
    assert m.confidence is Confidence.MANUAL  # two prefixes would need OR of LIKE
    one_dim = [{"name": "ApiName", "operator": "StartsWith", "values": ["Get"]}]
    m = one(metric_alert(criterion(dimensions=one_dim)))
    assert m.confidence is Confidence.APPROXIMATE
    assert "azure.compute.virtualmachines.ApiName LIKE 'Get%'" in nrql_for(m)


def test_the_data_path_names_what_must_be_in_place() -> None:
    assert one(metric_alert()).data_path == "native"
    prom = make_promql_alert()
    assert one(prom).data_path == "needs-prometheus-remote-write"


def make_promql_alert() -> Any:
    from azure_helpers import make

    props = {
        "severity": 2,
        "scopes": [VM],
        "evaluationFrequency": "PT5M",
        "windowSize": "PT5M",
        "criteria": {
            "odata.type": "Microsoft.Azure.Monitor.PromQLCriteria",
            "allOf": [{"query": "up > 0", "criterionType": "StaticThresholdCriterion"}],
        },
    }
    return make("metricAlerts", props)
