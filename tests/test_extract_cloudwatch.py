import json
import shutil
import subprocess
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
    """Stands in for subprocess.run, so the real allow-list and argument code is exercised."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.account = ACCOUNT
        self.dashboard_names = [DASH["DashboardName"]]
        self.fail: dict[str, str] = {}
        self.stdout_override: dict[str, str] = {}

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("shell") is not True
        assert isinstance(argv, list)
        args = argv[1:]
        self.calls.append(args)
        key = f"{args[0]} {args[1]}"
        if key in self.fail:
            return subprocess.CompletedProcess(argv, 255, "", self.fail[key])
        if key in self.stdout_override:
            return subprocess.CompletedProcess(argv, 0, self.stdout_override[key], "")
        if key == "sts get-caller-identity":
            out: Any = {"Account": self.account, "Arn": "arn:aws:iam::x:user/y"}
        elif key == "cloudwatch describe-alarms":
            out = ALARMS
        elif key == "cloudwatch list-dashboards":
            out = {"DashboardEntries": [{"DashboardName": n} for n in self.dashboard_names]}
        elif key == "cloudwatch get-dashboard":
            name = next(a for a in args if a.startswith("--dashboard-name=")).split("=", 1)[1]
            if name in self.fail:
                return subprocess.CompletedProcess(argv, 254, "", self.fail[name])
            out = {**DASH, "DashboardName": name}
        else:
            raise AssertionError(f"unexpected command {key}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(out), "")


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> FakeAws:
    fake = FakeAws()
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/aws")
    monkeypatch.setattr(subprocess, "run", fake)
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


def test_only_allowed_read_commands_run_with_safe_arguments(aws: FakeAws, tmp_path: Path) -> None:
    assert run(tmp_path / "o", "--profile", "p1").exit_code == 0
    assert {tuple(c[:2]) for c in aws.calls} <= ex.ALLOWED_COMMANDS
    for c in aws.calls:
        assert "--region=us-east-1" in c and "--profile=p1" in c and "--output=json" in c
    alarms_call = next(c for c in aws.calls if c[1] == "describe-alarms")
    i = alarms_call.index("--alarm-types")
    assert alarms_call[i + 1 : i + 3] == ["MetricAlarm", "CompositeAlarm"]


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


def test_hostile_dashboard_names_never_reach_file_names_or_options(
    aws: FakeAws, tmp_path: Path
) -> None:
    aws.dashboard_names = ["../../etc/x", "-rf", "a\x1b[31mb"]
    res = run(tmp_path / "o")
    assert res.exit_code == 0, res.output
    files = sorted(p.name for p in (tmp_path / "o/dashboards").iterdir())
    assert files == ["dashboard-0001.json", "dashboard-0002.json", "dashboard-0003.json"]
    calls = [c for c in aws.calls if c[1] == "get-dashboard"]
    assert "--dashboard-name=-rf" in calls[1]  # one argument, so never parsed as an option


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
    aws.fail["sts get-caller-identity"] = "ExpiredToken\x1b]0;pwn\x07"
    res = run(tmp_path / "o")
    assert res.exit_code == 1 and "ExpiredToken" in res.output
    assert "\x1b" not in res.output and "\x07" not in res.output


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("cloudwatch describe-alarms", "not json"),
        ("cloudwatch describe-alarms", json.dumps({"MetricAlarms": [], "NextToken": "t"})),
        ("cloudwatch describe-alarms", "[]"),
        ("cloudwatch list-dashboards", json.dumps({"DashboardEntries": [], "NextToken": "t"})),
        ("sts get-caller-identity", json.dumps({"Account": "abc"})),
    ],
)
def test_malformed_or_truncated_output_stops_the_export(
    aws: FakeAws, tmp_path: Path, key: str, text: str
) -> None:
    aws.stdout_override[key] = text
    assert run(tmp_path / "o").exit_code == 1


def test_missing_cli_and_timeouts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    res = run(tmp_path / "o")
    assert res.exit_code == 1 and "not installed" in res.output

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/aws")

    def boom(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired("aws", 1)

    monkeypatch.setattr(subprocess, "run", boom)
    res = run(tmp_path / "o2")
    assert res.exit_code == 1 and "timed out" in res.output


@pytest.mark.parametrize(
    "args",
    [
        ["ec2", "run-instances"],
        ["cloudwatch", "put-metric-alarm"],
        ["cloudwatch", "delete-alarms"],
        ["cloudwatch", "put-dashboard"],
        ["s3", "ls"],
        [],
    ],
)
def test_write_and_unlisted_commands_are_refused(args: list[str]) -> None:
    with pytest.raises(ex.AwsError, match="refusing"):
        ex.run_aws(args)
