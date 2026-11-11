"""Azure translation to IR: what converts, what is manual, and that converted rules emit."""

from typing import Any

import pytest

from azure_helpers import VM, VM2, criterion, log_rule, make, metric_alert
from importer.core.emit.common import nrql_for
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import terraform_file
from importer.core.ir import Confidence, Operator, Severity, SignalKind
from importer.sources.azure.models import NameMap
from importer.sources.azure.translate import translate, translate_alert


def one(alert: Any) -> Any:
    (monitor,) = translate_alert(alert)
    return monitor


def reasons(monitor: Any) -> str:
    return " | ".join(monitor.reasons)


def test_static_single_resource_metric_alert_converts_with_an_unverified_name() -> None:
    m = one(metric_alert())
    assert m.confidence is Confidence.APPROXIMATE
    expected = (
        "SELECT average(azure.compute.virtualmachines.PercentageCPU) FROM Metric "  # noqa: S608
        f"WHERE collector.name = 'azure-monitor' AND azure.resourceId = '{VM}'"
    )
    assert nrql_for(m) == expected
    assert "UNVERIFIED" in reasons(m)
    assert m.signal.window_seconds == m.signal.aggregation_window_seconds == 300
    assert m.emit is not None and m.emit.aggregation_method == "EVENT_TIMER"
    assert m.thresholds[0].operator is Operator.ABOVE and m.thresholds[0].value == 80


@pytest.mark.parametrize(
    ("severity", "level"),
    [(0, Severity.CRITICAL), (1, Severity.CRITICAL), (2, Severity.WARNING), (4, Severity.WARNING)],
)
def test_severity_collapses_to_two_levels_and_is_kept_in_the_description(
    severity: int, level: Severity
) -> None:
    m = one(metric_alert(severity=severity))
    assert m.thresholds[0].severity is level
    payload = nerdgraph_payload(m)
    assert f"Azure severity Sev{severity}" in payload["variables"]["condition"]["description"]


def test_an_out_of_range_severity_is_a_warning_with_a_note() -> None:
    m = one(metric_alert(severity=9))
    assert m.thresholds[0].severity is Severity.WARNING and "not 0 to 4" in reasons(m)


@pytest.mark.parametrize(
    ("agg", "expect"),
    [
        ("Average", "average("),
        ("Minimum", "min("),
        ("Maximum", "max("),
        ("Total", "sum("),
        ("Count", "count("),
    ],
)
def test_time_aggregations(agg: str, expect: str) -> None:
    m = one(metric_alert(criterion(timeAggregation=agg)))
    assert expect + "azure.compute" in nrql_for(m)


@pytest.mark.parametrize(
    ("op", "nr"),
    [
        ("GreaterThan", Operator.ABOVE),
        ("GreaterThanOrEqual", Operator.ABOVE_OR_EQUALS),
        ("LessThan", Operator.BELOW),
        ("LessThanOrEqual", Operator.BELOW_OR_EQUALS),
        ("Equals", Operator.EQUALS),
    ],
)
def test_operators(op: str, nr: Operator) -> None:
    assert one(metric_alert(criterion(operator=op))).thresholds[0].operator is nr


def test_frequency_below_window_uses_a_sliding_window() -> None:
    m = one(metric_alert(evaluationFrequency="PT1M", windowSize="PT15M"))
    assert m.emit is not None and m.emit.slide_by_seconds == 60
    assert nerdgraph_payload(m)["variables"]["condition"]["signal"]["slideBy"] == 60
    assert "sliding window" in reasons(m)


def test_a_frequency_that_does_not_divide_the_window_is_a_note_not_a_slide() -> None:
    m = one(metric_alert(evaluationFrequency="PT4M", windowSize="PT15M"))
    assert m.emit is not None and m.emit.slide_by_seconds is None
    assert "New Relic evaluates consecutive" in reasons(m)


def test_the_latency_note_is_always_present() -> None:
    assert "opens later than Azure" in reasons(one(metric_alert()))


