"""The data path guide: files with placeholders, naming consequences, and the check queries."""

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.datapath import plan as dp
from importer.sources.grafana.notifications import reasons
from importer.sources.grafana.notifications.models import DataSource

FILES = {
    "prometheus-remote-write.yml",
    "alloy-dual-write.alloy",
    "alloy-otlp.alloy",
    "naming-consequences.md",
    "check-queries.md",
}
PLAN_FILES = {"datapath-plan.md", "datapath-plan.json", "otel-collector.yml"}
REAL_KEY = "0123456789abcdef0123456789abcdef01234567NRAL"  # looks like a license key
SECRET = "SECRET-VALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests


def build(region: str = "us", **kwargs: Any) -> dict[str, str]:
    return dp.build_files(region, **kwargs)


def test_the_guide_has_the_five_files() -> None:
    assert set(build()) == FILES


@pytest.mark.parametrize(
    ("region", "write", "otlp"),
    [
        ("us", "https://metric-api.newrelic.com/prometheus/v1/write", "https://otlp.nr-data.net"),
        (
            "eu",
            "https://metric-api.eu.newrelic.com/prometheus/v1/write",
            "https://otlp.eu01.nr-data.net",
        ),
        (
            "jp",
            "https://metric-api.jp.nr-data.net/prometheus/v1/write",
            "https://otlp.jp.nr-data.net",
        ),
    ],
)
def test_the_endpoints_follow_the_region(region: str, write: str, otlp: str) -> None:
    files = build(region)
    assert (
        write in files["prometheus-remote-write.yml"] and write in files["alloy-dual-write.alloy"]
    )
    assert otlp in files["alloy-otlp.alloy"]
    others = {
        "https://metric-api.newrelic.com",
        "https://metric-api.eu.newrelic.com",
        "https://metric-api.jp.nr-data.net",
    }
    others -= {write.rsplit("/prometheus", 1)[0]}
    assert not any(o in text for o in others for text in files.values())


def test_the_prometheus_server_name_is_in_the_url_and_validated() -> None:
    assert (
        "prometheus_server=prod-1"
        in build(prometheus_server="prod-1")["prometheus-remote-write.yml"]
    )
    for bad in ("a b", "x;y", "a&b=c", "", "x" * 80, "é", "a\nb"):
        with pytest.raises(ValueError, match="prometheus server"):
            build(prometheus_server=bad)


def test_an_unknown_region_is_refused() -> None:
    with pytest.raises(ValueError, match="us, eu or jp"):
        build("mars")


def test_the_license_key_is_a_placeholder_and_never_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("NEW_RELIC_LICENSE_KEY", "NEW_RELIC_API_KEY", "INGEST_LICENSE_KEY"):
        monkeypatch.setenv(name, REAL_KEY)
    files = build()
    assert all(REAL_KEY not in text for text in files.values())
    assert "REPLACE_WITH_YOUR_NEW_RELIC_LICENSE_KEY" in files["prometheus-remote-write.yml"]
    # the Alloy files name the variable the key goes in; they never carry a key
    assert 'sys.env("NEW_RELIC_LICENSE_KEY")' in files["alloy-dual-write.alloy"]
    assert not any(re.search(r"[0-9a-f]{32,}", text) for text in files.values())


def test_the_files_state_what_was_not_verified() -> None:
    text = build()["naming-consequences.md"]
    for item in ("counter", "newrelic_metric_type", "prometheus_server", "UNVERIFIED", "48 hours"):
        assert item in text


def test_naming_consequences_cover_the_documented_rules() -> None:
    text = build()["naming-consequences.md"]
    for item in ("_bucket", "_total", "_sum", "le", "job", "instance", "colon", "delta"):
        assert item in text


def test_the_check_queries_are_listed_for_each_target() -> None:
    files = build(metrics=["queue_depth"], log_attributes=["app"])
    text = files["check-queries.md"]
    assert "SELECT count(*) FROM Metric WHERE metricName = 'queue_depth' SINCE 24 hours ago" in text
    assert "SELECT keyset() FROM Metric WHERE metricName = 'queue_depth'" in text
    assert "SELECT uniques(prometheus_server) FROM Metric WHERE metricName = 'queue_depth'" in text
    assert "FROM Log" in text and "app" in text


def test_without_targets_the_queries_are_templates() -> None:
    text = build()["check-queries.md"]
    assert "<metric name>" in text and "No converted conditions were given" in text


