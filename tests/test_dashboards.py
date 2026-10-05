import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.dashboard_ir import IRWidget, Visualization, WidgetKind
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import (
    dashboard_resource,
    dashboards_terraform,
    nerdgraph_dashboard,
)
from importer.core.ir import Confidence
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.cloudwatch.dashboard_models import CloudWatchDashboard, load_dashboards
from importer.sources.cloudwatch.dashboards import translate_dashboard, translate_widget
from importer.sources.cloudwatch.translate import load_namespace_map

FIXTURES = Path(__file__).parent / "fixtures" / "cloudwatch-dashboards"
PREFIXES = load_namespace_map()


def widget(rows: list[Any], **props: Any) -> IRWidget:
    body = {"type": "metric", "x": 0, "y": 0, "width": 12, "height": 6}
    body["properties"] = {"metrics": rows, "title": "t", **props}
    return translate_widget(body, PREFIXES)


def queries(w: IRWidget) -> list[str]:
    from importer.core.emit.nrql import widget_nrql

    ts = w.visualization in (Visualization.LINE, Visualization.AREA)
    return [widget_nrql(s, ts) for s in w.series]


def by_name() -> dict[str, Any]:
    return {d.name: d for d in DASHBOARD_REGISTRY["cloudwatch"].convert(FIXTURES)}


def test_simple_metric_row_becomes_a_timeseries_query() -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"]])
    assert w.kind == WidgetKind.CHART and w.visualization == Visualization.LINE
    assert queries(w) == [
        "SELECT average(aws.ec2.CPUUtilization) FROM Metric "
        "WHERE aws.ec2.InstanceId = 'i-1' AND aws.ec2.AutoScalingGroupName IS NULL "
        "AND aws.ec2.ImageId IS NULL AND aws.ec2.InstanceType IS NULL "
        "TIMESERIES 300 SECONDS"
    ]
    assert w.confidence == Confidence.APPROXIMATE


def test_dot_and_ellipsis_shorthand_expand_from_the_previous_row() -> None:
    w = widget(
        [
            ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "a"],
            ["...", "b"],
            [".", "Connections", ".", "."],
        ]
    )
    assert [s.signal.scope[0].value for s in w.series] == ["a", "b", "b"]
    assert any("expanded" in r for r in w.reasons)


def test_ellipsis_with_wrong_value_count_is_refused() -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"], ["...", "a", "b"]])
    assert len(w.series) == 1
    assert any("shorthand does not match" in r for r in w.reasons)


def test_partial_conversion_is_approximate_and_says_what_was_lost() -> None:
    w = widget(
        [
            ["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"],
            ["AWS/EC2", "CPUUtilization", "InstanceId", "i-1", {"stat": "SampleCount"}],
        ]
    )
    assert len(w.series) == 1 and w.kind == WidgetKind.CHART
    assert any("1 of 2 metrics converted" in r for r in w.reasons)


def test_nothing_convertible_is_manual_and_unsupported() -> None:
    w = widget([[{"expression": "m1*2", "id": "e1"}]])
    assert (w.kind, w.confidence) == (WidgetKind.UNSUPPORTED, Confidence.MANUAL)
    w2 = widget([["MyCompany/X", "M", "D", "v"]])
    assert "no New Relic mapping" in w2.reasons[0]


def test_hidden_series_are_skipped_without_a_failure() -> None:
    w = widget(
        [
            ["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"],
            ["AWS/EC2", "CPUUtilization", "InstanceId", "i-2", {"visible": False}],
        ]
    )
    assert len(w.series) == 1
    assert not any("metrics converted" in r for r in w.reasons)


@pytest.mark.parametrize(
    ("props", "viz", "timeseries"),
    [
        ({"view": "singleValue"}, Visualization.BILLBOARD, False),
        ({"view": "bar"}, Visualization.BAR, False),
        ({"view": "pie"}, Visualization.PIE, False),
        ({"stacked": True}, Visualization.AREA, True),
        ({}, Visualization.LINE, True),
    ],
)
def test_view_mapping(props: dict[str, Any], viz: Visualization, timeseries: bool) -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"]], **props)
    assert w.visualization == viz
    assert ("TIMESERIES" in queries(w)[0]) == timeseries


def test_unknown_view_falls_back_to_a_line_with_a_reason() -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"]], view="weird")
    assert w.visualization == Visualization.LINE
    assert any("'weird'" in r for r in w.reasons)


def test_short_periods_are_raised_to_a_minute_and_say_so() -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1", {"period": 10}]])
    assert w.series[0].period_seconds == 60
    assert any("raised to 60s" in r for r in w.reasons)
    w2 = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1", {"period": 120}]])
    assert w2.series[0].period_seconds == 120
    assert not any("raised" in r for r in w2.reasons)


def test_label_becomes_a_quoted_alias() -> None:
    w = widget([["AWS/EC2", "CPUUtilization", "InstanceId", "i-1", {"label": "it's\nbad"}]])
    assert "AS 'it\\'s?bad'" in queries(w)[0]


def test_layout_is_halved_and_stays_inside_the_grid() -> None:
    w = translate_widget(
        {
            "type": "text",
            "x": 20,
            "y": 10,
            "width": 24,
            "height": 3,
            "properties": {"markdown": "x"},
        },
        PREFIXES,
    )
    assert w.layout.column + w.layout.width - 1 <= 12
    assert w.layout.row == 6 and w.layout.height == 1
    junk = translate_widget(
        {"type": "text", "x": -5, "y": "a", "width": True, "properties": {"markdown": "x"}},
        PREFIXES,
    )
    assert (junk.layout.column, junk.layout.row) == (1, 1)