def test_windows_over_an_hour_or_under_a_minute_are_manual() -> None:
    assert "longer than the one hour" in reasons(one(metric_alert(windowSize="PT2H")))
    assert "shorter than one minute" in reasons(one(metric_alert(windowSize="PT30S")))
    assert "missing or not an ISO" in reasons(
        one(metric_alert(windowSize="P1M", evaluationFrequency="bad"))
    )


def test_auto_mitigate_false_and_time_to_resolve_are_reported() -> None:
    m = one(metric_alert(autoMitigate=False, resolveConfiguration={"timeToResolve": "PT10M"}))
    text = reasons(m)
    assert "autoMitigate is false" in text and "timeToResolve PT10M" in text


def test_a_disabled_rule_is_noted() -> None:
    assert "disabled" in reasons(one(metric_alert(enabled=False)))


def test_several_resources_get_in_and_a_facet() -> None:
    m = one(
        metric_alert(
            scopes=[VM, VM2],
            criteria={
                "odata.type": "Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria",
                "allOf": [criterion()],
            },
        )
    )
    q = nrql_for(m)
    assert f"azure.resourceId IN ('{VM}', '{VM2}')" in q and q.endswith("FACET azure.resourceId")


def test_resource_group_and_subscription_scopes_use_a_prefix() -> None:
    rg = one(metric_alert(scopes=["/subscriptions/s1/resourceGroups/rg1"]))
    assert "azure.resourceId LIKE '/subscriptions/s1/resourceGroups/rg1/%'" in nrql_for(rg)
    sub = one(
        metric_alert(
            scopes=["/subscriptions/s1"], targetResourceType="Microsoft.Compute/virtualMachines"
        )
    )
    assert "LIKE '/subscriptions/s1/%'" in nrql_for(sub)


def test_several_resource_groups_are_manual_with_one_step_each() -> None:
    m = one(
        metric_alert(
            scopes=["/subscriptions/s1/resourceGroups/a", "/subscriptions/s1/resourceGroups/b"]
        )
    )
    assert m.confidence is Confidence.MANUAL and len(m.remediation) == 2


def test_dimensions_become_facets_and_filters() -> None:
    dims = [
        {"name": "Status Code", "operator": "Include", "values": ["200", "201"]},
        {"name": "Api", "operator": "Include", "values": ["*"]},
        {"name": "Region", "operator": "Exclude", "values": ["x"]},
    ]
    m = one(metric_alert(criterion(dimensions=dims)))
    q = nrql_for(m)
    assert "azure.compute.virtualmachines.StatusCode IN ('200', '201')" in q
    assert "azure.compute.virtualmachines.Region NOT IN ('x')" in q
    assert "FACET azure.compute.virtualmachines.StatusCode, azure.compute.virtualmachines.Api" in q
    assert "UNVERIFIED" in reasons(m)


def test_a_bad_dimension_is_manual() -> None:
    dims = [{"name": "a;b", "operator": "Include", "values": ["1"]}]
    assert one(metric_alert(criterion(dimensions=dims))).confidence is Confidence.MANUAL


def test_name_map_replaces_the_derived_name_and_says_so() -> None:
    names = NameMap(metrics={"Percentage CPU": "azure.vm.cpu"}, attributes={"Api": "azure.vm.api"})
    dims = [{"name": "Api", "operator": "Include", "values": ["*"]}]
    m = one(metric_alert(criterion(dimensions=dims), names=names))
    assert "average(azure.vm.cpu)" in nrql_for(m) and "FACET azure.vm.api" in nrql_for(m)
    assert "comes from name-map.json" in reasons(m)


def test_namespace_falls_back_to_the_target_type_and_the_scope() -> None:
    no_ns = criterion()
    del no_ns["metricNamespace"]
    assert "virtualmachines.PercentageCPU" in nrql_for(one(metric_alert(no_ns)))
    assert "virtualmachines.PercentageCPU" in nrql_for(
        one(metric_alert(no_ns, targetResourceType="Microsoft.Compute/virtualMachines"))
    )


def test_two_criteria_are_manual_and_each_is_listed_alone() -> None:
    m = one(
        metric_alert(
            criterion(name="cpu"), criterion(name="mem", metricName="Available Memory Bytes")
        )
    )
    assert m.confidence is Confidence.MANUAL and "several criteria" in m.reasons[0]
    assert len(m.remediation) == 2
    assert "PercentageCPU" in m.remediation[0] and "AvailableMemoryBytes" in m.remediation[1]


