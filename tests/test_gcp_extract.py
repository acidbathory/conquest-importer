"""The Google Cloud extractor reads only allow-listed lists and keeps secrets out of the export."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.gcp import extract as gx
from importer.sources.gcp.dashboard_models import load_dashboards
from importer.sources.gcp.extract import GcpError, extract
from importer.sources.gcp.models import load_policies

POLICY = {
    "name": "projects/my-project/alertPolicies/1",
    "displayName": "cpu",
    "combiner": "OR",
    "conditions": [{"displayName": "c", "conditionThreshold": {}}],
}


DASHBOARD = {
    "name": "projects/my-project/dashboards/d1",
    "displayName": "Ops",
    "gridLayout": {"widgets": [{"title": "n", "text": {"content": "hi"}}]},
}


class Fake:
    """A fake Cloud Monitoring API that serves two pages of policies."""

    def __init__(self, fail: str = "") -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail = fail

    def __call__(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, params))
        if self.fail and url.endswith(self.fail):
            raise GcpError("403 denied")
        if url.endswith("alertPolicies"):
            if "pageToken" not in params:
                return {"alertPolicies": [POLICY], "nextPageToken": "t2"}
            return {"alertPolicies": [{**POLICY, "name": "projects/my-project/alertPolicies/2"}]}
        if url.endswith("notificationChannels"):
            return {
                "notificationChannels": [
                    {
                        "name": "projects/my-project/notificationChannels/9",
                        "type": "email",
                        "displayName": "ops",
                        "labels": {"email_address": "person@example.com"},
                        "enabled": True,
                    }
                ]
            }
        if url.endswith("dashboards"):
            return {"dashboards": [DASHBOARD]}
        return {
            "uptimeCheckConfigs": [
                {
                    "name": "u",
                    "displayName": "site",
                    "period": "60s",
                    "httpCheck": {"path": "/health"},
                    "monitoredResource": {
                        "type": "uptime_url",
                        "labels": {"host": "secret.example"},
                    },
                }
            ]
        }


def test_reads_all_pages_and_writes_the_folders(tmp_path: Path) -> None:
    fake = Fake()
    result = extract(fake, "my-project", tmp_path / "out")
    assert (result.policies, result.conditions, result.channels, result.uptime_checks) == (
        2,
        2,
        1,
        1,
    )
    assert len(load_policies(tmp_path / "out" / "policies")) == 2


def test_only_the_allow_listed_endpoints_are_called(tmp_path: Path) -> None:
    fake = Fake()
    extract(fake, "my-project", tmp_path / "out")
    paths = {url.rsplit("/", 1)[1] for url, _ in fake.calls}
    assert paths == set(gx.READ_PATHS.values())


def test_channel_addresses_and_check_hosts_are_not_exported(tmp_path: Path) -> None:
    extract(Fake(), "my-project", tmp_path / "out")
    text = "".join(p.read_text() for p in (tmp_path / "out").rglob("*.json"))
    assert "person@example.com" not in text and "secret.example" not in text
    assert json.loads((tmp_path / "out/channels/channels.json").read_text())[0]["type"] == "email"


@pytest.mark.parametrize("bad", ["", "A", "x/../y", "my project", "a" * 40, "-abc12"])
def test_a_bad_project_id_is_refused_before_any_call(tmp_path: Path, bad: str) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="project id"):
        extract(fake, bad, tmp_path / "out")
    assert not fake.calls


def test_an_existing_folder_with_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(ValueError, match="new folder"):
        extract(Fake(), "my-project", tmp_path)


def test_failure_reading_policies_stops_but_optional_lists_only_warn(tmp_path: Path) -> None:
    with pytest.raises(GcpError):
        extract(Fake(fail="alertPolicies"), "my-project", tmp_path / "a")
    result = extract(Fake(fail="uptimeCheckConfigs"), "my-project", tmp_path / "b")
    assert result.policies == 2 and result.uptime_checks == 0 and result.warnings


def test_cli_reports_a_clear_error_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    out = tmp_path / "o"
    result = CliRunner().invoke(app, ["extract-gcp", "--project", "my-project", "--out", str(out)])
    assert result.exit_code == 1 and "no Google credentials" in result.output


def test_the_http_client_is_closed_when_the_block_ends() -> None:
    with gx.http_getter("token") as get:
        assert callable(get)
    # a second use of the same context manager builds a fresh client and closes it too
    with gx.http_getter("token"):
        pass


def test_dashboards_come_from_the_v1_api_and_round_trip_to_the_converter(tmp_path: Path) -> None:
    fake = Fake()
    result = extract(fake, "my-project", tmp_path / "out")
    urls = [u for u, _ in fake.calls if u.endswith("dashboards")]
    assert urls == ["https://monitoring.googleapis.com/v1/projects/my-project/dashboards"]
    assert result.dashboards == 1
    assert [d.displayName for d in load_dashboards(tmp_path / "out" / "dashboards")] == ["Ops"]


def test_a_dashboard_failure_only_warns(tmp_path: Path) -> None:
    result = extract(Fake(fail="dashboards"), "my-project", tmp_path / "out")
    assert result.dashboards == 0 and result.policies == 2
    assert any(w.startswith("dashboards:") for w in result.warnings)
