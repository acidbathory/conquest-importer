"""Reading Grafana dashboards: three JSON shapes, old schemas, hostile and huge input."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.grafana.dashboards import classic as classic_reader
from importer.sources.grafana.dashboards import load, models
from importer.sources.grafana.dashboards.load import detect_shape, load_data, load_estate

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-dashboards"


def one(name: str) -> models.Dashboard:
    estate = load_estate(FIXTURES / name)
    assert not estate.unreadable
    return estate.dashboards[0]


def classic(panels: list[Any], **extra: Any) -> dict[str, Any]:
    return {"uid": "d", "title": "t", "schemaVersion": 39, "panels": panels, **extra}


def stat(i: int = 1, **extra: Any) -> dict[str, Any]:
    return {"id": i, "type": "stat", "title": f"p{i}", "targets": [], **extra}


@pytest.mark.parametrize(
    ("data", "shape"),
    [
        ({"title": "t", "panels": []}, "classic"),
        ({"rows": []}, "classic"),
        ({"dashboard": {"panels": []}, "meta": {}}, "classic"),
        (
            {"apiVersion": "dashboard.grafana.app/v1", "kind": "Dashboard", "spec": {"panels": []}},
            "v1",
        ),
        (
            {
                "apiVersion": "dashboard.grafana.app/v2",
                "kind": "Dashboard",
                "spec": {"elements": {}, "layout": {"kind": "GridLayout", "spec": {"items": []}}},
            },
            "v2",
        ),
        ({"elements": {}, "layout": {"kind": "GridLayout"}}, "v2"),
        ({"dashboard": {"elements": {}, "layout": {"kind": "RowsLayout"}}}, "v2"),
        ({"apiVersion": "x", "kind": "Dashboard", "spec": "text"}, ""),
        ({"hello": "world"}, ""),
        ({"elements": [], "layout": {}}, ""),
    ],
)
def test_the_shape_is_told_apart_in_order(data: dict[str, Any], shape: str) -> None:
    found = detect_shape(data)
    assert (found.shape if found else "") == shape


def test_the_easy_classic_fixture_is_read() -> None:
    d = one("easy-node-overview.json")
    assert (d.shape, d.schema_version, d.uid) == ("classic", 39, "easy-node-overview")
    assert [p.type for p in d.panels] == ["stat", "timeseries", "timeseries", "table", "text"]
    assert [v.kind for v in d.variables] == ["datasource", "query"]
    assert d.variables[1].multi and d.variables[1].include_all
    assert d.variables[1].query == "label_values(node_uname_info, job)"
    assert d.annotations[0].builtin
    assert d.panels[1].targets[0].legend == "{{instance}}"
    assert d.panels[1].ds == models.DsRef(type="prometheus", uid="${datasource}")
    assert d.panels[4].text_mode == "markdown"


def test_the_hard_fixture_keeps_rows_collapsed_members_and_mixed_targets() -> None:
    d = one("hard-service-mixed.json")
    assert d.rows == 2 and d.collapsed_rows == 1
    by_title = {p.title: p for p in d.panels}
    assert by_title["Retries"].section == "Details (collapsed)"
    assert by_title["Request rate"].section == "Traffic for $service"
    assert by_title["CPU by pod (library)"].library_uid == "lib-cpu-1"
    assert not by_title["CPU by pod (library)"].library_found
    error_ratio = by_title["Error ratio"]
    assert [t.hidden for t in error_ratio.targets] == [True, False]
    assert error_ratio.targets[1].expr_type == "math"
    assert error_ratio.mappings == 2
    assert by_title["Slow endpoints"].transformations == ["merge", "organize"]
    assert by_title["Request rate"].overrides == 1
    assert by_title["Retries"].links == 1
    assert [a.ds_type for a in d.annotations] == ["grafana", "loki"]
    assert d.links == 1


def test_the_legacy_rows_fixture_becomes_panels_with_sections_and_a_24_column_grid() -> None:
    d = one("legacy-rows-v12.json")
    assert d.schema_version == 12 and d.rows == 2 and d.collapsed_rows == 1
    assert d.inputs == {"DS_PROMETHEUS": "prometheus"}
    assert [(p.title, p.section, p.w) for p in d.panels] == [
        ("Instances up", "Health", 6),
        ("Request rate", "Health", 18),
        ("Per instance", "Collapsed extras", 24),
    ]
    assert d.panels[1].x == 6 and d.panels[2].repeat == ""
    assert d.panels[2].transformations == ["legacy-timeseries_to_rows"]  # old table `transform`
    assert d.row_repeats == 1
    assert d.annotations[0].ds_type == "" and d.annotations[0].name == "restarts"


def test_the_v2_fixture_is_read_through_rows_and_grids() -> None:
    d = one("v2-small.json")
    assert (d.shape, d.api_version, d.folder) == ("v2", "dashboard.grafana.app/v2beta1", "team-a")
    assert [(p.title, p.type, p.section) for p in d.panels] == [
        ("Targets up", "stat", "Overview"),
        ("Memory", "timeseries", "Overview"),
        ("Shared CPU panel", "", "Shared"),
    ]
    assert d.panels[0].targets[0].expr == 'sum(up{job=~"$job"})'
    assert d.panels[0].targets[0].ds == models.DsRef(type="prometheus", uid="prom-main")
    assert (d.panels[1].x, d.panels[1].w) == (8, 16)
    assert d.panels[2].library_uid == "lib-cpu-1"
    assert [(v.name, v.kind) for v in d.variables] == [("job", "query"), ("mode", "custom")]
    assert d.variables[0].query == "label_values(up, job)"
    assert d.rows == 2 and d.collapsed_rows == 1


def test_the_hostile_fixture_loads_and_every_entry_is_bounded() -> None:
    estate = load_estate(FIXTURES / "hostile.json")
    assert not estate.unreadable
    d = estate.dashboards[0]
    assert d.schema_version is None
    assert d.warnings == [models.W_NOT_OBJECTS.format(count=2)]
    first = d.panels[0]
    assert (first.x, first.y, first.w, first.h) == (0, 0, 24, 1)  # -5, -1, 99999 and 0 clamped
    assert [t.malformed for t in first.targets] == [False, True, False]  # "not a target" skipped
    assert len(d.variables) == 3 and d.variables[0].name == "x' OR 1=1 --"
    assert d.annotations[0].name == "list instead of object"  # annotations given as a list
    assert max(len(p.title) for p in d.panels) <= models.MAX_TEXT
    assert d.rows == 3 and d.collapsed_rows == 2
    deep = next(p for p in d.panels if p.title == "deep")
    assert (deep.depth, deep.section) == (3, "and again")
    assert [(p.id, p.section) for p in d.panels if p.id in (7, 8)] == [(7, ""), (8, "")]
    assert [p.title for p in d.panels if p.id == 8] == [""]  # a numeric title is not text


def test_a_wrapper_and_a_folder_title_are_read_from_the_api_shape(tmp_path: Path) -> None:
    body = {"dashboard": classic([stat()]), "meta": {"folderUid": "f1", "folderTitle": "Team"}}
    estate = load_data(body, "x.json")
    assert estate.dashboards[0].folder == "Team"
    (tmp_path / "folders.json").write_text(json.dumps({"folders": [{"uid": "f1", "title": "Ops"}]}))
    (tmp_path / "d.json").write_text(
        json.dumps({"dashboard": classic([]), "meta": {"folderUid": "f1"}})
    )
    assert load_estate(tmp_path).dashboards[0].folder == "Ops"


def test_a_v1_envelope_around_a_classic_dashboard_is_read() -> None:
    body = {
        "apiVersion": "dashboard.grafana.app/v1",
        "kind": "Dashboard",
        "metadata": {"name": "abc", "annotations": {"grafana.app/folder": "f"}},
        "spec": classic([stat()]),
    }
    d = load_data(body, "x").dashboards[0]
    assert (d.shape, d.uid, d.folder, len(d.panels)) == ("v1", "d", "f", 1)


def test_a_list_and_an_items_response_give_one_dashboard_each_and_bad_ones_are_unreadable() -> None:
    estate = load_data(
        [classic([], uid="a"), "text", 5, {"hello": 1}, classic([stat()], uid="b")], "list.json"
    )
    assert len(estate.dashboards) == 2
    assert [(u.position, u.reason) for u in estate.unreadable] == [
        (1, models.U_NOT_OBJECT),
        (2, models.U_NOT_OBJECT),
        (3, models.U_UNKNOWN_SHAPE),
    ]
    items = load_data({"items": [classic([])], "metadata": {"continue": ""}}, "api.json")
    assert len(items.dashboards) == 1


def test_a_folder_loads_the_good_files_and_names_the_bad_ones(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(classic([stat()])))
    (tmp_path / "b.json").write_text("{not json")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.json").write_text(json.dumps(classic([], uid="c")))
    (tmp_path / "notes.txt").write_text("ignored")
    estate = load_estate(tmp_path)
    assert len(estate.dashboards) == 2
    assert [(u.source, u.reason) for u in estate.unreadable] == [("b.json", models.U_BAD_JSON)]


def test_json_nested_a_hundred_thousand_deep_is_unreadable_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "deep.json").write_text("[" * 100_000 + "]" * 100_000)
    estate = load_estate(tmp_path)
    assert [u.reason for u in estate.unreadable] == [models.U_BAD_JSON]


def test_a_file_larger_than_the_cap_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "big.json").write_text(json.dumps(classic([stat()])))
    monkeypatch.setattr(load, "MAX_INPUT_BYTES", 10)
    assert [u.reason for u in load_estate(tmp_path).unreadable] == [models.U_TOO_BIG]


def test_ten_thousand_and_fifty_panels_are_cut_at_the_cap_with_a_note() -> None:
    panels = [stat(i) for i in range(models.MAX_PANELS + 50)]
    d = load_data(classic(panels), "big").dashboards[0]
    assert len(d.panels) == models.MAX_PANELS
    assert d.warnings == [models.W_PANELS]


def test_two_hundred_nested_rows_are_read_and_two_hundred_fifty_are_cut() -> None:
    def nest(levels: int) -> list[Any]:
        inner: list[Any] = [stat(9999, title="leaf")]
        for level in range(levels):
            inner = [{"id": level, "type": "row", "title": f"r{level}", "panels": inner}]
        return inner

    ok = load_data(classic(nest(190)), "n").dashboards[0]
    assert [(p.title, p.depth) for p in ok.panels] == [("leaf", 190)]
    assert not ok.warnings
    deep = load_data(classic(nest(250)), "n").dashboards[0]
    assert models.W_DEEP in deep.warnings
    assert all(p.depth <= models.MAX_DEPTH for p in deep.panels)


def test_two_hundred_fifty_nested_v2_layouts_are_cut_without_recursion_error() -> None:
    layout: dict[str, Any] = {"kind": "GridLayout", "spec": {"items": []}}
    for _ in range(250):
        layout = {
            "kind": "RowsLayout",
            "spec": {"rows": [{"kind": "RowsLayoutRow", "spec": {"title": "r", "layout": layout}}]},
        }
    body = {"elements": {}, "layout": layout, "title": "deep"}
    d = load_data(body, "v2").dashboards[0]
    assert models.W_DEEP in d.warnings


def test_a_one_megabyte_title_expression_and_markdown_body_are_cut() -> None:
    big = "A" * 1_000_000
    panel = stat(
        title=big,
        targets=[{"refId": "A", "expr": big, "legendFormat": big}],
    )
    text = {"id": 2, "type": "text", "title": "t", "options": {"mode": "markdown", "content": big}}
    d = load_data(classic([panel, text], title=big), "huge").dashboards[0]
    assert len(d.title) == models.MAX_TEXT == len(d.panels[0].title)
    assert len(d.panels[0].targets[0].expr) == models.MAX_EXPR
    assert len(d.panels[0].targets[0].legend) == models.MAX_TEXT
    assert d.panels[1].text_chars == 1_000_000


def test_many_variables_and_many_targets_are_capped() -> None:
    variables = [{"name": f"v{i}", "type": "custom", "query": "a,b"} for i in range(600)]
    targets = [{"refId": str(i), "expr": "up"} for i in range(250)]
    d = load_data(
        classic([stat(targets=targets)], templating={"list": variables}), "many"
    ).dashboards[0]
    assert len(d.variables) == models.MAX_VARIABLES
    assert len(d.panels[0].targets) == models.MAX_TARGETS
    assert {models.W_VARIABLES, models.W_TARGETS} <= set(d.warnings)


@pytest.mark.parametrize(
    "grid",
    [
        {"x": 1e308, "y": 1e308, "w": 1e308, "h": 1e308},
        {"x": "NaN", "y": float("inf"), "w": "wide", "h": None},
        {"x": -1, "y": -1, "w": -5, "h": -5},
        {"x": 0.5, "y": 2.7, "w": 3.9, "h": True},
        "not a dict",
        [1, 2, 3, 4],
    ],
)
def test_odd_grid_numbers_are_clamped_to_the_grid(grid: Any) -> None:
    p = load_data(classic([stat(gridPos=grid)]), "g").dashboards[0].panels[0]
    assert 0 <= p.x <= models.GRID_COLUMNS and 1 <= p.w <= models.GRID_COLUMNS
    assert 0 <= p.y <= models.MAX_Y and p.h >= 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [(39, 39), ("41", 41), ("forty", None), (-3, None), (None, None), (1e20, None), (True, None)],
)
def test_schema_version_is_tolerated(value: Any, expected: int | None) -> None:
    body = classic([])
    body["schemaVersion"] = value
    assert load_data(body, "s").dashboards[0].schema_version == expected


def test_a_newer_schema_version_is_noted_and_a_missing_one_is_not() -> None:
    newer = classic([])
    newer["schemaVersion"] = 99
    assert load_data(newer, "s").dashboards[0].warnings == [
        models.W_SCHEMA_NEWER.format(version=99)
    ]
    del newer["schemaVersion"]
    assert not load_data(newer, "s").dashboards[0].warnings


def test_library_panels_are_resolved_by_uid_from_the_export_and_from_elements(
    tmp_path: Path,
) -> None:
    model = {
        "type": "timeseries",
        "title": "From library",
        "datasource": {"type": "prometheus", "uid": "p"},
        "targets": [{"refId": "A", "expr": "up"}],
    }
    lib = {"libraryPanels": [{"uid": "L1", "name": "n", "type": "timeseries", "model": model}]}
    dash = classic(
        [
            {"id": 1, "libraryPanel": {"uid": "L1", "name": "n"}, "gridPos": {"x": 3, "w": 4}},
            {"id": 2, "libraryPanel": {"uid": "L2", "name": "m"}},
            {"id": 3, "libraryPanel": {"uid": "L3"}},
        ],
        __elements={
            "L3": {
                "uid": "L3",
                "name": "n",
                "kind": 1,
                "model": {**model, "title": "From elements"},
            }
        },
    )
    (tmp_path / "library-panels.json").write_text(json.dumps(lib))
    (tmp_path / "d.json").write_text(json.dumps(dash))
    estate = load_estate(tmp_path)
    assert estate.library_panels == 1
    panels = estate.dashboards[0].panels
    assert [(p.library_uid, p.library_found) for p in panels] == [
        ("L1", True),
        ("L2", False),
        ("L3", True),
    ]
    assert (panels[0].type, panels[0].x, panels[0].w) == ("timeseries", 3, 4)
    assert panels[0].targets[0].expr == "up" and panels[2].title == "From elements"


def test_v2_annotations_are_an_array_and_classic_ones_an_object() -> None:
    v2 = {
        "elements": {},
        "layout": {"kind": "GridLayout", "spec": {"items": []}},
        "annotations": [
            {
                "kind": "AnnotationQuery",
                "spec": {
                    "name": "deploys",
                    "enable": False,
                    "builtIn": False,
                    "query": {"kind": "DataQuery", "group": "loki", "spec": {"expr": '{a="b"}'}},
                },
            },
            "junk",
        ],
    }
    ann = load_data(v2, "v2").dashboards[0].annotations
    assert [(a.name, a.ds_type, a.enabled) for a in ann] == [("deploys", "loki", False)]


def test_a_dashboard_that_fails_to_normalise_is_unreadable_and_the_rest_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = classic_reader.read_classic

    def broken(root: dict[str, Any], *args: Any, **kwargs: Any) -> models.Dashboard:
        if root.get("title") == "boom":
            raise ValueError("secret detail that must not be shown")
        return real(root, *args, **kwargs)

    monkeypatch.setattr(load, "read_classic", broken)
    estate = load_data([classic([], title="boom"), classic([], title="fine")], "x")
    assert [d.title for d in estate.dashboards] == ["fine"]
    assert [u.reason for u in estate.unreadable] == [models.U_INVALID]
    assert "secret" not in estate.unreadable[0].model_dump_json()
