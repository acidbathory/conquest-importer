"""The extended dashboard IR: fields, limits, round trip, and unchanged defaults."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from importer.core.dashboard_ir import (
    MAX_VARIABLES,
    MAX_WIDGETS,
    PROVIDER_MAX_HEIGHT,
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Series,
    Threshold,
    ThresholdSeverity,
    VariableType,
    Visualization,
    WidgetKind,
    WidgetOptions,
    order_variables,
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
    if "raw_series" in extra and "series" not in extra:
        base["series"] = []
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
            chart(raw_series=[RawNrqlSeries(nrql="SELECT count(*) FROM Log")]),
            chart(
                series=[Series(signal=signal(), period_seconds=60, auto_period=True)],
                options=WidgetOptions(unit="BYTES", legend=False, y_min=0, y_max=10),
                thresholds=[Threshold(severity=ThresholdSeverity.CRITICAL, from_value=90)],
            ),
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


def test_layout_still_accepts_what_it_did_and_a_tall_widget_records_the_clamp() -> None:
    assert Layout(column=1, row=1, width=1, height=100).height == 100
    assert PROVIDER_MAX_HEIGHT == 12
    tall = chart(layout=Layout(column=1, row=1, width=4, height=15))
    assert tall.layout.height == 15
    assert tall.reasons == ["height 15 clamped to 12"]
    again = IRWidget.model_validate_json(tall.model_dump_json())
    assert again.reasons == ["height 15 clamped to 12"]
    assert chart(layout=Layout(column=1, row=1, width=4, height=12)).reasons == []


@pytest.mark.parametrize(
    "bad",
    ["SELECT 1 FROM Log SINCE 1 day ago", "DROP", "SELECT 1 FROM Log; SELECT 2", ""],
)
def test_raw_series_refuses_unsafe_nrql(bad: str) -> None:
    with pytest.raises(ValidationError):
        RawNrqlSeries(nrql=bad)


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
        {"thresholds": [Threshold(severity=ThresholdSeverity.WARNING, from_value=1)],
         "visualization": Visualization.PIE},
        {
            "thresholds": [
                Threshold(severity=ThresholdSeverity.WARNING, from_value=1, column_name="c")
            ]
        },
        {"thresholds": [Threshold(severity=ThresholdSeverity.SUCCESS, from_value=1)],
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
        Threshold(severity=ThresholdSeverity.WARNING)
    with pytest.raises(ValidationError):
        Threshold(severity=ThresholdSeverity.WARNING, from_value=5, to_value=1)
    with pytest.raises(ValidationError):
        Threshold(severity=ThresholdSeverity.WARNING, from_value=float("nan"))


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
            "nrql": "FROM Log SELECT uniques(a) LIMIT {{y}}",
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


# --- round 2: model limits -----------------------------------------------------------------


def variable(**kwargs: Any) -> IRVariable:
    base: dict[str, Any] = {"name": "v", "title": "V", "type": VariableType.STRING}
    return IRVariable(**{**base, **kwargs})


@pytest.mark.parametrize("ids", [["12"], [True], [1.0], [10**30], [0], [-3], [10**15]])
def test_account_ids_must_be_real_positive_integers(ids: list[Any]) -> None:
    with pytest.raises(ValidationError):
        variable(type=VariableType.NRQL, nrql="FROM Log SELECT uniques(a)", account_ids=ids)


def test_account_ids_in_range_are_kept() -> None:
    v = variable(
        type=VariableType.NRQL, nrql="FROM Log SELECT uniques(a)", account_ids=[1, 10**15 - 1]
    )
    assert v.account_ids == [1, 10**15 - 1]


def test_defaults_must_be_among_the_enum_items() -> None:
    items = [{"value": "a"}, {"value": "b"}]
    variable(type=VariableType.ENUM, items=items, default_values=["a"])
    with pytest.raises(ValidationError, match="not one of the items"):
        variable(type=VariableType.ENUM, items=items, default_values=["c"])


def test_a_single_select_variable_takes_at_most_one_default() -> None:
    with pytest.raises(ValidationError, match="one default"):
        variable(default_values=["a", "b"])
    multi = variable(
        default_values=["a", "b"],
        multi_select=True,
        type=VariableType.NRQL,
        nrql="FROM Log SELECT uniques(a)",
    )
    assert multi.multi_select


def test_collections_are_capped() -> None:
    th = [Threshold(severity=ThresholdSeverity.WARNING, from_value=float(i)) for i in range(51)]
    with pytest.raises(ValidationError):
        chart(thresholds=th)
    raw = [RawNrqlSeries(nrql="SELECT 1 FROM Log")] * 51
    with pytest.raises(ValidationError):
        chart(raw_series=raw)
    with pytest.raises(ValidationError):
        dashboard([chart()] * (MAX_WIDGETS + 1))
    assert len(dashboard([chart()] * MAX_WIDGETS).widgets) == MAX_WIDGETS
    many = [variable(name=f"v{i}") for i in range(MAX_VARIABLES + 1)]
    with pytest.raises(ValidationError):
        dashboard([chart()], variables=many)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1e15 + 1, -1e16])
def test_numbers_must_be_finite_and_below_1e15(value: float) -> None:
    with pytest.raises(ValidationError):
        WidgetOptions(y_max=value)
    with pytest.raises(ValidationError):
        Threshold(severity=ThresholdSeverity.WARNING, from_value=value)
    assert WidgetOptions(y_max=1e15).y_max == 1e15


def test_a_chart_uses_series_or_raw_series_not_both() -> None:
    raw = [RawNrqlSeries(nrql="SELECT 1 FROM Log")]
    with pytest.raises(ValidationError, match="not both"):
        chart(raw_series=raw, series=[Series(signal=signal(), period_seconds=60)])


@pytest.mark.parametrize("viz", [Visualization.BAR, Visualization.PIE, Visualization.BILLBOARD])
def test_auto_period_needs_a_chart_over_time(viz: Visualization) -> None:
    auto = [Series(signal=signal(), period_seconds=60, auto_period=True)]
    with pytest.raises(ValidationError, match="auto_period"):
        chart(series=auto, visualization=viz)
    assert chart(series=auto, visualization=Visualization.AREA).series[0].auto_period


def test_auto_period_wins_over_the_period_seconds_value() -> None:
    a = Series(signal=signal(), period_seconds=300, auto_period=True)
    assert a.auto_period and a.period_seconds == 300


def test_raw_series_has_no_label_field() -> None:
    assert "label" not in RawNrqlSeries.model_fields
    assert ThresholdSeverity.CRITICAL.value == "critical"


@pytest.mark.parametrize(
    "bad",
    [
        "SELECT 1 FROM Log\u2028WHERE a = 1",
        "SELECT 1 FROM Log\u2029",
        "SELECT 1 FROM Log\u0085",
        "SELECT 1 FROM \u202eLog",
        "SELECT 1 FROM Log\u2066",
        "SIN\u200bCE",
        "SELECT 1 FROM Log\ufeff",
        "SELECT 1 FROM Log\uff1b",
    ],
)
def test_hidden_characters_are_refused_in_raw_series(bad: str) -> None:
    with pytest.raises(ValidationError):
        RawNrqlSeries(nrql=bad)


def nrql_var(name: str, nrql: str, **extra: Any) -> IRVariable:
    return IRVariable(name=name, title=name, type=VariableType.NRQL, nrql=nrql, **extra)


def test_a_variable_query_may_use_another_variable_where_nrql_substitutes_it() -> None:
    child = nrql_var("machine", "FROM Table SELECT uniques(machine) WHERE env IN ({{env}})")
    assert child.nrql is not None and "{{env}}" in child.nrql


def test_a_parent_must_be_defined_before_its_child() -> None:
    parent = nrql_var("env", "FROM Transaction SELECT uniques(environment)")
    child = nrql_var("machine", "FROM Table SELECT uniques(machine) WHERE env IN ({{env}})")
    assert dashboard([chart()], variables=[parent, child]).variables == [parent, child]
    with pytest.raises(ValidationError, match="before"):
        dashboard([chart()], variables=[child, parent])


def test_a_variable_query_may_not_use_an_undefined_variable() -> None:
    child = nrql_var("machine", "FROM Table SELECT uniques(machine) WHERE env IN ({{env}})")
    with pytest.raises(ValidationError, match="not defined"):
        dashboard([chart()], variables=[child])


def test_a_variable_query_may_not_use_itself() -> None:
    selfish = nrql_var("a", "FROM Table SELECT uniques(x) WHERE y IN ({{a}})")
    with pytest.raises(ValidationError, match="itself"):
        dashboard([chart()], variables=[selfish])


def test_a_cycle_of_variables_is_refused_by_name() -> None:
    a = nrql_var("a", "FROM T SELECT uniques(x) WHERE y IN ({{b}})")
    b = nrql_var("b", "FROM T SELECT uniques(y) WHERE x IN ({{a}})")
    for order in ([a, b], [b, a]):
        with pytest.raises(ValidationError, match="cycle"):
            dashboard([chart()], variables=order)


def test_order_variables_puts_parents_first_and_leaves_ordered_lists_alone() -> None:
    free = IRVariable(name="free", title="f", type=VariableType.STRING)
    parent = nrql_var("env", "FROM T SELECT uniques(e)")
    child = nrql_var("machine", "FROM T SELECT uniques(m) WHERE e IN ({{env}})")
    grandchild = nrql_var("disk", "FROM T SELECT uniques(d) WHERE m IN ({{machine}})")
    assert order_variables([grandchild, free, child, parent]) == [parent, child, grandchild, free]
    assert order_variables([free, parent]) == [free, parent]  # nothing to move


def test_order_variables_reports_a_cycle() -> None:
    a = nrql_var("a", "FROM T SELECT uniques(x) WHERE y IN ({{b}})")
    b = nrql_var("b", "FROM T SELECT uniques(y) WHERE x IN ({{a}})")
    with pytest.raises(ValueError, match="cycle: a, b"):
        order_variables([a, b])


def test_a_dashboard_without_dependent_variables_serialises_as_before() -> None:
    v = nrql_var("env", "FROM T SELECT uniques(e)")
    dumped = dashboard([chart()], variables=[v]).model_dump(mode="json")
    assert set(dumped["variables"][0]) == set(v.model_dump(mode="json"))
