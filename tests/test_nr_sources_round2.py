"""EventBridge plans read the subscriber's account and region; Dynatrace labels."""

import pytest

from importer.core.notification_ir import build_plan
from importer.sources.cloudwatch.notifications import translate_topic
from importer.sources.dynatrace.translate import action_label

TOPIC = "arn:aws:sns:us-east-1:123456789012:ops-alerts"


def raw(*subs: tuple[str, str], topic: str = TOPIC) -> dict[str, object]:
    return {
        "TopicArn": topic,
        "Subscriptions": [
            {"Protocol": p, "Endpoint": e, "SubscriptionArn": "arn:aws:sns:x:1:t:abc"}
            for p, e in subs
        ],
    }


def steps(topic_raw: dict[str, object]) -> list[str]:
    return [s for m in translate_topic(topic_raw).manual_destinations for s in m.steps]


def test_account_and_region_come_from_the_subscriber_not_the_topic() -> None:
    text = " ".join(steps(raw(("lambda", "arn:aws:lambda:eu-west-1:210987654321:function:f"))))
    assert "account 210987654321" in text and "eu-west-1" in text
    assert "123456789012" not in text and "us-east-1" not in text


def test_endpoints_in_different_accounts_and_regions_each_get_a_destination() -> None:
    topic = translate_topic(
        raw(
            ("lambda", "arn:aws:lambda:eu-west-1:210987654321:function:a"),
            ("lambda", "arn:aws:lambda:us-east-2:210987654321:function:b"),
            ("lambda", "arn:aws:lambda:eu-west-1:210987654321:function:c"),
            ("sqs", "arn:aws:sqs:eu-west-1:210987654321:q"),
        )
    )
    keys = sorted((m.service, m.label) for m in topic.manual_destinations)
    assert keys == [
        ("lambda", "210987654321 eu-west-1"),
        ("lambda", "210987654321 us-east-2"),
        ("sqs", "210987654321 eu-west-1"),
    ]


@pytest.mark.parametrize(
    "endpoint",
    [
        "arn:aws:lambda:eu-west-1:١٢٣٤٥٦٧٨٩٠١٢:function:f",  # Arabic-Indic digits
        "arn:aws:lambda:eu-west-1:12345:function:f",
        "arn:aws:lambda:" + "a" * 5000 + ":210987654321:function:f",
        "arn:aws:lambda:EU WEST:210987654321:function:f",
        "not an arn",
        "",
    ],
)
def test_malformed_endpoints_fall_back_to_a_generic_account(endpoint: str) -> None:
    topic = translate_topic(raw(("lambda", endpoint)))
    (manual,) = topic.manual_destinations
    assert manual.label == "" and "your AWS account" in " ".join(manual.steps)


def test_no_manual_plan_for_a_topic_that_is_not_a_valid_sns_arn() -> None:
    topic = translate_topic(
        raw(("lambda", "arn:aws:lambda:eu-west-1:210987654321:function:f"), topic="garbage")
    )
    assert topic.manual_destinations == [] and "EventBridge destination" in topic.unsupported[0]


def test_the_steps_reach_the_report_lines() -> None:
    topic = translate_topic(raw(("lambda", "arn:aws:lambda:eu-west-1:210987654321:function:f")))
    lines = " | ".join(topic.unsupported)
    assert "210987654321" in lines and "partner event source" in lines and "rule" in lines
    assert all(len(line) <= 120 for line in topic.unsupported if line.startswith("lambda: "))
    assert build_plan(topic, [], "P:").needs_review


@pytest.mark.parametrize(
    ("action", "label"),
    [
        ("dynatrace.victorops:send-alert", "victorops"),
        ("dynatrace.xmatters:create-event", "xmatters"),
        ("app.xmatters:http-post", "xmatters"),
        ("dynatrace.opsgenie:create-alert", "opsgenie"),
        ("dynatrace.automations:http-function", "http request"),
        ("dynatrace.msteams:send-message", "ms teams"),
    ],
)
def test_vendor_actions_are_not_taken_for_a_plain_http_request(action: str, label: str) -> None:
    assert action_label(action) == label
