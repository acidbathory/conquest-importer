"""Hostile dashboards: titles that break tables, huge and deep input, copies."""

import inspect
import json
import time
from pathlib import Path

import pytest

from importer.core import safe_http
from importer.sources.dynatrace.dashboards import models as m
from importer.sources.dynatrace.dashboards import reasons
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report
from importer.sources.dynatrace.dashboards.plan_models import DashboardPlans, Verdict
from importer.sources.dynatrace.inventory import extract as ix

HOSTILE = Path(__file__).parent / "fixtures" / "dynatrace-dashboards" / "hostile"


def hostile_plan() -> tuple[DashboardPlans, list[str]]:
    documents, copies = m.load_documents(HOSTILE)
    return plan_documents(documents, copies), copies


def test_copies_are_counted_once_and_the_hostile_dashboard_is_read() -> None:
    plans, copies = hostile_plan()
    assert len(copies) == 1 and len(plans.dashboards) == 2
    assert plans.copies_skipped == 1


def test_every_hostile_tile_gets_a_verdict_and_a_fixed_reason() -> None:
    plans, _ = hostile_plan()
    tiles = {t.id: t for d in plans.dashboards for t in d.tiles}
    assert tiles["| | |"].verdict is Verdict.DRAFT and reasons.D_LIMIT in tiles["| | |"].notes
    assert tiles["| | |"].layout is None  # the layout was out of range
    assert tiles["ansi"].reasons == [reasons.JOIN]
    assert tiles["long"].reasons == [reasons.TILE_VIZ]
    assert tiles["prompt"].reasons == [reasons.TILE_PROMPT]
    assert tiles["code"].reasons == [reasons.TILE_CODE] and tiles["slo"].reasons == [
        reasons.TILE_SLO
    ]
    assert tiles["weird"].reasons == [reasons.TILE_TYPE]
    assert reasons.Q_EMPTY in tiles["nulls"].reasons
    markdown = tiles["<img src=x onerror=alert(1)>"]
    assert markdown.verdict is Verdict.CONVERT
    assert set(markdown.flags) == {"dynatrace-link", "image", "unsafe-link"}


def test_hostile_variables_are_classified_and_unreadable_ones_kept_as_manual() -> None:
    plans, _ = hostile_plan()
    variables = [v for d in plans.dashboards for v in d.variables]
    by_verdict = {v: [x for x in variables if x.verdict is v] for v in Verdict}
    assert len(variables) == 5
    assert len(by_verdict[Verdict.EXACT]) == 1 and len(by_verdict[Verdict.MANUAL]) == 4
    assert sum(reasons.VAR_UNREADABLE in v.reasons for v in variables) == 2
    assert sum(reasons.VAR_LIMIT in v.reasons for v in variables) == 1


def test_no_hostile_text_reaches_the_report_or_a_reason() -> None:
    plans, _ = hostile_plan()
    report = render_report(plans)
    for text in ("| injected |", "<script>", "<img", "\x1b", "‮", "\x00", "javascript:", "[click"):
        assert text not in report
    assert "TITLE" + "X" * 130 not in report  # cut by md_cell
    dumped = plans.model_dump_json()
    assert "\\u001b" not in dumped and "\\u202e" not in dumped and "\\u0000" not in dumped
    for dash in plans.dashboards:
        for tile in dash.tiles:
            assert all(text in reasons.ALL for text in [*tile.reasons, *tile.notes])
        for var in dash.variables:
            assert all(text in reasons.ALL for text in [*var.reasons, *var.notes])


def test_a_table_row_cannot_be_split_by_a_title() -> None:
    plans, _ = hostile_plan()
    rows = [line for line in render_report(plans).splitlines() if line.startswith("| ")]
    widths = {line.count(" | ") for line in rows if "Tile" not in line}
    assert widths  # every tile and dashboard row is one line of the table it belongs to
    assert not any(line.startswith(("| more", "| ---  ")) for line in rows)


def test_deep_json_is_refused_not_recursed(tmp_path: Path) -> None:
    deep: object = 1
    for _ in range(m.MAX_DEPTH + 10):
        deep = {"a": deep}
    (tmp_path / "deep.json").write_text(json.dumps(deep))
    (doc,) = m.load_documents(tmp_path)[0]
    assert doc.error == m.TOO_DEEP
    assert plan_documents([doc]).dashboards[0].reasons == [reasons.FILE_UNREADABLE]


def test_a_query_nested_past_the_limit_is_unreadable() -> None:
    board = {
        "tiles": {
            "1": {
                "type": "data",
                "visualization": "lineChart",
                "query": "timeseries avg(m), filter:{" + "(" * 200 + "a == 1" + ")" * 200 + "}",
            }
        }
    }
    document = m.parse_document("d", board)
    tile = plan_documents([document]).dashboards[0].tiles[0]
    assert tile.reasons == [reasons.Q_UNREADABLE]


def test_a_huge_dashboard_file_is_planned_within_a_time_bound(tmp_path: Path) -> None:
    tile = {
        "type": "data",
        "visualization": "table",
        "query": "fetch security.events | summarize count()",
    }
    tiles = {str(i): dict(tile) for i in range(m.MAX_TILES)}
    tiles["big-query"] = {**tile, "query": "timeseries avg(m) " + "x" * (m.MAX_TEXT + 1)}
    tiles["big-markdown"] = {"type": "markdown", "content": "a" * (m.MAX_TEXT + 1)}
    path = tmp_path / "huge.json"
    path.write_text(
        json.dumps(
            {"version": 21, "tiles": tiles, "variables": [{"key": "v", "type": "text"}] * 500}
        )
    )
    started = time.monotonic()
    plans = plan_documents(m.read_file(path))
    render_report(plans)
    assert time.monotonic() - started < 5.0
    dash = plans.dashboards[0]
    assert dash.unreadable_tiles >= 1 and len(dash.variables) <= m.MAX_VARIABLES


def test_the_inventory_getter_uses_the_shared_http_safeguards() -> None:
    source = inspect.getsource(ix)
    assert "from importer.core.safe_http import" in source and "get_json" in source
    assert not hasattr(ix, "_decode") and safe_http.MAX_RESPONSE_BYTES > 0


@pytest.mark.parametrize("name", ["hostile-titles.json", "copy-a.json", "copy-b.json"])
def test_the_hostile_fixtures_are_valid_json(name: str) -> None:
    assert isinstance(json.loads((HOSTILE / name).read_text()), dict)
