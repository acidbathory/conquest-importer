"""Reading Platform and classic Dynatrace dashboards: shapes, per-object validation, caps."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.dynatrace.dashboards import models as m

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"
PLATFORM_FILES = {
    "aws-firehose": (12, 4),
    "azure-app-service-plan": (27, 7),
    "host-vulnerability": (37, 5),
    "kubernetes-statistics": (17, 2),
}


def one(raw: Any, tmp_path: Path, name: str = "d.json") -> list[m.Document]:
    path = tmp_path / name
    path.write_text(json.dumps(raw))
    return m.read_file(path)


@pytest.mark.parametrize(("stem", "counts"), sorted(PLATFORM_FILES.items()))
def test_the_real_platform_dashboards_are_read_whole(stem: str, counts: tuple[int, int]) -> None:
    (doc,) = m.read_file(FIXTURES / f"{stem}.json")
    assert doc.kind == "platform" and doc.platform is not None
    assert (len(doc.platform.tiles), len(doc.platform.variables)) == counts
    assert doc.platform.version == 21 and not doc.platform.unreadable
    assert doc.name == stem  # a bare dashboard has no name; the file name stands in


def test_a_tile_keeps_the_fields_the_planner_needs() -> None:
    (doc,) = m.read_file(FIXTURES / "kubernetes-statistics.json")
    assert doc.platform is not None
    tile = next(t for t in doc.platform.tiles if t.id == "4")
    assert tile.kind == "data" and tile.visualization == "lineChart"
    assert tile.query.startswith("timeseries")
    assert tile.layout == m.Layout(x=12, y=7, w=12, h=7)
    assert not tile.tile_timeframe  # present in the file but switched off


def test_the_classic_dashboard_is_read() -> None:
    (doc,) = m.read_file(FIXTURES / "classic-dashboards" / "classic-ops.json")
    assert doc.kind == "classic" and doc.classic is not None
    assert doc.name == "Ops overview"
    assert len(doc.classic.tiles) == 7 and doc.classic.management_zone
    assert doc.classic.filters == 2
    assert [t.tile_type for t in doc.classic.tiles][:3] == ["MARKDOWN", "HEADER", "DATA_EXPLORER"]
    assert doc.classic.tiles[2].metrics == ["builtin:host.cpu.usage"]
    assert doc.classic.tiles[5].management_zone


def test_an_extract_envelope_carries_the_name(tmp_path: Path) -> None:
    content = {"version": 21, "variables": [], "tiles": {}, "layouts": {}}
    envelope = {
        "id": "x",
        "name": "My board",
        "type": "dashboard",
        "owner": "u1",
        "content": content,
    }
    (doc,) = one(envelope, tmp_path)
    assert (doc.kind, doc.name) == ("platform", "My board")


def test_one_bad_tile_does_not_stop_the_others(tmp_path: Path) -> None:
    raw = {
        "version": 21,
        "tiles": {
            "a": {"type": "markdown", "content": "hi"},
            "b": "not a tile",
            "c": {"title": "no type"},
            "d": {"type": "data", "query": "x" * (m.MAX_TEXT + 1)},
            "e": {"type": "data", "query": "timeseries avg(m)", "visualization": "lineChart"},
        },
        "variables": [{"key": "ok", "type": "text"}, 5, {"type": "query"}],
    }
    (doc,) = one(raw, tmp_path)
    assert doc.platform is not None
    assert [t.id for t in doc.platform.tiles] == ["a", "e"]
    assert sorted((u.ref, u.code) for u in doc.platform.unreadable) == [
        ("b", m.NOT_AN_OBJECT),
        ("c", m.BAD_FIELD),
        ("d", m.TOO_LARGE),
        ("variable 1", m.NOT_AN_OBJECT),
        ("variable 2", m.BAD_FIELD),
    ]
    assert [v.key for v in doc.platform.variables] == ["ok"]


def test_more_tiles_than_the_cap_are_counted_not_read(tmp_path: Path) -> None:
    tiles = {str(i): {"type": "markdown", "content": "x"} for i in range(m.MAX_TILES + 25)}
    (doc,) = one({"version": 21, "tiles": tiles}, tmp_path)
    assert doc.platform is not None and len(doc.platform.tiles) == m.MAX_TILES
    assert [u.code for u in doc.platform.unreadable] == [m.TOO_MANY]


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("{not json", m.NOT_JSON),
        ("[" * 100_000, m.TOO_DEEP),  # refused by our depth check, whatever the interpreter does
        ('{"a":' * 80 + "1" + "}" * 80, m.TOO_DEEP),
    ],
)
def test_hostile_files_become_unreadable_documents(text: str, code: str, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(text)
    (doc,) = m.read_file(path)
    assert (doc.kind, doc.error) == ("unreadable", code)


@pytest.mark.parametrize("raw", [5, "text", None, {"tiles": []}, {"hello": 1}])
def test_json_that_is_not_a_dashboard_is_unreadable(raw: Any, tmp_path: Path) -> None:
    (doc,) = one(raw, tmp_path)
    assert doc.kind == "unreadable"


def test_a_list_file_gives_one_document_per_item(tmp_path: Path) -> None:
    good = {"version": 21, "tiles": {}}
    docs = one([good, 7, good], tmp_path)
    assert [d.kind for d in docs] == ["platform", "unreadable", "platform"]
    assert [d.ref for d in docs] == ["d.json[0]", "d.json[1]", "d.json[2]"]


def test_exact_copies_are_counted_once(tmp_path: Path) -> None:
    board = {"version": 21, "tiles": {"1": {"type": "markdown", "content": "x"}}}
    (tmp_path / "a.json").write_text(json.dumps(board))
    (tmp_path / "b.json").write_text(json.dumps(board))
    (tmp_path / "c.json").write_text(json.dumps({**board, "tiles": {}}))
    docs, skipped = m.load_documents(tmp_path)
    assert len(docs) == 2 and len(skipped) == 1


def test_a_folder_is_read_with_the_extractor_sub_folders() -> None:
    docs, skipped = m.load_documents(FIXTURES)
    assert sorted(d.kind for d in docs) == ["classic"] + ["platform"] * 4 and skipped == []


def test_an_oversized_file_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "MAX_INPUT_BYTES", 10)
    (doc,) = one({"version": 21, "tiles": {}}, tmp_path)
    assert doc.error == m.TOO_LARGE
