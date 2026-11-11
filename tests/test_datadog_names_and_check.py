"""D3: the name map, the scaffold of unmapped metrics and the read-only data check."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.data_check import Status
from importer.core.ir import IRMonitor
from importer.sources.datadog import data_check as dc
from importer.sources.datadog.models import DatadogMonitor, load_monitors
from importer.sources.datadog.names import (
    NameMapError,
    find_name_map,
    load_name_map,
    scaffold,
)
from importer.sources.datadog.translate import translate_monitor

FIX = Path(__file__).parent / "fixtures" / "datadog"


class FakeNR:
    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.queries: list[str] = []
        self.writes = 0

    def run(self, query: str, variables: dict[str, Any], is_write: bool = False) -> dict[str, Any]:
        self.writes += is_write
        nrql = str(variables["n"])
        self.queries.append(nrql)
        for needle, rows in self.answers.items():
            if needle in nrql:
                return {"actor": {"account": {"nrql": {"results": rows}}}}
        return {"actor": {"account": {"nrql": {"results": []}}}}


def conv(query: str, type_: str = "query alert") -> IRMonitor:
    return translate_monitor(DatadogMonitor(type=type_, query=query, name="m", source_key="k"))


def write(tmp: Path, data: object, name: str = "name-map.json") -> Path:
    path = tmp / name
    path.write_text(json.dumps(data))
    return path


def test_map_is_read_strictly(tmp_path: Path) -> None:
    ok = write(tmp_path, {"metrics": {"a.b": {"attribute": "a.b.c", "kind": "count"}}})
    assert load_name_map(ok).metrics["a.b"].kind == "count"
    for bad in (
        {"metrics": {"a": {"attribute": "x y"}}},
        {"metrics": {"a": {"attribute": "x", "unit_scale": 0}}},
        {"metrics": {"a": {"attribute": "x", "kind": "sum"}}},
        {"metrics": {"a": {"attribute": "x", "extra": 1}}},
        {"attributes": {"env": "a b"}},
        {"unknown": 1},
    ):
        with pytest.raises(NameMapError):
            load_name_map(write(tmp_path, bad, "bad.json"))
    (tmp_path / "broken.json").write_text("{")
    with pytest.raises(NameMapError, match="not valid JSON"):
        load_name_map(tmp_path / "broken.json")


def test_map_beside_the_export_is_picked_up_by_the_loader(tmp_path: Path) -> None:
    write(tmp_path, {"metrics": {"m.x": {"attribute": "mapped.x"}}})
    write(tmp_path, {"type": "query alert", "query": "avg(last_5m):avg:m.x{*} > 1"}, "m.json")
    (monitor,) = load_monitors(tmp_path)
    assert translate_monitor(monitor).signal.metric.terms["a"].nr_target.attribute == "mapped.x"  # type: ignore[union-attr]
    assert find_name_map(tmp_path / "m.json").metrics  # a file path looks in its folder too


def test_map_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path, {"attributes": {"env": "deployment.environment.name"}}, "elsewhere.json")
    monkeypatch.setenv("IMPORTER_DATADOG_NAME_MAP", str(path))
    assert find_name_map(tmp_path).attributes == {"env": "deployment.environment.name"}


def test_scaffold_lists_unmapped_metrics_and_tags() -> None:
    result = scaffold(load_monitors(FIX / "real" / "monitors"))
    assert "appgate.apn_status" in result.metrics
    assert "collective_name" in result.tags
    assert "cisco_aci.code" in result.tags  # a log facet, without the @
    skeleton: Any = result.skeleton
    entry = skeleton["metrics"]["appgate.apn_status"]
    assert entry == {"event": "Metric", "attribute": "appgate.apn_status", "unit_scale": 1.0}


def test_scaffold_skips_what_the_map_covers(tmp_path: Path) -> None:
    write(tmp_path, {"metrics": {"m.x": {"attribute": "z"}}, "attributes": {"host": "host.name"}})
    write(
        tmp_path,
        {"type": "query alert", "query": "avg(last_5m):avg:m.x{*} by {host} > 1"},
        "a.json",
    )
    result = scaffold(load_monitors(tmp_path))
    assert result.metrics == [] and result.tags == []
    assert result.skeleton["metrics"]["m.x"]["attribute"] == "z"  # type: ignore[index]


def test_scaffold_command_writes_a_usable_map_and_refuses_to_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "name-map.json"
    res = CliRunner().invoke(
        app, ["scaffold-datadog-names", "--in", str(FIX / "real" / "monitors"), "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    assert load_name_map(out).metrics
    again = CliRunner().invoke(app, ["scaffold-datadog-names", "--in", str(FIX), "--out", str(out)])
    assert again.exit_code == 1 and "exists" in again.output


def test_metric_with_data_is_confirmed_and_only_selects_are_sent() -> None:
    nr = FakeNR({"IS NOT NULL": [{"count": 7}], "metricName = 'm.x'": [{"count": 120}]})
    m = conv("avg(last_5m):avg:m.x{env:prod} by {host} > 1")
    (check,) = dc.check_all(nr, 1, [m], 24)  # type: ignore[arg-type]
    assert check.status is Status.DATA and check.points == 120
    assert all(q.startswith("SELECT") for q in nr.queries) and nr.writes == 0
    assert "SINCE 24 hours ago" in nr.queries[0]
    assert "SELECT count(*) FROM Metric WHERE metricName = 'm.x' SINCE 24 hours ago" in nr.queries


def test_missing_metric_offers_candidates() -> None:
    nr = FakeNR({"uniques(metricName": [{"uniques.metricName": ["system.cpu.usage"]}]})
    (check,) = dc.check_all(nr, 1, [conv("avg(last_5m):avg:system.cpu.user{*} > 1")], 6)  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "system.cpu.usage" in check.note
    assert "name-map.json" in check.note
    assert any("LIKE 'system.cpu%'" in q for q in nr.queries)


def test_missing_tag_attribute_is_reported() -> None:
    nr = FakeNR({"IS NOT NULL": [{"count": 0}], "metricName = 'm.x'": [{"count": 5}]})
    (check,) = dc.check_all(nr, 1, [conv("avg(last_5m):avg:m.x{*} by {host} > 1")], 24)  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "host" in check.note


def test_log_check() -> None:
    q = 'logs("@a:b").index("*").rollup("count").last("5m") > 1'
    none = FakeNR({})
    (c1,) = dc.check_all(none, 1, [conv(q, "log alert")], 24)  # type: ignore[arg-type]
    assert c1.status is Status.NO_DATA
    some = FakeNR({"FROM Log SINCE": [{"count": 9}], "IS NOT NULL": [{"count": 3}]})
    (c2,) = dc.check_all(some, 1, [conv(q, "log alert")], 24)  # type: ignore[arg-type]
    assert c2.status is Status.DATA


def test_manual_monitors_are_not_checked_and_report_is_branded() -> None:
    nr = FakeNR({})
    (check,) = dc.check_all(nr, 1, [conv("1 && 2", "composite")], 24)  # type: ignore[arg-type]
    assert check.status is Status.NOT_CHECKED and nr.queries == []
    text = dc.render_report([check], 24)
    assert text.startswith("# Data check: Datadog to New Relic")
    assert dc.has_blind_conditions([check]) is False


def test_check_command_needs_an_account_id() -> None:
    res = CliRunner().invoke(app, ["check-datadog-data", "--alerts", "nowhere"])
    assert res.exit_code == 1 and "nr-account-id" in res.output
