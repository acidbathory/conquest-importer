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
        ("errorReportingPanel", "(error reporting panel)"),
        ("alertChart", "(alert chart)"),
    ):
        w = one({"title": "x", kind: {}})
        assert w.kind == WidgetKind.UNSUPPORTED
        assert words in w.reasons[0]
        assert steps_for(w.reasons) and classify(w.reasons[0]) is not Category.OTHER


def test_an_incident_list_becomes_an_issue_table() -> None:
    w = one({"title": "Incidents", "incidentList": {"policyNames": ["p"]}})
    assert w.kind == WidgetKind.CHART and w.visualization == Visualization.TABLE
    nrql = w.raw_series[0].nrql
    assert nrql.startswith("SELECT latest(title)") and "FROM NrAiIssue FACET issueId" in nrql
    notes = " ".join(w.reasons)
    assert "alert policies" in notes and "converted" in notes  # issues exist once alerts do
    assert w.confidence == Confidence.APPROXIMATE
    nerdgraph_dashboard(dash(tile(0, {"title": "Incidents", "incidentList": {}})))


def test_title_only_text_widget_becomes_a_heading() -> None:
    w = one({"title": "Topic", "text": {"content": ""}})
    assert w.kind == WidgetKind.MARKDOWN and w.text == "## Topic"
    assert w.confidence == Confidence.APPROXIMATE and "heading" in " ".join(w.reasons)


def test_text_widget_with_neither_title_nor_content_stays_a_placeholder() -> None:
    w = one({"text": {"content": "  "}})
    assert w.kind == WidgetKind.UNSUPPORTED and "no content" in w.reasons[0]


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


def test_a_scorecard_threshold_and_unit_are_kept() -> None:
    body = {
        "timeSeriesQuery": {**QUERY, "unitOverride": "10^2.%"},
        "thresholds": [
            {"color": "RED", "direction": "ABOVE", "value": 0.9},
            {"color": "YELLOW", "direction": "ABOVE", "value": 0.7},
        ],
    }
    w = one({"title": "s", "scorecard": body})
    assert w.visualization == Visualization.BILLBOARD and w.options.unit == "PERCENTAGE"
    assert [(t.severity.value, t.from_value) for t in w.thresholds] == [
        ("critical", 0.9),
        ("warning", 0.7),
    ]
    assert not any("thresholds" in r for r in w.reasons)


def test_thresholds_that_new_relic_cannot_show_are_named() -> None:
    body = {
        "timeSeriesQuery": QUERY,
        "thresholds": [
            {"color": "RED", "direction": "BELOW", "value": 5},
            {"color": "BLUE", "direction": "ABOVE", "value": 9},
        ],
    }
    w = one({"title": "s", "scorecard": body})
    assert not w.thresholds
    notes = " ".join(w.reasons)
    assert "below a value" in notes and "BLUE" in notes


def test_a_line_chart_threshold_becomes_a_band_bound() -> None:
    body = {
        "dataSets": [{"timeSeriesQuery": QUERY}],
        "thresholds": [{"color": "RED", "direction": "BELOW", "value": 5}],
    }
    w = one({"title": "x", "xyChart": body})
    assert w.visualization == Visualization.LINE
    assert [(t.severity.value, t.from_value, t.to_value) for t in w.thresholds] == [
        ("critical", None, 5.0)
    ]
    nerdgraph_dashboard(dash(tile(0, {"title": "x", "xyChart": body})))


def test_a_logs_panel_regex_becomes_rlike_that_searches_like_google() -> None:
    w = one({"title": "l", "logsPanel": {"filter": 'textPayload=~"timeout after [0-9]+ms"'}})
    nrql = w.raw_series[0].nrql
    # RLIKE must match the whole value, a Cloud Logging =~ matches anywhere in it
    assert "textPayload RLIKE '.*(timeout after [0-9]+ms).*'" in nrql
    assert not any("filter" in r and "every log line" in r for r in w.reasons)


def test_a_logs_panel_regex_with_a_placeholder_is_not_a_real_pattern() -> None:
    w = one({"title": "l", "logsPanel": {"filter": 'textPayload=~"${NAME}-[0-9]+"'}})
    assert "RLIKE" not in w.raw_series[0].nrql
    assert any("every log line" in r for r in w.reasons)


def test_a_logs_panel_label_that_starts_with_a_dot_is_backtick_quoted() -> None:
    w = one({"title": "l", "logsPanel": {"filter": 'labels.".serviceId"="appfabric"'}})
    assert "WHERE `labels..serviceId` = 'appfabric'" in w.raw_series[0].nrql


def test_dot_value_matcher_on_known_variable_maps_to_dashboard_variable() -> None:
    from importer.sources.gcp.dashboard_promql import promql_nrql

    done = promql_nrql(
        'ray_node_mem_used{cluster=~"${Cluster.value}",namespace=~"${Namespace.value}"}',
        timeseries=True,
        bucket_seconds=None,
        variables={"Cluster": "cluster", "Namespace": "namespace"},
    )
    assert not isinstance(done, str), done
    nrql, _notes, used = done
    assert "cluster IN ({{Cluster}})" in nrql
    assert "namespace IN ({{Namespace}})" in nrql
    assert used == ["Cluster", "Namespace"]


def test_dot_value_matcher_on_unknown_variable_is_not_rewritten() -> None:
    from importer.sources.gcp.dashboard_promql import expand_matchers

    expr = 'm{cluster=~"${Other.value}"}'
    assert expand_matchers(expr, {"Cluster": "cluster"}) == expr
