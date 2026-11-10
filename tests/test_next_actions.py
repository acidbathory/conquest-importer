"""The backlog must say what is done and when each number was measured."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "docs" / "improvement" / "next-actions.md").read_text(encoding="utf-8")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _section(title: str) -> list[str]:
    lines = TEXT.splitlines()
    start = lines.index(f"## {title}")
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return body


def _rows(title: str) -> list[list[str]]:
    rows = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in _section(title)
        if line.startswith("|") and not line.startswith("|---")
    ]
    return rows[1:]  # drop the header


def test_done_section_lists_work_with_a_commit() -> None:
    rows = _rows("Done")
    assert len(rows) >= 7
    for row in rows:
        assert re.search(r"\b[0-9a-f]{7}\b|improve/|docs/research/", " ".join(row)), row


def test_every_backlog_item_cites_a_measurement_date() -> None:
    rows = _rows("Ranked backlog")
    assert len(rows) >= 8
    for row in rows:
        assert DATE.search(row[-1]), f"item {row[0]} has no measurement date"
