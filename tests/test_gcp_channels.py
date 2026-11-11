"""Google Cloud notification channel types: every documented type has its own reason and steps."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.notification_ir import DestinationType
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.gcp.notifications import (
    CHANNEL_SPECS,
    CHANNEL_TYPES,
    GENERIC,
    reasons_for,
)

# `gcloud beta monitoring channel-descriptors list --format='value(type)'`, from
# docs/research/gcp-alerting-verified.md section 3 (cloud.google.com, updated 2026-11-07)
DESCRIPTOR_TYPES = [
    "campfire",
    "email",
    "google_chat",
    "hipchat",
    "pagerduty",
    "pubsub",
    "slack",
    "sms",
    "webhook_basicauth",
    "webhook_tokenauth",
]
TITLES = {
    "google_chat": "Google Chat channel",
    "hipchat": "Deprecated channel type",
    "campfire": "Deprecated channel type",
    "sms": "SMS channel",
    "pubsub": "Pub/Sub channel",
    "mobile": "Mobile app channel",
}


@pytest.mark.parametrize("kind", DESCRIPTOR_TYPES)
def test_every_documented_type_has_its_own_reason(kind: str) -> None:
    (reason,) = reasons_for(kind)
    assert reason != GENERIC
    assert classify(reason) is Category.MANUAL_FOLLOWUP, reason
    assert steps_for([reason])
    if kind in TITLES:
        assert [r.title for r in remedies_for([reason])] == [TITLES[kind]]


def test_deprecated_types_say_why_with_the_google_wording() -> None:
    for kind in ("hipchat", "campfire"):
        (reason,) = reasons_for(kind)
        assert "deprecated" in reason and "do not create new channels" in reason.lower()


def test_mobile_is_kept_manual_and_marked_unverified() -> None:
    (reason,) = reasons_for("mobile")
    assert "unverified" in reason and "descriptor" in reason
    assert CHANNEL_SPECS["mobile"].nr_type == DestinationType.MOBILE_PUSH.value


def test_unknown_types_get_the_generic_reason() -> None:
    assert reasons_for("something_new") == [GENERIC]
    assert reasons_for("x@corp.example https://h.example/?t=SECRET") == [GENERIC]


def test_the_new_relic_type_of_every_channel_is_one_of_the_destination_types() -> None:
    valid = {t.value for t in DestinationType}
    assert len(valid) == 14
    for spec in CHANNEL_SPECS.values():
        assert spec.nr_type in valid | {""}, spec
    assert set(CHANNEL_TYPES) == set(CHANNEL_SPECS)


def test_secrets_google_obfuscates_are_named_as_references() -> None:
    names = {k: s.secret.name for k, s in CHANNEL_SPECS.items() if s.secret is not None}
    assert names == {
        "slack": "GCP_SLACK_AUTH_TOKEN",
        "webhook_basicauth": "GCP_WEBHOOK_PASSWORD",
        "webhook_tokenauth": "GCP_WEBHOOK_TOKEN",
        "pagerduty": "GCP_PAGERDUTY_SERVICE_KEY",
    }
    for kind in names:
        text = " ".join(CHANNEL_SPECS[kind].steps)
        assert names[kind] in text


def test_the_plan_carries_a_manual_destination_per_channel_without_secret_values(
    tmp_path: Path,
) -> None:
    project = "projects/demo-project-123/notificationChannels/"
    kinds = ["slack", "pagerduty", "google_chat", "hipchat", "mobile"]
    channels = tmp_path / "c"
    channels.mkdir()
    (channels / "channels.json").write_text(
        json.dumps(
            [
                {"name": project + str(i), "type": k, "displayName": f"chan {k}"}
                for i, k in enumerate(kinds)
            ]
        )
    )
    policies = tmp_path / "p"
    policies.mkdir()
    (policies / "policies.json").write_text(
        json.dumps(
            [
                {
                    "name": "projects/demo-project-123/alertPolicies/1",
                    "displayName": "cpu",
                    "enabled": True,
                    "combiner": "OR",
                    "notificationChannels": [project + str(i) for i in range(len(kinds))],
                    "conditions": [
                        {
                            "name": "projects/demo-project-123/alertPolicies/1/conditions/1",
                            "displayName": "cpu high",
                            "conditionThreshold": {
                                "filter": 'metric.type="compute.googleapis.com/instance/cpu/'
                                'utilization"',
                                "comparison": "COMPARISON_GT",
                                "thresholdValue": 0.9,
                                "duration": "300s",
                                "aggregations": [
                                    {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}
                                ],
                            },
                        }
                    ],
                }
            ]
        )
    )
    alerts = tmp_path / "alerts"
    runner = CliRunner()
    done = runner.invoke(
        app, ["convert", "--source", "gcp", "--in", str(policies), "--out", str(alerts)]
    )
    assert done.exit_code == 0, done.output
    out = tmp_path / "n"
    done = runner.invoke(
        app,
        [
            "convert-gcp-notifications",
            "--channels",
            str(channels),
            "--alerts",
            str(alerts),
            "--out",
            str(out),
        ],
    )
    assert done.exit_code == 0, done.output
    (plan_file,) = (out / "notification-plans").glob("*.json")
    plan = json.loads(plan_file.read_text())
    manual = {m["service"]: m for m in plan["manual_destinations"]}
    assert set(manual) == set(kinds)
    assert manual["pagerduty"]["nr_type"] == "PAGERDUTY_SERVICE_INTEGRATION"
    assert manual["google_chat"]["nr_type"] == "WEBHOOK"
    assert manual["hipchat"]["nr_type"] == ""
    assert "GCP_PAGERDUTY_SERVICE_KEY" in " ".join(manual["pagerduty"]["steps"])
    assert plan["needs_review"] is True


def test_an_uptime_check_alert_points_to_the_synthetics_monitor_and_its_failure_query() -> None:
    reason = "an uptime check alert depends on the check itself, which is not migrated"
    steps = " ".join(steps_for([reason]))
    assert "SyntheticCheck" in steps and "FAILED" in steps
