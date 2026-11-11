"""Cloud Monitoring dashboards to the dashboard IR, NerdGraph and Terraform."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.dashboard_ir import IRDashboard, IRWidget, Visualization, WidgetKind
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.ir import Confidence
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.gcp.dashboard_models import MAX_WIDGETS, GcpDashboard, load_dashboards
from importer.sources.gcp.dashboards import translate_dashboard

FIXTURES = Path(__file__).parent / "fixtures" / "gcp-dashboards"


def converted() -> dict[str, IRDashboard]:
    source = DASHBOARD_REGISTRY["gcp"]
    return {d.name: d for d in source.convert(FIXTURES)}


def widget(dash: str, title: str) -> IRWidget:
    return next(w for w in converted()[dash].widgets if w.title == title)


def one_chart(filter_text: str, aggregation: dict[str, Any] | None = None) -> IRWidget:
    series: dict[str, Any] = {"filter": filter_text}
    if aggregation is not None:
        series["aggregation"] = aggregation
    raw = {
        "displayName": "d",
        "gridLayout": {
            "widgets": [
                {
                    "title": "w",
                    "xyChart": {"dataSets": [{"timeSeriesQuery": {"timeSeriesFilter": series}}]},
                }
            ]
        },
    }
    return translate_dashboard(GcpDashboard.model_validate(raw)).widgets[0]


def test_all_fixture_dashboards_load() -> None:
    assert sorted(converted()) == [
        "Column dashboard",
        "Empty dashboard",
        "Grid dashboard",
        "Ops overview",
        "Row dashboard",
    ]


def test_an_xy_chart_becomes_a_line_with_facet_and_the_metric_name() -> None:
    w = widget("Ops overview", "VM CPU by zone")
    assert w.kind is WidgetKind.CHART and w.visualization is Visualization.LINE
    nrql = nerdgraph_dashboard(converted()["Ops overview"])["variables"]["dashboard"]["pages"][0][
        "widgets"
    ][0]["rawConfiguration"]["nrqlQueries"][0]["query"]
    assert "gcp.compute.instance.cpu.utilization" in nrql
    assert "FACET gcp.zone" in nrql and "TIMESERIES 300 SECONDS" in nrql


def test_a_counter_chart_sums_the_change_per_poll_and_does_not_mention_a_threshold() -> None:
    w = widget("Ops overview", "Disk reads")
    assert w.visualization is Visualization.AREA
    assert w.series[0].period_seconds == 300
    notes = " ".join(w.reasons)
    assert "counter metric" in notes and "threshold" not in notes


def test_a_scorecard_becomes_a_billboard() -> None:
    w = widget("Ops overview", "Open messages")
    assert w.visualization is Visualization.BILLBOARD and len(w.series) == 1
    assert any("spark" in r for r in w.reasons)


@pytest.mark.parametrize(
    ("dash", "title", "text", "confidence", "reasons"),
    [
        ("Ops overview", "Runbook", "step one", Confidence.EXACT, []),
        (
            "Grid dashboard",
            "Note",
            "hello",
            Confidence.APPROXIMATE,
            ["raw text is shown as markdown"],
        ),
    ],
)
def test_text_widgets_become_markdown(
    dash: str, title: str, text: str, confidence: Confidence, reasons: list[str]
) -> None:
    w = widget(dash, title)
    assert w.kind is WidgetKind.MARKDOWN and text in (w.text or "")
    assert (w.confidence, w.reasons) == (confidence, reasons)


def test_a_failed_widget_keeps_its_source_type_for_the_report() -> None:
    assert widget("Ops overview", "Errors").source_type == "logsPanel"
    assert widget("Ops overview", "Open alerts").source_type == "alertChart"


def test_a_custom_metric_widget_converts_and_names_its_data_path() -> None:
    w = widget("Ops overview", "Custom metric")
    assert w.kind is WidgetKind.CHART and w.data_path == "needs-custom-ingest"
    assert any(r.startswith("data path: needs-custom-ingest") for r in w.reasons)


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("MQL chart", "MQL"),
        ("Error ratio", "share of the total"),  # no aggregation given: a ratio of means is wrong
        ("Open alerts", "alert chart"),
        ("Uptime", "uptime check"),
    ],
)
def test_everything_else_is_a_failed_widget_with_a_reason_and_steps(
    title: str, reason: str
) -> None:
    w = widget("Ops overview", title)
    assert w.kind is WidgetKind.UNSUPPORTED
    assert reason.lower() in " ".join(w.reasons).lower()
    assert steps_for(w.reasons), w.reasons
    assert all(classify(r) is not Category.OTHER for r in w.reasons), w.reasons


def test_every_failed_widget_has_remediation_in_every_fixture() -> None:
    for dash in converted().values():
        for w in dash.widgets:
            if w.confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED):
                assert steps_for(w.reasons), (w.title, w.reasons)


def test_mosaic_tiles_are_scaled_from_48_columns_to_12() -> None:
    w = widget("Ops overview", "Disk reads")
    assert (w.layout.column, w.layout.width) == (7, 6)
    assert w.layout.row >= 1 and w.layout.height >= 1


def test_grid_column_and_row_layouts_stay_inside_12_columns() -> None:
    for name in ("Grid dashboard", "Column dashboard", "Row dashboard"):
        for w in converted()[name].widgets:
            assert w.layout.column + w.layout.width - 1 <= 12, (name, w.title)
    cols = {w.title: w.layout for w in converted()["Column dashboard"].widgets}
    assert cols["Left"].width == 8 and cols["Right top"].column == 9
    assert cols["Right bottom"].row > cols["Right top"].row
    grid = converted()["Grid dashboard"].widgets
    assert grid[0].layout.column == 1 and grid[1].layout.column == 7


def test_an_untitled_widget_gets_a_title_and_a_dashboard_without_widgets_is_unsupported() -> None:
    assert converted()["Grid dashboard"].widgets[2].title == "xyChart widget"
    assert converted()["Empty dashboard"].confidence is Confidence.UNSUPPORTED


def test_a_label_value_with_quotes_is_escaped_in_the_nrql() -> None:
    w = one_chart(
        'metric.type="compute.googleapis.com/instance/cpu/utilization" '
        'resource.label.zone="a\' OR 1=1 --"',
        {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"},
    )
    dash = IRDashboard(
        source_system="gcp",
        source_key="k",
        name="d",
        widgets=[w],
        confidence=Confidence.APPROXIMATE,
    )
    query = nerdgraph_dashboard(dash)["variables"]["dashboard"]["pages"][0]["widgets"][0][
        "rawConfiguration"
    ]["nrqlQueries"][0]["query"]
    assert "\\'" in query and "'a' OR" not in query


@pytest.mark.parametrize(
    ("aggregation", "reason"),
    [
        ({"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_NEXT_OLDER"}, "aligner"),
        (
            {
                "alignmentPeriod": "300s",
                "perSeriesAligner": "ALIGN_MEAN",
                "crossSeriesReducer": "R",
            },
            "reducer",
        ),
    ],
)
def test_aggregations_that_cannot_be_mapped_fail_the_widget(
    aggregation: dict[str, Any], reason: str
) -> None:
    w = one_chart('metric.type="compute.googleapis.com/instance/cpu/utilization"', aggregation)
    assert w.kind is WidgetKind.UNSUPPORTED and reason in " ".join(w.reasons)
    assert steps_for(w.reasons)


def test_a_chart_without_an_aggregation_uses_the_polling_interval() -> None:
    w = one_chart('metric.type="compute.googleapis.com/instance/cpu/utilization"')
    assert w.kind is WidgetKind.CHART and w.series[0].period_seconds == 300


def test_the_widget_count_is_capped() -> None:
    raw = {
        "displayName": "big",
        "gridLayout": {"widgets": [{"text": {"content": "x"}}] * (MAX_WIDGETS + 5)},
    }
    dash = translate_dashboard(GcpDashboard.model_validate(raw))
    assert len(dash.widgets) == MAX_WIDGETS
    assert any("not converted" in r and "limit" in r for r in dash.reasons)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[1, 2]", r"d\.json: expected a dashboard"),
        ('{"dashboards": "x"}', r"d\.json: expected a dashboard"),
        ('{"displayName": 5}', r"displayName"),
        ("not json", r"Expecting value"),
    ],
)
def test_bad_files_raise_a_value_error(body: str, message: str, tmp_path: Path) -> None:
    (tmp_path / "d.json").write_text(body)
    with pytest.raises(ValueError, match=message):
        load_dashboards(tmp_path)


def test_the_cli_converts_gcp_dashboards_private(tmp_path: Path) -> None:
    out = tmp_path / "out"
    done = CliRunner().invoke(
        app,
        ["convert-dashboards", "--source", "gcp", "--in", str(FIXTURES), "--out", str(out)],
    )
    assert done.exit_code == 0, done.output
    tf = (out / "dashboards.tf").read_text()
    assert 'permissions = "private"' in tf and "gcp_dashboard_" in tf
    payloads = [json.loads(p.read_text()) for p in (out / "nerdgraph").glob("*.json")]
    assert payloads and all(
        p["variables"]["dashboard"]["permissions"] == "PRIVATE" for p in payloads
    )
    report = (out / "report.md").read_text()
    assert "Ops overview" in report and "logsPanel" in report
