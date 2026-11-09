"""The data path plan: steps, families, snippets with no secret, verdict tables."""

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from importer.cli import app
from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import IRMonitor
from importer.sources import REGISTRY
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.translate import convert_all, load_items
from importer.sources.grafana.datapath import guide, needs
from importer.sources.grafana.datapath import plan as dp
from importer.sources.grafana.notifications.models import DataSource

FIX = Path(__file__).parent / "fixtures"


def rules() -> list[IRMonitor]:
    return REGISTRY["grafana"].convert(FIX / "grafana")


def tiles() -> list[IRDashboard]:
    return convert_all(load_items(FIX / "grafana-dashboard-convert"), NameMap())[0]


REAL_KEY = "0123456789abcdef0123456789abcdef01234567NRAL"


def files(region: str = "us", sources: list[DataSource] | None = None) -> dict[str, str]:
    items = needs.rule_items(rules()) + needs.tile_items(tiles())
    return guide.plan_files(
        region, "prom-1", items, needs.judge(items, sources or []), sources or []
    )


def test_the_plan_adds_three_files() -> None:
    assert set(files()) == {"datapath-plan.md", "datapath-plan.json", "otel-collector.yml"}


def test_the_plan_names_the_data_source_the_families_and_the_attribute_mapping() -> None:
    text = files()["datapath-plan.md"]
    for item in (
        "Step 1",
        "Step 2",
        "NEW_RELIC_LICENSE_KEY",
        "https://metric-api.newrelic.com/prometheus/v1/write",
        "| http_requests_total | count |",
        "| node_load1 | gauge |",
        "delta",
        "metricName",
        "metric_name",
        "prometheus_server",
        "kube-state-metrics",
    ):
        assert item in text, item


def test_every_rule_and_tile_has_a_verdict_row() -> None:
    text = files()["datapath-plan.md"]
    data = json.loads(files()["datapath-plan.json"])
    assert len(data["verdicts"]) == len(rules()) + len(needs.tile_items(tiles()))
    assert "will have data after step 1 and 2" in text and "not converted" in text
    assert data["coverage"]["tiles"]["known"] > 0


def test_snippets_are_commented_and_carry_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", REAL_KEY)
    out = files("eu")
    otel = out["otel-collector.yml"]
    assert otel.lstrip().startswith("#") and "${env:NEW_RELIC_LICENSE_KEY}" in otel
    assert "https://otlp.eu01.nr-data.net" in otel and "api-key" in otel
    assert all(REAL_KEY not in t for t in out.values())
    assert not any(re.search(r"[0-9a-f]{32,}", t) for t in out.values())


def test_a_datasource_without_a_path_is_listed_as_manual() -> None:
    sources = [DataSource(type="prometheus", uid="a"), DataSource(type="mysql", uid="b")]
    text = files(sources=sources)["datapath-plan.md"]
    assert "mysql" in text and "no New Relic data path" in text
    many = [DataSource(type="prometheus", uid="a"), DataSource(type="prometheus", uid="b")]
    assert "distinct prometheus_server" in files(sources=many)["datapath-plan.md"]


def test_hostile_names_never_reach_the_plan_unescaped() -> None:
    bad = needs.Item("rule", "x|y\n## owned", True, "prometheus", ("ok_metric",), ("job",))
    out = guide.plan_files("us", "p", [bad], needs.judge([bad], []), [])
    assert "\n## owned" not in out["datapath-plan.md"]


def test_the_remote_write_and_alloy_snippets_keep_only_the_used_metrics() -> None:
    built = dp.build_files("us", metrics=["up", "job:lat:mean5m"])
    yml = built["prometheus-remote-write.yml"]
    assert '#     regex: "up|job:lat:mean5m"' in yml
    assert "metric_name" in yml and "metricName" in yml


# --- the command ---


def _stage(tmp_path: Path) -> tuple[Path, Path]:
    alerts, dash = tmp_path / "alerts", tmp_path / "dash"
    (alerts / "ir").mkdir(parents=True)
    (dash / "dashboard-ir").mkdir(parents=True)
    for i, m in enumerate(rules()):
        (alerts / "ir" / f"{i}.json").write_text(m.model_dump_json())
    for i, d in enumerate(tiles()):
        (dash / "dashboard-ir" / f"{i}.json").write_text(d.model_dump_json())
    (alerts / "report.md").write_text("# alerts\n")
    (dash / "report.md").write_text("# dash\n\n## Grafana data prerequisites\n\nx\n")
    return alerts, dash


def _run(tmp_path: Path, *extra: str) -> Result:
    return CliRunner().invoke(app, ["plan-grafana-datapath", "--out", str(tmp_path / "o"), *extra])


def test_the_command_writes_the_plan_and_the_snippets_hold_the_used_metrics(
    tmp_path: Path,
) -> None:
    alerts, dash = _stage(tmp_path)
    result = _run(tmp_path, "--alerts", str(alerts), "--dashboards", str(dash))
    assert result.exit_code == 0, result.output
    out = tmp_path / "o"
    assert (out / "datapath-plan.md").exists() and (out / "otel-collector.yml").exists()
    assert "node_cpu_seconds_total" in (out / "prometheus-remote-write.yml").read_text()
    assert "converted with a known data path" in result.output


def test_update_report_adds_one_coverage_section_to_each_report(tmp_path: Path) -> None:
    alerts, dash = _stage(tmp_path)
    args = ("--alerts", str(alerts), "--dashboards", str(dash), "--update-report")
    result = _run(tmp_path, *args)
    assert result.exit_code == 0, result.output
    a, d = (alerts / "report.md").read_text(), (dash / "report.md").read_text()
    assert a.count(needs.SECTION_HEADING) == 1 and "Rules:" in a
    assert d.count(needs.SECTION_HEADING) == 1 and "Tiles:" in d
    assert d.index("## Grafana data prerequisites") < d.index(needs.SECTION_HEADING)


def test_update_report_without_a_report_is_a_clean_error(tmp_path: Path) -> None:
    _, dash = _stage(tmp_path)
    (dash / "report.md").unlink()
    result = _run(tmp_path, "--dashboards", str(dash), "--update-report")
    assert result.exit_code == 1 and "report.md" in result.output
