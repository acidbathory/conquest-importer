"""The extended dashboard IR: fields, limits, round trip, and unchanged defaults."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from importer.core.dashboard_ir import (
    PROVIDER_MAX_HEIGHT,
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Series,
    Severity,
    Threshold,
    VariableType,
    Visualization,
    WidgetKind,
    WidgetOptions,
    layout_problem,
)
from importer.core.ir import (
    Aggregation,
    Confidence,
    MetricSignal,
    MetricTerm,
    NrTarget,
)


def signal() -> MetricSignal:
    term = MetricTerm(
        metric="cpu",
        space_aggregation=Aggregation.AVG,
        nr_target=NrTarget(event="Metric", attribute="cpu"),
    )
    return MetricSignal(terms={"a": term}, aggregation=Aggregation.AVG)


def chart(**extra: Any) -> IRWidget:
    base: dict[str, Any] = {
        "kind": WidgetKind.CHART,
        "source_type": "t",
        "title": "w",
        "layout": Layout(column=1, row=1, width=4, height=3),
        "visualization": Visualization.LINE,
        "series": [Series(signal=signal(), period_seconds=60)],
        "confidence": Confidence.EXACT,
    }
    return IRWidget(**{**base, **extra})


def dashboard(widgets: list[IRWidget], **extra: Any) -> IRDashboard:
    return IRDashboard(
        source_system="grafana",
        source_key="k",
        name="n",
        widgets=widgets,
        confidence=Confidence.EXACT,
        **extra,
    )


def test_defaults_are_left_out_of_the_serialised_ir() -> None:
    data = json.loads(dashboard([chart()]).model_dump_json())
    assert "variables" not in data
    widget = data["widgets"][0]
    assert not {"options", "thresholds", "raw_series"} & set(widget)
    assert "auto_period" not in widget["series"][0]


def test_set_fields_are_serialised_and_round_trip() -> None:
    dash = dashboard(
        [
            chart(
                series=[Series(signal=signal(), period_seconds=60, auto_period=True)],
                raw_series=[RawNrqlSeries(label="raw", nrql="SELECT count(*) FROM Log")],
                options=WidgetOptions(unit="BYTES", legend=False, y_min=0, y_max=10),
                thresholds=[Threshold(severity=Severity.CRITICAL, from_value=90)],
            )
        ],
        variables=[IRVariable(name="host", title="Host", type=VariableType.STRING)],
    )
    again = IRDashboard.model_validate_json(dash.model_dump_json())
    assert again == dash
    assert json.loads(dash.model_dump_json())["variables"][0]["name"] == "host"


def test_a_table_widget_is_a_chart_without_timeseries() -> None:
    w = chart(visualization=Visualization.TABLE)
    assert w.visualization == Visualization.TABLE
    assert Visualization.TABLE not in TIMESERIES_VISUALIZATIONS


def test_an_old_ir_file_without_the_new_fields_still_loads() -> None:
    text = dashboard([chart()]).model_dump_json()
    assert IRDashboard.model_validate_json(text).widgets[0].raw_series == []


def test_layout_still_accepts_what_it_did_and_the_provider_limit_is_reported() -> None:
    assert Layout(column=1, row=1, width=1, height=100).height == 100
    assert PROVIDER_MAX_HEIGHT == 12
    assert layout_problem(Layout(column=1, row=1, width=1, height=12)) is None
    problem = layout_problem(Layout(column=1, row=1, width=1, height=13))
    assert problem is not None and "12" in problem and "13" in problem


@pytest.mark.parametrize(
    "bad",
    ["SELECT 1 FROM Log SINCE 1 day ago", "DROP", "SELECT 1 FROM Log; SELECT 2", ""],
)
def test_raw_series_refuses_unsafe_nrql(bad: str) -> None:
    with pytest.raises(ValidationError):
        RawNrqlSeries(label="x", nrql=bad)


def test_raw_series_label_is_bounded() -> None:
    with pytest.raises(ValidationError):
        RawNrqlSeries(label="x" * 101, nrql="SELECT 1 FROM Log")


def test_a_chart_needs_at_least_one_query_of_either_kind() -> None:
    w = chart(series=[], raw_series=[RawNrqlSeries(label=None, nrql="SELECT 1 FROM Log")])
    assert w.raw_series


@pytest.mark.parametrize(
    "extra",
    [
        {"options": WidgetOptions(y_zero=True), "visualization": Visualization.AREA},
        {"options": WidgetOptions(stacked=True), "visualization": Visualization.LINE},
        {"options": WidgetOptions(y_min=0), "visualization": Visualization.PIE},
        {"options": WidgetOptions(legend=True), "visualization": Visualization.MARKDOWN},
        {"thresholds": [Threshold(severity=Severity.WARNING, from_value=1)],
         "visualization": Visualization.PIE},
        {"thresholds": [Threshold(severity=Severity.WARNING, from_value=1, column_name="c")]},
        {"thresholds": [Threshold(severity=Severity.SUCCESS, from_value=1)],
         "visualization": Visualization.BILLBOARD},
    ],
)  # fmt: skip
def test_options_the_provider_cannot_express_are_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        chart(**extra)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"y_min": 5, "y_max": 1},
        {"unit": "B" * 41},
        {"unit": "B\nC"},
        {"decimals": -1},
        {"y_min": float("inf")},
    ],
)
def test_invalid_widget_options_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WidgetOptions(**kwargs)


def test_a_threshold_needs_a_bound_and_ordered_bounds() -> None:
    with pytest.raises(ValidationError):
        Threshold(severity=Severity.WARNING)
    with pytest.raises(ValidationError):
        Threshold(severity=Severity.WARNING, from_value=5, to_value=1)
    with pytest.raises(ValidationError):
        Threshold(severity=Severity.WARNING, from_value=float("nan"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "bad name", "title": "t", "type": VariableType.STRING},
        {"name": "1x", "title": "t", "type": VariableType.STRING},
        {"name": "{{x}}", "title": "t", "type": VariableType.STRING},
        {"name": "x", "title": "", "type": VariableType.STRING},
        {"name": "x", "title": "t", "type": VariableType.ENUM},
        {"name": "x", "title": "t", "type": VariableType.NRQL},
        {"name": "x", "title": "t", "type": VariableType.STRING, "multi_select": True},
        {"name": "x", "title": "t", "type": VariableType.STRING, "nrql": "SELECT 1 FROM Log"},
        {
            "name": "x",
            "title": "t",
            "type": VariableType.NRQL,
            "nrql": "FROM Log SELECT uniques(a) LIMIT {{n}}",
        },
        {
            "name": "x",
            "title": "t",
            "type": VariableType.NRQL,
            "nrql": "FROM Log SELECT uniques(a) WHERE b IN ({{y}})",
        },
        {
            "name": "x",
            "title": "t",
            "type": VariableType.NRQL,
            "nrql": "FROM Log SELECT uniques(a) SINCE 1 day ago",
        },
        {
            "name": "x",
            "title": "t",
            "type": VariableType.ENUM,
            "items": [{"value": "a"}, {"value": "a"}],
        },
        {"name": "x", "title": "t", "type": VariableType.STRING, "account_ids": [0]},
    ],
)
def test_invalid_variables_are_refused(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        IRVariable(**kwargs)


def test_valid_variables_of_each_type() -> None:
    IRVariable(
        name="host",
        title="Host",
        type=VariableType.NRQL,
        nrql="FROM Metric SELECT uniques(host)",
        multi_select=True,
        default_values=["*"],
        account_ids=[1, 2],
    )
    IRVariable(
        name="env", title="Env", type=VariableType.ENUM, items=[{"title": "Prod", "value": "prod"}]
    )
    IRVariable(name="free", title="Free", type=VariableType.STRING, default_values=["x"])


def test_duplicate_variable_names_are_refused() -> None:
    v = IRVariable(name="a", title="A", type=VariableType.STRING)
    with pytest.raises(ValidationError):
        dashboard([chart()], variables=[v, v])


def test_a_raw_query_may_only_use_defined_variables() -> None:
    raw = RawNrqlSeries(label=None, nrql="SELECT count(*) FROM Log WHERE h IN ({{host}})")
    with pytest.raises(ValidationError, match="not defined"):
        dashboard([chart(raw_series=[raw])])
    v = IRVariable(name="host", title="Host", type=VariableType.STRING)
    assert dashboard([chart(raw_series=[raw])], variables=[v]).variables == [v]
