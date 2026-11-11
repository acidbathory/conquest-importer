"""Workbook items to dashboard widgets: what converts, what becomes a placeholder, and why."""

import json
from typing import Any

import pytest

from importer.core.dashboard_ir import IRDashboard, IRWidget, Visualization, WidgetKind
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.ir import Confidence
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources.azure import dashboard_kql
from importer.sources.azure.dashboard_kql import LOG_FORWARDING, KqlNrql
from importer.sources.azure.dashboard_models import AzureDashboard
from importer.sources.azure.workbook import translate_workbook

VM = (
    "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg/providers/"
    "Microsoft.Compute/virtualMachines/vm1"
)


def wb(*items: dict[str, Any]) -> IRDashboard:
    content = {"version": "Notebook/1.0", "items": list(items)}
    return translate_workbook(
        AzureDashboard(kind="workbook", key="k", name="Test workbook", content=content)
    )


def text(body: str = "## Hello", **extra: Any) -> dict[str, Any]:
    return {"type": 1, "content": {"json": body}, **extra}


def metrics(**over: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "version": "MetricsItem/2.0",
        "size": 1,
        "chartType": 2,
        "resourceType": "microsoft.compute/virtualmachines",
        "resourceIds": [VM],
        "metrics": [
            {
                "namespace": "microsoft.compute/virtualmachines",
                "metric": "microsoft.compute/virtualmachines--Percentage CPU",
                "aggregation": 4,
            }
        ],
    }
    content.update(over)
    return {"type": 10, "content": content, "name": "cpu"}


def query(q: str = "Heartbeat | take 1", **over: Any) -> dict[str, Any]:
    content: dict[str, Any] = {"version": "KqlItem/1.0", "query": q, "queryType": 0}
    content.update(over)
    return {"type": 3, "content": content, "name": "q"}


def params(*items: dict[str, Any]) -> dict[str, Any]:
    return {"type": 9, "content": {"version": "KqlParameterItem/1.0", "parameters": list(items)}}


def only(d: IRDashboard) -> IRWidget:
    (w,) = [w for w in d.widgets if w.source_type != "workbook group"]
    return w


def nrql(w: IRWidget) -> str:
    return w.raw_series[0].nrql if w.raw_series else nerdgraph_query(w)


def nerdgraph_query(w: IRWidget) -> str:
    from importer.core.emit.nrql import widget_nrql

    return widget_nrql(w.series[0], w.visualization in (Visualization.LINE, Visualization.AREA))


def test_a_text_item_is_a_markdown_widget() -> None:
    w = only(wb(text("## Hello\nworld")))
    assert (w.kind, w.visualization, w.confidence) == (
        WidgetKind.MARKDOWN,
        Visualization.MARKDOWN,
        Confidence.EXACT,
    )
    assert w.text == "## Hello\nworld"


def test_text_that_names_parameters_is_approximate() -> None:
    w = only(wb(text("Showing {Resource}")))
    assert w.confidence is Confidence.APPROXIMATE
    assert any("parameters written in braces" in r for r in w.reasons)


def test_a_metric_item_is_a_chart_over_the_metric_table() -> None:
    w = only(wb(metrics()))
    assert w.kind is WidgetKind.CHART and w.visualization is Visualization.LINE
    q = nrql(w)
    assert q.startswith("SELECT average(azure.compute.virtualmachines.PercentageCPU) FROM Metric")
    assert f"azure.resourceId IN ('{VM}')" in q
    assert "collector.name = 'azure-monitor'" in q
    assert w.data_path is None  # native: the Azure Monitor integration
    assert any("UNVERIFIED" in r for r in w.reasons)


def test_aggregations_and_the_namespace_style_metric_id() -> None:
    w = only(
        wb(
            metrics(
                resourceType="microsoft.storage/storageaccounts",
                metrics=[
                    {
                        "namespace": "microsoft.storage/storageaccounts",
                        "metric": "microsoft.storage/storageaccounts-Transaction-Transactions",
                        "aggregation": 1,
                    },
                    {
                        "namespace": "microsoft.storage/storageaccounts",
                        "metric": "microsoft.storage/storageaccounts-Transaction-Transactions",
                        "aggregation": 7,
                    },
                ],
            )
        )
    )
    assert len(w.series) == 2
    from importer.core.emit.nrql import widget_nrql

    first, second = (widget_nrql(s, False) for s in w.series)
    assert first.startswith("SELECT sum(azure.storage.storageaccounts.Transactions)")
    assert second.startswith("SELECT count(azure.storage.storageaccounts.Transactions)")


