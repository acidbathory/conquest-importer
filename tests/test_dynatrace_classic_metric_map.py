"""The metric map: Dynatrace metric keys to New Relic signals, with an honest status per row."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core.emit.nrql import identifier
from importer.sources.dynatrace.classic import metric_map
from importer.sources.dynatrace.classic.metric_map import (
    MapError,
    MetricMap,
    load_metric_map,
    lookup,
    prerequisite,
)

ROW: dict[str, Any] = {
    "keys": ["a.b"],
    "event": "Sample",
    "attribute": "val",
    "unit_factor": 1,
    "dimensions": {"dt.entity.host": "hostname"},
    "status": "unverified",
    "evidence": "test",
}


def write(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"prerequisites": [], "default_prerequisite": "d", "metrics": rows}))
    return path


def test_the_shipped_map_loads_and_every_row_is_marked_unverified_at_first() -> None:
    shipped = load_metric_map()
    assert len(shipped.metrics) >= 7
    assert {row.status for row in shipped.metrics} == {"unverified"}


def test_every_shipped_name_is_a_valid_nrql_identifier() -> None:
    for row in load_metric_map().metrics:
        identifier(row.event)
        if row.attribute:
            identifier(row.attribute)
        for attribute in row.dimensions.values():
            identifier(attribute)


def test_a_key_is_found_under_any_of_its_names() -> None:
    cpu = lookup("builtin:host.cpu.usage")
    assert cpu is not None and cpu.attribute == "cpuPercent" and cpu.event == "SystemSample"
    assert lookup("dt.host.cpu.usage") is cpu
    assert lookup("builtin:nothing.here") is None


def test_the_response_time_row_converts_microseconds_to_seconds() -> None:
    row = lookup("builtin:service.response.time")
    assert row is not None and row.unit_factor == pytest.approx(1e-6)


def test_a_select_row_has_no_attribute() -> None:
    row = lookup("builtin:service.requestCount.server")
    assert row is not None and row.select == "count(*)" and row.attribute is None


@pytest.mark.parametrize(
    "bad",
    [
        {"event": "bad event"},
        {"attribute": "a;b"},
        {"status": "great"},
        {"unit_factor": 0},
        {"unit_factor": float("inf")},
        {"unit_factor": -1},
        {"dimensions": {"dt.entity.host": "no good"}},
        {"attribute": None, "select": None},
        {"attribute": "x", "select": "count(*)"},
        {"keys": []},
        {"keys": [""]},
    ],
)
def test_a_bad_row_is_an_error_naming_the_file_not_a_crash(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    with pytest.raises(MapError):
        load_metric_map(write(tmp_path, [{**ROW, **bad}]))


def test_a_key_in_two_rows_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(MapError, match="more than one row"):
        load_metric_map(write(tmp_path, [ROW, {**ROW, "attribute": "other"}]))


def test_a_file_that_is_not_json_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text("{")
    with pytest.raises(MapError, match="not valid JSON"):
        load_metric_map(path)


def test_a_custom_map_replaces_the_shipped_one(tmp_path: Path) -> None:
    custom = load_metric_map(write(tmp_path, [ROW]))
    assert isinstance(custom, MetricMap)
    assert custom.find("a.b") is not None and custom.find("builtin:host.cpu.usage") is None


@pytest.mark.parametrize(
    ("key", "words"),
    [
        ("builtin:host.disk.free", "infrastructure agent"),
        ("dt.host.disk.free", "infrastructure agent"),
        ("builtin:service.keyRequest.count", "APM agent"),
        ("builtin:cloud.aws.ec2.cpu", "cloud integration"),
        ("builtin:kubernetes.node.cpu", "Kubernetes"),
        ("calc:service.mine", "calculated"),
        ("my.custom.metric", "OpenTelemetry"),
        ("builtin:something.unknown", "OneAgent"),
    ],
)
def test_the_prerequisite_names_what_must_be_installed(key: str, words: str) -> None:
    assert words.lower() in prerequisite(key).lower()


def test_the_longest_prefix_wins() -> None:
    entries = metric_map.load_metric_map().prerequisites
    assert [len(p.prefix) for p in entries] == sorted(
        (len(p.prefix) for p in entries), reverse=True
    )
