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


def run(tmp_path: Path, channels: Path = FIXTURES / "channels") -> Path:
    out = tmp_path / "notes"
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


def test_no_address_url_or_secret_reaches_any_output(tmp_path: Path) -> None:
    out = run(tmp_path)
    text = "".join(p.read_text() for p in out.rglob("*") if p.is_file())
    assert "@" not in text
    assert "https://" not in text


def test_channel_display_names_are_escaped_in_the_report(tmp_path: Path) -> None:
    channels = tmp_path / "c"
    channels.mkdir()
    name = "evil|name\x1b[31m\n# heading"
    (channels / "channels.json").write_text(
        json.dumps([{"name": PROJECT + "1", "type": "email", "displayName": name, "enabled": True}])
    )
    report = (run(tmp_path, channels) / "report.md").read_text()
    assert "\x1b" not in report and "\n# heading" not in report and "evil name" in report


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        ("email", "email destination"),
        ("webhook_tokenauth", "token"),
        ("webhook_basicauth", "password"),
        ("slack", "OAuth"),
        ("pagerduty", "PagerDuty"),
        ("sms", "no New Relic destination"),
        ("pubsub", "no New Relic destination"),
        ("campfire", "no New Relic destination"),
        ("something_new", "no New Relic destination"),
    ],
)
def test_every_channel_type_has_a_reason_a_rule_and_a_category(kind: str, words: str) -> None:
    reasons = reasons_for(kind)
    assert reasons
    for reason in reasons:
        assert classify(reason) is not Category.OTHER, reason
        assert remedies_for([reason]), reason
    steps = " ".join(s for r in remedies_for(reasons) for s in r.steps)
    assert words in steps or any(words in r for r in reasons)


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
