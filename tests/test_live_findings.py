"""Regression tests for bugs found by the live suite (docs/testing-results/2026-11-04.md)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import NewRelicClient
from importer.core.emit.nrql import identifier
from importer.core.manifest import CreatedObject
from importer.core.remediation import steps_for
from importer.core.rollback import current
from importer.sources.cloudwatch import extract as ex
from importer.sources.cloudwatch.models import CloudWatchAlarm
from importer.sources.cloudwatch.translate import load_namespace_map, translate_alarm

FIXTURES = Path(__file__).parent / "fixtures"
DASH = json.loads(
    sorted((FIXTURES / "cloudwatch-dashboards").glob("conquest-ops*.json"))[0].read_text()
)
ACCOUNT = "123456789012"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("aws.apigateway.5XXError", "`aws.apigateway.5XXError`"),
        ("aws.apigateway.4XXError", "`aws.apigateway.4XXError`"),
        ("aws.lambda.Errors", "aws.lambda.Errors"),
        (
            "aws.applicationelb.HTTPCode_Target_5XX_Count",
            "aws.applicationelb.HTTPCode_Target_5XX_Count",
        ),
    ],
)
def test_nrql_names_with_a_digit_leading_segment_are_backticked(name: str, expected: str) -> None:
    assert identifier(name) == expected


def test_identifier_is_safe_to_apply_twice() -> None:
    once = identifier("aws.apigateway.5XXError")
    assert identifier(once) == once


@pytest.mark.parametrize("name", ["a`b", "`a`b`", "``", "a b", "", "5start", "a;drop"])
def test_identifier_still_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError, match="invalid NRQL identifier"):
        identifier(name)


def _client(handler: Any) -> NewRelicClient:
    return NewRelicClient(
        SecretStr("k"), transport=httpx.MockTransport(handler), sleep=lambda _: None
    )


def test_reading_a_deleted_object_means_it_is_gone_not_an_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Not Found"}], "data": None})

    with _client(handler) as client:
        assert current(client, 7, CreatedObject(kind="condition", id="1", name="x")) is None


def test_other_read_errors_are_still_raised() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Forbidden"}], "data": None})

    with _client(handler) as client, pytest.raises(Exception, match="Forbidden"):
        current(client, 7, CreatedObject(kind="condition", id="1", name="x"))


@pytest.mark.parametrize("operator", ["GreaterThanUpperThreshold", "LessThanLowerThreshold"])
def test_anomaly_alarms_say_so_and_get_remediation(operator: str) -> None:
    alarm = CloudWatchAlarm(
        AlarmName="a",
        Namespace="AWS/Lambda",
        MetricName="Errors",
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=1,
        ComparisonOperator=operator,
    )
    monitor = translate_alarm(alarm, load_namespace_map())
    reasons = " ".join(monitor.reasons)
    assert "anomaly detection" in reasons
    assert steps_for(monitor.reasons)


def test_unmapped_custom_namespace_explains_the_limit_and_the_workaround() -> None:
    alarm = CloudWatchAlarm(
        AlarmName="a",
        Namespace="Custom/my-app",
        MetricName="Orders",
        Statistic="Sum",
        Period=300,
        EvaluationPeriods=1,
        Threshold=1.0,
        ComparisonOperator="GreaterThanThreshold",
    )
    monitor = translate_alarm(alarm, load_namespace_map())
    reasons = " ".join(monitor.reasons)
    assert "letters and digits" in reasons and "cloudwatch_map.json" not in reasons
    steps = " ".join(steps_for(monitor.reasons))
    assert "Custom/myapp" in steps


class DashboardsOnlyAws:
    """An AWS account with dashboards but no alarms, no topics."""

    def __call__(
        self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        key = f"{service} {op}"
        answers: dict[str, dict[str, Any]] = {
            "sts get_caller_identity": {"Account": ACCOUNT, "Arn": "arn:aws:iam::x:user/y"},
            "cloudwatch describe_alarms": {"MetricAlarms": [], "CompositeAlarms": []},
            "cloudwatch list_dashboards": {
                "DashboardEntries": [{"DashboardName": DASH["DashboardName"]}]
            },
            "cloudwatch get_dashboard": DASH,
        }
        return answers[key]


def test_migrate_plans_an_account_that_has_dashboards_but_no_alarms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ex, "run_boto3", DashboardsOnlyAws())
    out = tmp_path / "run"
    res = CliRunner().invoke(
        app,
        ["migrate", "--aws-account-id", ACCOUNT, "--nr-account-id", "7", "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    assert "No alarms found" in res.output
    assert (out / "report.html").exists() and (out / "plan.md").exists()


def test_migrate_says_when_there_is_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class Empty(DashboardsOnlyAws):
        def __call__(
            self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
        ) -> dict[str, Any]:
            if op == "list_dashboards":
                return {"DashboardEntries": []}
            return super().__call__(service, op, region, profile, params)

    monkeypatch.setattr(ex, "run_boto3", Empty())
    res = CliRunner().invoke(
        app,
        [
            "migrate",
            "--aws-account-id",
            ACCOUNT,
            "--nr-account-id",
            "7",
            "--out",
            str(tmp_path / "r"),
        ],
    )
    assert res.exit_code == 1 and "Nothing to migrate" in res.output


def test_a_stage_with_partial_failures_does_not_stop_the_run() -> None:
    from importer import cli

    def partial() -> None:
        cli._partial_failure()

    assert cli._step("x", partial, keep_going=True) is False
    assert cli._step("x", lambda: None, keep_going=True) is True


def test_a_stage_that_fails_outright_still_stops_the_run() -> None:
    import typer

    from importer import cli

    def hard() -> None:
        raise typer.Exit(code=1)

    with pytest.raises(typer.Exit):
        cli._step("x", hard, keep_going=True)
    with pytest.raises(typer.Exit):
        cli._step("x", hard)


def test_what_new_relic_refused_is_not_counted_as_migrated(tmp_path: Path) -> None:
    from importer.core.ir import Confidence
    from importer.core.manifest import Manifest, save_manifest
    from importer.core.migration_summary import Asset, _mark_refused

    manifest = Manifest(account_id=7)
    manifest.add_failed("condition", [("bad", "Validation Error")])
    save_manifest(manifest, tmp_path / "manifest.json")
    good = Asset("alarm", "good", "metric alarm", Confidence.APPROXIMATE)
    bad = Asset("alarm", "bad", "metric alarm", Confidence.APPROXIMATE)
    _mark_refused(tmp_path, [good, bad])
    assert good.migrated and not bad.migrated
    assert "could not be created in New Relic: Validation Error" in bad.reasons[0]
    assert steps_for(bad.reasons)


def test_a_later_successful_create_clears_the_recorded_failure() -> None:
    from importer.core.manifest import Manifest

    manifest = Manifest(account_id=7)
    manifest.add_failed("condition", [("a", "boom")])
    manifest.add([CreatedObject(kind="condition", id="1", name="a")])
    assert manifest.failed == []
