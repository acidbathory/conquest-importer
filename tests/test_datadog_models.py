"""D0: the Datadog loader reads every shape, keeps unknown fields and never crashes."""

import json
from pathlib import Path

import pytest

from importer.sources.datadog.models import (
    MAX_MONITORS,
    DatadogMonitor,
    load_monitors,
)

FIXTURES = Path(__file__).parent / "fixtures" / "datadog"


def _write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_all_real_monitors_load() -> None:
    monitors = load_monitors(FIXTURES / "real" / "monitors")
    assert len(monitors) == 70
    assert all(m.query_text for m in monitors)


def test_synthetic_monitors_load_without_crashing_and_dashboards_are_skipped() -> None:
    monitors = load_monitors(FIXTURES / "synthetic")
    names = {m.name_text for m in monitors}
    assert any("secrets in body" in n for n in names)
    assert len(monitors) == 14  # 17 synthetic files, 3 of them dashboards


def test_template_wrapper_is_unwrapped_and_keeps_the_title(tmp_path: Path) -> None:
    wrapper = {
        "version": 2,
        "title": "Wrapper title",
        "definition": {"type": "query alert", "query": "avg(last_5m):avg:x{*} > 1"},
    }
    (monitor,) = load_monitors(_write(tmp_path, "w.json", wrapper))
    assert monitor.query_text.startswith("avg(last_5m)")
    assert monitor.name_text == "Wrapper title"
    assert monitor.source_key.endswith("w")


def test_api_list_and_monitors_key_shapes(tmp_path: Path) -> None:
    one = {"id": 7, "name": "a", "type": "query alert", "query": "q"}
    assert [m.id for m in load_monitors(_write(tmp_path, "l.json", [one]))] == [7]
    assert [m.id for m in load_monitors(_write(tmp_path, "m.json", {"monitors": [one]}))] == [7]


def test_unknown_fields_and_options_are_kept(tmp_path: Path) -> None:
    raw = {"type": "query alert", "query": "q", "future": 1, "options": {"avalanche_window": 10}}
    (monitor,) = load_monitors(_write(tmp_path, "u.json", raw))
    assert (monitor.model_extra or {})["future"] == 1
    assert monitor.options_dict["avalanche_window"] == 10


def test_wrong_types_give_safe_accessors(tmp_path: Path) -> None:
    raw = {
        "id": "x",
        "name": 12,
        "type": ["a"],
        "query": {"q": 1},
        "tags": "env:prod",
        "options": 3,
    }
    (monitor,) = load_monitors(_write(tmp_path, "t.json", raw))
    assert monitor.type_text == ""
    assert monitor.query_text == ""
    assert monitor.tag_list == []
    assert monitor.options_dict == {}
    assert monitor.priority_level is None


def test_priority_levels() -> None:
    assert DatadogMonitor(priority=3).priority_level == 3
    assert DatadogMonitor(priority=True).priority_level is None
    assert DatadogMonitor(priority=9).priority_level is None
    assert DatadogMonitor(priority="P1").priority_level is None


def test_deeply_nested_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text("[" * 5000 + "]" * 5000)
    with pytest.raises(ValueError, match="nested too deeply"):
        load_monitors(path)


def test_big_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importer.sources.datadog.models.MAX_INPUT_BYTES", 10)
    path = _write(tmp_path, "b.json", {"type": "query alert", "query": "q" * 50})
    with pytest.raises(ValueError, match="larger than"):
        load_monitors(path)


def test_too_many_monitors_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importer.sources.datadog.models.MAX_MONITORS", 2)
    path = _write(tmp_path, "many.json", [{"type": "x", "query": "q"}] * 3)
    with pytest.raises(ValueError, match="more than 2 monitors"):
        load_monitors(path)
    assert MAX_MONITORS >= 1000


def test_keys_are_unique_and_stable(tmp_path: Path) -> None:
    raw = [{"type": "t", "query": "q", "name": "a"}, {"type": "t", "query": "q", "name": "a"}]
    path = _write(tmp_path, "d.json", raw)
    keys = [m.source_key for m in load_monitors(path)]
    assert len(set(keys)) == 2
    assert keys == [m.source_key for m in load_monitors(path)]


def test_name_map_file_is_not_read_as_a_monitor(tmp_path: Path) -> None:
    _write(tmp_path, "name-map.json", {"metrics": {}})
    _write(tmp_path, "m.json", {"type": "query alert", "query": "q"})
    assert len(load_monitors(tmp_path)) == 1
