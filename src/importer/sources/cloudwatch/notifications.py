"""Translate SNS topic subscriptions (exported by extract-cloudwatch) into notification IR."""

import json
import re
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.notification_ir import ChannelKind, IRChannel, IRTopic
from importer.core.text import printable
from importer.sources.cloudwatch.extract import _SNS_TOPIC

_EMAIL = re.compile(r"^[^@\s,;<>\"'\\]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_HTTPS = re.compile(r"^https://[^\s\"'<>\\]{1,2000}$")
NO_EQUIVALENT = {"sms", "lambda", "sqs", "application", "firehose"}


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


def translate_topic(raw: dict[str, Any]) -> IRTopic:
    arn = str(raw.get("TopicArn", ""))
    name = arn.rsplit(":", 1)[-1] if _SNS_TOPIC.match(arn) else "unknown-topic"
    channels: list[IRChannel] = []
    unsupported: list[str] = []
    for sub in raw["Subscriptions"]:
        if not isinstance(sub, dict):
            unsupported.append("subscription entry is not an object")
            continue
        protocol = str(sub.get("Protocol", "")).lower()
        endpoint = str(sub.get("Endpoint", ""))
        if str(sub.get("SubscriptionArn", "")) == "PendingConfirmation":
            unsupported.append(f"{protocol} subscription is not confirmed in AWS")
        elif protocol in ("email", "email-json"):
            if _EMAIL.match(endpoint):
                channels.append(IRChannel(kind=ChannelKind.EMAIL, target=endpoint))
            else:
                unsupported.append("email subscription has an address that is not supported")
        elif protocol == "https":
            if _HTTPS.match(endpoint):
                channels.append(IRChannel(kind=ChannelKind.WEBHOOK, target=endpoint))
            else:
                unsupported.append("https subscription has a URL that is not supported")
        elif protocol == "http":
            unsupported.append("plain http endpoints are not migrated (use https)")
        elif protocol in NO_EQUIVALENT:
            unsupported.append(f"{protocol} subscriptions have no New Relic equivalent")
        else:
            unsupported.append(
                f"subscription protocol {printable(protocol, 20)!r} is not supported"
            )
    return IRTopic(
        source_system="cloudwatch",
        source_key=arn,
        name=name,
        channels=channels,
        unsupported=unsupported,
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
