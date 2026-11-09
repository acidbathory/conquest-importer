"""Every registered source has report wording, a description label and a resource prefix."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.common import RESOURCE_PREFIXES, SOURCE_LABELS, source_description
from importer.core.ir import Confidence
from importer.core.migration_summary import Asset, Summary
from importer.core.report_html import PROFILES, render_report
from importer.sources import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
SUMMARY = Summary([Asset("alarm", "a", "rule", Confidence.APPROXIMATE)], created=None)
NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_source_has_a_report_profile(name: str) -> None:
    assert name in PROFILES


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_profile_renders_its_own_wording(name: str) -> None:
    profile = PROFILES[name]
    html = render_report(SUMMARY, aws_account="acct-1", source=name, generated=NOW)
    assert profile.account_label in html and "acct-1" in html
    assert profile.alarm_title[0] in html
    assert profile.read_phrase.split(".")[0][:30] in html or profile.read_phrase[:20] in html
    for step in profile.checklist:
        assert step.split("<")[0][:25] in html


def test_the_profiles_do_not_borrow_each_others_wording() -> None:
    others = {n: render_report(SUMMARY, source=n, generated=NOW) for n in PROFILES}
    assert (
        "AWS" not in others["dynatrace-classic"] and "CloudWatch" not in others["dynatrace-classic"]
    )
    assert "Dynatrace" in others["dynatrace-classic"]
    assert others["dynatrace-classic"] != others["dynatrace"]
    assert PROFILES["dynatrace-classic"].account_label == "Dynatrace tenant"


def test_the_classic_profile_describes_what_the_classic_source_reads() -> None:
    profile = PROFILES["dynatrace-classic"]
    text = " ".join([profile.read_phrase, profile.unit_sentence, *profile.checklist])
    for word in ("metric event", "anomaly detector", "metric map", "notification"):
        assert word in text.lower().replace("anomaly detection", "anomaly detector"), word


def test_the_report_command_accepts_every_profile_and_refuses_others(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()
    for name in PROFILES:
        res = CliRunner().invoke(
            app, ["report", str(tmp_path / "run"), "--source", name, "--out", str(tmp_path / "r")]
        )
        assert res.exit_code == 0, (name, res.output)
    bad = CliRunner().invoke(app, ["report", str(tmp_path / "run"), "--source", "nagios"])
    assert bad.exit_code == 1 and all(n in bad.output for n in PROFILES)


@pytest.mark.parametrize("name", ["cloudwatch", "gcp", "dynatrace-classic"])
def test_labels_and_prefixes_exist_where_output_is_not_pinned(name: str) -> None:
    assert name in SOURCE_LABELS and name in RESOURCE_PREFIXES
    assert RESOURCE_PREFIXES[name].isidentifier()


def test_the_classic_description_names_the_source() -> None:
    folder = FIXTURES / "dynatrace-classic"
    assert folder.is_dir()
    monitors = REGISTRY["dynatrace-classic"].convert(folder)
    assert monitors
    assert source_description(monitors[0]).startswith("Imported from Dynatrace classic alerting")
