"""Dashboard placement, identity and robustness: nothing overlaps, collides or aborts."""

from typing import Any

import pytest

from importer.core.dashboard_ir import IRDashboard, IRWidget, Layout, Visualization, WidgetKind
from importer.core.emit.dashboard import dashboard_id, dashboards_terraform, nerdgraph_dashboard
from importer.core.remediation import remedies_for, steps_for
from importer.sources.gcp import dashboards as gd
from importer.sources.gcp.dashboard_models import GcpDashboard
from importer.sources.gcp.dashboards import translate_dashboard, translate_dashboards
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import seconds, translate_policy

CPU = 'metric.type="compute.googleapis.com/instance/cpu/utilization"'
DISK = 'metric.type="compute.googleapis.com/instance/disk/read_bytes_count"'


def text(title: str = "t") -> dict[str, Any]:
    return {"title": title, "text": {"content": "x"}}


def build(**layout: Any) -> IRDashboard:
    return translate_dashboard(GcpDashboard.model_validate({"displayName": "d", **layout}))


def overlap(a: Layout, b: Layout) -> bool:
    return (
        a.column < b.column + b.width
        and b.column < a.column + a.width
        and a.row < b.row + b.height
        and b.row < a.row + a.height
    )


def assert_clean(dash: IRDashboard) -> None:
    boxes = [w.layout for w in dash.widgets]
    for i, a in enumerate(boxes):
        assert a.column >= 1 and a.column + a.width - 1 <= 12, a
        for b in boxes[i + 1 :]:
            assert not overlap(a, b), (a, b)


def tile(x: Any, y: Any, w: Any, h: Any, title: str = "t") -> dict[str, Any]:
    return {"xPos": x, "yPos": y, "width": w, "height": h, "widget": text(title)}


LAYOUTS: dict[str, dict[str, Any]] = {
    "row of 14": {"rowLayout": {"rows": [{"widgets": [text(str(i)) for i in range(14)]}]}},
    "17 columns": {"columnLayout": {"columns": [{"widgets": [text(str(i))]} for i in range(17)]}},
    "odd heights": {
        "mosaicLayout": {
            "columns": 48,
            "tiles": [tile(0, 0, 24, 5), tile(0, 5, 24, 3), tile(24, 0, 24, 7), tile(24, 7, 24, 3)],
        }
    },
    "narrow tiles": {
        "mosaicLayout": {"columns": 48, "tiles": [tile(i, 0, 1, 4, str(i)) for i in range(8)]}
    },
    "missing positions": {"mosaicLayout": {"tiles": [{"widget": text(str(i))} for i in range(4)]}},
    "odd types": {
        "mosaicLayout": {
            "columns": 48,
            "tiles": [
                tile(None, None, None, None),
                tile(1.5, 2.5, 10.5, 4.5),
                tile(True, False, True, True),
                tile("7", "x", "-3", "5"),
            ],
        }
    },
    "negative and huge": {
        "mosaicLayout": {
            "columns": 48,
            "tiles": [
                tile(-5, -5, -1, -1),
                tile(10**9, 10**9, 10**9, 10**9),
                tile(0, 0, 48, 10**9),
            ],
        }
    },
    "same spot": {"mosaicLayout": {"columns": 48, "tiles": [tile(0, 0, 24, 8)] * 3}},
}


@pytest.mark.parametrize("layout", LAYOUTS.values(), ids=LAYOUTS.keys())
def test_no_layout_ever_overlaps_or_leaves_the_grid(layout: dict[str, Any]) -> None:
    dash = build(**layout)
    assert dash.widgets
    assert_clean(dash)


def test_a_resolved_collision_is_said_in_the_dashboard_reasons() -> None:
    dash = build(**LAYOUTS["same spot"])
    assert any("overlapped" in r and "moved down" in r for r in dash.reasons)
    clean = build(gridLayout={"widgets": [text("a"), text("b")]})
    assert not any("overlapped" in r for r in clean.reasons)


def test_fourteen_in_a_row_are_all_kept() -> None:
    assert len(build(**LAYOUTS["row of 14"]).widgets) == 14


def chart(plot: str, flt: str, agg: dict[str, Any] | None = None, kind: str = "xyChart") -> Any:
    query = {"timeSeriesFilter": {"filter": flt, "aggregation": agg or {}}}
    body = (
        {"dataSets": [{"plotType": plot, "timeSeriesQuery": query}]}
        if kind == "xyChart"
        else {"timeSeriesQuery": query}
    )
    return {"title": "w", kind: body}


def widget_of(raw: dict[str, Any]) -> IRWidget:
    return build(gridLayout={"widgets": [raw]}).widgets[0]


def test_stacked_bars_get_a_time_axis_and_say_how_they_are_shown() -> None:
    w = widget_of(chart("STACKED_BAR", CPU))
    assert w.visualization is Visualization.AREA
    assert any("stacked bar" in r and "area chart" in r for r in w.reasons)
    dash = build(gridLayout={"widgets": [chart("STACKED_BAR", CPU)]})
    payload = nerdgraph_dashboard(dash)["variables"]["dashboard"]["pages"][0]["widgets"][0]
    assert "TIMESERIES" in payload["rawConfiguration"]["nrqlQueries"][0]["query"]