@pytest.mark.parametrize(
    ("chart_type", "viz", "note"),
    [
        (0, Visualization.AREA, ""),
        (1, Visualization.AREA, "bars over time"),
        (2, Visualization.LINE, ""),
        (3, Visualization.LINE, "scatter"),
        (-1, Visualization.TABLE, ""),
    ],
)
def test_metric_chart_types(chart_type: int, viz: Visualization, note: str) -> None:
    w = only(wb(metrics(chartType=chart_type)))
    assert w.visualization is viz
    assert note in " ".join(w.reasons)


def test_a_resource_picker_becomes_a_variable_the_chart_uses() -> None:
    d = wb(
        params(
            {
                "name": "VMs",
                "label": "Machines",
                "type": 5,
                "multiSelect": True,
                "typeSettings": {"resourceTypeFilter": {"microsoft.compute/virtualmachines": True}},
            }
        ),
        metrics(resourceIds=["{VMs}"]),
    )
    (var,) = d.variables
    assert var.name == "VMs" and var.title == "Machines" and var.multi_select
    assert var.nrql is not None and "uniques(azure.resourceId" in var.nrql
    assert "metricName LIKE 'azure.compute.virtualmachines.%'" in var.nrql
    w = only(d)
    assert "azure.resourceId IN ({{VMs}})" in nrql(w)
    assert "TIMESERIES AUTO" in nrql(w)
    assert nerdgraph_dashboard(d)  # the whole dashboard emits


def test_a_resource_graph_backed_picker_widens_the_chart_and_says_so() -> None:
    d = wb(
        params({"name": "VMs", "type": 5, "queryType": 1, "query": "Resources | where x"}),
        metrics(resourceIds=["{VMs}"]),
    )
    w = only(d)
    assert not d.variables
    assert "azure.resourceId" in nrql(w) and "IN" not in nrql(w).split("FACET")[0]
    assert "FACET azure.resourceId" in nrql(w)
    assert any("resource selection {VMs} is not migrated" in r for r in w.reasons)


def test_a_metric_with_a_parameter_in_its_name_is_a_placeholder() -> None:
    w = only(wb(metrics(metrics=[{"namespace": "x", "metric": "{Metric}", "aggregation": 4}])))
    assert w.kind is WidgetKind.UNSUPPORTED
    assert w.reasons[0].startswith("workbook item depends on a parameter")


def test_a_custom_metric_namespace_is_a_placeholder() -> None:
    w = only(wb(metrics(metrics=[{"namespace": "custom/ns", "metric": "Foo", "aggregation": 4}])))
    assert w.kind is WidgetKind.UNSUPPORTED
    assert w.reasons[0].startswith("workbook metric cannot be converted")


def test_a_kql_query_is_a_placeholder_until_a_translator_is_connected() -> None:
    w = only(wb(query()))
    assert w.kind is WidgetKind.UNSUPPORTED and w.confidence is Confidence.MANUAL
    assert w.reasons[0].startswith("workbook query is KQL")


@pytest.mark.parametrize(
    ("kql_viz", "viz", "timeseries"),
    [
        ("timechart", Visualization.LINE, True),
        ("linechart", Visualization.LINE, True),
        ("areachart", Visualization.AREA, True),
        ("barchart", Visualization.BAR, False),
        ("barchart", Visualization.AREA, True),
        ("piechart", Visualization.PIE, False),
        ("table", Visualization.TABLE, False),
        (None, Visualization.TABLE, False),
        ("tiles", Visualization.BILLBOARD, False),
        ("graph", Visualization.TABLE, False),
        ("map", Visualization.TABLE, False),
    ],
)
def test_translated_kql_maps_the_visualization(
    monkeypatch: pytest.MonkeyPatch, kql_viz: str | None, viz: Visualization, timeseries: bool
) -> None:
    def fake(q: str, variables: Any) -> KqlNrql:
        return KqlNrql(nrql="SELECT count(*) FROM Log", timeseries=timeseries)

    monkeypatch.setattr(dashboard_kql, "translate_kql", fake)
    w = only(wb(query(visualization=kql_viz)))
    assert w.kind is WidgetKind.CHART and w.visualization is viz
    assert w.data_path == LOG_FORWARDING
    assert w.raw_series[0].nrql.startswith("SELECT count(*) FROM Log")


