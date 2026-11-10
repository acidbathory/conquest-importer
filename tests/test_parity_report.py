"""The four parity outputs: schema, escaping and the labels file."""

import json
from datetime import UTC, datetime
from pathlib import Path

from importer.parity.model import Item, Outcome, Pair, SeriesResult, Verdict
from importer.parity.report import labels_json, summary, to_html, to_json, to_markdown, write_all

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
EVIL = "<script>alert(1)</script>|`x`\n[a](javascript:alert(1))"


def item(verdict: Verdict, title: str = "Requests", kind: str = "widget", **kw: object) -> Item:
    pair = Pair(kind, EVIL, "uid1", title, "prometheus", "up", "SELECT 1 FROM Metric", True, ())
    outcome = Outcome(verdict, "because", 58, 57, 0.01, [SeriesResult({EVIL: EVIL}, 58, 57, 0.01)])
    for k, v in kw.items():
        setattr(outcome, k, v)
    return Item(pair, outcome)


def test_json_has_exactly_the_scoreboard_schema() -> None:
    items = [
        item(Verdict.VERIFIED),
        item(Verdict.NO_DATA, "b"),
        item(Verdict.NOT_COMPARABLE, "c", "alert"),
    ]
    data = json.loads(to_json(items, 6, 60, NOW))
    assert set(data) == {"generated", "window_hours", "step_seconds", "items", "summary"}
    assert set(data["items"][0]) == {
        "id",
        "kind",
        "verdict",
        "reason",
        "points",
        "within_tolerance",
    }
    assert data["items"][1]["verdict"] == "NO_DATA" and data["items"][2]["kind"] == "alert"
    assert data["summary"] == {
        "VERIFIED": 1,
        "CLOSE": 0,
        "MISMATCH": 0,
        "NO_DATA": 1,
        "NOT_COMPARABLE": 1,
    }
    assert 0 <= data["items"][0]["within_tolerance"] <= 1
    assert data["items"][0]["id"].startswith("uid1:")


def test_markdown_and_html_escape_hostile_text() -> None:
    items = [item(Verdict.MISMATCH)]
    md = to_markdown(items, 6, 60, 0.05, NOW)
    html = to_html(items, 6, 60, 0.05, NOW)
    assert "<script" not in md and "](" not in md and "`x`" not in md
    assert "<script" not in html and "Grafana to New Relic" in html
    assert "Limits of this check" in md and "no data" in md.lower()
    assert "Grafana to New Relic" in md


def test_labels_hold_only_verified_widgets() -> None:
    items = [item(Verdict.VERIFIED), item(Verdict.CLOSE, "b"), item(Verdict.VERIFIED, "c", "alert")]
    labels = json.loads(labels_json(items, NOW))
    assert [x["widget"] for x in labels["verified"]] == ["Requests"]
    assert labels["verified"][0]["verified_on"] == "2026-11-10"
    assert len(labels["verified"][0]["nrql_sha256"]) == 16


def test_write_all_creates_the_four_files(tmp_path: Path) -> None:
    paths = write_all(tmp_path / "parity", [item(Verdict.VERIFIED)], 6, 60, 0.05, NOW)
    assert {p.name for p in paths} == {
        "parity.json",
        "parity.md",
        "parity.html",
        "verified-labels.json",
    }
    assert all(p.exists() for p in paths)
    assert summary([]) == dict.fromkeys(
        ["VERIFIED", "CLOSE", "MISMATCH", "NO_DATA", "NOT_COMPARABLE"], 0
    )