@pytest.mark.parametrize(
    ("crit", "text"),
    [
        (criterion(criterionType="DynamicThresholdCriterion"), "dynamic threshold"),
        (
            criterion(metricNamespace="Azure.VM.Windows.GuestMetrics"),
            "not an Azure platform namespace",
        ),
        (
            criterion(
                metricName="\\Processor(_Total)\\% Processor Time",
                metricNamespace="azure.vm.windows.guestmetrics",
            ),
            "not an Azure platform namespace",
        ),
        (criterion(operator="Approximately"), "operator is not supported"),
        (criterion(threshold="high"), "threshold is not a number"),
        (criterion(timeAggregation="Median"), "time aggregation is not supported"),
        (criterion(metricName=""), "cannot be written"),
        (
            criterion(criterionType="QuantumThresholdCriterion"),
            "unknown metric alert criteria type",
        ),
    ],
)
def test_metric_alert_manual_cases(crit: dict[str, Any], text: str) -> None:
    m = one(metric_alert(crit))
    assert m.confidence is Confidence.MANUAL and text in m.reasons[0]


def test_other_criteria_kinds_are_manual() -> None:
    web = metric_alert(
        criteria={"odata.type": "Microsoft.Azure.Monitor.WebtestLocationAvailabilityCriteria"}
    )
    assert "availability test" in one(web).reasons[0]
    future = metric_alert(criteria={"odata.type": "Microsoft.Azure.Monitor.FutureCriteria"})
    assert "unknown metric alert criteria type" in one(future).reasons[0]
    assert "no readable criteria" in one(metric_alert(criteria=None)).reasons[0]
    assert (
        "no readable criteria"
        in one(
            metric_alert(
                criteria={
                    "odata.type": "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
                    "allOf": [],
                }
            )
        ).reasons[0]
    )


def test_application_insights_scope_is_manual() -> None:
    scope = "/subscriptions/s/resourceGroups/r/providers/microsoft.insights/components/ai1"
    assert "Application Insights" in one(metric_alert(scopes=[scope])).reasons[0]


def test_every_converted_metric_alert_emits_to_nerdgraph_and_terraform() -> None:
    m = one(metric_alert(criterion(timeAggregation="Count")))
    assert nerdgraph_payload(m)["variables"]["condition"]["enabled"] is False
    assert "newrelic_nrql_alert_condition" in terraform_file([m])


def test_hostile_text_never_reaches_the_query_unescaped() -> None:
    dims = [{"name": "Api", "operator": "Include", "values": ["a' OR 1=1 --", "b\\"]}]
    m = one(metric_alert(criterion(dimensions=dims), name="evil\nname'; drop"))
    q = nrql_for(m)
    assert "a\\' OR 1=1 --" in q and "b\\\\" in q
    assert "\n" not in nerdgraph_payload(m)["variables"]["condition"]["name"]


# --- scheduled query rules -------------------------------------------------------------------


def test_a_count_over_a_table_with_filters_converts_to_a_log_count() -> None:
    m = one(log_rule('AzureDiagnostics | where Category == "Admin" and ResultType != "200"'))
    assert m.confidence is Confidence.APPROXIMATE and m.signal.kind is SignalKind.LOG
    assert nrql_for(m) == (
        "SELECT count(*) FROM Log WHERE plugin.type = 'azure' AND Category = 'Admin' "
        "AND ResultType != '200'"
    )
    assert m.thresholds[0].severity is Severity.CRITICAL and m.thresholds[0].value == 5
    assert "UNVERIFIED" in reasons(m) and "Event Hub" in reasons(m)
    assert nerdgraph_payload(m)["variables"]["condition"]["nrql"]["query"].startswith("SELECT")


def test_kql_operators_become_like_in_and_negations() -> None:
    q = (
        'T | where A contains "x" and B startswith "y" and C in ("1","2")'
        ' and D !in ("3") and E endswith "z"'
    )
    text = nrql_for(one(log_rule(q)))
    assert "A LIKE '%x%'" in text and "B LIKE 'y%'" in text
    assert "C IN ('1', '2')" in text and "D NOT IN ('3')" in text and "E LIKE '%z'" in text


