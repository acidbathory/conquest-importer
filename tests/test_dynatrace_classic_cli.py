"""The documented sequence for the Dynatrace classic source, run end to end through the CLI."""

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.commands import dynatrace_classic as command
from importer.sources.dynatrace.classic.extract import http_getter

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-classic"
TOKEN = "dt0s16.ABCDEFGH.SECRETSECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests


def fixture_items() -> dict[str, list[dict[str, object]]]:
    """The docs-derived and synthetic objects, grouped by schema, as a tenant could return them."""
    grouped: dict[str, list[dict[str, object]]] = {}
    for file in sorted(FIXTURES.glob("*.json")):
        for item in json.loads(file.read_text())["items"]:
            if isinstance(item, dict) and isinstance(item.get("schemaId"), str):
                grouped.setdefault(str(item["schemaId"]), []).append(item)
    return grouped


def test_extract_then_convert_then_read_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grouped = fixture_items()

    def handler(request: httpx.Request) -> httpx.Response:
        schema = request.url.params["schemaIds"]
        # not json=: a fixture holds a number that is infinity once parsed
        body = json.dumps({"items": grouped.get(schema, [])}).encode()
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    monkeypatch.setattr(
        command,
        "http_getter",
        lambda token, url: http_getter(token, url, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setenv("DYNATRACE_API_TOKEN", TOKEN)
    monkeypatch.setenv("DYNATRACE_ENV_URL", "https://abc12345.apps.dynatrace.com")
    export = tmp_path / "export"
    runner = CliRunner()
    first = runner.invoke(app, ["extract-dynatrace-classic", "--out", str(export)])
    assert first.exit_code == 0, first.output
    assert TOKEN not in first.output
    assert "importer convert --source dynatrace-classic" in first.output

    alerts = export / "alerts"
    second = runner.invoke(
        app,
        ["convert", "--source", "dynatrace-classic", "--in", str(export), "--out", str(alerts)],
    )
    assert second.exit_code == 0, second.output
    report = (alerts / "report.md").read_text()
    assert "SECRETSECRET" not in report and "SHOULD-NEVER-BE-PRINTED" not in report
    assert list((alerts / "nerdgraph").glob("*.json")), "converted conditions were emitted"
    assert (alerts / "main.tf").read_text().count("newrelic_nrql_alert_condition") >= 5


def test_convert_lists_the_source_in_its_help() -> None:
    result = CliRunner().invoke(app, ["convert", "--help"])
    assert "dynatrace-classic" in result.output
