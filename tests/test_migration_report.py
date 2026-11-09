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


def test_preview_and_applied_banners() -> None:
    preview = render_report(Summary([], None), aws_account="123456789012", nr_account=7)
    assert "Preview" in preview and "forecast" in preview
    assert "--yes" in preview and "--aws-account-id 123456789012" in preview
    applied = render_report(Summary([], {"condition": 3, "workflow": 1}, refused=2), nr_account=7)
    assert "3 conditions, 1 workflow" in applied and "refused <b>2</b>" in applied
    assert "importer rollback" in applied and "Preview" not in applied


def test_the_hero_says_forecast_until_something_is_created() -> None:
    assets = [Asset("alarm", "a", "metric alarm", Confidence.APPROXIMATE)]
    assert "can be migrated" in render_report(Summary(assets, None))
    applied = render_report(Summary(assets, {"condition": 1}))
    assert "can be migrated" not in applied and "assets migrated" in applied


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


def test_headline_states_total_completion() -> None:
    assets = [
        Asset("alarm", "a", "metric alarm", Confidence.APPROXIMATE),
        Asset("alarm", "b", "metric alarm", Confidence.EXACT),
        Asset("alarm", "c", "metric alarm", Confidence.MANUAL),
        Asset("dashboard", "d", "dashboard", Confidence.UNSUPPORTED),
    ]
    assert Summary(assets, {"condition": 2}).headline() == (
        "Total completion: 50% (2 of 4 assets migrated, 2 need manual work)"
    )
    assert Summary(assets, None).headline() == (
        "Total completion (forecast): 50% (2 of 4 assets can be migrated, 2 need manual work; "
        "nothing has been created in New Relic yet)"
    )
    assert "nothing found" in Summary([], None).headline()


def test_cli_report_prints_the_headline(run_dir: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["report", str(run_dir), "--out", str(tmp_path / "r.html")])
    assert "Total completion (forecast):" in result.output


def test_the_applied_banner_says_what_already_existed() -> None:
    page = render_report(
        Summary([], {"condition": 1}, existing={"condition": 20, "dashboard": 1}), nr_account=7
    )
    assert "20 conditions, 1 dashboard already existed and were left unchanged." in page
    assert "already existed" not in render_report(Summary([], {"condition": 1}), nr_account=7)


def test_a_google_cloud_report_uses_google_wording_and_a_command_that_exists() -> None:
    assets = [Asset("alarm", "cpu", "conditionThreshold", Confidence.APPROXIMATE)]
    html = render_report(
        Summary(assets, None), aws_account="my-project", nr_account=7, source="gcp"
    )
    assert "Google Cloud project my-project" in html
    assert "Alert conditions" in html and "importer apply --in" in html
    assert "AWS" not in html and "CloudWatch" not in html and "EventBridge" not in html
    assert "Workload Identity Federation" in html


def test_a_dynatrace_report_uses_dynatrace_wording() -> None:
    assets = [Asset("alarm", "w", "workflow", Confidence.APPROXIMATE)]
    html = render_report(
        Summary(assets, None), aws_account="abc12345", nr_account=7, source="dynatrace"
    )
    assert "Dynatrace tenant abc12345" in html and "Workflows and SLOs" in html
    assert "AWS" not in html and "CloudWatch" not in html and "Google" not in html
    assert "importer apply --in" in html and "--confirm-account 7" in html
    assert "Davis problem workflows are notification routes" in html


def test_the_default_report_is_still_the_aws_one() -> None:
    html = render_report(Summary([], None), aws_account="123456789012")
    assert "AWS account 123456789012" in html and "importer migrate" in html
