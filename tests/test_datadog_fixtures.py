"""The Datadog research fixtures: valid JSON, complete provenance, and the synthetic marking.

`real/` files are copies of BSD-3-Clause integration assets (see SOURCES.md); `synthetic/` files
are hostile hand-written cases. Nothing here parses Datadog semantics; that is the future source's
job. These tests only keep the corpus honest so later work can rely on it.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parent / "fixtures" / "datadog"
SOURCES = ROOT / "SOURCES.md"
SHA = re.compile(r"^[0-9a-f]{40}$")
ROW = re.compile(r"^\| (real/\S+) \| (\S+) \| (\S+) \| (\S+) \| (\S+) \| ([0-9a-f]{64}) \|$")


def _real() -> list[Path]:
    return sorted((ROOT / "real").rglob("*.json"))


def _synthetic() -> list[Path]:
    return sorted((ROOT / "synthetic").rglob("*.json"))


def _rows() -> dict[str, tuple[str, str, str, str, str]]:
    rows: dict[str, tuple[str, str, str, str, str]] = {}
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3), m.group(4), m.group(5), m.group(6))
    return rows


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_counts_meet_the_brief() -> None:
    monitors = [p for p in _real() if p.parent.name == "monitors"]
    dashboards = [p for p in _real() if p.parent.name == "dashboards"]
    assert len(monitors) >= 60
    assert len(dashboards) >= 25
    assert len(_synthetic()) >= 15


@pytest.mark.parametrize("path", _real() + _synthetic(), ids=lambda p: p.name)
def test_every_fixture_is_valid_json(path: Path) -> None:
    assert isinstance(_load(path), dict)


def test_every_real_file_has_a_complete_sources_row() -> None:
    rows = _rows()
    names = {str(p.relative_to(ROOT)) for p in _real()}
    assert names == set(rows), "SOURCES.md rows and real/ files differ"
    for name, (repo, path, sha, licence, digest) in rows.items():
        assert repo == "DataDog/integrations-core"
        assert "/assets/" in path and path.endswith(".json")
        assert SHA.match(sha)
        assert licence == "BSD-3-Clause"
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name


def test_sources_states_the_licence_and_the_commit() -> None:
    text = SOURCES.read_text(encoding="utf-8")
    assert "BSD 3-Clause" in text
    assert "Copyright (c) 2016, Datadog, Inc." in text
    commits = {row[2] for row in _rows().values()}
    assert len(commits) == 1
    assert commits.pop() in text


def test_every_synthetic_file_is_marked_and_listed() -> None:
    text = SOURCES.read_text(encoding="utf-8")
    for path in _synthetic():
        assert path.name.startswith("SYNTHETIC_")
        assert str(_load(path).get("_synthetic", "")).startswith("SYNTHETIC")
        assert f"synthetic/{path.name}" in text


def test_real_monitors_have_the_template_shape() -> None:
    for path in (p for p in _real() if p.parent.name == "monitors"):
        data = _load(path)
        assert {"title", "definition"} <= set(data), path.name
        assert isinstance(data["definition"].get("query"), str), path.name
        assert isinstance(data["definition"].get("type"), str), path.name


def test_real_dashboards_have_widgets() -> None:
    """A few shipped templates are empty placeholders (`widgets: []`); most must have content."""
    populated = 0
    for path in (p for p in _real() if p.parent.name == "dashboards"):
        data = _load(path)
        assert isinstance(data.get("widgets"), list), path.name
        assert isinstance(data.get("title"), str), path.name
        populated += bool(data["widgets"])
    assert populated >= 25


def test_no_fixture_carries_a_real_looking_secret() -> None:
    pattern = re.compile(r"AKIA[0-9A-Z]{16}|BEGIN (RSA |EC )?PRIVATE KEY|xox[bp]-\d")
    for path in _real() + _synthetic():
        assert not pattern.search(path.read_text(encoding="utf-8")), path.name
