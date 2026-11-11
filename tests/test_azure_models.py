"""Azure export loader: official example wrappers, list pages, hostile shapes, name map."""

import json
from pathlib import Path

import pytest

from importer.sources.azure.models import (
    AzureAlert,
    iso_seconds,
    load_alerts,
)

FIX = Path(__file__).parent / "fixtures" / "azure"


def _write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def _rule(name: str = "r", kind: str = "Microsoft.Insights/metricAlerts") -> dict[str, object]:
    return {
        "id": f"/subscriptions/s/resourceGroups/g/providers/x/{name}",
        "name": name,
        "type": kind,
        "properties": {"severity": 2},
    }


def test_loads_a_list_page_and_a_single_resource(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", {"value": [_rule("a"), _rule("b")], "nextLink": "https://x"})
    _write(tmp_path, "b.json", _rule("c"))
    names = sorted(a.name for a in load_alerts(tmp_path))
    assert names == ["a", "b", "c"]


def test_loads_the_official_example_wrapper_from_the_response_not_the_request() -> None:
    found = load_alerts(FIX / "metric_alerts" / "2026-01-01__getMetricAlertSingleResource.json")
    assert len(found) == 1 and found[0].family == "metricAlerts"


def test_status_resources_and_other_types_are_not_alerts(tmp_path: Path) -> None:
    status = _rule("s", "Microsoft.Insights/metricAlerts/status")
    group = _rule("g", "Microsoft.Insights/actionGroups")
    _write(tmp_path, "a.json", {"value": [status, group, _rule("ok")]})
    assert [a.name for a in load_alerts(tmp_path)] == ["ok"]


def test_identical_copies_are_read_once(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _rule("a"))
    _write(tmp_path, "b.json", _rule("a"))
    assert len(load_alerts(tmp_path)) == 1


def test_a_missing_name_comes_from_the_id() -> None:
    alert = AzureAlert.model_validate({"id": "/a/b/metricAlerts/cpu-high", "type": "x"})
    assert alert.name == "cpu-high"


def test_unknown_and_wrong_typed_fields_do_not_fail() -> None:
    alert = AzureAlert.model_validate(
        {"name": 5, "type": None, "properties": "text", "tags": [1], "zzz": {"a": 1}}
    )
    assert alert.properties == {} and alert.tags == {}


def test_family_is_found_from_the_folder_or_the_shape_when_type_is_missing(tmp_path: Path) -> None:
    folder = tmp_path / "metric_alerts"
    folder.mkdir()
    _write(folder, "x.json", {"value": [{"name": "n", "properties": {"severity": 1}}]})
    assert [a.family for a in load_alerts(folder)] == ["metricAlerts"]
    _write(tmp_path, "sniff.json", {"name": "p", "properties": {"rules": [], "scopes": []}})
    assert "prometheusRuleGroups" in {a.family for a in load_alerts(tmp_path / "sniff.json")}


def test_too_deep_or_too_large_input_is_refused(tmp_path: Path) -> None:
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 500 + "]" * 500)
    with pytest.raises(ValueError, match="nested too deeply"):
        load_alerts(deep)
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_alerts(tmp_path / "bad.json")


def test_name_map_next_to_the_export_is_attached(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _rule("a"))
    _write(tmp_path, "name-map.json", {"metrics": {"Percentage CPU": "azure.x.Cpu"}})
    (alert,) = load_alerts(tmp_path)
    assert alert.names.metrics == {"Percentage CPU": "azure.x.Cpu"}


def test_a_bad_name_map_is_a_clear_error(tmp_path: Path) -> None:
    _write(tmp_path, "a.json", _rule("a"))
    _write(tmp_path, "name-map.json", {"metrics": {"A": "bad name; drop"}})
    with pytest.raises(ValueError, match=r"name-map\.json"):
        load_alerts(tmp_path)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("PT1M", 60),
        ("PT5M", 300),
        ("PT1H", 3600),
        ("P1D", 86400),
        ("PT1H30M", 5400),
        ("P1DT12H", 129600),
        ("PT30S", 30),
    ],
)
def test_iso_durations(text: str, seconds: int) -> None:
    assert iso_seconds(text) == seconds


@pytest.mark.parametrize("text", ["P1M", "P1Y", "", "5m", "PT", "P", "PT-5M", None, 5, "PT1.5H"])
def test_ambiguous_or_broken_durations_are_none(text: object) -> None:
    assert iso_seconds(text) is None


def test_every_fixture_loads_without_error() -> None:
    assert len(load_alerts(FIX)) > 50