def test_summarize_count_by_bin_and_dimension_facets() -> None:
    q = "T | where A == 'x' | summarize count() by bin(TimeGenerated, 5m), Resource"
    dims = [{"name": "Resource", "operator": "Include", "values": ["*"]}]
    m = one(log_rule(q, timeAggregation="Total", dimensions=dims))
    assert nrql_for(m).endswith("FACET Resource")


def test_summarize_by_a_column_that_is_not_a_dimension_is_manual() -> None:
    q = "T | summarize count() by bin(TimeGenerated, 5m), Resource"
    assert "not a rule dimension" in one(log_rule(q, timeAggregation="Total")).reasons[0]


def test_bin_size_must_match_the_window() -> None:
    q = "T | summarize count() by bin(TimeGenerated, 10m)"
    assert "bin size differs" in one(log_rule(q, timeAggregation="Total")).reasons[0]


def test_an_average_of_a_column_is_a_metric_on_log() -> None:
    q = "T | where A == 'x' | summarize avg(Latency) by bin(TimeGenerated, 5m)"
    m = one(log_rule(q, timeAggregation="Average"))
    assert nrql_for(m).startswith("SELECT average(Latency) FROM Log WHERE plugin.type = 'azure'")


def test_a_measure_column_that_is_not_a_plain_name_is_manual() -> None:
    m = one(log_rule('T | where A == "x"', metricMeasureColumn="a;b`c", timeAggregation="Average"))
    assert m.confidence is Confidence.MANUAL and "measures a column" in m.reasons[0]


def test_below_rules_are_null_safe() -> None:
    m = one(log_rule('T | where A == "x"', operator="LessThan", threshold=1))
    assert m.emit is not None and m.emit.null_safe_count is True
    assert nrql_for(m).startswith(
        "SELECT filter(count(*), WHERE A = 'x') FROM Log WHERE plugin.type"
    )


def test_failing_periods_extend_the_duration() -> None:
    fp = {"numberOfEvaluationPeriods": 4, "minFailingPeriodsToAlert": 3}
    m = one(log_rule('T | where A == "x"', failingPeriods=fp))
    assert m.signal.window_seconds == 900 and m.signal.aggregation_window_seconds == 300
    assert "3 failing periods out of 4" in reasons(m)


@pytest.mark.parametrize(
    ("query", "text"),
    [
        ("Heartbeat | summarize arg_max(TimeGenerated, *) by Computer", "arg_max"),
        ("T | join U on a", "join has no New Relic form"),
        ("T | where A =~ 'x'", "case-insensitive"),
        ("union T, U", "union has no New Relic form"),
        ("T | extend z = 1", "extend that computes a value"),
        ("T | where A == 'tab\\there'", "string escape"),
    ],
)
def test_kql_that_has_no_form_is_manual_with_the_reason(query: str, text: str) -> None:
    m = one(log_rule(query))
    assert m.confidence is Confidence.MANUAL and text in m.reasons[0]


def test_other_log_rule_shapes() -> None:
    assert (
        "publishes a metric" in one(make("scheduledQueryRules", {}, kind="LogToMetric")).reasons[0]
    )
    assert "kind is not an alert" in one(make("scheduledQueryRules", {}, kind="Future")).reasons[0]
    assert "legacy" in one(make("scheduledQueryRules", {"source": {}, "action": {}})).reasons[0]
    assert "no readable condition" in one(make("scheduledQueryRules", {})).reasons[0]
    two = log_rule("T")
    two.properties["criteria"]["allOf"].append({"query": "T"})
    assert "several conditions" in one(two).reasons[0]
    assert (
        "dynamic threshold"
        in one(log_rule("T", criterionType="DynamicThresholdCriterion")).reasons[0]
    )
    ai = log_rule("T")
    ai.properties["scopes"] = [
        "/subscriptions/s/resourceGroups/r/providers/microsoft.insights/components/a"
    ]
    assert "not data that New Relic receives" in one(ai).reasons[0]
    rng = log_rule("T")
    rng.properties["overrideQueryTimeRange"] = "P2D"
    assert "longer range" in one(rng).reasons[0]


