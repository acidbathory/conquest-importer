"""SNS subscriptions that need an EventBridge destination, and the https payload warning."""

import json
from typing import Any

import pytest

from importer.core.notification_ir import build_plan, plan_json
from importer.sources.cloudwatch.notifications import translate_topic

TOPIC = "arn:aws:sns:us-east-1:123456789012:ops-alerts"


def sub(protocol: str, endpoint: str) -> dict[str, str]:
    return {"Protocol": protocol, "Endpoint": endpoint, "SubscriptionArn": "arn:aws:sns:x:1:t:abc"}


def topic_file(*subs: dict[str, str]) -> dict[str, Any]:
    return {"TopicArn": TOPIC, "Subscriptions": list(subs)}


@pytest.mark.parametrize("protocol", ["lambda", "sqs", "firehose"])
def test_aws_targets_become_an_eventbridge_plan_with_the_aws_side_steps(protocol: str) -> None:
    topic = translate_topic(topic_file(sub(protocol, f"arn:aws:{protocol}:us-east-1:1:x")))
    (manual,) = topic.manual_destinations
    assert (manual.service, manual.nr_type) == (protocol, "EVENT_BRIDGE")
    text = " ".join(manual.steps)
    assert "account 123456789012" in text and "us-east-1" in text  # from the topic ARN
    assert "partner event source" in text and "EventBridge rule" in text
    plan = build_plan(topic, [], "P:")  # kept even though no condition used the topic
    assert plan.needs_review and plan.manual_destinations == [manual] and plan.workflow is None
    assert "manual_destinations" in plan_json(plan)


def test_several_subscriptions_of_one_kind_share_one_manual_destination() -> None:
    topic = translate_topic(
        topic_file(sub("lambda", "arn:aws:lambda:x:1:function:a"), sub("lambda", "arn:b"))
    )
    assert len(topic.manual_destinations) == 1 and len(topic.unsupported) == 2


def test_email_and_https_plans_gain_no_new_fields() -> None:
    topic = translate_topic(
        topic_file(sub("email", "a@example.com"), sub("https", "https://h.x/y"))
    )
    plan = build_plan(topic, ["c"], "P:")
    assert not plan.needs_review and not plan.notes and not plan.manual_destinations
    assert set(json.loads(plan_json(plan))) == {"topic", "destinations", "workflow"}


def test_https_warning_is_opt_in_so_default_output_stays_identical() -> None:
    raw = topic_file(sub("https", "https://h.example.com/x"))
    assert translate_topic(raw).warnings == []
    warned = translate_topic(raw, warn_https_payload=True)
    (text,) = warned.warnings
    assert "not the SNS message envelope" in text and "handshake" in text
    assert build_plan(warned, ["c"], "P:").notes == [text]
    both = translate_topic(
        topic_file(
            sub("https", "https://h.example.com/a"), sub("https", "https://h.example.com/b")
        ),
        warn_https_payload=True,
    )
    assert len(both.warnings) == 1
