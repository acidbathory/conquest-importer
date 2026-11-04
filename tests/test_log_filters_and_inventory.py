"""Log metric filters become log queries; other AWS monitoring config is listed with next steps."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, SignalKind
from importer.core.migration_summary import load_summary
from importer.core.remediation import remedies_for, steps_for
from importer.core.report_html import render_report
from importer.sources.cloudwatch import extract as ex
from importer.sources.cloudwatch.inventory import inventory_assets
from importer.sources.cloudwatch.logfilter import (
    UnsupportedPattern,
    attach_log_filters,
    parse_pattern,
)
from importer.sources.cloudwatch.models import CloudWatchAlarm, LogMetricRef
from importer.sources.cloudwatch.streaming import alarm_metrics
from importer.sources.cloudwatch.translate import load_namespace_map, translate_alarm


def where(pattern: str) -> list[tuple[str, str, bool, bool]]:
    return [(f.key, f.value, f.negate, f.wildcard) for f in parse_pattern(pattern).filters]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("", []),
        ("ERROR", [("message", "*ERROR*", False, True)]),
        (
            "ERROR Exception",
            [("message", "*ERROR*", False, True), ("message", "*Exception*", False, True)],
        ),
        (
            '"Error occurred" -DEBUG',
            [("message", "*Error occurred*", False, True), ("message", "*DEBUG*", True, True)],
        ),
        ('{ $.level = "ERROR" }', [("level", "ERROR", False, False)]),
        (
            '{ $.a.b = "x*" && $.c != "y" }',
            [("a.b", "x*", False, True), ("c", "y", True, False)],
        ),
    ],
)
def test_simple_patterns_translate(
    pattern: str, expected: list[tuple[str, str, bool, bool]]
) -> None:
    assert where(pattern) == expected


@pytest.mark.parametrize(
    ("pattern", "why"),
    [
        ("?ERROR ?WARN", "OR terms"),
        ("ERR*", "wildcards"),
        ("a%b", "contains %"),
        ("[ip, user, status=4*]", "space-delimited"),
        ("{ $.code = 500 }", "only string comparisons"),
        ('{ $.a = "x" || $.b = "y" }', "OR, grouping"),
        ('{ $.status = "x" }', "field name"),
        ('"unbalanced', "unbalanced"),
    ],
)
def test_other_patterns_are_refused_with_a_reason(pattern: str, why: str) -> None:
    with pytest.raises(UnsupportedPattern, match=why):
        parse_pattern(pattern)


def log_alarm(**changes: Any) -> CloudWatchAlarm:
    base: dict[str, Any] = {
        "AlarmName": "errors",
        "Namespace": "Custom/app",
        "MetricName": "ErrorCount",
        "Statistic": "Sum",
        "Period": 300,
        "EvaluationPeriods": 1,
        "Threshold": 5.0,
        "ComparisonOperator": "GreaterThanThreshold",
        "log_metric_filters": [
            LogMetricRef(filter_name="f", log_group="/app/web", pattern="ERROR", value="1")
        ],
    }
    return CloudWatchAlarm.model_validate({**base, **changes})


def test_an_alarm_on_a_log_metric_becomes_a_count_of_log_events() -> None:
    monitor = translate_alarm(log_alarm(), load_namespace_map())
    assert monitor.confidence is Confidence.APPROXIMATE
    assert monitor.signal.kind is SignalKind.LOG and monitor.source_type == "log-metric alarm"
    assert nrql_for(monitor) == (
        "SELECT count(*) FROM Log WHERE logGroup = '/app/web' AND message LIKE '%ERROR%'"
    )
    assert any("forwarded to New Relic" in r for r in monitor.reasons)


@pytest.mark.parametrize(
    ("changes", "words"),
    [
        ({"Statistic": "Average"}, "statistic"),
        (
            {
                "log_metric_filters": [
                    LogMetricRef(filter_name="f", log_group="/g", pattern="E", value="$.ms")
                ]
            },
            "taken from the log line",
        ),
        (
            {
                "log_metric_filters": [
                    LogMetricRef(filter_name="f", log_group="/g", pattern="?A ?B", value="1")
                ]
            },
            "OR terms",
        ),
        (
            {
                "log_metric_filters": [
                    LogMetricRef(
                        filter_name="f", log_group="/g", pattern="E", value="1", has_dimensions=True
                    )
                ]
            },
            "dimensions",
        ),
    ],
)
def test_log_alarms_that_cannot_convert_say_why_and_get_steps(
    changes: dict[str, Any], words: str
) -> None:
    monitor = translate_alarm(log_alarm(**changes), load_namespace_map())
    assert monitor.confidence is Confidence.MANUAL
    assert words in " ".join(monitor.reasons)
    assert next(r.title for r in remedies_for(monitor.reasons)) == "Log metric filter"


def test_filters_are_linked_to_the_alarm_that_watches_their_metric() -> None:
    alarm = CloudWatchAlarm.model_validate(
        {
            "AlarmName": "a",
            "Namespace": "Custom/app",
            "MetricName": "ErrorCount",
            "Statistic": "Sum",
            "Period": 60,
            "EvaluationPeriods": 1,
            "ComparisonOperator": "GreaterThanThreshold",
        }
    )
    filters = [
        {
            "filterName": "f1",
            "logGroupName": "/app",
            "filterPattern": "ERROR",
            "metricTransformations": [
                {"metricName": "ErrorCount", "metricNamespace": "Custom/app", "metricValue": "1"}
            ],
        },
        {
            "filterName": "other",
            "metricTransformations": [
                {"metricName": "X", "metricNamespace": "Y", "metricValue": "1"}
            ],
        },
    ]
    attach_log_filters([alarm], filters)
    assert [r.filter_name for r in alarm.log_metric_filters] == ["f1"]


def test_log_derived_alarms_do_not_add_namespaces_to_the_metric_stream() -> None:
    assert alarm_metrics([log_alarm()]) == []


def inventory_folder(tmp_path: Path) -> Path:
    export = tmp_path / "export"
    (export / "inventory").mkdir(parents=True)
    (export / "logs").mkdir()

    def put(kind: str, items: list[dict[str, Any]]) -> None:
        (export / "inventory" / f"{kind}.json").write_text(
            json.dumps({"kind": kind, "items": items})
        )

    pattern = json.dumps(
        {"source": ["aws.cloudwatch"], "detail-type": ["CloudWatch Alarm State Change"]}
    )
    put(
        "eventbridge-rules",
        [
            {
                "Name": "alarm-route",
                "State": "ENABLED",
                "EventPattern": pattern,
                "Targets": [{"Arn": "arn:aws:sns:us-east-1:1:t"}],
            },
            {
                "Name": "ec2-events",
                "State": "ENABLED",
                "EventPattern": json.dumps({"source": ["aws.ec2"]}),
                "Targets": [{"Arn": "arn:aws:lambda:us-east-1:1:function:f"}],
            },
            {"Name": "nightly", "State": "ENABLED", "ScheduleExpression": "rate(1 day)"},
            {"Name": "off", "State": "DISABLED", "EventPattern": "{}"},
        ],
    )
    put("synthetics-canaries", [{"Name": "home"}])
    put("contributor-insights-rules", [{"Name": "top-ips"}])
    put("rum-app-monitors", [{"Name": "web"}])
    put("slos", [{"Name": "latency"}])
    put(
        "route53-health-checks",
        [
            {
                "Id": "1",
                "HealthCheckConfig": {"Type": "HTTPS", "FullyQualifiedDomainName": "example.org"},
            },
            {"Id": "2", "HealthCheckConfig": {"Type": "CALCULATED"}},
        ],
    )
    (export / "logs" / "metric-filters.json").write_text(
        json.dumps(
            {
                "metricFilters": [
                    {
                        "filterName": "used",
                        "logGroupName": "/a",
                        "filterPattern": "E",
                        "metricTransformations": [],
                    },
                    {
                        "filterName": "unused",
                        "logGroupName": "/b",
                        "filterPattern": "E",
                        "metricTransformations": [],
                    },
                ]
            }
        )
    )
    return export


def test_inventory_becomes_manual_assets_each_with_steps(tmp_path: Path) -> None:
    export = inventory_folder(tmp_path)
    used = log_alarm()
    used.log_metric_filters = [
        LogMetricRef(filter_name="used", log_group="/a", pattern="E", value="1")
    ]
    assets, ignored = inventory_assets(export, [used])
    by_type = {(a.source_type, a.name): a for a in assets}
    assert ("log metric filter", "unused") in by_type and (
        "log metric filter",
        "used",
    ) not in by_type
    assert {a.source_type for a in assets} == {
        "log metric filter",
        "EventBridge rule",
        "Synthetics canary",
        "Contributor Insights rule",
        "RUM app monitor",
        "Application Signals SLO",
        "Route 53 health check",
    }
    assert len([a for a in assets if a.source_type == "EventBridge rule"]) == 2
    assert by_type[("EventBridge rule", "alarm-route")].detail == "targets: sns"
    assert all(a.confidence is Confidence.MANUAL and a.kind == "other" for a in assets)
    assert all(steps_for(a.reasons) for a in assets), (
        "every inventory reason needs remediation steps"
    )
    assert any("scheduled EventBridge" in i for i in ignored) and any(
        "disabled" in i for i in ignored
    )


def test_the_report_counts_and_explains_the_other_config(tmp_path: Path) -> None:
    run = tmp_path / "run"
    inventory_folder(run)
    summary = load_summary(run)
    # two unused filters, two rules, a canary, a rule, an app monitor, an SLO, two health checks
    assert len(summary.of("other")) == 10 and summary.ignored
    page = render_report(summary)
    assert "Other monitoring config" in page and "Not counted." in page
    assert "Synthetics canary" in page and "Log metric filter" in page


class Aws:
    """The core calls plus whatever the optional reads are told to return or fail with."""

    def __init__(self) -> None:
        self.fail: set[str] = set()
        self.answers: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []

    def __call__(
        self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        key = f"{service} {op}"
        self.calls.append(key)
        if key in self.fail:
            raise ex.AwsError(f"'{key}' failed: AccessDenied")
        core: dict[str, dict[str, Any]] = {
            "sts get_caller_identity": {"Account": "123456789012", "Arn": "x"},
            "cloudwatch describe_alarms": {"MetricAlarms": [], "CompositeAlarms": []},
            "cloudwatch list_dashboards": {"DashboardEntries": []},
        }
        return core.get(key) or self.answers.get(key, {})


def test_extract_writes_the_inventory_and_a_denied_read_only_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    aws = Aws()
    aws.answers["logs describe_metric_filters"] = {"metricFilters": [{"filterName": "f"}]}
    aws.answers["events list_rules"] = {"Rules": [{"Name": "r", "EventPattern": "{}"}]}
    aws.answers["events list_targets_by_rule"] = {"Targets": [{"Arn": "arn:aws:sns:us-east-1:1:t"}]}
    aws.fail.add("synthetics describe_canaries")
    monkeypatch.setattr(ex, "run_boto3", aws)
    out = tmp_path / "export"
    res = CliRunner().invoke(
        app, ["extract-cloudwatch", "--account-id", "123456789012", "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert "Also found: 1 log metric filters, 1 EventBridge rules" in res.output
    assert "Could not read Synthetics canaries" in res.output
    rules = json.loads((out / "inventory" / "eventbridge-rules.json").read_text())["items"]
    assert rules[0]["Targets"][0]["Arn"].startswith("arn:aws:sns")
    assert (out / "logs" / "metric-filters.json").exists()
    assert not (out / "inventory" / "synthetics-canaries.json").exists()


def test_every_new_read_is_on_the_allow_list_and_is_a_read() -> None:
    for service, op in ex.ALLOWED_OPERATIONS:
        assert op.startswith(("get_", "list_", "describe_")), (service, op)