def test_simple_log_alert_counts_records_over_a_minute() -> None:
    props = {
        "severity": 3,
        "scopes": [VM],
        "criteria": {"allOf": [{"query": "T | where A == 'x'", "minRecurrenceCount": 3}]},
    }
    m = one(make("scheduledQueryRules", props, kind="SimpleLogAlert"))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.aggregation_window_seconds == 60
    assert m.thresholds[0].operator is Operator.ABOVE_OR_EQUALS and m.thresholds[0].value == 3


def test_mute_actions_duration_is_noted() -> None:
    alert = log_rule("T | where A == 'x'")
    alert.properties["muteActionsDuration"] = "PT30M"
    assert "muteActionsDuration PT30M" in reasons(one(alert))


def test_kql_literals_are_escaped_in_the_query() -> None:
    m = one(log_rule("T | where A == 'x\"y'"))
    assert "A = 'x\"y'" in nrql_for(m)


# --- activity log, smart detector, Prometheus ------------------------------------------------


def test_activity_log_alerts_are_manual_with_the_condition_in_the_steps() -> None:
    props = {
        "scopes": [VM],
        "condition": {
            "allOf": [
                {"field": "category", "equals": "Administrative"},
                {
                    "anyOf": [
                        {"field": "level", "equals": "Error"},
                        {"field": "level", "equals": "Critical"},
                    ]
                },
            ]
        },
    }
    m = one(make("activityLogAlerts", props))
    assert m.confidence is Confidence.MANUAL and "activity log alert" in m.reasons[0]
    assert (
        "category = Administrative" in m.remediation[0]
        and "level = Error or level = Critical" in m.remediation[0]
    )


def test_service_health_alerts_have_their_own_reason() -> None:
    props = {
        "condition": {"allOf": [{"field": "category", "equals": "ServiceHealth"}]},
        "tenantScope": "t",
    }
    m = one(make("activityLogAlerts", props))
    assert "service health" in m.reasons[0] and "tenant" in m.reasons[0]


def test_smart_detector_rules_are_manual_and_name_the_detector() -> None:
    m = one(
        make("smartDetectorAlertRules", {"detector": {"id": "VMMemoryLeak"}, "severity": "Sev3"})
    )
    assert "smart detector" in m.reasons[0] and "VMMemoryLeak" in m.reasons[0]
    assert m.confidence is Confidence.UNSUPPORTED


def prom_group(*rules: dict[str, Any]) -> Any:
    return make("prometheusRuleGroups", {"interval": "PT1M", "rules": list(rules)}, name="grp")


def test_a_simple_prometheus_alert_goes_through_the_grafana_translator() -> None:
    rule = {
        "alert": "HighCpu",
        "expression": "avg(node_cpu_seconds) > 90",
        "for": "PT5M",
        "severity": 1,
    }
    m = one(prom_group(rule))
    assert m.confidence is not Confidence.MANUAL, m.reasons
    assert m.name == "grp / HighCpu" and m.thresholds[0].severity is Severity.CRITICAL
    assert m.thresholds[0].value == 90
    assert "Prometheus remote write" in reasons(m) and "Grafana" not in reasons(m)


def test_a_quoted_azure_selector_is_rewritten() -> None:
    rule = {
        "alert": "A",
        "expression": 'avg({"system.cpu.utilization", host="a"}) > 90',
        "for": "PT2M",
    }
    m = one(prom_group(rule))
    assert m.confidence is not Confidence.MANUAL, m.reasons
    assert "system_cpu_utilization" in reasons(m)


def test_prometheus_placeholders_bad_durations_and_missing_expressions_are_manual() -> None:
    ms = translate_alert(
        prom_group(
            {"alert": "A", "expression": "up{c='${C}'} == 0"},
            {"alert": "B", "expression": "up == 0", "for": "5 minutes"},
            {"alert": "C"},
        )
    )
    assert [m.confidence for m in ms] == [Confidence.MANUAL] * 3
    assert "placeholder" in ms[0].reasons[0] and "duration" in ms[1].reasons[0]
    assert "no expression" in ms[2].reasons[0]


