"""Azure commands: extract-azure, check-azure-data, convert --source azure, doctor, report."""

from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.commands import azure as cmd
from importer.core.doctor import DOCTOR_SOURCES, passed, run_checks
from importer.sources.azure.extract import AzureError, ExtractResult

SUB = "00000000-0000-0000-0000-000000000001"
FIX = Path(__file__).parent / "fixtures" / "azure"
GOOD = {
    "AZURE_TENANT_ID": "00000000-0000-0000-0000-000000000002",
    "AZURE_CLIENT_ID": "00000000-0000-0000-0000-000000000003",
    "AZURE_CLIENT_SECRET": "s3cret~value",
}


def test_help_texts(help_text: Callable[..., str]) -> None:
    text = help_text("extract-azure", "--help")
    assert "--subscription" in text and "--out" in text
    assert "AZURE_CLIENT_SECRET" in text and "Read-only" in text
    data = help_text("check-azure-data", "--help")
    assert "--nr-account-id" in data and "--alerts" in data


def test_extract_azure_reports_and_prints_next_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    @contextmanager
    def fake_open() -> Any:
        yield lambda _url, _params=None: {}

    def fake_extract(get: Any, sub: str, out: Path) -> ExtractResult:
        seen.update(sub=sub, out=out)
        return ExtractResult(sub, {"metricAlerts": 3, "actionGroups": 1}, ["x: denied"], 4)

    monkeypatch.setattr(cmd, "_session", fake_open)
    monkeypatch.setattr(cmd, "extract", fake_extract)
    out = tmp_path / "az"
    result = CliRunner().invoke(app, ["extract-azure", "--subscription", SUB, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert seen["sub"] == SUB and "3 metricAlerts" in result.output
    assert "4 secret values were dropped" in result.output
    assert f"convert --source azure --in {out}" in result.output
    assert "Could not read" in result.output


def test_extract_azure_uses_the_subscription_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AZURE_SUBSCRIPTION_ID", SUB)
    seen: dict[str, Any] = {}

    @contextmanager
    def fake_open() -> Any:
        yield lambda _url, _params=None: {}

    monkeypatch.setattr(cmd, "_session", fake_open)
    monkeypatch.setattr(
        cmd, "extract", lambda _g, s, _o: seen.setdefault("s", s) and ExtractResult(s)
    )
    result = CliRunner().invoke(app, ["extract-azure", "--out", str(tmp_path / "o")])
    assert result.exit_code == 0 and seen["s"] == SUB


def test_extract_azure_errors_are_short_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    bad = CliRunner().invoke(
        app, ["extract-azure", "--subscription", "nope", "--out", str(tmp_path / "o")]
    )
    assert bad.exit_code == 1 and "UUID" in bad.output

    @contextmanager
    def failing() -> Any:
        raise AzureError("Azure sign-in returned 401: invalid_client")
        yield

    monkeypatch.setattr(cmd, "_session", failing)
    result = CliRunner().invoke(
        app, ["extract-azure", "--subscription", SUB, "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1 and "401" in result.output and "Traceback" not in result.output


def test_convert_source_azure_writes_ir_nerdgraph_terraform_and_a_report(tmp_path: Path) -> None:
    out = tmp_path / "conv"
    result = CliRunner().invoke(
        app, ["convert", "--source", "azure", "--in", str(FIX / "metric_alerts"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert list((out / "ir").glob("*.json")) and (out / "report.md").is_file()
    assert "Azure Monitor to New Relic" in (out / "report.md").read_text()
    assert list((out / "nerdgraph").glob("*.json"))
    assert "newrelic_nrql_alert_condition" in (out / "main.tf").read_text()


def test_check_azure_data_exits_2_for_blind_conditions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conv = tmp_path / "conv"
    CliRunner().invoke(
        app,
        ["convert", "--source", "azure", "--in", str(FIX / "metric_alerts"), "--out", str(conv)],
    )

    class Empty:
        def run(
            self, query: str, variables: dict[str, Any], is_write: bool = False
        ) -> dict[str, Any]:
            assert not is_write
            return {"actor": {"account": {"nrql": {"results": []}}}}

    @contextmanager
    def client(region: str) -> Any:
        yield Empty()

    monkeypatch.setattr(cmd, "new_relic_client", client)
    result = CliRunner().invoke(
        app, ["check-azure-data", "--alerts", str(conv), "--nr-account-id", "1"]
    )
    assert result.exit_code == 2 and "no data" in result.output
    missing = CliRunner().invoke(app, ["check-azure-data", "--alerts", str(conv)])
    assert missing.exit_code == 1


def test_doctor_azure_checks_the_environment_without_showing_values() -> None:
    assert "azure" in DOCTOR_SOURCES
    ok = run_checks("us-east-1", None, env={**GOOD, "AZURE_SUBSCRIPTION_ID": SUB}, source="azure")
    assert passed(ok)
    text = " ".join(c.detail for c in ok)
    assert "s3cret" not in text and GOOD["AZURE_TENANT_ID"] not in text
    token_only = run_checks(
        "us-east-1", None, env={"AZURE_ACCESS_TOKEN": "abc.def"}, source="azure"
    )
    assert passed(token_only)
    missing = run_checks("us-east-1", None, env={}, source="azure")
    assert not passed(missing) and any("AZURE_CLIENT_SECRET" in c.detail for c in missing)
    smart = run_checks(
        "us-east-1", None, env={**GOOD, "AZURE_CLIENT_SECRET": "bad”secret"}, source="azure"
    )
    assert not passed(smart) and any("plain" in c.detail for c in smart)
    bad_id = run_checks(
        "us-east-1", None, env={**GOOD, "AZURE_TENANT_ID": "x/../y"}, source="azure"
    )
    assert not passed(bad_id)


def test_doctor_command_accepts_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    result = CliRunner().invoke(app, ["doctor", "--source", "azure"])
    assert result.exit_code == 0 and "azure credentials" in result.output
