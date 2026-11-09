"""Loader round 2: ambiguous shapes, v2 placement, duplicates, encodings, plan files, caps."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.dashboards import load, models, v2
from importer.sources.grafana.dashboards.load import load_data, load_estate
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import plan_json


def grid(*items: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "GridLayout",
        "spec": {
            "items": [
                {
                    "kind": "GridLayoutItem",
                    "spec": {
                        "x": 0,
                        "y": 0,
                        "width": 12,
                        "height": 4,
                        "element": {"name": n},
                        **extra,
                    },
                }
                for n, extra in items
            ]
        },
    }


def element(title: str) -> dict[str, Any]:
    return {
        "kind": "Panel",
        "spec": {"id": 1, "title": title, "vizConfig": {"group": "stat", "spec": {}}},
    }


def test_a_classic_dashboard_with_elements_and_a_layout_is_read_as_classic_with_a_note() -> None:
    body = {
        "title": "t",
        "panels": [{"id": 1, "type": "stat", "title": "real", "targets": []}],
        "elements": {},
        "layout": {"kind": "GridLayout", "spec": {"items": []}},
    }
    d = load_data(body, "x").dashboards[0]
    assert [p.title for p in d.panels] == ["real"]
    assert models.W_BOTH_SHAPES in d.warnings


def test_a_v2_repeat_belongs_to_its_own_element_even_when_an_earlier_item_failed() -> None:
    body = {
        "elements": {"a": element("A"), "b": element("B")},
        "layout": grid(
            ("missing", {"repeat": {"value": "region"}}),
            ("a", {}),
            ("b", {"repeat": {"value": "env"}}),
        ),
    }
    d = load_data(body, "x").dashboards[0]
    assert [(p.title, p.repeat) for p in d.panels] == [("A", ""), ("B", "env")]


def test_an_element_placed_twice_is_noted_and_unplaced_counts_only_panels() -> None:
    body = {
        "elements": {
            "a": element("A"),
            "unused": element("U"),
            "junk": 5,
            "other": {"kind": "Text"},
        },
        "layout": grid(("a", {}), ("a", {})),
    }
    d = load_data(body, "x").dashboards[0]
    assert models.W_PLACED_TWICE.format(count=1) in d.warnings
    assert models.W_UNPLACED.format(count=1) in d.warnings  # only "unused"
    assert len(d.panels) == 3


def test_duplicate_dashboard_uids_are_kept_once_and_warned_once() -> None:
    same = {"uid": "dup", "title": "t", "panels": [{"id": 1, "type": "stat", "targets": []}]}
    estate = load_data(
        [
            same,
            dict(same),
            dict(same),
            {"uid": "", "title": "a", "panels": []},
            {"uid": "", "title": "b", "panels": []},
        ],
        "x",
    )
    assert len(estate.dashboards) == 3  # "dup" once, and the two without a uid
    assert estate.warnings == [models.W_DUPLICATE_UID.format(count=1)]


def test_a_utf8_bom_file_is_read_and_a_bad_encoding_is_named(tmp_path: Path) -> None:
    (tmp_path / "bom.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"title": "t", "panels": []}).encode()
    )
    (tmp_path / "latin.json").write_bytes(b'{"title": "caf\xe9", "panels": []}')
    estate = load_estate(tmp_path)
    assert len(estate.dashboards) == 1
    assert [(u.source, u.reason) for u in estate.unreadable] == [("latin.json", models.U_ENCODING)]
    assert "encoding" in models.U_ENCODING


@pytest.mark.parametrize("value", ["²", "٣٩", "4²", "  "])
def test_a_schema_version_with_odd_digits_does_not_crash_the_dashboard(value: str) -> None:
    d = load_data({"title": "t", "panels": [], "schemaVersion": value}, "x").dashboards[0]
    assert d.schema_version is None


def test_a_plan_file_is_skipped_and_the_command_refuses_an_output_inside_the_input(
    tmp_path: Path,
) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "d.json").write_text(json.dumps({"uid": "d", "title": "t", "panels": []}))
    plan = build_plan(load_estate(src))
    (src / "plan.json").write_text(plan_json(plan))
    estate = load_estate(src)
    assert len(estate.dashboards) == 1
    assert any("plan" in w for w in estate.warnings)
    result = CliRunner().invoke(
        app, ["plan-grafana-dashboards", "--in", str(src), "--out", str(src / "plan")]
    )
    assert result.exit_code == 1 and "inside" in result.output
    assert not (src / "plan").exists()


def test_more_than_the_dashboard_cap_keeps_the_first_ones_and_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(models, "MAX_DASHBOARDS", 3)
    monkeypatch.setattr(load, "MAX_DASHBOARDS", 3)
    entries = [{"uid": f"u{i}", "title": "t", "panels": []} for i in range(10)]
    estate = load_data(entries, "x")
    assert [d.uid for d in estate.dashboards] == ["u0", "u1", "u2"]
    assert estate.warnings == [models.W_TOO_MANY_DASHBOARDS.format(limit=3)]


def test_the_limits_are_the_documented_ones() -> None:
    assert (models.MAX_DASHBOARDS, models.MAX_PANELS, models.MAX_DEPTH) == (5000, 10_000, 200)
    assert (models.MAX_FILES, models.MAX_LIBRARY, models.MAX_TARGETS) == (10_000, 5000, 200)


def test_a_symlinked_json_file_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"title": "secret", "panels": []}))
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "link.json").symlink_to(outside)
    assert load_estate(folder).dashboards == []


def test_a_library_page_in_the_raw_api_shape_is_resolved(tmp_path: Path) -> None:
    raw = {
        "result": {
            "totalCount": 1,
            "elements": [{"uid": "L", "model": {"type": "stat", "title": "m", "targets": []}}],
        }
    }
    (tmp_path / "library-panels.json").write_text(json.dumps(raw))
    (tmp_path / "d.json").write_text(
        json.dumps({"title": "t", "panels": [{"id": 1, "libraryPanel": {"uid": "L"}}]})
    )
    estate = load_estate(tmp_path)
    assert estate.library_panels == 1 and estate.dashboards[0].panels[0].library_found


def test_v2_panels_keep_transformations_hidden_queries_and_the_panel_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = {"kind": "DataQuery", "group": "prometheus", "spec": {"expr": "up"}}
    spec = {
        "id": 1,
        "title": "t",
        "data": {
            "kind": "QueryGroup",
            "spec": {
                "queries": [
                    {"kind": "PanelQuery", "spec": {"refId": "A", "hidden": True, "query": query}}
                ],
                "transformations": [{"kind": "merge", "spec": {}}],
            },
        },
        "vizConfig": {"kind": "VizConfig", "group": "table", "spec": {}},
    }
    body = {"elements": {"a": {"kind": "Panel", "spec": spec}}, "layout": grid(*[("a", {})] * 3)}
    d = load_data(body, "x").dashboards[0]
    assert d.panels[0].transformations == ["merge"] and d.panels[0].targets[0].hidden
    monkeypatch.setattr(v2, "MAX_PANELS", 2)
    capped = load_data(body, "x").dashboards[0]
    assert len(capped.panels) == 2 and models.W_PANELS in capped.warnings
