"""Azure portal dashboard parts to widgets."""

from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import IRDashboard, IRWidget, Visualization, WidgetKind
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.emit.nrql import widget_nrql
from importer.core.ir import Confidence
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.azure import dashboard_kql
from importer.sources.azure.dashboard_kql import LOG_FORWARDING, KqlNrql
from importer.sources.azure.dashboard_models import AzureDashboard, load_dashboards
from importer.sources.azure.portal_dashboard import translate_portal

FIXTURES = Path(__file__).parent / "fixtures" / "azure"
VM = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg/providers/"
    "Microsoft.Compute/virtualMachines/vm1"
)


def part(kind: str, x: int = 0, y: int = 0, w: int = 6, h: int = 4, **meta: Any) -> dict[str, Any]:
    return {
        "position": {"x": x, "y": y, "colSpan": w, "rowSpan": h},
        "metadata": {"type": f"Extension/HubsExtension/PartType/{kind}", **meta},
    }


def dash(*parts: dict[str, Any], metadata: dict[str, Any] | None = None) -> IRDashboard:
    return translate_portal(
        AzureDashboard(
            kind="portal",
            key="k",
            name="Portal",
            content={"lenses": [{"parts": list(parts)}]},
            metadata=metadata or {},
        )
    )


def one(*parts: dict[str, Any]) -> IRWidget:
    (w,) = dash(*parts).widgets
    return w


