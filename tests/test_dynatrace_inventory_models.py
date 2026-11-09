"""Reading synthetic monitors, locations, entities and SLO lists."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.dynatrace.inventory import models as m

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"


def test_monitors_are_read_and_bad_entries_counted() -> None:
    loaded = m.load_monitors(FIXTURES)
    assert len(loaded.items) == 12 and loaded.skipped == 2
    first = loaded.items[0]
    assert (first.type, first.frequency_min, len(first.locations)) == ("HTTP", 5, 2)
    assert first.outage_handling and first.loading_thresholds
    assert first.script["requests"][0]["method"] == "GET"
    assert [x.enabled for x in loaded.items].count(False) == 1
    assert loaded.items[0].ref == "monitors.json[0]"


def test_locations_are_keyed_by_entity_id() -> None:
    loaded = m.load_locations(FIXTURES)
    by_id = {x.id: x for x in loaded.items}
    assert by_id["GEOLOCATION-0000000000000002"].city == "Frankfurt"
    assert by_id["SYNTHETIC_LOCATION-0000000000000009"].kind == "PRIVATE"


def test_entities_keep_type_technology_and_os_only() -> None:
    loaded = m.load_entities(FIXTURES)
    assert len(loaded.items) == 23 and loaded.skipped == 1 and loaded.reported_total == 24
    billing = next(e for e in loaded.items if "ASP.NET" in e.technologies)
    assert billing.technologies == ["IIS app pool", "ASP.NET", "DotNet"]
    assert {e.os for e in loaded.items if e.type == "HOST"} == {"LINUX", "WINDOWS", "AIX"}
    assert "displayName" not in m.Entity.model_fields  # names are not kept


def test_slos_are_classed_by_shape() -> None:
    loaded = m.load_slos(FIXTURES)
    assert [(s.kind, s.timeframe) for s in loaded.items] == [
        ("custom", "now-7d"),
        ("custom", "now-30d"),
        ("template", ""),
        ("classic", "-1w"),
    ]
    assert loaded.skipped == 1


@pytest.mark.parametrize("raw", ["[1, 2", "[" * 100_000, '{"a":' * 80 + "1" + "}" * 80])
def test_a_hostile_file_is_skipped_not_fatal(raw: str, tmp_path: Path) -> None:
    (tmp_path / "monitors.json").write_text(raw)
    loaded = m.load_monitors(tmp_path)
    assert loaded.items == [] and loaded.skipped == 1


def test_an_oversized_file_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "entities.json").write_text(json.dumps([{"type": "HOST"}]))
    monkeypatch.setattr(m, "MAX_INPUT_BYTES", 5)
    assert m.load_entities(tmp_path).skipped == 1


def test_more_items_than_the_cap_stop_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "MAX_ENTITIES", 3)
    (tmp_path / "entities.json").write_text(json.dumps([{"type": "HOST"}] * 4))
    with pytest.raises(ValueError, match="more than 3"):
        m.load_entities(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [{"type": 5}, {"type": ["HOST"]}, None, 7, {"properties": {}}],
)
def test_entity_objects_without_a_text_type_are_skipped(raw: Any) -> None:
    assert m.parse_entity(raw) is None


def test_long_text_and_long_lists_are_cut() -> None:
    entity = m.parse_entity(
        {"type": "SERVICE", "properties": {"serviceTechnologyTypes": ["x" * 999] * 100}}
    )
    assert entity is not None
    assert len(entity.technologies) == m.MAX_LIST_ITEMS and len(entity.technologies[0]) == 200
