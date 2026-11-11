"""Reading Azure workbooks and portal dashboards from exports, official examples and templates."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.azure.dashboard_models import load_dashboards

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
SERIALIZED = {"version": "Notebook/1.0", "items": [{"type": 1, "content": {"json": "hi"}}]}


def write(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data))
    return path


def workbook(name: str = "wb1", serialized: Any = None) -> dict[str, Any]:
    return {
        "id": f"/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg/providers/"
        f"microsoft.insights/workbooks/{name}",
        "name": name,
        "type": "Microsoft.Insights/workbooks",
        "properties": {
            "displayName": f"Workbook {name}",
            "category": "workbook",
            "serializedData": json.dumps(SERIALIZED if serialized is None else serialized),
        },
    }


def test_a_list_page_of_workbooks_is_read(tmp_path: Path) -> None:
    write(tmp_path / "workbooks.json", {"value": [workbook("a"), workbook("b")]})
    found = load_dashboards(tmp_path)
    assert [(d.kind, d.name) for d in found] == [
        ("workbook", "Workbook a"),
        ("workbook", "Workbook b"),
    ]
    assert found[0].content == SERIALIZED
    assert found[0].error == ""


def test_serialized_data_that_is_not_json_is_kept_with_an_error(tmp_path: Path) -> None:
    bad = workbook("a")
    bad["properties"]["serializedData"] = '{"items":[{"content":"{"json":1}"}]}'
    write(tmp_path / "workbooks.json", {"value": [bad]})
    (found,) = load_dashboards(tmp_path)
    assert found.content is None
    assert "not valid JSON" in found.error


def test_a_listing_without_content_is_an_error_not_a_crash(tmp_path: Path) -> None:
    empty = workbook("a")
    del empty["properties"]["serializedData"]
    write(tmp_path / "workbooks.json", {"value": [empty]})
    (found,) = load_dashboards(tmp_path)
    assert "canFetchContent" in found.error


def test_a_raw_workbook_template_file_is_read(tmp_path: Path) -> None:
    write(tmp_path / "My Template.workbook", SERIALIZED)
    (found,) = load_dashboards(tmp_path)
    assert (found.kind, found.name, found.content) == ("workbook", "My Template", SERIALIZED)


def test_an_official_example_is_read_from_its_response_not_its_request(tmp_path: Path) -> None:
    example = {
        "parameters": {"api-version": "2022-04-01", "workbookProperties": workbook("request")},
        "responses": {"200": {"body": workbook("response")}, "204": {}},
    }
    write(tmp_path / "ex.json", example)
    (found,) = load_dashboards(tmp_path)
    assert found.name == "Workbook response"


def test_a_portal_dashboard_in_every_shape_is_read(tmp_path: Path) -> None:
    props = {"lenses": [{"order": 0, "parts": []}]}
    arm = {
        "resources": [{"type": "Microsoft.Portal/dashboards", "name": "d1", "properties": props}]
    }
    write(tmp_path / "template.json", arm)
    write(
        tmp_path / "list.json",
        {"value": [{"name": "d2", "type": "Microsoft.Portal/dashboards", "properties": props}]},
    )
    write(tmp_path / "bare.json", {"name": "d3", "lenses": {"0": {"parts": {}}}})
    found = load_dashboards(tmp_path)
    assert sorted((d.kind, d.name) for d in found) == [
        ("portal", "d1"),
        ("portal", "d2"),
        ("portal", "d3"),
    ]


def test_alert_files_are_ignored_and_copies_are_read_once(tmp_path: Path) -> None:
    write(
        tmp_path / "metricAlerts.json",
        {"value": [{"name": "a", "type": "Microsoft.Insights/metricAlerts", "properties": {}}]},
    )
    write(tmp_path / "one.json", workbook("a"))
    write(tmp_path / "two.json", workbook("a"))
    assert len(load_dashboards(tmp_path)) == 1


def test_the_official_workbook_examples_load() -> None:
    found = load_dashboards(FIXTURES / "workbooks")
    assert found
    assert {d.kind for d in found} == {"workbook"}


def test_the_official_dashboard_examples_load() -> None:
    found = load_dashboards(FIXTURES / "dashboards")
    assert found
    assert {d.kind for d in found} == {"portal"}


def test_a_file_that_is_too_deep_or_broken_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("[" * 500)
    with pytest.raises(ValueError, match="deeply"):
        load_dashboards(tmp_path)
