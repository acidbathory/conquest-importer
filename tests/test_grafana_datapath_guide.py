"""The data path plan: steps, families, snippets with no secret, verdict tables."""

import json
import re
from pathlib import Path

import pytest

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
