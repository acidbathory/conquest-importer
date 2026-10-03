from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.ir import Confidence, IRMonitor
from importer.core.stats import Run, build_stats, load_run, reason_pattern, render_markdown
from importer.core.taxonomy import Category
from importer.sources import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("reason", "pattern"),
    [
        (
            "no New Relic mapping for metric 'custom.a'; add it to metric_map.json",
            "no New Relic mapping for metric '{X}'; add it to metric_map.json",
        ),
        (
            'percentile statistic "p99" is not supported yet',
            "percentile statistic '{X}' is not supported yet",
        ),
        (
            "3 of 5 datapoints is approximated as 3 consecutive breaches",
            "{N} of {N} datapoints is approximated as {N} consecutive breaches",
        ),
        (
            "composite alarm combines 2 alarms (a-b, c-d); no composite",
            "composite alarm combines {N} alarms ({LIST}); no composite",
        ),
        (
            "composite monitor combines monitors 9000001, 9000002; none",
            "composite monitor combines monitors {N}; none",
        ),
        ("settings not migrated: evaluation_delay, priority", "settings not migrated: {LIST}"),
        (
            "notification handles must be configured manually: @pagerduty-x",
            "notification handles must be configured manually: {LIST}",
        ),
        (
            "group-by tags mapped to unverified attributes: ['device']",
            "group-by tags mapped to unverified attributes: {LIST}",
        ),
        (
            "evaluation window 172800s is outside 60s-24h",
            "evaluation window {N}s is outside {N}s-{N}h",
        ),
        # names that merely contain digits are not numbers
        ("ec2 and p99 stay", "ec2 and p99 stay"),
    ],
)
def test_reason_pattern(reason: str, pattern: str) -> None:
    assert reason_pattern(reason) == pattern


def test_two_different_failures_of_one_kind_share_a_pattern() -> None:
    a = reason_pattern("no New Relic mapping for namespace 'Custom/A'; add it")
    b = reason_pattern("no New Relic mapping for namespace 'Other/B'; add it")
    assert a == b


def _monitor(conf: Confidence, reasons: list[str], source_type: str = "metric alarm") -> IRMonitor:
    base = REGISTRY["cloudwatch"].convert(FIXTURES / "cloudwatch-real")[0]
    return base.model_copy(
        update={"confidence": conf, "reasons": reasons, "source_type": source_type}
    )


def test_build_stats_counts_and_cross_references_reasons() -> None:
    run = Run(
        Path("r"),
        [
            _monitor(Confidence.APPROXIMATE, ["actions must be set up", "3 of 5 datapoints"]),
            _monitor(Confidence.MANUAL, ["actions must be set up", "percentile 'p99' unsupported"]),
            _monitor(Confidence.MANUAL, ["percentile 'p50' unsupported"], "composite alarm"),
        ],
    )
    s = build_stats([run])["cloudwatch"]
    assert (s.total, s.converted) == (3, 1)
    assert s.by_type["metric alarm"].total == 2
    assert s.by_type["composite alarm"].converted == 0
    assert s.reasons_not_converted["percentile '{X}' unsupported"] == 2
    # "actions must be set up" is on both a failed and a converted monitor
    assert s.reasons_converted["actions must be set up"] == 1
    text = render_markdown([run], {"cloudwatch": s})
    assert "| actions must be set up | 1 | 1 |" in text
    assert "1 converted (33%)" in text


def test_a_reason_repeated_on_one_monitor_counts_once() -> None:
    m = _monitor(Confidence.MANUAL, ["no mapping for 'a'", "no mapping for 'b'"])
    s = build_stats([Run(Path("r"), [m])])["cloudwatch"]
    assert s.reasons_not_converted["no mapping for '{X}'"] == 1


def test_sources_are_reported_separately() -> None:
    runs = [
        Run(Path("a"), [_monitor(Confidence.EXACT, [])]),
        Run(Path("b"), REGISTRY["datadog"].convert(FIXTURES / "datadog")),
    ]
    assert list(build_stats(runs)) == ["cloudwatch", "datadog"]


def test_hostile_text_cannot_reach_output() -> None:
    evil = "x\x1b]0;pwn\x07\n| injected | row |`<script>`"
    m = _monitor(Confidence.MANUAL, [evil], evil)
    run = Run(Path("r"), [m])
    text = render_markdown([run], build_stats([run]))
    assert "\x1b" not in text and "\x07" not in text
    assert "<script>" not in text and "| injected | row |" not in text


def test_load_run_rejects_folder_without_ir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no ir/"):
        load_run(tmp_path)


def test_cli_end_to_end(tmp_path: Path) -> None:
    runner = CliRunner()
    for name, src in (("cw", "cloudwatch-real"), ("dd", "datadog")):
        source = "cloudwatch" if name == "cw" else "datadog"
        res = runner.invoke(
            app,
            [
                "convert",
                "--source",
                source,
                "--in",
                str(FIXTURES / src),
                "--out",
                str(tmp_path / name),
            ],
        )
        assert res.exit_code == 0, res.output
    report = tmp_path / "stats.md"
    res = runner.invoke(
        app, ["stats", str(tmp_path / "cw"), str(tmp_path / "dd"), "--out", str(report)]
    )
    assert res.exit_code == 0, res.output
    assert "## cloudwatch: 9 monitors" in res.output and "## datadog:" in res.output
    assert report.read_text() == res.output


def test_cli_reports_a_bad_folder(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["stats", str(tmp_path)])
    assert res.exit_code == 1
    assert "no ir/" in res.output


def test_category_table_counts_each_monitor_once() -> None:
    run = Run(
        Path("r"),
        [
            _monitor(
                Confidence.MANUAL,
                ["no New Relic mapping for metric 'a'", "no New Relic mapping for metric 'b'"],
            ),
            _monitor(Confidence.APPROXIMATE, ["alarm actions must be configured manually"]),
        ],
    )
    s = build_stats([run])["cloudwatch"]
    assert s.categories_not_converted[Category.MISSING_MAPPING] == 1
    assert s.categories_converted[Category.MANUAL_FOLLOWUP] == 1
    text = render_markdown([run], {"cloudwatch": s})
    assert "| missing mapping | 1 | 0 |" in text
    assert "uncategorised" not in text
