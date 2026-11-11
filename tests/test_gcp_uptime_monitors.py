"""The synthetic monitor plan for Google uptime checks: URL, period, regions, timeout, secrets."""

import json
from pathlib import Path
from typing import Any

from importer.sources.gcp import uptime
from importer.sources.gcp.extract import _uptime_summary
from importer.sources.gcp.uptime_monitors import MonitorSpec, plan_all

FIX = Path(__file__).parent / "fixtures" / "gcp-extract-plus"


def summaries() -> list[dict[str, Any]]:
    raw = json.loads((FIX / "api-uptime-check-configs.json").read_text())["uptimeCheckConfigs"]
    return [_uptime_summary(c) for c in raw]


def plans() -> dict[str, MonitorSpec]:
    return {p.check_id: p for p in plan_all(summaries())}


def test_a_plain_get_is_a_simple_monitor_with_url_period_regions_and_timeout() -> None:
    p = plans()["site-health"]
    assert (p.target, p.uri, p.frequency) == (
        "SIMPLE",
        "https://host.example.com/health",
        "EVERY_MINUTE",
    )
    assert p.locations == [
        "AWS_US_EAST_1",
        "AWS_US_WEST_1",
        "AWS_EU_WEST_1",
        "AWS_EU_CENTRAL_1",
        "AWS_AP_SOUTHEAST_1",
        "AWS_AP_SOUTHEAST_2",
    ]
    assert p.verify_ssl is True and p.google_timeout == "10s" and not p.created
    assert plans()["with-header"].target == "SCRIPT_API"
    assert plans()["home"].response_validation_text == "Welcome"
    assert plans()["home"].frequency == "EVERY_5_MINUTES"


def test_basic_auth_headers_and_body_become_secret_refs_and_a_scripted_monitor() -> None:
    auth = plans()["api-auth"]
    assert auth.target == "SCRIPT_API"
    refs = {s.secret_ref for s in auth.secrets}
    assert "GCP_UPTIME_API_AUTH_PASSWORD" in refs and len(auth.secrets) == 2
    post = plans()["post-check"]
    assert post.target == "SCRIPT_API" and post.uri == "https://host.example.com:8443/hook"
    assert any(s.purpose == "request body" for s in post.secrets)


def test_tcp_internal_and_regions_are_explained() -> None:
    assert plans()["tcp"].target == "NONE" and "TCP" in plans()["tcp"].notes[0]
    assert any("private location" in n for n in plans()["internal"].notes)
    assert plans()["fast"].locations == ["AWS_SA_EAST_1"]
    assert plans()["internal"].frequency == "EVERY_15_MINUTES"


def test_no_secret_value_reaches_the_plan_file(tmp_path: Path) -> None:
    uptime.write_outputs(summaries(), tmp_path)
    text = (tmp_path / "synthetic-monitor-plan.json").read_text()
    for planted in ("PLANTED-SECRET-PASSWORD", "PLANTED-HEADER-TOKEN", "probe-user", "eyJ0b2tl"):
        assert planted not in text
    assert "synthetic-monitor-plan.json" in (tmp_path / "report.md").read_text()


def test_an_older_export_without_details_writes_no_plan(tmp_path: Path) -> None:
    old = [{k: v for k, v in s.items() if k != "details"} for s in summaries()]
    uptime.write_outputs(old, tmp_path)
    assert not (tmp_path / "synthetic-monitor-plan.json").exists()
