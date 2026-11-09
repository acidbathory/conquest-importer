"""Translate SNS topic subscriptions (exported by extract-cloudwatch) into notification IR."""

import json
import re
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.notification_ir import (
    EMAIL_ADDRESS,
    ChannelKind,
    DestinationType,
    IRChannel,
    IRTopic,
    ManualDestination,
    spec_for,
)
from importer.core.text import printable
from importer.sources.cloudwatch.extract import _SNS_TOPIC

_HTTPS = re.compile(r"^https://[^\s\"'<>\\]{1,2000}$")
NO_EQUIVALENT = {"sms", "application"}
# SNS protocols a New Relic EventBridge destination can reach, through a rule on the AWS side
EVENT_BRIDGE_TARGETS = {
    "lambda": "Lambda function",
    "sqs": "SQS queue",
    "firehose": "Firehose stream",
}
# The subscriber's own ARN names the account and region the EventBridge destination is for. ASCII
# digits only, a bounded region, and a length cap before any matching.
_ENDPOINT = re.compile(
    r"arn:aws[a-z-]{0,12}:(?:lambda|sqs|firehose):"
    r"(?P<region>[a-z]{2}(?:-[a-z]{2,12}){1,3}-[0-9]{1,2}):(?P<account>[0-9]{12}):"
)
MAX_ENDPOINT = 2048
HTTPS_WARNING = (
    "https subscription: the receiver gets New Relic's webhook payload, not the SNS message "
    "envelope, and cannot answer the SNS subscription handshake; an endpoint written for SNS "
    "will not understand it, so check the payload template"
)


def _where(endpoint: str) -> tuple[str, str]:
    """(account, region) of a Lambda, SQS or Firehose ARN, or ("", "") when it is not one."""
    match = _ENDPOINT.match(endpoint) if len(endpoint) <= MAX_ENDPOINT else None
    return (match.group("account"), match.group("region")) if match else ("", "")


def _event_bridge(protocol: str, account: str, region: str) -> tuple[ManualDestination, list[str]]:
    """The manual route for one account and region, and short lines for the report."""
    where = f"AWS account {account} in {region}" if account else "your AWS account"
    target = EVENT_BRIDGE_TARGETS[protocol]
    steps = [
        f"Create a New Relic EventBridge destination for {where}, then a channel with the "
        "event source name New Relic gives it.",
        *spec_for(DestinationType.EVENT_BRIDGE).human_steps,
        f"Add an EventBridge rule on that event bus that sends the events to the {target} "
        "this SNS topic delivered to.",
    ]
    label = f"{account} {region}" if account else ""
    lines = [
        f"{protocol}: create a New Relic EventBridge destination for {where}",
        f"{protocol}: in AWS, associate the New Relic partner event source with an event bus",
        f"{protocol}: add an EventBridge rule on that bus that targets the {target}",
    ]
    return ManualDestination(
        service=protocol, nr_type="EVENT_BRIDGE", label=label, steps=steps
    ), lines


def load_topics(path: Path) -> list[dict[str, Any]]:
    """Read every topic-*.json file in a folder (or one file)."""
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    topics: list[dict[str, Any]] = []
    for file in files:
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // 1024 // 1024} MB")
        data = json.loads(file.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("Subscriptions"), list):
            raise ValueError(f"{file.name}: expected TopicArn and a Subscriptions list")
        topics.append(data)
    return topics


def translate_topic(raw: dict[str, Any], warn_https_payload: bool = False) -> IRTopic:
    arn = str(raw.get("TopicArn", ""))
    name = arn.rsplit(":", 1)[-1] if _SNS_TOPIC.match(arn) else "unknown-topic"
    channels: list[IRChannel] = []
    unsupported: list[str] = []
    manual: dict[tuple[str, str, str], ManualDestination] = {}
    step_lines: list[str] = []
    warnings: list[str] = []
    for sub in raw["Subscriptions"]:
        if not isinstance(sub, dict):
            unsupported.append("subscription entry is not an object")
            continue
        protocol = str(sub.get("Protocol", "")).lower()
        endpoint = str(sub.get("Endpoint", ""))
        if str(sub.get("SubscriptionArn", "")) == "PendingConfirmation":
            unsupported.append(f"{protocol} subscription is not confirmed in AWS")
        elif protocol in ("email", "email-json"):
            if EMAIL_ADDRESS.match(endpoint):
                channels.append(IRChannel(kind=ChannelKind.EMAIL, target=endpoint))
            else:
                unsupported.append("email subscription has an address that is not supported")
        elif protocol == "https":
            if _HTTPS.match(endpoint):
                channels.append(IRChannel(kind=ChannelKind.WEBHOOK, target=endpoint))
                if warn_https_payload and HTTPS_WARNING not in warnings:
                    warnings.append(HTTPS_WARNING)
            else:
                unsupported.append("https subscription has a URL that is not supported")
        elif protocol == "http":
            unsupported.append("plain http endpoints are not migrated (use https)")
        elif protocol in EVENT_BRIDGE_TARGETS:
            if _SNS_TOPIC.match(arn):
                key = (protocol, *_where(endpoint))
                if key not in manual:
                    manual[key], lines = _event_bridge(*key)
                    step_lines += lines
            unsupported.append(
                f"{protocol} subscriptions are reached through a New Relic EventBridge "
                "destination, which needs steps in AWS; see the manual steps in the plan"
            )
        elif protocol in NO_EQUIVALENT:
            unsupported.append(f"{protocol} subscriptions have no New Relic equivalent")
        else:
            unsupported.append(
                f"subscription protocol {printable(protocol, 20)!r} is not supported"
            )
    unsupported += step_lines  # the report prints these lines; the plan file holds the full steps
    return IRTopic(
        source_system="cloudwatch",
        source_key=arn,
        name=name,
        channels=channels,
        unsupported=unsupported,
        manual_destinations=list(manual.values()),
        warnings=warnings,
    )


def routing(monitors: list[IRMonitor], condition_names: dict[int, str]) -> dict[str, list[str]]:
    """SNS topic ARN -> names of the converted conditions whose alarms notified it.

    `condition_names` maps a monitor's source_id to the name used in New Relic; monitors that
    were not converted are left out.
    """
    out: dict[str, list[str]] = {}
    for m in monitors:
        name = condition_names.get(m.source_id)
        if name is None:
            continue
        for handle in m.notification.handles:
            if _SNS_TOPIC.match(handle):
                out.setdefault(handle, []).append(name)
    return {arn: sorted(set(names)) for arn, names in out.items()}