def test_translator_notes_and_a_refusal_reach_the_widget(monkeypatch: pytest.MonkeyPatch) -> None:
    results: list[KqlNrql | str] = [
        KqlNrql(nrql="SELECT count(*) FROM Log", notes=("table name guessed",)),
        "kql query cannot be converted: join",
    ]
    monkeypatch.setattr(dashboard_kql, "translate_kql", lambda _q, _v: results.pop(0))
    a, b = wb(query(), query()).widgets
    assert "table name guessed" in a.reasons
    assert b.kind is WidgetKind.UNSUPPORTED and b.reasons == ["kql query cannot be converted: join"]


@pytest.mark.parametrize(
    ("query_type", "start"),
    [
        (1, "workbook query is Azure Resource Graph"),
        (12, "workbook query calls an Azure Resource Manager"),
        (7, "workbook query merges"),
        (8, "workbook query holds static JSON"),
        (9, "workbook query reads Azure Data Explorer"),
        (99, "workbook query type is not supported"),
    ],
)
def test_other_query_types_are_placeholders_with_their_own_reason(
    query_type: int, start: str
) -> None:
    w = only(wb(query(queryType=query_type)))
    assert w.kind is WidgetKind.UNSUPPORTED and w.reasons[0].startswith(start)


def test_conditional_visibility_is_a_placeholder_for_every_item_inside() -> None:
    group = {
        "type": 12,
        "conditionalVisibility": {"parameterName": "x", "comparison": "isEqualTo", "value": "1"},
        "content": {"groupType": "editable", "title": "g", "items": [text(), metrics()]},
    }
    d = wb(group, {**text(), "conditionalVisibilities": [{"parameterName": "y"}]})
    kinds = [(w.kind, w.reasons[0][:30]) for w in d.widgets]
    assert kinds == [(WidgetKind.UNSUPPORTED, "workbook conditional visibilit")] * 3


def test_tabs_become_pages() -> None:
    tabs = {
        "type": 11,
        "content": {
            "version": "LinkItem/1.0",
            "style": "tabs",
            "links": [
                {
                    "id": "1",
                    "linkTarget": "parameter",
                    "parameterName": "tab",
                    "parameterValue": "a",
                    "linkLabel": "Overview",
                },
                {
                    "id": "2",
                    "linkTarget": "parameter",
                    "parameterName": "tab",
                    "parameterValue": "b",
                    "linkLabel": "Details",
                },
            ],
        },
    }

    def tab_group(value: str, *items: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": 12,
            "conditionalVisibility": {
                "parameterName": "tab",
                "comparison": "isEqualTo",
                "value": value,
            },
            "content": {"groupType": "editable", "items": list(items)},
        }

    d = wb(tabs, tab_group("a", text("one")), tab_group("b", text("two"), text("three")))
    assert [(w.page, w.kind) for w in d.widgets] == [
        ("Overview", WidgetKind.MARKDOWN),
        ("Details", WidgetKind.MARKDOWN),
        ("Details", WidgetKind.MARKDOWN),
    ]
    assert [w.layout.row for w in d.widgets] == [1, 1, 3]
    assert any("tabs became pages" in r for r in d.reasons)


def test_a_group_is_a_heading_followed_by_its_items() -> None:
    d = wb(
        {"type": 12, "content": {"groupType": "editable", "title": "Network", "items": [text()]}}
    )
    heading, body = d.widgets
    assert heading.source_type == "workbook group" and heading.text == "## Network"
    assert body.kind is WidgetKind.MARKDOWN


def test_a_linked_template_group_is_a_placeholder() -> None:
    w = only(wb({"type": 12, "content": {"groupType": "template", "loadFromTemplateId": "x"}}))
    assert w.reasons[0].startswith("workbook group loads another workbook template")


