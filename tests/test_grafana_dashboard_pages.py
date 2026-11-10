"""Dashboards over the 150-widget page limit: split at Grafana rows into pages, end to end."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core.apply import GuardError
from importer.core.apply_dashboards import DashboardItem, DashboardPlan, with_account
from importer.core.dashboard_ir import MAX_PAGES, PAGE_WIDGETS, IRDashboard
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import dashboard_resource, nerdgraph_dashboard
from importer.sources import DASHBOARD_REGISTRY

HOSTILE = '"; ${x} {{v}} \\ `r` <b>'


def text_panel(i: int, y: int) -> dict[str, Any]:
    return {
        "id": i + 1,
        "type": "text",
        "title": f"t{i}",
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 2},
        "options": {"content": "hi", "mode": "markdown"},
    }


def row(i: int, y: int, title: str) -> dict[str, Any]:
    return {
        "id": 10_000 + i,
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def convert(panels: list[dict[str, Any]], tmp_path: Path, title: str = "Big") -> IRDashboard:
    body = {"uid": "u", "title": title, "schemaVersion": 39, "panels": panels}
    (tmp_path / "d.json").write_text(json.dumps(body))
    found = DASHBOARD_REGISTRY["grafana"].convert(tmp_path)
    assert len(found) == 1
    return found[0]


def flat(count: int) -> list[dict[str, Any]]:
    return [text_panel(i, i * 2) for i in range(count)]


def sections(rows: int, each: int, name: str = "Section @") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    y = 0
    for r in range(rows):
        out.append(row(r, y, name.replace("@", str(r))))
        y += 1
        for i in range(each):
            out.append(text_panel(r * each + i, y))
            y += 2
    return out


def pages(d: IRDashboard) -> dict[str, int]:
    nerd = nerdgraph_dashboard(d)["variables"]["dashboard"]["pages"]
    return {p["name"]: len(p["widgets"]) for p in nerd}


@pytest.mark.parametrize("count", [160, 300, 3000])
def test_flat_dashboards_are_split_in_pages_of_150(count: int, tmp_path: Path) -> None:
    d = convert(flat(count), tmp_path)
    sizes = pages(d)
    assert sum(sizes.values()) == count and max(sizes.values()) <= PAGE_WIDGETS
    assert len(sizes) == -(-count // PAGE_WIDGETS) and list(sizes)[:2] == ["Big", "Big part 2"]
    assert not any("left out" in x for x in d.reasons)
    assert any("split over" in x for x in d.reasons)


def test_rows_become_pages_named_after_the_row(tmp_path: Path) -> None:
    d = convert(sections(4, 60), tmp_path)
    assert pages(d) == {f"Section {i}": 61 for i in range(4)}
    hcl = dashboard_resource(d, 1)
    assert hcl.count("  page {") == 4 and 'name = "Section 3"' in hcl


def test_more_than_25_pages_are_dropped_with_a_note(tmp_path: Path) -> None:
    d = convert(flat(3900), tmp_path)
    assert sum(pages(d).values()) == PAGE_WIDGETS * MAX_PAGES
    assert any("50 widgets over the limit of 25 pages" in x for x in d.reasons)


def test_hostile_row_titles_are_escaped_in_both_outputs(tmp_path: Path) -> None:
    d = convert(sections(3, 60, HOSTILE + " @"), tmp_path)
    hcl = dashboard_resource(d, 1)
    assert '"; ${x}' not in hcl  # the quote and the interpolation are escaped
    assert "$${x}" in hcl and '\\"; ' in hcl
    names = list(pages(d))
    assert len(names) == 3 and all(HOSTILE in n for n in names)  # JSON carries it as plain data


def test_apply_fills_the_account_on_every_page(tmp_path: Path) -> None:
    payload = nerdgraph_dashboard(convert(flat(160), tmp_path))
    sent = with_account(payload, 42)
    assert "ACCOUNT_ID_PLACEHOLDER" not in json.dumps(sent)
    assert len(sent["variables"]["dashboard"]["pages"]) == 2
    plan = DashboardPlan(items=[DashboardItem(name="x", widgets=160, payload=payload)], skipped=0)
    assert plan.items[0].widgets == 160


def test_unexpected_placeholder_on_a_later_page_is_refused(tmp_path: Path) -> None:
    payload = nerdgraph_dashboard(convert(flat(160), tmp_path))
    payload["variables"]["dashboard"]["pages"][1]["widgets"][0]["rawConfiguration"] = {
        "nrqlQueries": [{"accountIds": ["other"], "query": "x"}]
    }
    with pytest.raises(GuardError):
        with_account(payload, 1)


def test_the_emitter_refuses_a_page_over_the_limit(tmp_path: Path) -> None:
    d = convert(flat(150), tmp_path)
    twice = d.model_copy(update={"widgets": d.widgets * 2})  # not re-validated
    named = [w.model_copy(update={"page": "p"}) for w in twice.widgets]
    with pytest.raises(CannotEmit):
        nerdgraph_dashboard(d.model_copy(update={"widgets": named}))


def test_small_dashboards_are_unchanged(tmp_path: Path) -> None:
    d = convert(flat(10), tmp_path)
    assert all(w.page is None for w in d.widgets)
    assert "page" not in d.model_dump()["widgets"][0]
    assert list(pages(d)) == ["Big"]
