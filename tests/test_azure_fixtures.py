"""Azure fixtures: public examples plus labelled synthetic cases; valid JSON, all documented."""

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
FILES = sorted(p for p in FIXTURES.rglob("*.json"))


def _listed() -> set[str]:
    text = (FIXTURES / "SOURCES.md").read_text(encoding="utf-8")
    return set(re.findall(r"`([a-z_]+/[^`\s]+\.json)`", text))


def _rel(path: Path) -> str:
    return path.relative_to(FIXTURES).as_posix()


def test_every_fixture_is_valid_json() -> None:
    assert FILES, "no Azure fixtures found"
    for path in FILES:
        json.loads(path.read_text(encoding="utf-8"))


def test_sources_lists_every_fixture_and_nothing_else() -> None:
    on_disk = {_rel(p) for p in FILES}
    listed = _listed()
    assert on_disk - listed == set(), "fixtures missing from SOURCES.md"
    assert listed - on_disk == set(), "SOURCES.md lists files that do not exist"


def test_sources_records_licence_and_pinned_commits() -> None:
    text = (FIXTURES / "SOURCES.md").read_text(encoding="utf-8")
    assert "MIT" in text
    assert "939d1a81ec5159e61394cd8ca2589c2c0ce49ccd" in text


def test_upstream_files_have_a_source_url() -> None:
    text = (FIXTURES / "SOURCES.md").read_text(encoding="utf-8")
    for path in FILES:
        rel = _rel(path)
        if rel.startswith("synthetic/"):
            continue
        row = next(line for line in text.splitlines() if line.startswith(f"| `{rel}`"))
        assert "https://raw.githubusercontent.com/" in row, rel


def test_synthetic_files_are_labelled_and_free_of_real_ids() -> None:
    guid = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
    placeholder = re.compile(r"00000000-0000-0000-0000-0{9}[0-9]{3}")
    synthetic = [p for p in FILES if _rel(p).startswith("synthetic/")]
    assert len(synthetic) >= 15
    for path in synthetic:
        assert path.name.startswith("SYNTHETIC__"), path.name
        raw = path.read_text(encoding="utf-8")
        assert "SYNTHETIC" in json.loads(raw)["_synthetic"]
        for found in guid.findall(raw):
            assert placeholder.fullmatch(found), (path.name, found)


def test_no_signed_url_secret_left_in_upstream_copies() -> None:
    for path in FILES:
        assert "sig=Abpsb72" not in path.read_text(encoding="utf-8"), path.name


def test_fixture_counts_meet_the_plan() -> None:
    def count(folder: str) -> int:
        return sum(1 for p in FILES if _rel(p).startswith(folder + "/"))

    assert count("metric_alerts") >= 15
    assert count("scheduled_query_rules") >= 10
    assert count("activity_log_alerts") >= 8
    assert count("action_groups") >= 8
    assert count("alert_processing_rules") >= 5
    assert count("prometheus_rule_groups") >= 3
    assert count("dashboards") >= 1 and count("workbooks") >= 1