def test_links_are_shown_as_text_and_an_unknown_item_type_is_a_placeholder() -> None:
    links = {
        "type": 11,
        "content": {
            "style": "list",
            "links": [
                {"linkTarget": "Url", "linkLabel": "Docs", "url": "https://example.com/x"},
                {"linkTarget": "OpenBlade", "linkLabel": "Blade"},
            ],
        },
    }
    a, b = wb(links, {"type": 99, "content": {}}).widgets
    assert a.kind is WidgetKind.MARKDOWN and "[Docs](https://example.com/x)" in (a.text or "")
    assert "Blade" in (a.text or "") and a.confidence is Confidence.APPROXIMATE
    assert b.reasons[0].startswith("workbook item type is not supported")


def test_a_dependent_parameter_is_not_migrated_and_its_users_are_placeholders() -> None:
    d = wb(
        params(
            {"name": "A", "type": 2, "query": "x | distinct a"},
            {"name": "B", "type": 5, "query": "{A} | y"},
        ),
        metrics(resourceIds=["{B}"]),
        metrics(
            metrics=[
                {
                    "namespace": "microsoft.compute/virtualmachines",
                    "metric": "{B}",
                    "aggregation": 4,
                }
            ]
        ),
    )
    first, second = d.widgets
    assert first.kind is WidgetKind.CHART  # the picker is widened, with the note
    assert second.kind is WidgetKind.UNSUPPORTED
    assert second.reasons[0].startswith("workbook item depends on a parameter")


def test_a_static_dropdown_and_a_kql_dropdown_become_variables_when_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(q: str, variables: Any) -> KqlNrql:
        if q.startswith("param"):
            return KqlNrql(nrql="SELECT uniques(host, 200) FROM Log")
        assert {"Host", "Size"} <= set(variables)
        return KqlNrql(
            nrql="SELECT count(*) FROM Log WHERE host IN ({{Host}})", variables=("Host",)
        )

    monkeypatch.setattr(dashboard_kql, "translate_kql", fake)
    d = wb(
        params(
            {"name": "Host", "type": 2, "query": "param", "queryType": 0},
            {
                "name": "Size",
                "type": 2,
                "queryType": 8,
                "query": json.dumps(
                    {
                        "version": "1.0.0",
                        "content": json.dumps(
                            [{"value": "s", "label": "Small"}, {"value": "l", "label": "Large"}]
                        ),
                        "transformers": None,
                    }
                ),
            },
        ),
        query("data | where host == '{Host}'"),
    )
    assert [v.name for v in d.variables] == ["Host"]  # Size is not used by a converted widget
    assert only(d).kind is WidgetKind.CHART
    assert nerdgraph_dashboard(d)


def test_layout_puts_half_width_items_side_by_side() -> None:
    d = wb(
        {**text("a"), "customWidth": "50"},
        {**text("b"), "customWidth": "50"},
        text("c"),
        {**text("d"), "halfWidth": True},
    )
    cells = [(w.layout.column, w.layout.row, w.layout.width) for w in d.widgets]
    assert cells[:2] == [(1, 1, 6), (7, 1, 6)]
    assert cells[2][0] == 1 and cells[2][2] == 12 and cells[2][1] > 1
    assert cells[3][2] == 6


def test_a_workbook_that_could_not_be_read_is_one_placeholder() -> None:
    d = translate_workbook(
        AzureDashboard(
            kind="workbook", key="k", name="Broken", error="serializedData is not valid JSON"
        )
    )
    (w,) = d.widgets
    assert w.kind is WidgetKind.UNSUPPORTED and d.confidence is Confidence.MANUAL
    assert w.reasons[0].startswith("workbook content could not be read")


def test_an_empty_workbook_is_unsupported() -> None:
    d = wb()
    assert d.confidence is Confidence.UNSUPPORTED and not d.widgets


def test_string_content_is_read_like_object_content() -> None:
    item = {"type": 1, "content": json.dumps({"json": "text"})}
    assert only(wb(item)).text == "text"