def test_recording_rules_are_not_alerts_and_are_noted_on_the_alerts() -> None:
    rec = {"record": "job:x:rate5m", "expression": "sum(rate(x[5m]))"}
    alert = {"alert": "A", "expression": "avg(up) > 1", "for": "PT5M"}
    assert "1 recording rule" in reasons(one(prom_group(rec, alert)))
    only = one(prom_group(rec))
    assert only.confidence is Confidence.MANUAL and "recording rule" in only.reasons[0]


def test_disabled_prometheus_rules_are_noted() -> None:
    rule = {"alert": "A", "expression": "avg(up) > 1", "enabled": False}
    assert "disabled" in reasons(one(prom_group(rule)))


def test_a_promql_metric_alert_criteria() -> None:
    crit = {
        "name": "c",
        "criterionType": "StaticThresholdCriterion",
        "query": 'avg({"system.cpu.utilization"}) > 90',
    }
    alert = metric_alert(
        criteria={
            "odata.type": "Microsoft.Azure.Monitor.PromQLCriteria",
            "failingPeriods": {"for": "PT2M"},
            "allOf": [crit],
        },
        evaluationFrequency="PT1M",
    )
    m = one(alert)
    assert m.confidence is not Confidence.MANUAL, m.reasons
    dyn = metric_alert(
        criteria={
            "odata.type": "Microsoft.Azure.Monitor.PromQLCriteria",
            "allOf": [crit | {"criterionType": "DynamicThresholdCriterion"}],
        }
    )
    assert "dynamic threshold" in one(dyn).reasons[0]
    two = metric_alert(
        criteria={"odata.type": "Microsoft.Azure.Monitor.PromQLCriteria", "allOf": [crit, crit]}
    )
    assert "several criteria" in one(two).reasons[0]


def test_unknown_families_are_manual_and_keys_stay_distinct() -> None:
    a, b = metric_alert(name="same"), metric_alert(name="same")
    monitors = translate([a, b])
    assert len({m.source_id for m in monitors}) == 2
    odd = make("metricAlerts", {})
    odd.family = "somethingNew"
    assert "not recognised" in one(odd).reasons[0]


# --- the link to the notification side (docs/dev/azure-notification-link.md on its branch) ----

GROUP = "/subscriptions/S1/resourceGroups/RG1/providers/microsoft.insights/actionGroups/Ops"


def test_converted_and_manual_rules_carry_the_action_group_tag_and_handles() -> None:
    ok = one(metric_alert(actions=[{"actionGroupId": GROUP}]))
    manual_one = one(
        metric_alert(
            criterion(criterionType="DynamicThresholdCriterion"), actions=[{"actionGroupId": GROUP}]
        )
    )
    for m in (ok, manual_one):
        assert "azure.actionGroup:s1/rg1/ops" in m.tags
        assert GROUP in m.notification.handles


def test_every_rule_family_names_its_action_groups() -> None:
    log = log_rule("T")
    log.properties["actions"] = {"actionGroups": [GROUP]}
    activity = make("activityLogAlerts", {"actions": {"actionGroups": [{"actionGroupId": GROUP}]}})
    smart = make("smartDetectorAlertRules", {"actionGroups": {"groupIds": [GROUP]}})
    prom = prom_group(
        {"alert": "A", "expression": "avg(up) > 1", "actions": [{"actionGroupId": GROUP}]}
    )
    for alert in (log, activity, smart, prom):
        assert "azure.actionGroup:s1/rg1/ops" in one(alert).tags


def test_scoping_tags_for_processing_rules() -> None:
    m = one(metric_alert(severity=1))
    assert "azure.severity:Sev1" in m.tags
    assert f"azure.resourceId:{VM}" in m.tags
    assert "azure.resourceGroup:rg1" in m.tags
    assert "azure.resourceType:microsoft.compute/virtualmachines" in m.tags


def test_odd_action_group_ids_make_no_tag_but_never_fail() -> None:
    m = one(metric_alert(actions=[{"actionGroupId": "x\ny"}, {"actionGroupId": 5}, "z", None]))
    assert not [t for t in m.tags if t.startswith("azure.actionGroup")]
