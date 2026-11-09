"""Read alarms and dashboards from an AWS account, read-only, through boto3.

Credentials come from the standard AWS places (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY and
optionally AWS_SESSION_TOKEN in the environment, or a named profile); this code never reads or
stores them. Only the read operations in ALLOWED_OPERATIONS can run, with fixed parameters (no
user text is ever used as an operation name). Output is written as the same JSON the converters
read, so the result feeds straight into `convert` and `convert-dashboards`.

Exports can contain account ids, ARNs and resource names: review them before sharing.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable

# (service, operation, region, profile, parameters) -> the response as plain JSON data
Runner = Callable[[str, str, str, str | None, dict[str, Any]], dict[str, Any]]

ALLOWED_OPERATIONS = {
    ("sts", "get_caller_identity"),
    ("cloudwatch", "describe_alarms"),
    ("cloudwatch", "list_dashboards"),
    ("cloudwatch", "get_dashboard"),
    ("sns", "list_subscriptions_by_topic"),
    ("cloudwatch", "get_metric_statistics"),
    # Optional inventory: each is read-only and a failure only produces a warning.
    ("logs", "describe_metric_filters"),
    ("events", "list_rules"),
    ("events", "list_targets_by_rule"),
    ("synthetics", "describe_canaries"),
    ("cloudwatch", "describe_insight_rules"),
    ("rum", "list_app_monitors"),
    ("application-signals", "list_service_level_objectives"),
    ("route53", "list_health_checks"),
}
MAX_RULES = 500
MAX_DASHBOARDS = 1000
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_PROFILE = re.compile(r"^[A-Za-z0-9_.@+=][A-Za-z0-9_.@+=-]{0,63}$")
_ACCOUNT = re.compile(r"^\d{12}$")
_SNS_TOPIC = re.compile(r"^arn:aws[a-z-]*:sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9_-]{1,256}$")

NO_CREDENTIALS = (
    "no AWS credentials found. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (plus "
    "AWS_SESSION_TOKEN for temporary keys) in your environment, or pass --profile"
)


class AwsError(Exception):
    """Reading from AWS failed; the message is safe to show."""


def _plain(value: object) -> str:
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _merge_pages(pages: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for page in pages:
        for key, value in page.items():
            if key == "ResponseMetadata":
                continue
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            else:
                merged.setdefault(key, value)
    return merged


def run_boto3(
    service: str, operation: str, region: str, profile: str | None, params: dict[str, Any]
) -> dict[str, Any]:
    """Run one allow-listed read operation (all pages) and return plain JSON data."""
    if (service, operation) not in ALLOWED_OPERATIONS:
        raise AwsError(f"refusing to run '{service} {operation}': not an allowed read operation")
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, ProfileNotFound

    try:
        session = boto3.Session(region_name=region, profile_name=profile)
        client = session.client(
            service,
            config=Config(connect_timeout=10, read_timeout=60, retries={"max_attempts": 4}),
        )
        if client.can_paginate(operation):
            data = _merge_pages(client.get_paginator(operation).paginate(**params))
        else:
            data = dict(getattr(client, operation)(**params))
            data.pop("ResponseMetadata", None)
    except NoCredentialsError:
        raise AwsError(NO_CREDENTIALS) from None
    except ProfileNotFound:
        raise AwsError("that AWS profile does not exist") from None
    except ClientError as exc:
        err = exc.response.get("Error", {})
        detail = f"{err.get('Code', 'error')}: {err.get('Message', '')}"
        raise AwsError(f"'{service} {operation}' failed: {printable(detail, 300)}") from None
    except BotoCoreError as exc:
        raise AwsError(f"'{service} {operation}' failed: {printable(str(exc), 300)}") from None
    text = json.dumps(data, default=_plain)
    if len(text) > MAX_INPUT_BYTES:
        raise AwsError(f"'{service} {operation}' returned more than the size limit")
    result: dict[str, Any] = json.loads(text)
    return result


@dataclass
class Reader:
    region: str
    profile: str | None = None
    runner: Runner | None = None  # default: run_boto3, looked up at call time

    def __post_init__(self) -> None:
        if not _REGION.match(self.region):
            raise ValueError(f"invalid AWS region: {printable(self.region, 30)!r}")
        if self.profile is not None and not _PROFILE.match(self.profile):
            raise ValueError(f"invalid AWS profile name: {printable(self.profile, 30)!r}")

    def call(self, service: str, operation: str, **params: Any) -> dict[str, Any]:
        run = self.runner or run_boto3
        data = run(service, operation, self.region, self.profile, params)
        if not isinstance(data, dict):
            raise AwsError(f"'{service} {operation}' returned an unexpected response")
        return data


def caller_account(reader: Reader) -> str:
    data = reader.call("sts", "get_caller_identity")
    account = data.get("Account")
    if not isinstance(account, str) or not _ACCOUNT.match(account):
        raise AwsError("could not determine the AWS account id")
    return account


def fetch_alarms(reader: Reader) -> dict[str, Any]:
    # Without AlarmTypes the API returns metric alarms only, never composite alarms.
    data = reader.call(
        "cloudwatch", "describe_alarms", AlarmTypes=["MetricAlarm", "CompositeAlarm"]
    )
    if not isinstance(data.get("MetricAlarms", []), list):
        raise AwsError("unexpected describe-alarms output")
    return data


def list_dashboard_names(reader: Reader) -> list[str]:
    data = reader.call("cloudwatch", "list_dashboards")
    entries = data.get("DashboardEntries", [])
    if not isinstance(entries, list):
        raise AwsError("unexpected list-dashboards output")
    names = [e["DashboardName"] for e in entries if isinstance(e, dict) and "DashboardName" in e]
    if len(names) > MAX_DASHBOARDS:
        raise AwsError(f"more than {MAX_DASHBOARDS} dashboards; export them in smaller groups")
    return [n for n in names if isinstance(n, str)]


def fetch_dashboard(reader: Reader, name: str) -> dict[str, Any]:
    data = reader.call("cloudwatch", "get_dashboard", DashboardName=name)
    if "DashboardBody" not in data:
        raise AwsError("unexpected get-dashboard output")
    return data


def sns_topics(alarms: dict[str, Any]) -> list[str]:
    """Every SNS topic ARN that any exported alarm notifies (other action kinds are ignored)."""
    found: set[str] = set()
    for key in ("MetricAlarms", "CompositeAlarms"):
        for alarm in alarms.get(key, []):
            for field_name in ("AlarmActions", "OKActions", "InsufficientDataActions"):
                found.update(a for a in alarm.get(field_name, []) if _SNS_TOPIC.match(str(a)))
    return sorted(found)


def fetch_subscriptions(reader: Reader, topic_arn: str) -> dict[str, Any]:
    if not _SNS_TOPIC.match(topic_arn):
        raise AwsError("not an SNS topic ARN")
    data = reader.call("sns", "list_subscriptions_by_topic", TopicArn=topic_arn)
    if not isinstance(data.get("Subscriptions", []), list):
        raise AwsError("unexpected list-subscriptions-by-topic output")
    return {"TopicArn": topic_arn, "Subscriptions": data.get("Subscriptions", [])}


@dataclass(frozen=True)
class Collector:
    """One optional read-only list call whose items the report shows as inventory."""

    kind: str  # also the file name under inventory/
    label: str
    service: str
    operation: str
    key: str  # the response field that holds the list


COLLECTORS = (
    Collector(
        "log-metric-filters",
        "log metric filters",
        "logs",
        "describe_metric_filters",
        "metricFilters",
    ),
    Collector("eventbridge-rules", "EventBridge rules", "events", "list_rules", "Rules"),
    Collector(
        "synthetics-canaries", "Synthetics canaries", "synthetics", "describe_canaries", "Canaries"
    ),
    Collector(
        "contributor-insights-rules",
        "Contributor Insights rules",
        "cloudwatch",
        "describe_insight_rules",
        "InsightRules",
    ),
    Collector(
        "rum-app-monitors", "RUM app monitors", "rum", "list_app_monitors", "AppMonitorSummaries"
    ),
    Collector(
        "slos",
        "Application Signals SLOs",
        "application-signals",
        "list_service_level_objectives",
        "SloSummaries",
    ),
    Collector(
        "route53-health-checks",
        "Route 53 health checks",
        "route53",
        "list_health_checks",
        "HealthChecks",
    ),
)


@dataclass
class ExtractResult:
    account: str
    metric_alarms: int = 0
    composite_alarms: int = 0
    dashboards: int = 0
    failed_dashboards: list[str] = field(default_factory=list)
    topics: int = 0
    failed_topics: list[str] = field(default_factory=list)
    inventory: dict[str, int] = field(default_factory=dict)  # collector label -> items found
    warnings: list[str] = field(default_factory=list)  # optional reads that did not work


def _attach_targets(reader: Reader, rules: list[Any], warnings: list[str]) -> None:
    """Add each non-scheduled EventBridge rule's targets (what the rule actually does)."""
    for rule in rules[:MAX_RULES]:
        if not isinstance(rule, dict) or not isinstance(rule.get("Name"), str):
            continue
        if rule.get("ScheduleExpression"):
            continue
        try:
            data = reader.call("events", "list_targets_by_rule", Rule=rule["Name"])
        except AwsError as exc:
            warnings.append(f"targets of an EventBridge rule: {exc}")
            return
        rule["Targets"] = data.get("Targets", [])


