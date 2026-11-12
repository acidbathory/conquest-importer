"""Dashboard sum() and count() across the series of a gauge: average * cardinality (approximate)."""

import pytest

from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

LINE = QueryOptions(variables={}, name_map=NameMap(), timeseries=True)
STAT = QueryOptions(variables={}, name_map=NameMap(), timeseries=False)


def test_chart_sum_by_facets_and_multiplies() -> None:
    out = convert_query('sum by (zone) (widget_depth{env="a"})', "prometheus", LINE)
    assert out.nrql == (
        "SELECT average(widget_depth) * cardinality(widget_depth) FROM Metric "
        "WHERE env = 'a' FACET zone LIMIT MAX TIMESERIES AUTO"
    )
    assert r.N_SUM_CARDINALITY in out.notes


def test_plain_sum_has_no_facet() -> None:
    out = convert_query("sum(widget_depth)", "prometheus", LINE)
    assert "FACET" not in out.nrql and "cardinality(widget_depth)" in out.nrql


@pytest.mark.parametrize("expr", ["sum(widget_depth)", "count by (zone) (widget_up)"])
def test_a_single_value_stays_refused_because_since_is_not_allowed_in_a_widget(expr: str) -> None:
    with pytest.raises(Unconvertible) as stop:
        convert_query(expr, "prometheus", STAT)
    assert stop.value.reason == r.C_GAUGE_SERIES


def test_count_is_cardinality() -> None:
    out = convert_query("count by (zone) (widget_up)", "prometheus", LINE)
    assert out.nrql.startswith("SELECT cardinality(widget_up) FROM Metric FACET zone")
    assert r.N_COUNT_CARDINALITY in out.notes and r.N_COUNT not in out.notes


def test_the_sum_stays_inside_arithmetic() -> None:
    out = convert_query("100 / sum(widget_depth)", "prometheus", LINE)
    assert "100 / (average(widget_depth) * cardinality(widget_depth))" in out.nrql


def test_a_proven_single_series_keeps_the_exact_path() -> None:
    names = NameMap(series={"widget_depth": ["zone"]})
    opts = QueryOptions(variables={}, name_map=names, timeseries=True)
    out = convert_query("sum by (zone) (widget_depth)", "prometheus", opts)
    assert "cardinality" not in out.nrql and r.N_SUM_GAUGE in out.notes


def test_a_known_series_set_with_a_stat_keeps_the_exact_nested_path() -> None:
    names = NameMap(series={"widget_depth": ["zone"]})
    opts = QueryOptions(variables={}, name_map=names, timeseries=False)
    out = convert_query("sum(widget_depth)", "prometheus", opts)
    assert "cardinality" not in out.nrql


@pytest.mark.parametrize(
    "expr",
    [
        "sum(widget_total)",  # a raw counter
        "sum(sum by (zone) (widget_depth))",  # nested aggregation
        "sum(widget_depth) > 5",
        "sum(widget_depth) or sum(other_depth)",
        "sum(widget_depth) unless sum(other_depth)",
        'sum(label_replace(widget_depth, "a", "$1", "b", "(.*)"))',
        "sum by (a) (widget_depth) / on(b) sum by (b) (other_depth)",
    ],
)
def test_other_shapes_stay_refused(expr: str) -> None:
    with pytest.raises(Unconvertible):
        convert_query(expr, "prometheus", LINE)