def chart_part(metrics: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    value = {"id": VM, "chartType": 0, "metrics": metrics, **over}
    return part(
        "MetricsChartPart",
        inputs=[{"name": "queryInputs", "value": value}],
    )


def test_a_markdown_part_is_a_markdown_widget() -> None:
    w = one(
        part(
            "MarkdownPart",
            settings={"content": {"settings": {"content": "## Hi\r\nthere", "title": "T"}}},
        )
    )
    assert (w.kind, w.confidence, w.title) == (WidgetKind.MARKDOWN, Confidence.EXACT, "T")
    assert w.text == "## Hi\nthere"


def test_a_metrics_chart_part_is_a_chart_over_the_metric_table() -> None:
    w = one(chart_part([{"name": "Percentage CPU", "resourceId": VM}]))
    assert w.kind is WidgetKind.CHART and w.visualization is Visualization.LINE
    q = widget_nrql(w.series[0], True)
    assert q.startswith("SELECT average(azure.compute.virtualmachines.PercentageCPU) FROM Metric")
    assert f"azure.resourceId IN ('{VM}')" in q
    assert w.data_path is None


def test_a_template_expression_for_the_resource_widens_the_chart_with_a_note() -> None:
    expr = "[resourceId(parameters('rg'), 'Microsoft.Compute/virtualMachines', parameters('vm'))]"
    w = one(chart_part([{"name": "Percentage CPU", "resourceId": expr}], id=expr))
    q = widget_nrql(w.series[0], True)
    assert "azure.compute.virtualmachines.PercentageCPU" in q and "FACET azure.resourceId" in q
    assert any("resource selection" in r for r in w.reasons)


def test_a_monitor_chart_part_with_options_is_read() -> None:
    options = {
        "chart": {
            "title": "Requests",
            "metrics": [
                {
                    "resourceMetadata": {
                        "id": VM.replace("Compute/virtualMachines/vm1", "Web/sites/app")
                    },
                    "name": "Requests",
                    "aggregationType": 1,
                    "namespace": "microsoft.web/sites",
                }
            ],
        }
    }
    w = one(part("MonitorChartPart", inputs=[{"name": "options", "value": options}]))
    assert w.title == "Requests"
    assert widget_nrql(w.series[0], True).startswith("SELECT sum(azure.web.sites.Requests)")


def test_a_chart_type_without_a_counterpart_is_a_line_with_a_note() -> None:
    w = one(chart_part([{"name": "Percentage CPU", "resourceId": VM}], chartType=7))
    assert w.visualization is Visualization.LINE
    assert any("chart type 7" in r for r in w.reasons)


def test_a_metric_that_cannot_be_written_is_a_placeholder() -> None:
    w = one(chart_part([{"name": "a-b", "resourceId": VM}]))
    assert w.kind is WidgetKind.UNSUPPORTED
    assert w.reasons[0].startswith("dashboard metric chart cannot be converted")


def test_a_logs_part_goes_through_the_kql_translator(monkeypatch: pytest.MonkeyPatch) -> None:
    logs = part(
        "LogsDashboardPart",
        inputs=[
            {"name": "Query", "value": "Heartbeat | summarize count() by bin(TimeGenerated, 1h)"},
            {"name": "PartTitle", "value": "Heartbeats"},
            {"name": "SpecificChart", "value": "Line"},
        ],
    )
    assert one(logs).reasons[0].startswith("workbook query is KQL")
    monkeypatch.setattr(
        dashboard_kql,
        "translate_kql",
        lambda _q, _v: KqlNrql(nrql="SELECT count(*) FROM Log", timeseries=True),
    )
    w = one(logs)
    assert (w.title, w.visualization, w.data_path) == (
        "Heartbeats",
        Visualization.LINE,
        LOG_FORWARDING,
    )


def test_a_logs_part_without_a_query_and_other_parts_are_placeholders() -> None:
    a, b = dash(part("LogsDashboardPart", inputs=[]), part("VideoPart", y=4)).widgets
    assert a.reasons[0].startswith("dashboard logs part has no query")
    assert b.reasons[0].startswith("dashboard part type has no New Relic equivalent: VideoPart")


def test_layout_is_scaled_to_twelve_columns_and_never_overlaps() -> None:
    d = dash(
        part("MarkdownPart", 0, 0, 12, 4),
        part("MarkdownPart", 12, 0, 12, 4),
        part("MarkdownPart", 0, 0, 24, 4),
    )
    for w in d.widgets:
        assert w.layout.column + w.layout.width - 1 <= 12
    boxes = [(w.layout.column, w.layout.row, w.layout.width, w.layout.height) for w in d.widgets]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert not (
                a[0] < b[0] + b[2]
                and b[0] < a[0] + a[2]
                and a[1] < b[1] + b[3]
                and b[1] < a[1] + a[3]
            )


def test_a_portal_time_range_is_noted_and_extra_lenses_become_pages() -> None:
    content = {"lenses": [{"parts": [part("MarkdownPart")]}, {"parts": [part("MarkdownPart")]}]}
    d = translate_portal(
        AzureDashboard(
            kind="portal",
            key="k",
            name="P",
            content=content,
            metadata={"model": {"timeRange": {"value": {"relative": {"duration": 24}}}}},
        )
    )
    assert [w.page for w in d.widgets] == [None, "Lens 2"]
    assert any("portal time range" in r for r in d.reasons)


def test_a_dashboard_without_parts_is_unsupported_and_an_error_is_a_placeholder() -> None:
    assert dash().confidence is Confidence.UNSUPPORTED
    broken = translate_portal(AzureDashboard(kind="portal", key="k", name="B", error="no lenses"))
    assert broken.widgets[0].kind is WidgetKind.UNSUPPORTED


def test_the_official_quickstart_dashboard_converts_and_emits() -> None:
    (found,) = load_dashboards(
        FIXTURES / "quickstart_templates" / "azure-portal-dashboard.azuredeploy.json"
    )
    d = translate_portal(found)
    kinds = [w.source_type for w in d.widgets]
    assert kinds.count("dashboard markdown") == 2
    assert sum(w.kind is WidgetKind.CHART for w in d.widgets) == 3
    assert nerdgraph_dashboard(d)
    for w in d.widgets:
        if w.kind is WidgetKind.UNSUPPORTED:
            assert steps_for(w.reasons)
            assert classify(w.reasons[0]) is not Category.OTHER
