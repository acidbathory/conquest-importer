"""The plan command, the Markdown report and the JSON: escaping, bounds and the documented flow."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.dashboards import models as m
from importer.sources.grafana.dashboards.extract import http_getter
from importer.sources.grafana.dashboards.load import load_data, load_estate
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import build_report, plan_json

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-dashboards"
HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_SECRETSECRET"  # noqa: S105 - a made-up value for leak tests


def run(*args: str) -> Any:
    return CliRunner().invoke(app, ["plan-grafana-dashboards", *args])


def test_the_report_has_the_tier_table_and_the_caveat() -> None:
    report = build_report(build_plan(load_estate(FIXTURES)))
    assert "# Grafana dashboards: coverage plan" in report
    assert "| T1 |" in report and "| T3 with extra widget types |" in report
    assert "do not say whether the metric exists" in report
    assert "## Why things do not convert" in report and "## Panel types" in report
    assert report == build_report(build_plan(load_estate(FIXTURES)))  # the same twice


def test_untrusted_text_in_the_report_is_escaped() -> None:
    body = {
        "uid": "u|v`w",
        "title": "<script>alert(1)</script> | ``` [x](javascript:alert(1)) \x1b[31mred‮",
        "panels": [
            {
                "id": 1,
                "type": "stat",
                "title": "<img src=x onerror=alert(1)>|\n# heading\x00",
                "targets": [{"refId": "A", "expr": "up == 1"}],
            }
        ],
        "templating": {"list": [{"name": "<b>|`", "type": "custom"}]},
    }
    report = build_report(build_plan(load_data(body, "x|y`z.json")))
    for bad in ("<script>", "<img", "\x1b", "‮", "\x00", "`", "[x](", "</"):
        assert bad not in report
    rows = [line for line in report.splitlines() if line.startswith("| ")]
    assert all(not line.startswith("| #") for line in rows)
    first = next(line for line in rows if "alert" in line and "stat" in line)
    assert first.count("|") == 4  # a title with a pipe did not add a column


def test_the_json_is_ascii_valid_and_carries_the_totals() -> None:
    plan = build_plan(load_estate(FIXTURES))
    text = plan_json(plan)
    assert text.isascii() and "‮" not in text
    data = json.loads(text)
    assert data["totals"]["dashboards"] == 5
    assert data["dashboards"][0]["panels"][0]["reasons"] is not None
    assert "variables" not in data["dashboards"][0]["panels"][0]  # the working set is not saved


def test_a_huge_estate_gives_a_bounded_report() -> None:
    panels = [
        {
            "id": i,
            "type": "gauge",
            "title": "t" * 300,
            "targets": [{"refId": "A", "expr": "up == 1"}],
        }
        for i in range(m.MAX_PANELS)
    ]
    dashboards = [{"uid": f"u{i}", "title": "d" * 300, "panels": panels[:50]} for i in range(400)]
    report = build_report(build_plan(load_data(dashboards, "many")))
    assert len(report) < 1_500_000
    assert "more in plan.json" in report


def test_the_command_writes_a_report_and_json_and_prints_the_numbers(tmp_path: Path) -> None:
    out = tmp_path / "plan"
    result = run("--in", str(FIXTURES), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert (out / "report.md").read_text().startswith("# Grafana dashboards")
    assert json.loads((out / "plan.json").read_text())["totals"]["dashboards"] == 5
    assert "5 dashboards" in result.output and "T2" in result.output
    assert sorted(p.name for p in out.iterdir()) == ["plan.json", "report.md"]


def test_the_command_says_which_files_could_not_be_read(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "good.json").write_text(json.dumps({"uid": "g", "title": "g", "panels": []}))
    (src / "bad.json").write_text("{not json")
    result = run("--in", str(src), "--out", str(tmp_path / "o"))
    assert result.exit_code == 0 and "1 could not be read" in result.output
    assert "bad.json" in (tmp_path / "o" / "report.md").read_text()


@pytest.mark.parametrize("kind", ["missing", "empty", "no-dashboards"])
def test_the_command_refuses_input_with_no_dashboards(tmp_path: Path, kind: str) -> None:
    src = tmp_path / "in"
    if kind != "missing":
        src.mkdir()
    if kind == "no-dashboards":
        (src / "x.json").write_text(json.dumps({"hello": "world"}))
    result = run("--in", str(src), "--out", str(tmp_path / "o"))
    assert result.exit_code == 1 and "Error" in result.output
    assert not (tmp_path / "o").exists()


def test_the_documented_sequence_extract_then_plan_works_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dash = json.loads((FIXTURES / "easy-node-overview.json").read_text())

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/search":
            return httpx.Response(200, json=[{"uid": "easy-node-overview", "type": "dash-db"}])
        if path.startswith("/api/dashboards/uid/"):
            return httpx.Response(200, json={"dashboard": dash, "meta": {"folderTitle": "Ops"}})
        if path == "/api/folders":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"result": {"totalCount": 0, "elements": []}})

    monkeypatch.setenv("GRAFANA_URL", HOST)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    monkeypatch.setattr(
        "importer.commands.grafana_dashboards.http_getter",
        lambda t, b, allow_host=None: http_getter(t, b, httpx.MockTransport(handler), allow_host),
    )
    out = tmp_path / "export"
    extracted = CliRunner().invoke(app, ["extract-grafana-dashboards", "--out", str(out)])
    assert extracted.exit_code == 0, extracted.output
    next_step = next(x for x in extracted.output.splitlines() if "plan-grafana-dashboards" in x)
    args = next_step.split()[2:]  # the printed command, run as printed
    planned = CliRunner().invoke(app, ["plan-grafana-dashboards", *args])
    assert planned.exit_code == 0, planned.output
    report = (Path(f"{out}-plan") / "report.md").read_text()
    assert "Node overview (easy)" in report and "Ops" not in report.split("## Dashboards")[0]
    assert TOKEN not in report and TOKEN not in planned.output
