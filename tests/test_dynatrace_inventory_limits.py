"""Each limit is tested at its exact edge, with the number written out (not read from the code)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.sources.dynatrace.dashboards import models as dm
from importer.sources.dynatrace.dashboards import reasons
from importer.sources.dynatrace.dashboards.dql_rules import analyse
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report
from importer.sources.dynatrace.inventory import extract as ix
from importer.sources.dynatrace.inventory import models as im

HOST = "https://abc12345.apps.dynatrace.com"
TOKEN = "dt0s16.AAAAAAAA.SECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests


def nested(depth: int) -> object:
    value: object = 1
    for _ in range(depth - 1):
        value = {"a": value}
    return value  # a dict nested `depth` levels deep, counting the innermost value


@pytest.mark.parametrize(("depth", "refused"), [(64, False), (65, True)])
def test_json_nesting_of_a_dashboard_file_is_limited_to_64(
    depth: int, refused: bool, tmp_path: Path
) -> None:
    (tmp_path / "d.json").write_text(json.dumps(nested(depth)))
    (doc,) = dm.read_file(tmp_path / "d.json")
    assert (doc.error == dm.TOO_DEEP) is refused


@pytest.mark.parametrize(("depth", "refused"), [(64, False), (65, True)])
def test_inventory_read_json_refuses_only_beyond_64(
    depth: int, refused: bool, tmp_path: Path
) -> None:
    (tmp_path / "x.json").write_text(json.dumps(nested(depth)))
    if refused:
        with pytest.raises(ValueError, match="nested more deeply"):
            im.read_json(tmp_path / "x.json")
    else:
        assert isinstance(im.read_json(tmp_path / "x.json"), dict)


@pytest.mark.parametrize(("depth", "refused"), [(64, False), (65, True)])
def test_bracket_nesting_of_a_query_is_limited_to_64(depth: int, refused: bool) -> None:
    query = "timeseries avg(m), filter:{" + "(" * (depth - 1) + "a == 1" + ")" * (depth - 1) + "}"
    # the braces are one level, so depth - 1 parentheses make `depth` levels in all
    assert (reasons.Q_UNREADABLE in analyse(query).blockers) is refused


@pytest.mark.parametrize(("size", "refused"), [(100_000, False), (100_001, True)])
def test_a_query_is_limited_to_100000_characters(size: int, refused: bool) -> None:
    padded = "timeseries avg(m)" + " " * (size - len("timeseries avg(m)"))
    assert (reasons.Q_UNREADABLE in analyse(padded).blockers) is refused


@pytest.mark.parametrize(("size", "unreadable"), [(100_000, 0), (100_001, 1)])
def test_a_tile_text_is_limited_to_100000_characters(size: int, unreadable: int) -> None:
    doc = dm.parse_document("d", {"tiles": {"1": {"type": "markdown", "content": "a" * size}}})
    assert doc.platform is not None and len(doc.platform.unreadable) == unreadable


@pytest.mark.parametrize(("count", "kept"), [(500, 500), (501, 500)])
def test_a_dashboard_keeps_at_most_500_tiles(count: int, kept: int) -> None:
    tiles = {str(i): {"type": "markdown", "content": "x"} for i in range(count)}
    doc = dm.parse_document("d", {"tiles": tiles})
    assert doc.platform is not None and len(doc.platform.tiles) == kept
    assert len(doc.platform.unreadable) == (1 if count > 500 else 0)


@pytest.mark.parametrize(("count", "kept"), [(100, 100), (101, 100)])
def test_a_dashboard_keeps_at_most_100_variables(count: int, kept: int) -> None:
    variables = [{"key": f"v{i}", "type": "text"} for i in range(count)]
    doc = dm.parse_document("d", {"tiles": {}, "variables": variables})
    assert doc.platform is not None and len(doc.platform.variables) == kept


@pytest.mark.parametrize(
    ("field", "value", "valid"),
    [
        ("x", 0, True), ("x", 10_000, True), ("x", 10_001, False), ("x", -1, False),
        ("y", 0, True), ("y", 100_000, True), ("y", 100_001, False), ("y", -1, False),
        ("w", 1, True), ("w", 10_000, True), ("w", 10_001, False), ("w", 0, False),
        ("h", 1, True), ("h", 100_000, True), ("h", 100_001, False), ("h", 0, False),
    ],
)  # fmt: skip
def test_each_layout_field_has_its_exact_range(field: str, value: int, valid: bool) -> None:
    layout = {"x": 0, "y": 0, "w": 1, "h": 1, field: value}
    tile = dm.parse_tile("t", {"type": "markdown"}, layout)
    assert isinstance(tile, dm.Tile) and (tile.layout is not None) is valid


def test_the_report_escapes_a_dashboard_name_in_the_summary_table() -> None:
    doc = dm.parse_document("d", {"name": "a|b<b>x</b>[l](u)", "tiles": {}})
    report = render_report(plan_documents([doc]))
    assert "a|b" not in report and "<b>" not in report and "[l](u)" not in report


def test_a_list_inside_an_entity_is_limited_to_20_names() -> None:
    entity = im.parse_entity(
        {"type": "SERVICE", "properties": {"serviceTechnologyTypes": [f"T{i}" for i in range(30)]}}
    )
    assert entity is not None and len(entity.technologies) == 20


def make_board(tiles: int) -> dict[str, Any]:
    return {"tiles": {str(i): {"type": "markdown", "content": "x"} for i in range(tiles)}}


@pytest.mark.parametrize(("count", "saved"), [(500, 500), (501, 500)])
def test_the_extractor_saves_at_most_500_tiles_per_dashboard(
    count: int, saved: int, tmp_path: Path
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/platform/document/v1/documents":
            return httpx.Response(
                200, json={"documents": [{"id": "0" * 8 + "-0000-0000-0000-" + "0" * 12}]}
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, json=make_board(count))
        return httpx.Response(404, json={})

    with ix.http_getter(TOKEN, transport=httpx.MockTransport(handler)) as get:
        ix.extract(get, HOST, tmp_path / "out")
    envelope = json.loads(next((tmp_path / "out/dashboards").glob("*.json")).read_text())
    assert len(envelope["content"]["tiles"]) == saved


@pytest.mark.parametrize(("length", "followed"), [(2000, True), (2001, False)])
def test_a_page_key_is_limited_to_2000_characters(
    length: int, followed: bool, tmp_path: Path
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/platform/document/v1/documents":
            body: dict[str, Any] = {"documents": []}
            if "page-key" not in request.url.params:
                body["nextPageKey"] = "k" * length
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={})

    with ix.http_getter(TOKEN, transport=httpx.MockTransport(handler)) as get:
        result = ix.extract(get, HOST, tmp_path / "out")
    pages = [r for r in seen if r.url.path == "/platform/document/v1/documents"]
    assert (len(pages) == 2) is followed
    assert any("too long" in w for w in result.warnings) is not followed


def test_the_run_stops_after_400_list_pages_in_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ix, "MAX_PAGES", 1000)  # so the total, not the per-list cap, ends it
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/platform/document/v1/documents":
            return httpx.Response(200, json={"documents": [], "nextPageKey": "again"})
        return httpx.Response(200, json={})

    with ix.http_getter(TOKEN, transport=httpx.MockTransport(handler)) as get:
        result = ix.extract(get, HOST, tmp_path / "out")
    documents = [r for r in seen if r.url.path == "/platform/document/v1/documents"]
    assert len(documents) == 400
    assert any("more than 400 pages in total" in w for w in result.warnings)
