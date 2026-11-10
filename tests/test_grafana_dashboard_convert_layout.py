"""Grid conversion, headings, packing, and matching display options to panels."""

import json
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import PROVIDER_MAX_HEIGHT, IRWidget, Layout, WidgetKind
from importer.core.ir import Confidence
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.layout import Placed, _fit, _overlaps, lay_out, pack
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.panels import Draft
from importer.sources.grafana.dashboard_convert.raw import classic_bodies, read_raw
from importer.sources.grafana.dashboard_convert.translate import Item, convert_all
from importer.sources.grafana.dashboards.load import load_data


def draft(title: str = "w") -> Draft:
    return Draft(WidgetKind.MARKDOWN, "text", title, Confidence.EXACT, [], text="t")


def placed(x: int, y: int, w: int, h: int, section: str = "", title: str = "w") -> Placed:
    return Placed(draft(title), x, y, w, h, section)


@pytest.mark.parametrize(
    ("box", "cell"),
    [
        ((0, 0, 24, 8), (1, 1, 12, 4)),
        ((12, 0, 12, 8), (7, 1, 6, 4)),
        ((0, 0, 6, 4), (1, 1, 3, 2)),
        ((6, 8, 6, 5), (4, 5, 3, 3)),  # y 8 to 13 on 2-unit rows: rows 4 to 6, so row 5 height 3
        ((1, 0, 1, 1), (2, 1, 1, 1)),  # never narrower than one column
        ((23, 0, 1, 1), (12, 1, 1, 1)),  # never outside the grid
        ((0, 0, 24, 1), (1, 1, 12, 1)),
        ((0, 0, 24, 300), (1, 1, 12, 100)),
    ],
)
def test_the_grid_is_halved_by_edges(
    box: tuple[int, int, int, int], cell: tuple[int, int, int, int]
) -> None:
    layout = _fit(placed(*box), 0)
    assert (layout.column, layout.row, layout.width, layout.height) == cell


def test_neighbours_that_touched_still_touch() -> None:
    left = _fit(placed(0, 0, 7, 8), 0)
    right = _fit(placed(7, 0, 7, 8), 0)
    assert left.column + left.width == right.column


def test_packing_moves_an_overlapping_widget_below_and_keeps_reading_order() -> None:
    a, b, c = draft("a"), draft("b"), draft("c")
    out = pack(
        [
            (b, Layout(column=1, row=1, width=12, height=2)),
            (a, Layout(column=1, row=1, width=12, height=3)),
            (c, Layout(column=1, row=2, width=6, height=1)),
        ]
    )
    rows = {d.title: (cell.row, cell.height) for d, cell in out}
    assert rows == {"b": (1, 2), "a": (3, 3), "c": (6, 1)}
    assert [d.title for d, _ in out] == ["b", "a", "c"]


def test_sections_get_a_heading_and_start_under_the_section_before() -> None:
    items = [
        placed(0, 0, 24, 8, "", "free"),
        placed(0, 9, 12, 8, "Row A", "a1"),
        placed(12, 9, 12, 4, "Row A", "a2"),
        placed(0, 30, 24, 8, "Row B", "b1"),
    ]
    widgets, skipped = lay_out(items)
    where = {w.title: (w.layout.row, w.layout.height) for w in widgets}
    assert skipped == 0
    assert where == {
        "free": (1, 4),
        "Row A": (5, 1),
        "a1": (6, 4),
        "a2": (6, 2),
        "Row B": (10, 1),
        "b1": (11, 4),
    }
    assert [w.title for w in widgets if w.source_type == "row"] == ["Row A", "Row B"]


def test_a_heading_is_markdown_and_its_text_is_escaped() -> None:
    widgets, _ = lay_out([placed(0, 0, 24, 2, "Row | `x` <b> [y]", "p")])
    head = widgets[0]
    assert head.text is not None and head.text == "## Row x b y"
    assert head.kind == WidgetKind.MARKDOWN and head.layout.width == 12


def test_members_of_a_collapsed_row_keep_their_relative_place() -> None:
    items = [placed(0, 40, 12, 8, "Row", "x"), placed(12, 40, 12, 8, "Row", "y")]
    widgets, _ = lay_out(items)
    cells = {w.title: (w.layout.column, w.layout.row) for w in widgets if w.source_type != "row"}
    assert cells == {"x": (1, 2), "y": (7, 2)}


def test_a_tall_panel_is_clamped_by_the_ir_and_says_so() -> None:
    widgets, _ = lay_out([placed(0, 0, 24, 60)], "D")
    assert widgets[0].layout.height == 30
    assert f"height 30 clamped to {PROVIDER_MAX_HEIGHT}" in widgets[0].reasons


def pages_of(widgets: list[IRWidget]) -> dict[str | None, list[IRWidget]]:
    out: dict[str | None, list[IRWidget]] = {}
    for w in widgets:
        out.setdefault(w.page, []).append(w)
    return out


