from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.ir import Confidence
from importer.core.migration_summary import Asset, Summary, load_summary, percent
from importer.core.report_html import e, render_report, rich

FIXTURES = Path("tests/fixtures")


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    run = tmp_path_factory.mktemp("run")
    runner = CliRunner()
    for args in (
        [
            "convert",
            "--source",
            "cloudwatch",
            "--in",
            str(FIXTURES / "cloudwatch"),
            "--out",
            str(run / "alerts"),
        ],
        [
            "convert-dashboards",
            "--in",
            str(FIXTURES / "cloudwatch-dashboards"),
            "--out",
            str(run / "dashboards"),
        ],
    ):
        assert runner.invoke(app, args).exit_code == 0
    return run


@pytest.mark.parametrize(
    ("part", "total", "expected"),
    [(0, 0, None), (0, 10, 0), (10, 10, 100), (1, 1000, 1), (999, 1000, 99), (7, 10, 70)],
)
def test_percent_never_rounds_to_the_extremes(part: int, total: int, expected: int | None) -> None:
    assert percent(part, total) == expected


def test_percentages_add_up_in_the_report(run_dir: Path) -> None:
    summary = load_summary(run_dir)
    counts = summary.counts()
    total = sum(counts.values())
    done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
    migrated, rest = percent(done, total), percent(total - done, total)
    assert migrated is not None and rest is not None
    assert migrated + rest == 100
    html = render_report(summary)
    assert f"{migrated}%" in html
    assert f"{rest}%" in html


def test_every_unmigrated_asset_appears_in_exactly_one_group(run_dir: Path) -> None:
    summary = load_summary(run_dir)
    grouped = [a.name for g in summary.groups() for a, _ in g.items]
    expected = [a.name for a in summary.assets if not a.migrated]
    assert sorted(grouped) == sorted(expected)
    assert all(g.steps for g in summary.groups())


def test_composite_gets_specific_steps_and_is_open(run_dir: Path) -> None:
    html = render_report(load_summary(run_dir))
    assert "Composite alarm" in html
    assert "<details open>" in html
    assert "Child alarms that convert to conditions" in html


def test_report_is_self_contained(run_dir: Path) -> None:
    html = render_report(load_summary(run_dir))
    assert "default-src 'none'" in html
    assert "<script" not in html
    assert "http://" not in html.replace("http://www.w3.org", "")
    assert "https://" not in html


def test_hostile_names_cannot_inject_markup() -> None:
    evil = '<script>alert(1)</script><img src=x onerror=alert(1)>"\x1b[31m'
    summary = Summary([Asset("alarm", evil, evil, Confidence.MANUAL, [evil], [evil])], created=None)
    html = render_report(summary, aws_account=evil, region=evil)
    assert "<script>" not in html
    assert "<img" not in html
    assert "\x1b" not in html
    assert "&lt;script&gt;" in html


def test_rich_escapes_before_adding_code_tags() -> None:
    assert rich("run `a<b>` now") == "run <code>a&lt;b&gt;</code> now"
    assert e("<b>") == "&lt;b&gt;"


def test_plan_only_and_created_banners() -> None:
    assert "Plan only" in render_report(Summary([], None))
    created = render_report(Summary([], {"condition": 3, "workflow": 1}))
    assert "3 condition" in created
    assert "Plan only" not in created


def test_empty_run_does_not_divide_by_zero() -> None:
    html = render_report(Summary([], None))
    assert "Nothing was found to migrate" in html


def test_cli_report_command(run_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "r.html"
    result = CliRunner().invoke(app, ["report", str(run_dir), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("<!doctype html>")


def test_cli_report_rejects_bad_input(tmp_path: Path) -> None:
    (tmp_path / "alerts" / "ir").mkdir(parents=True)
    (tmp_path / "alerts" / "ir" / "x.json").write_text("{not json")
    result = CliRunner().invoke(app, ["report", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error" in result.output
