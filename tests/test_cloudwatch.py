import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.emit.common import CannotEmit, nrql_for
from importer.emit.nerdgraph import nerdgraph_payload
from importer.emit.terraform import resource_block
from importer.models.cloudwatch import CloudWatchAlarm, load_alarms
from importer.models.ir import Confidence, IRMonitor, Occurrences
from importer.translate.cloudwatch import (
    alarm_source_id,
    load_namespace_map,
    translate_alarm,
    translate_composite,
)

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures" / "cloudwatch"
GOLDEN = ROOT / "golden" / "cloudwatch_expected.json"
PREFIXES = load_namespace_map()


def alarm(**fields: Any) -> IRMonitor:
    base: dict[str, Any] = {
        "AlarmName": "a",
        "Namespace": "AWS/EC2",
        "MetricName": "CPUUtilization",
        "Statistic": "Average",
        "Period": 60,
        "EvaluationPeriods": 1,
        "Threshold": 50.0,
        "ComparisonOperator": "GreaterThanThreshold",
    }
    base.update(fields)
    return translate_alarm(CloudWatchAlarm.model_validate(base), PREFIXES)


# --- golden ---------------------------------------------------------------------------


def current_results() -> dict[str, dict[str, Any]]:
    metric, composite = load_alarms(FIXTURES)
    irs = [translate_alarm(a, PREFIXES) for a in metric] + [
        translate_composite(c) for c in composite
    ]
    results: dict[str, dict[str, Any]] = {}
    for ir in irs:
        try:
            nrql: str | None = nrql_for(ir)
            payload = nerdgraph_payload(ir)["variables"]["condition"]
            shape: dict[str, Any] | None = {
                "duration": payload["terms"][0]["thresholdDuration"],
                "window": payload["signal"]["aggregationWindow"],
                "occurrences": payload["terms"][0]["thresholdOccurrences"],
                "expiration": "expiration" in payload,
            }
        except CannotEmit as exc:
            nrql, shape = f"not emitted: {exc}", None
        results[ir.name] = {"confidence": ir.confidence.value, "nrql": nrql, "shape": shape}
    return results


def test_cloudwatch_fixtures_match_golden() -> None:
    results = current_results()
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(results, indent=2) + "\n")
    assert results == json.loads(GOLDEN.read_text())


def test_fixtures_cover_every_confidence_level() -> None:
    assert {r["confidence"] for r in current_results().values()} == {
        "approximate",
        "manual",
        "unsupported",
    }


# --- semantics -------------------------------------------------------------------------


def test_classic_alarm_maps_period_and_evaluation_periods() -> None:
    ir = alarm(Period=300, EvaluationPeriods=3, DatapointsToAlarm=3)
    assert ir.signal.aggregation_window_seconds == 300
    assert ir.signal.window_seconds == 900
    assert ir.signal.occurrences == Occurrences.ALL
    assert nrql_for(ir) == "SELECT average(aws.ec2.CPUUtilization) FROM Metric"


def test_one_of_n_is_at_least_once_exactly() -> None:
    ir = alarm(Period=60, EvaluationPeriods=5, DatapointsToAlarm=1)
    assert ir.signal.occurrences == Occurrences.AT_LEAST_ONCE
    assert ir.signal.window_seconds == 300
    assert not any("approximated" in r for r in ir.reasons)


def test_m_of_n_is_approximated_with_a_reason() -> None:
    ir = alarm(Period=60, EvaluationPeriods=5, DatapointsToAlarm=3)
    assert ir.signal.occurrences == Occurrences.ALL
    assert ir.signal.window_seconds == 180
    assert any("3 of 5" in r for r in ir.reasons)


def test_breaching_missing_data_opens_violation_on_expiration() -> None:
    ir = alarm(Period=300, EvaluationPeriods=2, TreatMissingData="breaching")
    assert ir.no_data.notify and ir.no_data.timeout_minutes == 10


@pytest.mark.parametrize("mode", ["notBreaching", "ignore"])
def test_unsupported_missing_data_modes_are_flagged(mode: str) -> None:
    ir = alarm(TreatMissingData=mode)
    assert not ir.no_data.notify
    assert any(mode in r for r in ir.reasons)


