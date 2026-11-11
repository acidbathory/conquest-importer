"""Reading workbooks and portal dashboards: allow-list, parameters, secrets, the 502 case."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.commands import azure as cmd
from importer.sources.azure.dashboard_models import load_dashboards
from importer.sources.azure.extract import AzureError, extract
from importer.sources.azure.sanitize import sanitize_resource  # noqa: F401  (alert path unchanged)

SUB = "00000000-0000-0000-0000-000000000001"
BASE = f"https://management.azure.com/subscriptions/{SUB}/providers/"
SECRET_URL = "https://hooks.example.com/run?sig=abcdef123456"  # noqa: S105


def workbook(name: str = "wb") -> dict[str, Any]:
    inner = {
        "version": "Notebook/1.0",
        "items": [{"type": 1, "content": {"json": f"call {SECRET_URL} now"}}],
    }
    return {
        "id": f"/subscriptions/{SUB}/resourceGroups/rg/providers/microsoft.insights/workbooks/"
        + name,
        "name": name,
        "type": "Microsoft.Insights/workbooks",
        "kind": "shared",
        "identity": {
            "type": "UserAssigned",
            "userAssignedIdentities": {"/x": {"principalId": "p"}},
        },
        "properties": {
            "displayName": "WB",
            "category": "workbook",
            "userId": "someone@example.com",
            "storageUri": "https://acct.blob.core.windows.net/c?sig=zzz",
            "serializedData": json.dumps(inner),
        },
    }


class Azure:
    def __init__(self, dashboards: str = "ok") -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.dashboards = dashboards

    def __call__(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        self.calls.append((url, dict(params or {})))
        if url == BASE + "Microsoft.Insights/workbooks":
            return {"value": [workbook()]}
        if url == BASE + "Microsoft.Portal/dashboards":
            if self.dashboards == "502":
                raise AzureError("Azure returned 502")
            return {
                "value": [
                    {
                        "name": "d",
                        "type": "Microsoft.Portal/dashboards",
                        "properties": {"lenses": [{"parts": []}]},
                    }
                ]
            }
        return {"value": []}


def test_the_default_export_does_not_ask_for_dashboards(tmp_path: Path) -> None:
    get = Azure()
    result = extract(get, SUB, tmp_path / "out")
    assert not any("workbooks" in u or "dashboards" in u for u, _ in get.calls)
    assert "workbooks" not in result.counts


def test_dashboards_are_read_with_the_documented_versions_and_query(tmp_path: Path) -> None:
    get = Azure()
    result = extract(get, SUB, tmp_path / "out", dashboards=True)
    params = dict(get.calls)
    assert params[BASE + "Microsoft.Insights/workbooks"] == {
        "api-version": "2023-06-01",
        "category": "workbook",
        "canFetchContent": "true",
    }
    assert params[BASE + "Microsoft.Portal/dashboards"] == {"api-version": "2025-04-01-preview"}
    assert result.counts["workbooks"] == 1 and result.counts["portalDashboards"] == 1
    summary = json.loads((tmp_path / "out" / "extract-summary.json").read_text())
    assert summary["apiVersions"]["workbooks"] == "2023-06-01"
    # the alert export in the same folder is unchanged, and the files load as dashboards
    assert (tmp_path / "out" / "metricAlerts.json").exists()
    assert {d.kind for d in load_dashboards(tmp_path / "out")} == {"workbook", "portal"}


def test_identity_user_and_secrets_are_stripped_from_workbooks(tmp_path: Path) -> None:
    result = extract(Azure(), SUB, tmp_path / "out", dashboards=True)
    text = (tmp_path / "out" / "workbooks.json").read_text()
    for leaked in ("principalId", "someone@example.com", "acct.blob", "sig=abcdef", "sig=zzz"):
        assert leaked not in text
    (saved,) = json.loads(text)["value"]
    assert "identity" not in saved
    assert isinstance(saved["properties"]["serializedData"], str)  # still an escaped string
    assert json.loads(saved["properties"]["serializedData"])["version"] == "Notebook/1.0"
    assert result.secrets_dropped >= 1


def test_a_502_for_portal_dashboards_is_reported_as_not_available(tmp_path: Path) -> None:
    result = extract(Azure("502"), SUB, tmp_path / "out", dashboards=True)
    assert "portalDashboards" not in result.counts
    (note,) = [w for w in result.warnings if w.startswith("portalDashboards")]
    assert "not available" in note and "convert-dashboards --source azure" in note
    assert "workbooks" in result.counts


def test_the_command_has_a_dashboards_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, help_text: Any
) -> None:
    from contextlib import contextmanager

    seen: dict[str, Any] = {}

    @contextmanager
    def fake_open() -> Any:
        yield Azure()

    monkeypatch.setattr(cmd, "_session", fake_open)
    assert "--dashboards" in help_text("extract-azure", "--help")
    res = CliRunner().invoke(
        app,
        ["extract-azure", "--subscription", SUB, "--out", str(tmp_path / "o"), "--dashboards"],
    )
    seen["out"] = res.output
    assert res.exit_code == 0, res.output
    assert "1 workbooks" in res.output and "convert-dashboards --source azure" in res.output
