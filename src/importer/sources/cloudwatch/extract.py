"""Read alarms and dashboards from an AWS account, read-only, through the AWS CLI.

Why the CLI and not boto3: no extra dependency, and this code never sees credentials; the CLI
uses the profile the user already configured. Only the four read commands in ALLOWED_COMMANDS
can run, each as an argument list (no shell). Output is written as the same JSON the converters
read, so the result feeds straight into `convert` and `convert-dashboards`.

Exports can contain account ids, ARNs and resource names: review them before sharing.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from importer.core.text import printable
from importer.sources.cloudwatch.models import MAX_INPUT_BYTES

Runner = Callable[[list[str]], str]

ALLOWED_COMMANDS = {
    ("sts", "get-caller-identity"),
    ("cloudwatch", "describe-alarms"),
    ("cloudwatch", "list-dashboards"),
    ("cloudwatch", "get-dashboard"),
}
TIMEOUT_SECONDS = 120
MAX_DASHBOARDS = 1000
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_PROFILE = re.compile(r"^[A-Za-z0-9_.@+=][A-Za-z0-9_.@+=-]{0,63}$")
_ACCOUNT = re.compile(r"^\d{12}$")


class AwsError(Exception):
    """Reading from AWS failed; the message is safe to show."""


def run_aws(args: list[str]) -> str:
    """Run one allow-listed AWS CLI command and return its standard output."""
    if tuple(args[:2]) not in ALLOWED_COMMANDS:
        raise AwsError(f"refusing to run 'aws {' '.join(args[:2])}': not an allowed read command")
    aws = shutil.which("aws")
    if aws is None:
        raise AwsError("the AWS CLI ('aws') is not installed or not on PATH")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed allow-list, argument list, no shell
            [aws, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise AwsError(f"'aws {args[0]} {args[1]}' timed out after {TIMEOUT_SECONDS}s") from None
    if proc.returncode != 0:
        raise AwsError(f"'aws {args[0]} {args[1]}' failed: {printable(proc.stderr.strip(), 300)}")
    if len(proc.stdout) > MAX_INPUT_BYTES:
        raise AwsError(f"'aws {args[0]} {args[1]}' returned more than the size limit")
    return proc.stdout


@dataclass
class Reader:
    region: str
    profile: str | None = None
    runner: Runner = run_aws

    def __post_init__(self) -> None:
        if not _REGION.match(self.region):
            raise ValueError(f"invalid AWS region: {printable(self.region, 30)!r}")
        if self.profile is not None and not _PROFILE.match(self.profile):
            raise ValueError(f"invalid AWS profile name: {printable(self.profile, 30)!r}")

    def call(self, service: str, operation: str, *extra: str) -> Any:
        # "--name=value" form, so a value starting with "-" can never be read as an option.
        args = [service, operation, *extra, "--output=json", "--no-cli-pager"]
        args.append(f"--region={self.region}")
        if self.profile:
            args.append(f"--profile={self.profile}")
        try:
            return json.loads(self.runner(args))
        except json.JSONDecodeError:
            raise AwsError(f"'aws {service} {operation}' did not return JSON") from None


def caller_account(reader: Reader) -> str:
    data = reader.call("sts", "get-caller-identity")
    account = data.get("Account") if isinstance(data, dict) else None
    if not isinstance(account, str) or not _ACCOUNT.match(account):
        raise AwsError("could not determine the AWS account id")
    return account


def fetch_alarms(reader: Reader) -> dict[str, Any]:
    # Without --alarm-types the API returns metric alarms only, never composite alarms.
    data = reader.call(
        "cloudwatch", "describe-alarms", "--alarm-types", "MetricAlarm", "CompositeAlarm"
    )
    if not isinstance(data, dict) or not isinstance(data.get("MetricAlarms", []), list):
        raise AwsError("unexpected describe-alarms output")
    if data.get("NextToken"):
        raise AwsError("describe-alarms output was truncated (NextToken present)")
    return data


def list_dashboard_names(reader: Reader) -> list[str]:
    data = reader.call("cloudwatch", "list-dashboards")
    entries = data.get("DashboardEntries", []) if isinstance(data, dict) else None
    if not isinstance(entries, list) or (isinstance(data, dict) and data.get("NextToken")):
        raise AwsError("unexpected or truncated list-dashboards output")
    names = [e["DashboardName"] for e in entries if isinstance(e, dict) and "DashboardName" in e]
    if len(names) > MAX_DASHBOARDS:
        raise AwsError(f"more than {MAX_DASHBOARDS} dashboards; export them in smaller groups")
    return [n for n in names if isinstance(n, str)]


def fetch_dashboard(reader: Reader, name: str) -> dict[str, Any]:
    data = reader.call("cloudwatch", "get-dashboard", f"--dashboard-name={name}")
    if not isinstance(data, dict) or "DashboardBody" not in data:
        raise AwsError("unexpected get-dashboard output")
    return data


@dataclass
class ExtractResult:
    account: str
    metric_alarms: int = 0
    composite_alarms: int = 0
    dashboards: int = 0
    failed_dashboards: list[str] = field(default_factory=list)


def _check_empty(folder: Path) -> None:
    if folder.is_dir() and any(folder.glob("*.json")):
        raise AwsError(f"{folder} already contains JSON files; use a new --out folder")


def extract(
    reader: Reader, expected_account: str, out: Path, include_dashboards: bool = True
) -> ExtractResult:
    """Export alarms (and dashboards) to `out/alarms` and `out/dashboards`."""
    if not _ACCOUNT.match(expected_account):
        raise ValueError("--account-id must be a 12-digit AWS account id")
    account = caller_account(reader)
    if account != expected_account:
        raise AwsError(
            f"the AWS CLI is signed in to account {account}, not {expected_account}; "
            "nothing was read"
        )
    alarm_dir, dash_dir = out / "alarms", out / "dashboards"
    _check_empty(alarm_dir)
    if include_dashboards:
        _check_empty(dash_dir)

    result = ExtractResult(account)
    alarms = fetch_alarms(reader)
    result.metric_alarms = len(alarms.get("MetricAlarms", []))
    result.composite_alarms = len(alarms.get("CompositeAlarms", []))
    alarm_dir.mkdir(parents=True, exist_ok=True)
    (alarm_dir / "describe-alarms.json").write_text(json.dumps(alarms, indent=2) + "\n")

    if include_dashboards:
        names = list_dashboard_names(reader)
        dash_dir.mkdir(parents=True, exist_ok=True)
        for n, name in enumerate(names, start=1):
            try:
                body = fetch_dashboard(reader, name)
            except AwsError as exc:
                result.failed_dashboards.append(f"{printable(name, 60)}: {exc}")
                continue
            # Numbered file names: dashboard names are untrusted text (the name is inside).
            (dash_dir / f"dashboard-{n:04d}.json").write_text(json.dumps(body, indent=2) + "\n")
            result.dashboards += 1
    return result