def test_the_counter_note_does_not_claim_a_per_window_value_for_every_chart() -> None:
    agg = {"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_RATE"}
    for raw in (chart("LINE", DISK, agg), chart("LINE", DISK, agg, "scorecard")):
        note = next(r for r in widget_of(raw).reasons if "counter metric" in r)
        assert "adds up those changes" in note and "whole time range" in note
        assert "per 300s window" not in note


def test_a_scorecard_with_a_group_by_says_it_shows_one_value_per_group() -> None:
    agg = {
        "alignmentPeriod": "300s",
        "perSeriesAligner": "ALIGN_MEAN",
        "groupByFields": ["resource.label.zone"],
    }
    w = widget_of(chart("LINE", CPU, agg, "scorecard"))
    assert w.visualization is Visualization.BILLBOARD
    assert any("one value per group" in r for r in w.reasons)
    plain = widget_of(
        chart(
            "LINE", CPU, {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}, "scorecard"
        )
    )
    assert not any("one value per group" in r for r in plain.reasons)


def named(n: int, **extra: Any) -> GcpDashboard:
    return GcpDashboard.model_validate({"gridLayout": {"widgets": [text()]}, **extra})


def test_dashboards_with_the_same_long_name_prefix_or_no_name_get_distinct_files() -> None:
    prefix = "projects/demo-project-123/dashboards/" + "a" * 220
    dashboards = [named(i, name=prefix + str(i)) for i in range(4)] + [named(i) for i in range(4)]
    out = translate_dashboards(dashboards)
    assert len({d.source_key for d in out}) == 8
    assert len({dashboard_id(d) for d in out}) == 8
    tf = dashboards_terraform([(dashboard_id(d), d) for d in out])
    resources = [line for line in tf.splitlines() if line.startswith("resource ")]
    assert len(resources) == 8 and len(set(resources)) == 8


def test_the_same_name_twice_is_suffixed_not_dropped() -> None:
    out = translate_dashboards([named(1, name="projects/p/dashboards/x")] * 2)
    assert [d.source_key for d in out] == ["projects/p/dashboards/x", "projects/p/dashboards/x#2"]


@pytest.mark.parametrize(
    "period", ["\u00b2s", "\u0663\u0660\u0660s", "\u00b3s", "1.5s", "-60s", " 60s", "60", "60S"]
)
def test_odd_periods_never_raise_and_count_as_unsupported(period: str) -> None:
    assert seconds(period) == 0
    agg = {"alignmentPeriod": period, "perSeriesAligner": "ALIGN_MEAN"}
    w = widget_of(chart("LINE", CPU, agg))
    assert w.kind is WidgetKind.CHART  # a missing/zero period falls back to the polling interval


def test_an_odd_period_in_an_alert_does_not_raise() -> None:
    policy = GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "conditions": [
                {
                    "displayName": "c",
                    "conditionThreshold": {
                        "filter": CPU,
                        "comparison": "COMPARISON_GT",
                        "duration": "\u00b2s",
                        "aggregations": [
                            {"alignmentPeriod": "\u00b2s", "perSeriesAligner": "ALIGN_MEAN"}
                        ],
                    },
                }
            ],
        }
    )
    (monitor,) = translate_policy(policy)
    assert monitor.confidence.value == "manual"


def test_a_widget_that_raises_becomes_a_failed_widget_and_the_run_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = gd._text

    def boom(body: dict[str, Any], title: str, layout: Layout) -> IRWidget:
        if title == "bad":
            raise RuntimeError("boom")
        return real(body, title, layout)

    monkeypatch.setattr(gd, "_text", boom)
    dash = build(gridLayout={"widgets": [text("bad"), text("good")]})
    bad, good = dash.widgets
    assert bad.kind is WidgetKind.UNSUPPORTED and "could not be read" in bad.reasons[0]
    assert steps_for(bad.reasons)
    assert good.kind is WidgetKind.MARKDOWN


@pytest.mark.parametrize("field", ["displayName", "name"])
def test_a_null_name_is_tolerated(field: str) -> None:
    dash = translate_dashboard(
        GcpDashboard.model_validate({field: None, "gridLayout": {"widgets": [text()]}})
    )
    assert dash.name == "dashboard" and dash.widgets


@pytest.mark.parametrize(
    "kind", ["uptime check", "forecast", "no thresholds", "mql conditions", "x@corp.example"]
)
def test_a_widget_type_never_reaches_the_reason_so_no_older_rule_can_claim_it(kind: str) -> None:
    w = widget_of({"title": "w", kind: {}})
    assert w.reasons == ["dashboard widget type is not supported"]
    assert [r.title for r in remedies_for(w.reasons)] == ["Dashboard widget type not converted"]