def test_every_placeholder_reason_has_a_remedy_and_a_category() -> None:
    items = [
        query(),
        query(queryType=1),
        query(queryType=12),
        query(queryType=7),
        query(queryType=8),
        query(queryType=9),
        query(queryType=99),
        query(""),
        metrics(metrics=[{"namespace": "custom/ns", "metric": "Foo"}]),
        metrics(metrics=[{"namespace": "x", "metric": "{M}"}]),
        {"type": 99, "content": {}},
        {"type": 12, "content": {"groupType": "template"}},
        {**text(), "conditionalVisibility": {"parameterName": "p"}},
    ]
    d = wb(*items)
    placeholders = [w for w in d.widgets if w.kind is WidgetKind.UNSUPPORTED]
    assert len(placeholders) == len(items)
    for w in placeholders:
        assert steps_for(w.reasons), w.reasons
        assert classify(w.reasons[0]) is not Category.OTHER, w.reasons
    broken = translate_workbook(AzureDashboard(kind="workbook", key="k", name="b", error="x"))
    assert steps_for(broken.widgets[0].reasons)


def test_an_options_group_parameter_makes_tabs_for_single_items_too() -> None:
    options = json.dumps([{"value": "o", "label": "Overview"}, {"value": "d", "label": "Details"}])
    d = wb(
        params({"name": "selectedTab", "type": 10, "jsonData": options}),
        {
            **metrics(),
            "conditionalVisibility": {
                "parameterName": "selectedTab",
                "comparison": "isEqualTo",
                "value": "o",
            },
        },
        {
            **text("d1"),
            "conditionalVisibility": {
                "parameterName": "selectedTab",
                "comparison": "isEqualTo",
                "value": "d",
            },
        },
        {
            **text("d2"),
            "conditionalVisibilities": [
                {"parameterName": "selectedTab", "comparison": "isEqualTo", "value": "d"},
                {"parameterName": "Resource", "comparison": "isNotEqualTo"},
            ],
        },
    )
    assert [(w.page, w.kind) for w in d.widgets] == [
        ("Overview", WidgetKind.CHART),
        ("Details", WidgetKind.MARKDOWN),
        ("Details", WidgetKind.MARKDOWN),
    ]
    last = d.widgets[2]
    assert last.confidence is Confidence.APPROXIMATE
    assert any("parameter Resource has a value" in r for r in last.reasons)


def test_a_condition_that_is_not_a_tab_stays_a_placeholder() -> None:
    d = wb(
        params(
            {
                "name": "selectedTab",
                "type": 10,
                "jsonData": json.dumps([{"value": "o", "label": "O"}]),
            }
        ),
        {
            **text(),
            "conditionalVisibility": {
                "parameterName": "selectedTab",
                "comparison": "isNotEqualTo",
                "value": "o",
            },
        },
        {
            **text(),
            "conditionalVisibility": {
                "parameterName": "other",
                "comparison": "isEqualTo",
                "value": "o",
            },
        },
    )
    assert [w.kind for w in d.widgets] == [WidgetKind.UNSUPPORTED] * 2


def time_param(name: str = "TimeRange") -> dict[str, Any]:
    return {"name": name, "type": 4, "value": {"durationMs": 3600000}}


def test_a_workbook_time_range_parameter_in_a_query_follows_the_time_picker() -> None:
    q = (
        "AzureDiagnostics | where ResourceType == 'VAULTS' | where TimeGenerated {TimeRange} "
        "| summarize count() by Op, bin(TimeGenerated, {TimeRange:grain})"
    )
    w = only(wb(params(time_param()), query(q)))
    assert w.kind is not WidgetKind.UNSUPPORTED
    assert "TIMESERIES AUTO" in nrql(w) and "{" not in nrql(w)


def test_a_dropdown_parameter_in_a_query_is_not_guessed() -> None:
    q = "AzureDiagnostics | where ResourceType == '{Pick}' | summarize count()"
    pick = {"name": "Pick", "type": 2, "jsonData": '[{"value":"a","label":"A"}]'}
    w = only(wb(params(time_param(), pick), query(q)))
    assert w.kind is WidgetKind.UNSUPPORTED and "{Pick}" in w.reasons[0]
    assert steps_for(w.reasons)