def test_a_dashboard_that_fits_stays_on_one_unnamed_page() -> None:
    widgets, skipped = lay_out([placed(0, i * 2, 24, 2, "", f"p{i}") for i in range(150)], "D")
    assert skipped == 0 and {w.page for w in widgets} == {None}


def test_a_long_flat_dashboard_is_split_at_widget_boundaries() -> None:
    items = [placed(0, i * 2, 24, 2, "", f"p{i}") for i in range(155)]
    widgets, skipped = lay_out(items, "Big")
    pages = pages_of(widgets)
    assert skipped == 0 and list(pages) == ["Big", "Big part 2"]
    assert [len(v) for v in pages.values()] == [150, 5]
    assert [w.title for w in widgets] == [f"p{i}" for i in range(155)]  # order kept
    assert min(w.layout.row for w in pages["Big part 2"]) == 1  # re-based


def test_rows_become_pages_named_after_the_row_and_are_never_cut() -> None:
    items = [placed(0, i * 2, 24, 2, f"row {i // 40}", f"p{i}") for i in range(160)]
    widgets, skipped = lay_out(items, "D")
    pages = pages_of(widgets)
    assert skipped == 0 and list(pages) == ["row 0", "row 1", "row 2", "row 3"]
    for page in pages.values():
        assert len(page) == 41  # heading and 40 panels
        assert page[0].layout.row == 1 and page[0].source_type == "row"


def test_a_row_over_the_limit_is_split_with_part_suffix() -> None:
    items = [placed(0, i * 2, 24, 2, "big", f"p{i}") for i in range(160)]
    items += [placed(0, 400 + i * 2, 24, 2, "small", f"q{i}") for i in range(3)]
    pages = pages_of(lay_out(items, "D")[0])
    assert list(pages) == ["big", "big part 2", "small"]
    assert [len(v) for v in pages.values()] == [150, 11, 4]


def test_more_than_25_rows_are_packed_into_pages_without_cutting_a_row() -> None:
    items = [placed(0, i * 2, 24, 2, f"r{i}", f"p{i}") for i in range(60)]
    items += [placed(0, 200 + i * 2, 24, 2, "tail", f"t{i}") for i in range(120)]
    widgets, skipped = lay_out(items, "D")
    pages = pages_of(widgets)
    assert skipped == 0 and len(pages) <= 25
    assert all(len(v) <= 150 for v in pages.values())
    assert {w.page for w in widgets if w.title == "t5"} == {
        w.page for w in widgets if w.title == "tail"
    }


def test_the_25_page_limit_drops_the_rest_and_counts_it() -> None:
    items = [placed(0, i * 2, 24, 2, "", f"p{i}") for i in range(3800)]
    widgets, skipped = lay_out(items, "D")
    assert len(widgets) == 3750 and skipped == 50 and len(pages_of(widgets)) == 25