@pytest.mark.parametrize(
    ("op", "expected"),
    [
        ("GreaterThanThreshold", "above"),
        ("GreaterThanOrEqualToThreshold", "above_or_equals"),
        ("LessThanThreshold", "below"),
        ("LessThanOrEqualToThreshold", "below_or_equals"),
    ],
)
def test_comparison_operators(op: str, expected: str) -> None:
    assert alarm(ComparisonOperator=op).thresholds[0].operator.value == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"ComparisonOperator": "LessThanLowerOrGreaterThanUpperThreshold"},
        {"ComparisonOperator": "SomethingNew"},
        {"Statistic": "SampleCount"},
        {"Statistic": None, "ExtendedStatistic": "p99"},
        {"Namespace": "Custom/App"},
        {"Period": 10},
        {"Period": 90},
        {"MetricName": "Bad-Name"},
        {"Dimensions": [{"Name": "Bad Name", "Value": "x"}]},
        {"Threshold": None},
        {"EvaluationPeriods": 2, "DatapointsToAlarm": 5},
    ],
)
def test_unsupported_forms_are_manual_not_errors(fields: dict[str, Any]) -> None:
    ir = alarm(**fields)
    assert ir.confidence == Confidence.MANUAL
    assert ir.reasons


def test_every_converted_alarm_stays_approximate_until_all_services_are_verified() -> None:
    assert alarm().confidence == Confidence.APPROXIMATE
    assert any("assumed for other services" in r for r in alarm().reasons)


def test_actions_are_listed_not_migrated() -> None:
    ir = alarm(AlarmActions=["arn:aws:sns:us-east-1:1:topic"], OKActions=["arn:aws:sns:x"])
    assert ir.notification.handles == ["arn:aws:sns:us-east-1:1:topic"]
    assert "OKActions" in ir.unmapped
    assert any("alarm actions" in r for r in ir.reasons)


def test_dimensions_become_where_clauses_with_escaping() -> None:
    ir = alarm(Dimensions=[{"Name": "InstanceId", "Value": "i-1' OR 'a'='a"}])
    assert nrql_for(ir).endswith("WHERE aws.ec2.InstanceId = 'i-1\\' OR \\'a\\'=\\'a'")


def test_dimension_value_with_newline_is_manual() -> None:
    ir = alarm(Dimensions=[{"Name": "InstanceId", "Value": "a\nb"}])
    assert ir.confidence == Confidence.MANUAL


# --- metric math -------------------------------------------------------------------------


def q(qid: str, metric: str, stat: str = "Sum", period: int = 60, dim: str = "f") -> dict[str, Any]:
    return {
        "Id": qid,
        "ReturnData": False,
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/Lambda",
                "MetricName": metric,
                "Dimensions": [{"Name": "FunctionName", "Value": dim}],
            },
            "Period": period,
            "Stat": stat,
        },
    }


def math_alarm(expression: str, *queries: dict[str, Any]) -> IRMonitor:
    return alarm(
        Namespace=None,
        MetricName=None,
        Statistic=None,
        Period=None,
        Metrics=[*queries, {"Id": "e1", "Expression": expression, "ReturnData": True}],
    )


def test_metric_math_ratio_and_precedence() -> None:
    ir = math_alarm("(m1 + m2) / m3 * 100", q("m1", "A"), q("m2", "B"), q("m3", "C"))
    assert nrql_for(ir) == (
        "SELECT (sum(aws.lambda.A) + sum(aws.lambda.B)) / sum(aws.lambda.C) * 100 "
        "FROM Metric WHERE aws.lambda.FunctionName = 'f'"
    )


@pytest.mark.parametrize(
    ("expression", "queries"),
    [
        ("FILL(m1, 0)", [q("m1", "A")]),
        ("m1 / m9", [q("m1", "A")]),
        ("m1 / m2", [q("m1", "A"), q("m2", "B", period=300)]),
        ("m1 / m2", [q("m1", "A"), q("m2", "B", stat="Average")]),
        ("m1 / m2", [q("m1", "A"), q("m2", "B", dim="other")]),
        ("m1 +", [q("m1", "A")]),
        ("(m1", [q("m1", "A")]),
        ("m1 ; m2", [q("m1", "A"), q("m2", "B")]),
    ],
)
def test_unsupported_metric_math_is_manual(expression: str, queries: list[dict[str, Any]]) -> None:
    assert math_alarm(expression, *queries).confidence == Confidence.MANUAL


def test_never_raises_on_hostile_input() -> None:
    for name in ["", "x" * 1000, "\x00\x1b[31m", "'; DROP TABLE a; --"]:
        ir = alarm(AlarmName=name or "n", MetricName=name or "m", Namespace="AWS/EC2")
        assert ir.confidence in (Confidence.MANUAL, Confidence.APPROXIMATE)


# --- composite ----------------------------------------------------------------------------


def test_composite_is_unsupported_and_lists_alarm_names() -> None:
    from importer.models.cloudwatch import CompositeAlarm

    comp = CompositeAlarm(AlarmName="c", AlarmRule='ALARM("one") OR (ALARM("two") AND OK("three"))')
    ir = translate_composite(comp)
    assert ir.confidence == Confidence.UNSUPPORTED
    assert all(n in ir.reasons[0] for n in ("one", "two", "three"))


# --- ids and loading ------------------------------------------------------------------------