def collect_inventory(reader: Reader, out: Path, result: ExtractResult) -> None:
    """Read the optional inventory. A call that fails (often a missing permission) only warns."""
    for c in COLLECTORS:
        try:
            data = reader.call(c.service, c.operation)
        except AwsError as exc:
            result.warnings.append(f"{c.label}: {exc}")
            continue
        items = data.get(c.key, [])
        if not isinstance(items, list) or not items:
            continue
        if c.kind == "eventbridge-rules":
            _attach_targets(reader, items, result.warnings)
        folder, body = (
            (out / "logs", {"metricFilters": items})
            if c.kind == "log-metric-filters"
            else (out / "inventory", {"kind": c.kind, "label": c.label, "items": items})
        )
        folder.mkdir(parents=True, exist_ok=True)
        name = "metric-filters.json" if c.kind == "log-metric-filters" else f"{c.kind}.json"
        (folder / name).write_text(json.dumps(body, indent=2) + "\n")
        result.inventory[c.label] = len(items)


def _check_empty(folder: Path) -> None:
    if folder.is_dir() and any(folder.glob("*.json")):
        raise AwsError(f"{folder} already contains JSON files; use a new --out folder")


def extract(
    reader: Reader,
    expected_account: str,
    out: Path,
    include_dashboards: bool = True,
    include_notifications: bool = True,
) -> ExtractResult:
    """Export alarms, dashboards and the SNS subscriptions behind alarm actions."""
    if not _ACCOUNT.match(expected_account):
        raise ValueError("--account-id must be a 12-digit AWS account id")
    account = caller_account(reader)
    if account != expected_account:
        raise AwsError(
            f"the AWS credentials belong to account {account}, not {expected_account}; "
            "nothing was read"
        )
    alarm_dir, dash_dir = out / "alarms", out / "dashboards"
    note_dir = out / "notifications"
    _check_empty(alarm_dir)
    if include_dashboards:
        _check_empty(dash_dir)
    if include_notifications:
        _check_empty(note_dir)

    result = ExtractResult(account)
    alarms = fetch_alarms(reader)
    result.metric_alarms = len(alarms.get("MetricAlarms", []))
    result.composite_alarms = len(alarms.get("CompositeAlarms", []))
    alarm_dir.mkdir(parents=True, exist_ok=True)
    (alarm_dir / "describe-alarms.json").write_text(json.dumps(alarms, indent=2) + "\n")

    if include_notifications:
        note_dir.mkdir(parents=True, exist_ok=True)
        for n, arn in enumerate(sns_topics(alarms), start=1):
            try:
                subs = fetch_subscriptions(reader, arn)
            except AwsError as exc:
                result.failed_topics.append(f"{printable(arn, 80)}: {exc}")
                continue
            (note_dir / f"topic-{n:04d}.json").write_text(json.dumps(subs, indent=2) + "\n")
            result.topics += 1

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
    collect_inventory(reader, out, result)
    return result