@pytest.mark.parametrize(
    "name", ["bad name", "x'; DROP", "a`b", "q\nr", "é", "", "1abc", "a" * 300, "a-b"]
)
def test_a_name_that_is_not_a_valid_metric_or_attribute_never_reaches_a_query(name: str) -> None:
    text = build(metrics=[name], log_attributes=[name])["check-queries.md"]
    assert name not in text or name == ""
    assert "not valid" in text


def test_metric_queries_come_from_one_builder() -> None:
    assert dp.metric_queries("a_b:c", 6) == [
        "SELECT count(*) FROM Metric WHERE metricName = 'a_b:c' SINCE 6 hours ago",
        "SELECT keyset() FROM Metric WHERE metricName = 'a_b:c' SINCE 6 hours ago",
        "SELECT uniques(prometheus_server) FROM Metric "
        "WHERE metricName = 'a_b:c' SINCE 6 hours ago",
    ]
    with pytest.raises(ValueError, match="metric name"):
        dp.metric_queries("bad name", 6)


def test_the_hours_are_bounded() -> None:
    for hours in (0, -1, 169):
        with pytest.raises(ValueError, match="hours"):
            dp.metric_queries("a", hours)


def test_data_sources_show_their_path_by_type() -> None:
    sources = [
        DataSource(type="prometheus", uid="p"),
        DataSource(type="loki", uid="l"),
        DataSource(type="tempo", uid="t"),
        DataSource(type="grafana-azure-monitor-datasource", uid="z"),
    ]
    paths = {p.type: p for p in dp.data_paths(sources)}
    assert "remote write" in paths["prometheus"].path
    assert "OTLP" in paths["loki"].path or "Log API" in paths["loki"].path
    assert "OTLP" in paths["tempo"].path
    assert paths["grafana-azure-monitor-datasource"].path == ""
    assert paths["grafana-azure-monitor-datasource"].reason == reasons.R_DATASOURCE_PATH


def test_the_data_source_section_lists_counts_by_type_only() -> None:
    sources = [DataSource(type="prometheus", uid="p1"), DataSource(type="prometheus", uid="p2")]
    text = build(datasources=sources)["naming-consequences.md"]
    assert "prometheus: 2" in text and "p1" not in text


# --- the command ---


def run(tmp_path: Path, *extra: str) -> Any:
    return CliRunner().invoke(app, ["plan-grafana-datapath", "--out", str(tmp_path / "o"), *extra])


def test_the_command_writes_the_files_and_prints_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", REAL_KEY)
    result = run(tmp_path, "--region", "eu")
    assert result.exit_code == 0, result.output
    assert {p.name for p in (tmp_path / "o").iterdir()} == FILES | PLAN_FILES
    written = "".join(p.read_text() for p in (tmp_path / "o").iterdir())
    assert REAL_KEY not in written + result.output
    assert "metric-api.eu.newrelic.com" in written


def test_the_command_refuses_a_bad_region_and_a_used_folder(tmp_path: Path) -> None:
    assert run(tmp_path, "--region", "mars").exit_code == 1
    ok = run(tmp_path)
    assert ok.exit_code == 0
    again = run(tmp_path)
    assert again.exit_code == 1 and "already has files" in again.output


def test_the_command_reads_data_sources_and_the_converted_conditions(tmp_path: Path) -> None:
    export = tmp_path / "export"
    export.mkdir()
    (export / "datasources.json").write_text(
        json.dumps([{"type": "prometheus", "uid": "p1", "name": SECRET}])
    )
    alerts = tmp_path / "alerts"
    (alerts / "ir").mkdir(parents=True)
    from importer.sources import REGISTRY

    for i, m in enumerate(
        REGISTRY["grafana"].convert(Path(__file__).parent / "fixtures" / "grafana")
    ):
        (alerts / "ir" / f"{i}.json").write_text(m.model_dump_json())
    result = run(tmp_path, "--export", str(export), "--alerts", str(alerts))
    assert result.exit_code == 0, result.output
    queries = (tmp_path / "o" / "check-queries.md").read_text()
    assert "metricName = 'queue_depth'" in queries and "metricName = 'node_cpu_busy'" in queries
    assert SECRET not in queries + result.output


def test_a_bad_prometheus_server_name_is_a_clean_error(tmp_path: Path) -> None:
    result = run(tmp_path, "--prometheus-server", "a b")
    assert result.exit_code == 1 and "Traceback" not in result.output
