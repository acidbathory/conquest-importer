"""Copies are skipped only when the raw dashboard is the same; different ones are all kept."""

import json
from pathlib import Path
from typing import Any

from importer.sources.dynatrace.dashboards import models as m
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report

BOARD: dict[str, Any] = {
    "version": 21,
    "variables": [],
    "tiles": {"1": {"type": "data", "query": "timeseries avg(m)", "visualization": "lineChart"}},
}


def put(folder: Path, name: str, data: object) -> None:
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(data))


def test_same_content_under_another_name_and_id_is_one_dashboard(tmp_path: Path) -> None:
    put(tmp_path, "a.json", {"id": "1", "name": "A", "owner": "x", "content": BOARD})
    put(tmp_path, "b.json", {"id": "2", "name": "B", "owner": "y", "content": BOARD})
    docs, copies = m.load_documents(tmp_path)
    assert len(docs) == 1 and len(copies) == 1
    assert "B" in copies[0] and "same as A" in copies[0]


def test_a_difference_the_models_ignore_still_keeps_both(tmp_path: Path) -> None:
    other = {**BOARD, "tiles": {"1": {**BOARD["tiles"]["1"], "queryConfig": {"x": 1}}}}
    put(tmp_path, "a.json", BOARD)
    put(tmp_path, "b.json", other)
    assert len(m.load_documents(tmp_path)[0]) == 2


def classic(filters: list[str], zone: bool = False) -> dict[str, Any]:
    tile: dict[str, Any] = {"name": "t", "tileType": "MARKDOWN", "markdown": "x"}
    if zone:
        tile["tileFilter"] = {"managementZone": {"id": "1"}}
    return {
        "dashboardMetadata": {"name": "n", "owner": "o", "dynamicFilters": {"filters": filters}},
        "tiles": [tile],
    }


def test_classic_dashboards_with_different_filters_do_not_collapse(tmp_path: Path) -> None:
    put(tmp_path, "a.json", classic(["OS_TYPE"]))
    put(tmp_path, "b.json", classic(["HOST_TAG_KEY:env"]))
    put(tmp_path, "c.json", classic(["OS_TYPE"], zone=True))
    docs, copies = m.load_documents(tmp_path)
    assert len(docs) == 3 and copies == []


def test_classic_dashboards_differing_only_in_name_and_owner_collapse(tmp_path: Path) -> None:
    a, b = classic(["OS_TYPE"]), classic(["OS_TYPE"])
    b["dashboardMetadata"].update(name="other", owner="someone else")
    put(tmp_path, "a.json", a)
    put(tmp_path, "b.json", b)
    assert len(m.load_documents(tmp_path)[0]) == 1


def test_the_report_lists_the_names_of_skipped_copies_and_escapes_them(tmp_path: Path) -> None:
    put(tmp_path, "a.json", {"name": "first", "content": BOARD})
    put(tmp_path, "b.json", {"name": "| copy <b>x</b> [l](u)", "content": BOARD})
    docs, copies = m.load_documents(tmp_path)
    report = render_report(plan_documents(docs, copies))
    assert "## Skipped copies" in report and "<b>" not in report and "[l](u)" not in report
    assert "copy" in report


def test_many_copies_are_counted_but_only_fifty_names_are_kept(tmp_path: Path) -> None:
    for i in range(60):
        put(tmp_path, f"d{i:02d}.json", {"name": f"n{i}", "content": BOARD})
    docs, copies = m.load_documents(tmp_path)
    plans = plan_documents(docs, copies)
    assert len(docs) == 1 and plans.copies_skipped == 59 and len(plans.copy_names) == 50
    assert "and 9 more" in render_report(plans)


def test_the_owner_of_a_document_is_not_kept() -> None:
    doc = m.parse_document("d", {"owner": "a-user-id", "name": "n", "content": BOARD})
    assert "a-user-id" not in doc.model_dump_json()
