"""The improvement loop's measuring half: rates, regressions and the history file."""

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from importer.cli import app
from importer.core import improve

ROOT = Path(__file__).parent.parent


def result(total: int, converted: int, unguided: list[str] | None = None) -> dict[str, Any]:
    return {
        "measured": "t",
        "sources": {
            "gcp": {
                "total": total,
                "converted": converted,
                "rate": converted / total,
                "unguided": unguided or [],
                "top_blockers": [],
            }
        },
    }


def test_measure_covers_every_source_with_sane_numbers() -> None:
    sources = improve.measure(ROOT)["sources"]
    assert set(sources) == {"cloudwatch", "gcp", "dynatrace", "dynatrace-classic", "grafana"}
    for s in sources.values():
        assert 0 < s["converted"] <= s["total"] and 0 <= s["rate"] <= 1


def test_every_blocker_in_the_fixtures_has_guidance() -> None:
    # the loop treats a reason with no manual steps as the first thing to fix
    for s in improve.measure(ROOT)["sources"].values():
        assert s["unguided"] == []


def test_fewer_conversions_on_the_same_estate_is_a_regression() -> None:
    problems = improve.compare(result(10, 8), result(10, 7))
    assert [p.what for p in problems] == ["converted 7 of 10, was 8"]


def test_a_lower_rate_on_a_different_estate_is_a_regression() -> None:
    assert improve.compare(result(10, 8), result(20, 14))


def test_equal_or_better_is_not_a_regression() -> None:
    assert not improve.compare(result(10, 8), result(10, 8))
    assert not improve.compare(result(10, 8), result(12, 11))


def test_a_new_reason_without_guidance_is_a_regression() -> None:
    problems = improve.compare(result(10, 8), result(10, 8, ["odd new reason"]))
    assert "no guidance for: odd new reason" in str(problems[0])


def test_a_lost_source_is_a_regression() -> None:
    assert improve.compare(result(10, 8), {"sources": {}})


def test_history_is_appended_one_object_per_line(tmp_path: Path) -> None:
    history = tmp_path / "d" / "history.jsonl"
    improve.record(history, result(1, 1))
    improve.record(history, result(2, 1))
    lines = history.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["sources"]["gcp"]["total"] == 2


def test_the_committed_baseline_still_holds() -> None:
    baseline = ROOT / "docs" / "improvement" / "baseline.json"
    out = CliRunner().invoke(
        app, ["improve-measure", "--root", str(ROOT), "--baseline", str(baseline)]
    )
    assert out.exit_code == 0, out.output
