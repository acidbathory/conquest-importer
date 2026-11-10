"""Every report names where it came from; the HTML shows inline logos, nothing remote."""

from datetime import UTC, datetime

import pytest

from importer.core.migration_summary import Summary
from importer.core.report_brand import (
    PAIRS,
    badge_line,
    header_lines,
    logo_strip,
    pair_name,
)
from importer.core.report_html import PROFILES, render_report


def test_pair_names() -> None:
    assert pair_name("grafana") == "Grafana to New Relic"
    assert pair_name("datadog") == "Datadog to New Relic"
    assert pair_name("dynatrace") == "Dynatrace to New Relic"
    assert pair_name("dynatrace-classic") == "Dynatrace to New Relic"
    assert pair_name("cloudwatch") == "AWS CloudWatch to New Relic"
    assert pair_name("gcp") == "Google Cloud Monitoring to New Relic"


def test_unknown_source_falls_back_to_new_relic_only() -> None:
    assert pair_name("mystery") == "New Relic"
    assert header_lines("Report", "mystery")[0] == "# Report"


def test_header_lines_markdown() -> None:
    lines = header_lines("Conversion report", "grafana")
    assert lines[0] == "# Conversion report: Grafana to New Relic"
    assert lines[1] == ""
    assert "![" not in lines[2] and "http" not in lines[2]
    assert lines[2] == badge_line("grafana")


@pytest.mark.parametrize("source", sorted(PAIRS))
def test_html_report_has_pair_title_and_inline_logos(source: str) -> None:
    html = (
        render_report(Summary([], None), source=source, generated=datetime(2026, 1, 1, tzinfo=UTC))
        if source in PROFILES
        else logo_strip(source)
    )
    assert "<svg" in html and "http://www.w3.org/2000/svg" in html
    assert "<img" not in html and "src=" not in html and "url(http" not in html
    if source in PROFILES:
        assert f"<title>{pair_name(source)} migration report</title>" in html
        assert 'role="img"' in html and "aria-label" in html


def test_aws_gets_text_badge_not_a_logo() -> None:
    strip = logo_strip("cloudwatch")
    assert "AWS CloudWatch" in strip
    assert strip.count("<path") == 2  # arrow + New Relic mark only