def test_pages_have_no_overlap_and_stay_in_the_grid() -> None:
    items = [placed((i % 2) * 12, (i // 2) * 4, 12, 4, f"r{i // 100}") for i in range(400)]
    for page in pages_of(lay_out(items, "D")[0]).values():
        assert min(w.layout.row for w in page) == 1
        for a, b in combinations(page, 2):
            assert not _overlaps(a.layout, b.layout)
        assert all(w.layout.column >= 1 and w.layout.column + w.layout.width <= 13 for w in page)


def test_hostile_row_names_are_kept_as_text_and_made_unique() -> None:
    evil = '"; DROP\n${var} {{x}} \x00'
    items = [placed(0, i * 2, 24, 2, evil if i < 100 else evil + " ", "p") for i in range(200)]
    names = [n or "" for n in pages_of(lay_out(items, "D")[0])]
    assert len(names) == len(set(names)) and all("\x00" not in n and "\n" not in n for n in names)


# --- matching raw display options to panels ---


def classic(panels: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"uid": "u", "title": "t", "schemaVersion": 39, "panels": panels, **extra}


def estate_of(body: dict[str, Any]) -> Any:
    return load_data(body, "x.json").dashboards[0]


def test_bodies_follow_the_order_the_planner_reads_rows() -> None:
    body = classic(
        [
            {"id": 1, "type": "stat", "title": "first"},
            {"id": 2, "type": "row", "title": "open"},
            {"id": 3, "type": "stat", "title": "member of open"},
            {
                "id": 4,
                "type": "row",
                "title": "closed",
                "collapsed": True,
                "panels": [{"id": 5, "type": "stat", "title": "inside"}],
            },
            {"id": 6, "type": "stat", "title": "after closed"},
        ]
    )
    titles = [b["title"] for b in classic_bodies(body, {})]
    assert titles == [p.title for p in estate_of(body).panels]
    assert titles == ["first", "member of open", "inside", "after closed"]


def test_legacy_rows_are_read_in_order() -> None:
    body = {
        "uid": "l",
        "title": "old",
        "schemaVersion": 12,
        "rows": [
            {"title": "r1", "panels": [{"id": 1, "type": "graph", "title": "g1", "span": 6}]},
            {"title": "r2", "panels": [{"id": 2, "type": "graph", "title": "g2", "span": 6}]},
        ],
    }
    dash = estate_of(body)
    raw = read_raw(body, "classic", dash, {})
    assert raw.matched and len(raw.displays) == 2 == len(dash.panels)


def test_a_library_panel_takes_its_display_from_the_model() -> None:
    body = classic([{"id": 1, "title": "lib", "libraryPanel": {"uid": "L"}}])
    library = {"L": {"type": "stat", "fieldConfig": {"defaults": {"unit": "bytes"}}}}
    dash = load_data(body, "x.json", library).dashboards[0]
    raw = read_raw(body, "classic", dash, library)
    assert raw.matched and raw.displays[0] is not None and raw.displays[0].unit == "bytes"


def test_when_panels_and_bodies_disagree_nothing_is_guessed_and_the_widget_says_so() -> None:
    body = classic(
        [{"id": 1, "type": "stat", "title": "a"}, {"id": 2, "type": "stat", "title": "b"}]
    )
    dash = estate_of(body)
    other = classic([{"id": 1, "type": "stat", "title": "a"}])
    raw = read_raw(other, "classic", dash, {})
    assert not raw.matched and raw.displays == [None, None]


def test_a_dashboard_whose_extras_are_missing_is_converted_with_a_note_per_widget() -> None:
    panel = {
        "id": 1,
        "type": "timeseries",
        "title": "cpu",
        "datasource": {"type": "prometheus", "uid": "p"},
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
        "targets": [{"refId": "A", "expr": "avg(up)"}],
    }
    dash = estate_of(classic([panel]))
    made, _ = convert_all([Item(dash=dash, raw=None)], NameMap())
    assert r.N_DISPLAY in made[0].widgets[0].reasons


def test_v2_panels_are_matched_by_title_and_read_their_unit(tmp_path: Path) -> None:
    body = {
        "apiVersion": "dashboard.grafana.app/v2beta1",
        "kind": "Dashboard",
        "metadata": {"name": "v2u"},
        "spec": {
            "title": "v2",
            "elements": {
                "e1": {
                    "kind": "Panel",
                    "spec": {
                        "id": 1,
                        "title": "same",
                        "vizConfig": {
                            "kind": "VizConfig",
                            "group": "stat",
                            "spec": {"fieldConfig": {"defaults": {"unit": "ms"}}},
                        },
                        "data": {"kind": "QueryGroup", "spec": {"queries": []}},
                    },
                },
                "e2": {
                    "kind": "Panel",
                    "spec": {
                        "id": 2,
                        "title": "same",
                        "vizConfig": {
                            "kind": "VizConfig",
                            "group": "stat",
                            "spec": {"fieldConfig": {"defaults": {"unit": "s"}}},
                        },
                        "data": {"kind": "QueryGroup", "spec": {"queries": []}},
                    },
                },
            },
            "layout": {
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
                                "element": {"name": "e1"},
                            },
                        },
                        {
                            "kind": "GridLayoutItem",
                            "spec": {
                                "x": 12,
                                "y": 0,
                                "width": 12,
                                "height": 4,
                                "element": {"name": "e2"},
                            },
                        },
                    ]
                },
            },
        },
    }
    (tmp_path / "v2.json").write_text(json.dumps(body))
    from importer.sources.grafana.dashboard_convert.translate import load_items

    item = load_items(tmp_path)[0]
    assert item.raw is not None
    units = [d.unit if d else None for d in item.raw.displays]
    assert units == ["ms", "s"]


def classic_body_of(count: int) -> dict[str, Any]:
    return classic([{"id": i, "type": "stat", "title": f"p{i}"} for i in range(count)])


def test_the_panel_cap_applies_to_both_readers(monkeypatch: pytest.MonkeyPatch) -> None:
    from importer.sources.grafana.dashboard_convert import raw as raw_module
    from importer.sources.grafana.dashboards import classic as classic_module

    monkeypatch.setattr(classic_module, "MAX_PANELS", 3)
    monkeypatch.setattr(raw_module, "MAX_PANELS", 3)
    body = classic_body_of(5)
    dash = estate_of(body)
    assert len(dash.panels) == 3
    assert len(classic_bodies(body, {})) == 3
    assert read_raw(body, "classic", dash, {}).matched


def test_rows_nested_deeper_than_the_cap_are_left_out_by_both_readers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importer.sources.grafana.dashboard_convert import raw as raw_module
    from importer.sources.grafana.dashboards import classic as classic_module

    monkeypatch.setattr(classic_module, "MAX_DEPTH", 0)
    monkeypatch.setattr(raw_module, "MAX_DEPTH", 0)
    inner = [{"id": 3, "type": "stat", "title": "hidden"}]
    body = classic(
        [
            {"id": 1, "type": "stat", "title": "top"},
            {"id": 2, "type": "row", "title": "r", "collapsed": True, "panels": inner},
        ]
    )
    dash = estate_of(body)
    assert [p.title for p in dash.panels] == ["top"]
    assert [b["title"] for b in classic_bodies(body, {})] == ["top"]
