"""Findings of the independent review: wrong numbers, silent approximations and NR limits."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.core.ir import Confidence
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.layout import Placed, lay_out
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.panels import Draft
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboard_convert.variables import (
    RawVar,
    Variables,
    convert_variables,
)
from importer.sources.grafana.dashboards.models import Dashboard, Variable
from importer.sources.grafana.dashboards.plan import plan_dashboard

LINE = QueryOptions(variables={}, name_map=NameMap(), timeseries=True)
STAT = QueryOptions(variables={}, name_map=NameMap(), timeseries=False)


@pytest.mark.parametrize("agg", ["avg", "min", "max"])
@pytest.mark.parametrize("call", ["rate", "irate", "increase"])
@pytest.mark.parametrize("by", ["", " by (job)"])
@pytest.mark.parametrize("opts", [LINE, STAT])
def test_an_average_minimum_or_maximum_of_rates_is_refused(
    agg: str, call: str, by: str, opts: QueryOptions
) -> None:
    # rate(average(c), 1 second) divides one sample's delta by the whole bucket: too low by
    # the number of scrapes in a bucket, so no number is better than that number
    with pytest.raises(Unconvertible) as stop:
        convert_query(f"{agg}{by} ({call}(x_total[5m]))", "prometheus", opts)
    assert stop.value.reason == r.C_AGG_RATE


def test_the_cpu_idle_formula_is_a_placeholder_not_a_wrong_percentage() -> None:
    expr = '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))'
    with pytest.raises(Unconvertible) as stop:
        convert_query(expr, "prometheus", LINE)
    assert stop.value.reason == r.C_AGG_RATE


def test_a_sum_sums_still_converts_for_rates() -> None:
    out = convert_query("sum by (job) (rate(x_total[5m]))", "prometheus", LINE)
    assert out.nrql.startswith("SELECT rate(sum(x_total), 1 second)")
    assert r.N_SUM_GAUGE not in out.notes


@pytest.mark.parametrize("opts", [LINE, STAT])
def test_sum_of_a_gauge_says_it_adds_every_sample(opts: QueryOptions) -> None:
    out = convert_query("sum by (instance) (node_memory_MemFree_bytes)", "prometheus", opts)
    assert r.N_SUM_GAUGE in out.notes
    assert r.N_SUM_GAUGE not in convert_query("avg(up)", "prometheus", opts).notes


def draft(title: str = "w") -> Draft:
    return Draft(WidgetKind.MARKDOWN, "text", title, Confidence.EXACT, [], text="t")


def test_one_page_never_holds_more_than_150_widgets() -> None:
    items = [Placed(draft(f"p{i}"), 0, i * 2, 24, 2, "") for i in range(155)]
    widgets, skipped = lay_out(items)
    assert len(widgets) == 150 and skipped == 5


def test_headings_count_toward_the_page_limit() -> None:
    items = [Placed(draft(f"p{i}"), 0, i * 2, 24, 2, f"row {i // 10}") for i in range(150)]
    widgets, skipped = lay_out(items)
    assert len(widgets) == 150 and skipped == 15


def _convert(data: dict[str, Any], tmp_path: Path) -> IRDashboard:
    (tmp_path / "d.json").write_text(json.dumps(data))
    return DASHBOARD_REGISTRY["grafana"].convert(tmp_path)[0]


def _panel(count: int) -> dict[str, Any]:
    return {
        "uid": "d",
        "title": "d",
        "schemaVersion": 39,
        "panels": [
            {
                "id": 1,
                "type": "timeseries",
                "title": "many",
                "datasource": {"type": "prometheus", "uid": "p"},
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
                "targets": [{"refId": f"R{i}", "expr": f"sum(m{i})"} for i in range(count)],
            }
        ],
    }


def test_a_widget_has_at_most_20_queries(tmp_path: Path) -> None:
    ok = _convert(_panel(20), tmp_path)
    assert len(ok.widgets[0].raw_series) == 20
    too_many = _convert(_panel(21), tmp_path)
    assert too_many.widgets[0].kind == WidgetKind.UNSUPPORTED


def build(variables: list[Variable], raws: list[RawVar]) -> Variables:
    dash = Dashboard(uid="u", title="t", variables=variables)
    return convert_variables(dash, plan_dashboard(dash).variables, raws, NameMap())


def test_unprintable_text_in_variable_values_is_replaced() -> None:
    hostile = "a‮b⁦c\x1b[31m"
    found = build([Variable(name="t", kind="textbox", query=hostile)], [RawVar(current=[hostile])])
    value = found.variables[0].default_values[0]
    assert value.isprintable() and "‮" not in value
    enum = build(
        [Variable(name="c", kind="custom", query=f"{hostile},ok")],
        [RawVar(current=[hostile])],
    )
    for item in enum.variables[0].items:
        assert item.value.isprintable()
    assert all(d.isprintable() for d in enum.variables[0].default_values)
