"""Cloud Monitoring dashboard widgets beyond charts: groups, headers, tables, logs, pie, PromQL."""

from pathlib import Path
from typing import Any

from importer.core.dashboard_ir import IRDashboard, IRWidget, Visualization, WidgetKind
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.ir import Confidence
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.gcp.dashboard_models import GcpDashboard, load_dashboards
from importer.sources.gcp.dashboards import translate_dashboard, translate_dashboards

SAMPLES = Path(__file__).parent / "fixtures" / "gcp-dash"
FILTER = 'metric.type="compute.googleapis.com/instance/cpu/utilization"'
QUERY = {
    "timeSeriesFilter": {
        "filter": FILTER,
        "aggregation": {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"},
    }
}


def tile(y: int, widget: dict[str, Any], x: int = 0, w: int = 24, h: int = 4) -> dict[str, Any]:
    return {"xPos": x, "yPos": y, "width": w, "height": h, "widget": widget}


def dash(*tiles: dict[str, Any]) -> IRDashboard:
    raw = {"displayName": "d", "mosaicLayout": {"columns": 48, "tiles": list(tiles)}}
    return translate_dashboard(GcpDashboard.model_validate(raw))


def chart(title: str = "cpu") -> dict[str, Any]:
    return {"title": title, "xyChart": {"dataSets": [{"timeSeriesQuery": QUERY}]}}


def one(widget: dict[str, Any]) -> IRWidget:
    return dash(tile(0, widget)).widgets[0]


def test_collapsible_groups_become_pages_and_their_tiles_move_onto_them() -> None:
    d = dash(
        tile(0, chart("before")),
        tile(4, {"title": "Disk", "collapsibleGroup": {"collapsed": True}}),
        tile(8, chart("disk a")),
        tile(12, chart("disk b")),
        tile(16, {"title": "Network", "singleViewGroup": {}}),
        tile(20, chart("net a")),
    )
    pages = {w.title: w.page for w in d.widgets}
    assert pages == {"before": None, "disk a": "Disk", "disk b": "Disk", "net a": "Network"}
    assert [w.layout.row for w in d.widgets if w.page == "Disk"] == [1, 3]  # rows restart
    assert any("page" in r for r in d.reasons)
    nerdgraph_dashboard(d)  # the emitter accepts the pages


def test_a_section_header_is_a_markdown_heading_on_the_same_page() -> None:
    d = dash(tile(0, {"title": "Latency", "sectionHeader": {"subtitle": "p99 by zone"}}))
    w = d.widgets[0]
    assert w.kind == WidgetKind.MARKDOWN and w.page is None
    assert w.text is not None and w.text.startswith("## Latency") and "p99 by zone" in w.text


def test_a_time_series_table_becomes_a_table_widget() -> None:
    w = one({"title": "t", "timeSeriesTable": {"dataSets": [{"timeSeriesQuery": QUERY}]}})
    assert w.visualization == Visualization.TABLE and w.series
    assert any("column" in r for r in w.reasons)


def test_a_pie_chart_becomes_a_pie() -> None:
    w = one({"title": "p", "pieChart": {"dataSets": [{"timeSeriesQuery": QUERY}]}})
    assert w.visualization == Visualization.PIE and w.series


def test_a_logs_panel_becomes_a_log_table_and_says_when_the_filter_is_lost() -> None:
    simple = one({"title": "l", "logsPanel": {"filter": 'resource.type="gce_instance"'}})
    assert simple.visualization == Visualization.TABLE
    assert "FROM Log" in simple.raw_series[0].nrql and "gce_instance" in simple.raw_series[0].nrql
    odd = one({"title": "l", "logsPanel": {"filter": "(a=1 OR b=2)"}})
    assert odd.raw_series and "filter" in " ".join(odd.reasons)
    assert "OR" not in odd.raw_series[0].nrql


def test_what_new_relic_cannot_show_stays_a_placeholder_with_its_own_reason() -> None:
    for kind, words in (
        ("incidentList", "(incident list)"),
        ("errorReportingPanel", "(error reporting panel)"),
        ("alertChart", "(alert chart)"),
    ):
        w = one({"title": "x", kind: {}})
        assert w.kind == WidgetKind.UNSUPPORTED
        assert words in w.reasons[0]
        assert steps_for(w.reasons) and classify(w.reasons[0]) is not Category.OTHER


def test_blank_tiles_are_dropped() -> None:
    assert dash(tile(0, {"blank": {}}), tile(4, chart())).widgets[0].title == "cpu"


def test_promql_widgets_convert_through_the_prometheus_translator() -> None:
    w = one(
        {
            "title": "p",
            "xyChart": {
                "dataSets": [
                    {"timeSeriesQuery": {"prometheusQuery": "sum(rate(http_requests_total[5m]))"}}
                ]
            },
        }
    )
    assert w.kind == WidgetKind.CHART and w.raw_series
    assert "http_requests_total" in w.raw_series[0].nrql


def test_mql_stays_manual_with_google_dates() -> None:
    w = one(
        {
            "title": "m",
            "xyChart": {
                "dataSets": [{"timeSeriesQuery": {"timeSeriesQueryLanguage": "fetch gce_instance"}}]
            },
        }
    )
    assert w.kind == WidgetKind.UNSUPPORTED and "MQL" in w.reasons[0]


def test_a_secondary_aggregation_that_changes_nothing_is_accepted() -> None:
    query = {
        "timeSeriesFilter": {
            **QUERY["timeSeriesFilter"],
            "secondaryAggregation": {"perSeriesAligner": "ALIGN_NONE"},
        }
    }
    w = one({"title": "s", "xyChart": {"dataSets": [{"timeSeriesQuery": query}]}})
    assert w.kind == WidgetKind.CHART


def test_sample_dashboards_convert_at_least_the_measured_number() -> None:
    total = converted = 0
    for path in sorted(SAMPLES.glob("*.json")):
        for ir in translate_dashboards(load_dashboards(path)):
            if ir.confidence != Confidence.MANUAL:
                nerdgraph_dashboard(ir)
            total += len(ir.widgets)
            converted += sum(w.kind != WidgetKind.UNSUPPORTED for w in ir.widgets)
    # before this change 16 of the 350 content widgets converted; placeholders never count
    assert total == 350 and converted >= 115