def test_source_id_is_stable_and_distinct() -> None:
    assert alarm_source_id("a") == alarm_source_id("a")
    assert alarm_source_id("a") != alarm_source_id("b")


def test_loader_accepts_bare_list_and_directory(tmp_path: Path) -> None:
    one = {"AlarmName": "x", "EvaluationPeriods": 1, "ComparisonOperator": "GreaterThanThreshold"}
    (tmp_path / "a.json").write_text(json.dumps([one]))
    (tmp_path / "b.json").write_text(json.dumps({"MetricAlarms": [{**one, "AlarmName": "y"}]}))
    metric, composite = load_alarms(tmp_path)
    assert sorted(a.AlarmName for a in metric) == ["x", "y"] and not composite


def test_loader_rejects_wrong_shape(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text('"just a string"')
    with pytest.raises(ValueError):
        load_alarms(tmp_path / "bad.json")


# --- emitters and CLI ----------------------------------------------------------------------


def test_terraform_uses_cloudwatch_label_and_occurrences() -> None:
    ir = alarm(Period=60, EvaluationPeriods=5, DatapointsToAlarm=1, AlarmArn="arn:x:alarm:a")
    block = resource_block(ir)
    assert f'"cw_{ir.source_id}"' in block
    assert 'threshold_occurrences = "at_least_once"' in block
    assert "aggregation_window = 60" in block
    assert "aggregation_delay  = 300" in block
    assert "Imported from CloudWatch alarm arn:x:alarm:a" in block


def test_nerdgraph_uses_cloudwatch_settings() -> None:
    ir = alarm(Period=300, EvaluationPeriods=2, DatapointsToAlarm=2)
    cond = nerdgraph_payload(ir)["variables"]["condition"]
    assert cond["signal"]["aggregationWindow"] == 300
    assert cond["signal"]["aggregationDelay"] == 300
    assert cond["terms"][0]["thresholdDuration"] == 600
    assert cond["terms"][0]["thresholdOccurrences"] == "ALL"


def test_duration_must_be_multiple_of_window() -> None:
    ir = alarm(Period=60)
    bad = ir.model_copy(update={"signal": ir.signal.model_copy(update={"window_seconds": 90})})
    with pytest.raises(CannotEmit, match="multiple"):
        nerdgraph_payload(bad)


def test_cli_convert_cloudwatch_end_to_end(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["convert", "--source", "cloudwatch", "--in", str(FIXTURES), "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    report = (tmp_path / "report.md").read_text()
    assert "Monitors read: **23**" in report
    assert "Metric Streams" in report
    terraform = (tmp_path / "main.tf").read_text()
    assert "$${evil}" in terraform
    emitted = len(list((tmp_path / "nerdgraph").glob("*.json")))
    assert terraform.count('resource "newrelic_nrql_alert_condition"') == emitted == 15


def test_cli_rejects_unknown_source_and_missing_input(tmp_path: Path) -> None:
    runner = CliRunner()
    bad = runner.invoke(app, ["convert", "--source", "nagios", "--in", str(tmp_path)])
    assert bad.exit_code == 1
    missing = runner.invoke(
        app, ["convert", "--source", "cloudwatch", "--in", str(tmp_path / "nope.json")]
    )
    assert missing.exit_code == 1
    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    none = runner.invoke(app, ["convert", "--source", "cloudwatch", "--in", str(empty)])
    assert none.exit_code == 1


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_generated_terraform_validates(tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = CliRunner().invoke(
        app, ["convert", "--source", "cloudwatch", "--in", str(FIXTURES), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    terraform = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("cannot download the New Relic provider (offline or blocked)")
    check = subprocess.run(  # noqa: S603
        [terraform, "validate", "-no-color"], cwd=out, capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stdout + check.stderr


# --- real AWS export ---------------------------------------------------------------------------

REAL = ROOT / "fixtures" / "cloudwatch-real"


def test_real_aws_export_converts_as_expected(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["convert", "--source", "cloudwatch", "--in", str(REAL), "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    irs = {
        json.loads(p.read_text())["name"]: json.loads(p.read_text())
        for p in (tmp_path / "ir").glob("*.json")
    }
    assert len(irs) == 9
    assert {n: v["confidence"] for n, v in irs.items()} == {
        "conquest-test-ec2-cpu-high": "approximate",
        "conquest-test-rds-free-storage": "approximate",
        "conquest-test-lambda-errors": "approximate",
        "conquest-test-connections-3of5": "approximate",
        "conquest-test-alb-latency-p99": "manual",
        "conquest-test-sqs-age": "approximate",
        "conquest-test-custom-namespace": "manual",
        "conquest-test-lambda-error-rate": "approximate",
        "conquest-test-composite": "unsupported",
    }
    assert len(list((tmp_path / "nerdgraph").glob("*.json"))) == 6
    assert "926634327293" not in (REAL / "describe-alarms.json").read_text()
