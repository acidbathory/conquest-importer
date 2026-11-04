import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.cloudwatch import extract as ex

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT = "123456789012"
ALARMS = json.loads(next((FIXTURES / "cloudwatch-real").glob("*.json")).read_text())
DASH_FILES = sorted((FIXTURES / "cloudwatch-dashboards").glob("conquest-ops*.json"))
DASH = json.loads(DASH_FILES[0].read_text())


class FakeAws:
    """Stands in for run_boto3 (the default Reader runner), recording every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, str | None, dict[str, Any]]] = []
        self.account = ACCOUNT
        self.dashboard_names = [DASH["DashboardName"]]
        self.fail: dict[str, str] = {}
        self.override: dict[str, Any] = {}

    def __call__(
        self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((service, op, region, profile, params))
        key = f"{service} {op}"
        if key in self.fail:
            raise ex.AwsError(self.fail[key])
        if key in self.override:
            return self.override[key]  # type: ignore[no-any-return]
        if key == "sts get_caller_identity":
            return {"Account": self.account, "Arn": "arn:aws:iam::x:user/y"}
        if key == "cloudwatch describe_alarms":
            return ALARMS  # type: ignore[no-any-return]
        if key == "cloudwatch list_dashboards":
            return {"DashboardEntries": [{"DashboardName": n} for n in self.dashboard_names]}
        if key == "cloudwatch get_dashboard":
            name = params["DashboardName"]
            if name in self.fail:
                raise ex.AwsError(self.fail[name])
            return {**DASH, "DashboardName": name}
        if (service, op) in ex.ALLOWED_OPERATIONS and service not in ("sts", "sns"):
            return {}  # the optional inventory reads: this account has none of them
        raise AssertionError(f"unexpected call {key}")


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> FakeAws:
    fake = FakeAws()
    monkeypatch.setattr(ex, "run_boto3", fake)
    return fake


def run(out: Path, *extra: str) -> Any:
    return CliRunner().invoke(
        app, ["extract-cloudwatch", "--account-id", ACCOUNT, "--out", str(out), *extra]
    )


def test_happy_path_writes_files_the_converters_read(aws: FakeAws, tmp_path: Path) -> None:
    res = run(tmp_path / "o", "--region", "eu-west-1", "--profile", "test-acct")
    assert res.exit_code == 0, res.output
    assert f"Read account {ACCOUNT}" in res.output and "1 dashboards" in res.output
    for cmd in (
        ["convert", "--source", "cloudwatch", "--in", str(tmp_path / "o/alarms")],
        ["convert-dashboards", "--in", str(tmp_path / "o/dashboards")],
    ):
        out = tmp_path / ("c" + cmd[0])
        r = CliRunner().invoke(app, [*cmd, "--out", str(out)])
        assert r.exit_code == 0, r.output


def test_only_allowed_read_operations_run_with_fixed_parameters(
    aws: FakeAws, tmp_path: Path
) -> None:
    assert run(tmp_path / "o", "--profile", "p1").exit_code == 0
    assert {(c[0], c[1]) for c in aws.calls} <= ex.ALLOWED_OPERATIONS
    for c in aws.calls:
        assert c[2] == "us-east-1" and c[3] == "p1"
    alarms_call = next(c for c in aws.calls if c[1] == "describe_alarms")
    assert alarms_call[4] == {"AlarmTypes": ["MetricAlarm", "CompositeAlarm"]}


def test_account_mismatch_reads_nothing(aws: FakeAws, tmp_path: Path) -> None:
    aws.account = "999999999999"
    res = run(tmp_path / "o")
    assert res.exit_code == 1 and "999999999999" in res.output and "nothing was read" in res.output
    assert [c[0] for c in aws.calls] == ["sts"]
    assert not (tmp_path / "o").exists()


def test_bad_account_id_is_rejected(aws: FakeAws, tmp_path: Path) -> None:
    res = CliRunner().invoke(
        app, ["extract-cloudwatch", "--account-id", "12", "--out", str(tmp_path / "o")]
    )
    assert res.exit_code == 1 and not aws.calls


UNSAFE = [["--region", "us east"], ["--region", "--x"], ["--profile", "--debug"]]


@pytest.mark.parametrize("extra", [*UNSAFE, ["--profile", "a b"]])
def test_unsafe_region_or_profile_is_rejected(
    aws: FakeAws, tmp_path: Path, extra: list[str]
) -> None:
    res = run(tmp_path / "o", *extra)
    assert res.exit_code == 1 and not aws.calls


def test_hostile_dashboard_names_never_reach_file_names_or_operations(
    aws: FakeAws, tmp_path: Path
) -> None:
    aws.dashboard_names = ["../../etc/x", "-rf", "a\x1b[31mb"]
    res = run(tmp_path / "o")
    assert res.exit_code == 0, res.output
    files = sorted(p.name for p in (tmp_path / "o/dashboards").iterdir())
    assert files == ["dashboard-0001.json", "dashboard-0002.json", "dashboard-0003.json"]
    calls = [c for c in aws.calls if c[1] == "get_dashboard"]
    assert calls[1][4] == {"DashboardName": "-rf"}  # a parameter value, never an option


def test_failed_dashboard_is_reported_and_others_are_kept(aws: FakeAws, tmp_path: Path) -> None:
    aws.dashboard_names = ["good", "bad"]
    aws.fail["bad"] = "AccessDenied \x1b[31m"
    res = run(tmp_path / "o")
    assert res.exit_code == 1
    assert "bad" in res.output and "\x1b" not in res.output
    assert [p.name for p in (tmp_path / "o/dashboards").iterdir()] == ["dashboard-0001.json"]


def test_no_dashboards_option(aws: FakeAws, tmp_path: Path) -> None:
    assert run(tmp_path / "o", "--no-dashboards").exit_code == 0
    assert not any(c[1] in ("list-dashboards", "get-dashboard") for c in aws.calls)
    assert not (tmp_path / "o/dashboards").exists()


def test_refuses_to_mix_into_a_used_folder(aws: FakeAws, tmp_path: Path) -> None:
    out = tmp_path / "o"
    assert run(out).exit_code == 0
    again = run(out)
    assert again.exit_code == 1 and "already contains" in again.output


def test_aws_errors_are_shown_safely(aws: FakeAws, tmp_path: Path) -> None:
    aws.fail["sts get_caller_identity"] = "ExpiredToken"
    res = run(tmp_path / "o")
    assert res.exit_code == 1 and "ExpiredToken" in res.output
    assert "\x1b" not in res.output and "\x07" not in res.output


@pytest.mark.parametrize(
    ("key", "response"),
    [
        ("cloudwatch describe_alarms", {"MetricAlarms": "nope"}),
        ("cloudwatch list_dashboards", {"DashboardEntries": "nope"}),
        ("sts get_caller_identity", {"Account": "abc"}),
        ("sts get_caller_identity", {}),
    ],
)
def test_malformed_output_stops_the_export(
    aws: FakeAws, tmp_path: Path, key: str, response: Any
) -> None:
    aws.override[key] = response
    assert run(tmp_path / "o").exit_code == 1


class FakeClientError(Exception):
    pass


def test_missing_credentials_are_explained(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    res = run(tmp_path / "o")
    assert res.exit_code == 1 and "AWS_ACCESS_KEY_ID" in res.output


@pytest.mark.parametrize(
    ("service", "op"),
    [
        ("ec2", "run_instances"),
        ("cloudwatch", "put_metric_alarm"),
        ("cloudwatch", "delete_alarms"),
        ("cloudwatch", "put_dashboard"),
        ("s3", "list_buckets"),
        ("", ""),
    ],
)
def test_write_and_unlisted_operations_are_refused(service: str, op: str) -> None:
    with pytest.raises(ex.AwsError, match="refusing"):
        ex.run_boto3(service, op, "us-east-1", None, {})


def test_pages_are_merged_and_metadata_dropped() -> None:
    pages = [
        {"MetricAlarms": [{"AlarmName": "a"}], "ResponseMetadata": {"x": 1}},
        {"MetricAlarms": [{"AlarmName": "b"}], "CompositeAlarms": [{"AlarmName": "c"}]},
    ]
    merged = ex._merge_pages(pages)
    assert [a["AlarmName"] for a in merged["MetricAlarms"]] == ["a", "b"]
    assert "ResponseMetadata" not in merged and len(merged["CompositeAlarms"]) == 1
