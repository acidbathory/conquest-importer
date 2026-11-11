"""Google Cloud notification channels: planned per channel set, secrets and addresses never read."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.remediation import remedies_for
from importer.core.taxonomy import Category, classify
from importer.sources.gcp.notifications import CHANNEL_TYPES, load_channels, reasons_for

FIXTURES = Path(__file__).parent / "fixtures" / "gcp-notifications"
PROJECT = "projects/demo-project-123/notificationChannels/"


def convert_alerts(tmp_path: Path) -> Path:
    alerts = tmp_path / "alerts"
    done = CliRunner().invoke(
        app,
        ["convert", "--source", "gcp", "--in", str(FIXTURES / "policies"), "--out", str(alerts)],
    )
    assert done.exit_code == 0, done.output
    return alerts


def run(tmp_path: Path, channels: Path = FIXTURES / "channels", folder: str = "notes") -> Path:
    out = tmp_path / folder
    done = CliRunner().invoke(
        app,
        [
            "convert-gcp-notifications",
            "--channels",
            str(channels),
            "--alerts",
            str(convert_alerts(tmp_path)),
            "--out",
            str(out),
        ],
    )
    assert done.exit_code == 0, done.output
    return out


def test_one_plan_per_channel_set_with_the_conditions_it_routes(tmp_path: Path) -> None:
    out = run(tmp_path)
    plans = sorted((out / "notification-plans").glob("*.json"))
    assert len(plans) == 3  # {1,2} shared by two policies, {3,4}, and {99} (not exported)
    report = (out / "report.md").read_text()
    assert "cpu-oncall" in report and "cpu-oncall-copy" in report and "cpu-chat" in report
    assert "cpu-lonely" in report  # a policy with no channel is listed as unrouted
    assert "notificationChannels/99" in report
    assert "not in the export" in report


def test_channel_display_names_are_escaped_in_the_report(tmp_path: Path) -> None:
    channels = tmp_path / "c"
    channels.mkdir()
    name = "evil|name `x` <b>\x1b[31m\n# heading"
    (channels / "channels.json").write_text(
        json.dumps([{"name": PROJECT + "1", "type": "email", "displayName": name, "enabled": True}])
    )
    report = (run(tmp_path, channels) / "report.md").read_text()
    # control characters make the name look unsafe, so the resource id stands in for it
    assert "\x1b" not in report and "heading" not in report and "evil" not in report
    assert f"- {PROJECT}1 (email)" in report
    plain = tmp_path / "c2"
    plain.mkdir()
    (plain / "channels.json").write_text(
        json.dumps([{"name": PROJECT + "1", "type": "email", "displayName": "evil|name `x` <b>"}])
    )
    report = (run(tmp_path, plain, "notes2") / "report.md").read_text()
    assert "- evil name" in report and "|name" not in report


@pytest.mark.parametrize(
    ("kind", "title"),
    [
        ("email", "Email channel"),
        ("webhook_tokenauth", "Webhook channel with a token"),
        ("webhook_basicauth", "Webhook channel with basic authentication"),
        ("slack", "Slack channel"),
        ("pagerduty", "PagerDuty channel"),
        ("sms", "SMS channel"),
        ("pubsub", "Pub/Sub channel"),
        ("campfire", "Deprecated channel type"),
        ("something_new", "Channel type without a New Relic destination"),
        ("uptime check", "Channel type without a New Relic destination"),
        ("forecast no thresholds mql conditions", "Channel type without a New Relic destination"),
    ],
)
def test_every_channel_type_has_one_reason_a_category_and_its_own_rule(
    kind: str, title: str
) -> None:
    (reason,) = reasons_for(kind)
    assert classify(reason) is Category.MANUAL_FOLLOWUP, reason
    assert [r.title for r in remedies_for([reason])] == [title]


def test_a_channel_type_never_reaches_a_reason() -> None:
    hostile = "x@corp.example https://h.example/?t=SECRET"
    assert reasons_for(hostile) == reasons_for("something_new")


def test_the_type_table_covers_the_documented_types() -> None:
    assert {"email", "slack", "pagerduty", "webhook_tokenauth", "webhook_basicauth", "sms"} <= set(
        CHANNEL_TYPES
    )


def test_loading_a_folder_without_channels_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"channels\.json"):
        load_channels(tmp_path)


def test_a_missing_channels_folder_is_a_clean_cli_error(tmp_path: Path) -> None:
    done = CliRunner().invoke(
        app,
        [
            "convert-gcp-notifications",
            "--channels",
            str(tmp_path / "nope"),
            "--alerts",
            str(convert_alerts(tmp_path)),
            "--out",
            str(tmp_path / "o"),
        ],
    )
    assert done.exit_code == 1 and "Error" in done.output


def test_a_second_run_into_the_same_folder_is_refused_and_leaves_the_first_untouched(
    tmp_path: Path,
) -> None:
    out = run(tmp_path)
    before = sorted(p.name for p in out.rglob("*") if p.is_file())
    done = CliRunner().invoke(
        app,
        [
            "convert-gcp-notifications",
            *["--channels", str(FIXTURES / "channels")],
            *["--alerts", str(tmp_path / "alerts"), "--out", str(out)],
        ],
    )
    assert done.exit_code == 1 and "already has files" in done.output
    assert sorted(p.name for p in out.rglob("*") if p.is_file()) == before