def test_fixture_dashboards() -> None:
    d = by_name()
    ops, hard, empty = d["conquest-ops-overview"], d["conquest-hard-cases"], d["conquest-empty"]
    assert ops.confidence == Confidence.APPROXIMATE
    assert all(w.kind != WidgetKind.UNSUPPORTED for w in ops.widgets)
    assert sum(w.kind == WidgetKind.UNSUPPORTED for w in hard.widgets) == 3
    assert empty.confidence == Confidence.UNSUPPORTED
    with pytest.raises(CannotEmit):
        nerdgraph_dashboard(empty)


def test_nerdgraph_payload_shape_and_placeholders() -> None:
    payload = nerdgraph_dashboard(by_name()["conquest-ops-overview"])
    dash = payload["variables"]["dashboard"]
    assert payload["variables"]["accountId"] == "ACCOUNT_ID_PLACEHOLDER"
    assert dash["permissions"] == "PRIVATE"
    first_chart = dash["pages"][0]["widgets"][1]
    assert first_chart["visualization"] == {"id": "viz.line"}
    assert first_chart["rawConfiguration"]["nrqlQueries"][0]["accountIds"] == [
        "ACCOUNT_ID_PLACEHOLDER"
    ]
    assert json.dumps(payload)  # serialisable


def test_unconverted_widgets_become_visible_placeholders() -> None:
    payload = nerdgraph_dashboard(by_name()["conquest-hard-cases"])
    texts = [
        w["rawConfiguration"]["text"]
        for w in payload["variables"]["dashboard"]["pages"][0]["widgets"]
        if w["visualization"]["id"] == "viz.markdown"
    ]
    assert len(texts) == 3 and all(t.startswith("**Not migrated:**") for t in texts)


def test_hostile_text_is_escaped_in_every_output() -> None:
    evil = 'x"\n${evil} %{if} \x1b[31m| <script>'
    body = {
        "widgets": [
            {"type": "log", "properties": {"title": evil}},
            {
                "type": "metric",
                "properties": {
                    "title": evil,
                    "metrics": [["AWS/EC2", "CPUUtilization", "InstanceId", "i-1"]],
                },
            },
            {"type": evil},
        ]
    }
    d = translate_dashboard(CloudWatchDashboard(name=evil, widgets=body["widgets"]), PREFIXES)
    tf = dashboard_resource(d, 1)
    assert "${evil}" not in tf.replace("$${evil}", "") and "%{if}" not in tf.replace("%%{if}", "")
    assert "\x1b" not in tf and "\x1b" not in json.dumps(nerdgraph_dashboard(d))
    # The title is data in a GraphQL variable / HCL string; markdown text must have no markup.
    placeholder = nerdgraph_dashboard(d)["variables"]["dashboard"]["pages"][0]["widgets"][0]
    assert "<script>" not in placeholder["rawConfiguration"]["text"]
    assert "|" not in placeholder["rawConfiguration"]["text"]


def test_loader_accepts_get_dashboard_output_and_a_bare_body(tmp_path: Path) -> None:
    (tmp_path / "bare.json").write_text(json.dumps({"widgets": []}))
    (tmp_path / "wrapped.json").write_text(
        json.dumps({"DashboardName": "named", "DashboardBody": json.dumps({"widgets": []})})
    )
    assert sorted(d.name for d in load_dashboards(tmp_path)) == ["bare", "named"]


@pytest.mark.parametrize("bad", ['{"widgets": 5}', '{"widgets": [1]}', "[]", "not json"])
def test_loader_rejects_malformed_input(tmp_path: Path, bad: str) -> None:
    (tmp_path / "x.json").write_text(bad)
    with pytest.raises(ValueError, match=r".+"):
        load_dashboards(tmp_path)


def test_cli_end_to_end_and_report(tmp_path: Path) -> None:
    out = tmp_path / "o"
    res = CliRunner().invoke(app, ["convert-dashboards", "--in", str(FIXTURES), "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "Read 3 dashboards, emitted 2" in res.output
    assert len(list((out / "nerdgraph").glob("*.json"))) == 2
    assert len(list((out / "dashboard-ir").glob("*.json"))) == 3
    report = (out / "report.md").read_text()
    assert "conquest-hard-cases" in report and "log widget" in report
    assert "newrelic_one_dashboard" in (out / "dashboards.tf").read_text()


def test_cli_rejects_unknown_source_and_empty_input(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["convert-dashboards", "--source", "nope"]).exit_code == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    res = runner.invoke(app, ["convert-dashboards", "--in", str(empty), "--out", str(tmp_path)])
    assert res.exit_code == 1


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_generated_dashboard_terraform_validates(tmp_path: Path) -> None:
    out = tmp_path / "o"
    res = CliRunner().invoke(app, ["convert-dashboards", "--in", str(FIXTURES), "--out", str(out)])
    assert res.exit_code == 0, res.output
    terraform = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("cannot download the New Relic provider (offline or blocked)")
    check = subprocess.run(  # noqa: S603
        [terraform, "validate", "-no-color"], cwd=out, capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stdout + check.stderr


def test_terraform_header_appears_once() -> None:
    d = by_name()["conquest-ops-overview"]
    text = dashboards_terraform([(1, d), (2, d)])
    assert text.count('provider "newrelic"') == 1 and text.count("newrelic_one_dashboard") == 2
