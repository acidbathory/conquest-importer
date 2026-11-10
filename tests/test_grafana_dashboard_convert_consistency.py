"""The converter and the coverage planner must agree.

The planner says which queries and panels convert; the converter writes them. Every query the
planner reads at tier 1 to 3 has to produce NRQL, every panel it calls convertible has to produce
a widget, and every other panel has to produce a placeholder that says why.
"""

from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from importer.core.dashboard_ir import WidgetKind
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.panels import EXTRA_CONVERTIBLE
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboard_convert.translate import (
    Drafted,
    Item,
    draft_panels,
    load_items,
)
from importer.sources.grafana.dashboards.panels import panel_type
from importer.sources.grafana.dashboards.query_tier import classify_query

FIXTURES = Path(__file__).parent / "fixtures"
FOLDERS = [FIXTURES / "grafana-dashboards", FIXTURES / "grafana-dashboard-convert"]
KNOWN = frozenset({"job", "env", "instance", "factor"})
VARIABLES: dict[str, str | None] = {"job": None, "env": None, "instance": None, "factor": None}
# Reasons only the converter can give; a panel with one of them was convertible for the planner.
CONVERTER_ONLY = {
    r.C_VAR_NAME,
    r.C_VAR_MATCHER,
    r.C_BUILTIN,
    r.C_EVENTS,
    r.C_FILTERS,
    r.C_AGG_RATE,
    r.C_GAUGE_SERIES,
    r.C_VARIABLE_UNDEFINED,
}

PROMQL = [
    "up",
    'up{job="a"}',
    'up{job=~"a|b"}',
    'up{job=~"web.*"}',
    'up{job!~".*db"}',
    'up{job=~"$job"}',
    'up{job=~"$job", env="$env"}',
    'up{job="$job"}',
    "avg(up)",
    "sum by (a, b) (up)",
    "sum(up) by (a)",
    'max by (a) (up{x!="y"})',
    "min(up)",
    "sum without_by (up)",
    "rate(x_total[5m])",
    "sum(rate(x_total[5m]))",
    'sum by (a) (rate(x_total{b="c"}[$__rate_interval]))',
    "irate(x_total[1m])",
    "sum(irate(x_total[$instance]))",
    "increase(x_total[1h])",
    "sum(increase(x_total[$__range]))",
    "sum by (a) (increase(x_total[$__interval]))",
    "avg(rate(x_total[5m]))",
    "max by (a) (rate(x_total[5m]))",
    "histogram_quantile(0.5, rate(x_bucket[5m]))",
    "histogram_quantile(0.95, sum by (le) (rate(x_bucket[5m])))",
    "histogram_quantile(0.95, sum(rate(x_bucket[5m])) by (le, a))",
    'histogram_quantile(0.99, sum by (le, a) (rate(x_bucket{job="a"}[5m])))',
    "histogram_quantile(.9, sum by (le) (rate(x_bucket[$__rate_interval])))",
    "topk(3, sum by (a) (rate(x_total[5m])))",
    "topk(5, sum by (a) (up))",
    "topk(5, up)",
    "count(up)",
    "count by (a) (up)",
    'count(up{job="a"}) by (b)',
    '1 - avg(rate(x_total{mode="idle"}[5m]))',
    "sum(rate(a_total[5m])) / sum(rate(b_total[5m]))",
    "sum(rate(a_total[5m])) / sum(rate(b_total[5m])) * 100",
    "(a - b) / a",
    "100 * (1 - a / b)",
    "-up",
    "sum(up) + 1",
    "sum(up) * $factor",
    "sum by (a) (up) / sum by (a) (down)",
    "sum(up) / 2 + sum(down) * 3",
    "sum(up) * 1e-3",
    "sum(up) * 1.5e+3",
    'sum(rate(a_total{c="1"}[5m])) / sum(rate(a_total[5m]))',
    "histogram_quantile(0.9, sum by (le) (rate(x_bucket[5m]))) * 1000",
    "sum(rate(x_total[5m] )) ",
    "sum( rate( x_total [5m] ) )",
    "sum by (a)(rate(x_total[5m]))",
    "x_total",
    "sum(x_total)",
    'up{job=~".+"}',
    "sum(up) > 1",
    "up offset 1h",
    "sum(rate(a_total[5m])) / on() sum(rate(b_total[5m]))",
    "rate(x_total[5m]) by (a)",
    "avg_over_time(up[5m])",
    'label_replace(up, "a", "b", "c", "d")',
    "histogram_quantile(0.9, x_bucket)",
    "histogram_quantile(0.9, sum(rate(x_bucket[5m])))",
    "sum by ($job) (up)",
    'up{job="x$job"}',
    "sum(up) * $__interval",
    'up{__name__="x"}',
    "",
    "(",
]
LOGQL = [
    'count_over_time({a="b"}[5m])',
    'count_over_time({a="b"} |= "x" [$__interval])',
    'sum by (l) (count_over_time({a="b"}[1m]))',
    'rate({a="b"} |~ "err" [5m])',
    'sum(rate({a="b"} != "z" [$__rate_interval]))',
    'sum by (l) (rate({a=~"x|y"}[5m]))',
    'sum(rate({a="b"}[5m])) / sum(rate({c="d"}[5m]))',
    'sum(count_over_time({a="b"} |= "x" [$__range]))',
    '{a="b"}',
    'rate({a="b"} | json [5m])',
    'sum(rate({a="$job"}[5m]))',
]


def options(timeseries: bool) -> QueryOptions:
    return QueryOptions(variables=VARIABLES, name_map=NameMap(), timeseries=timeseries)


def corpus() -> list[tuple[str, str]]:
    return [(q, "prometheus") for q in PROMQL] + [(q, "loki") for q in LOGQL]


@pytest.mark.parametrize("timeseries", [True, False], ids=["chart", "single value"])
def test_every_query_the_planner_reads_produces_nrql(timeseries: bool) -> None:
    tiers: Counter[int | None] = Counter()
    refused: list[tuple[str, str]] = []
    for expr, family in corpus():
        verdict = classify_query(expr, family, KNOWN)
        tiers[verdict.tier] += 1
        if verdict.tier is None:
            continue
        try:
            convert_query(expr, family, options(timeseries))
        except Unconvertible as stop:
            refused.append((expr, stop.reason))
    allowed = [(e, why) for e, why in refused if why not in CONVERTER_ONLY]
    assert allowed == []
    assert tiers[1] >= 10 and tiers[2] >= 10 and tiers[3] >= 8 and tiers[None] >= 10, tiers


def test_converter_only_reasons_on_the_corpus_are_listed_and_few() -> None:
    refused = 0
    for expr, family in corpus():
        if classify_query(expr, family, KNOWN).tier is None:
            continue
        try:
            convert_query(expr, family, options(True))
        except Unconvertible:
            refused += 1
    # `up{job="x$job"}`, `sum(up) * $__interval` and the three averages of a rate (refused on
    # purpose: rate(average()) reads too low by the scrapes in a bucket) at most
    assert refused <= 20


def test_queries_the_planner_refuses_are_never_converted_by_accident() -> None:
    """If the planner calls a query manual the converter does not quietly write it."""
    leaked = []
    for expr, family in corpus():
        if classify_query(expr, family, KNOWN).tier is not None:
            continue
        try:
            leaked.append((expr, convert_query(expr, family, options(True)).nrql))
        except Unconvertible:
            continue
    assert leaked == []


def drafts_of(folder: Path) -> Iterator[tuple[Item, Drafted]]:
    for item in load_items(folder):
        if item.dash is not None:
            yield item, draft_panels(item.dash, item.raw, NameMap())


@pytest.mark.parametrize("folder", FOLDERS, ids=lambda p: p.name)
def test_convertible_panels_become_widgets_and_manual_panels_become_placeholders(
    folder: Path,
) -> None:
    widgets = placeholders = extra = 0
    for item, found in drafts_of(folder):
        assert item.dash is not None
        for panel, plan, draft in zip(
            item.dash.panels, found.plan.panels, found.drafts, strict=True
        ):
            kind = panel_type(panel.type)
            if plan.tier is not None:
                assert draft.kind != WidgetKind.UNSUPPORTED or draft.reasons[0] in CONVERTER_ONLY, (
                    panel.title,
                    draft.reasons,
                )
                widgets += 1
            elif plan.extra_tier is not None and kind.label in EXTRA_CONVERTIBLE:
                extra += draft.kind != WidgetKind.UNSUPPORTED
            else:
                assert draft.kind == WidgetKind.UNSUPPORTED, panel.title
                assert draft.reasons[: len(plan.reasons)][0] == plan.reasons[0]
                assert draft.confidence.value == "manual"
                placeholders += 1
    assert widgets + placeholders + extra > 0


def test_the_extra_widget_types_convert_when_their_queries_do() -> None:
    converted = refused = 0
    for item, found in drafts_of(FIXTURES / "grafana-dashboard-convert"):
        assert item.dash is not None
        for panel, plan, draft in zip(
            item.dash.panels, found.plan.panels, found.drafts, strict=True
        ):
            extra = plan.tier is None and plan.extra_tier is not None
            if extra and panel_type(panel.type).label in EXTRA_CONVERTIBLE:
                converted += draft.kind != WidgetKind.UNSUPPORTED
                refused += (
                    draft.kind == WidgetKind.UNSUPPORTED and r.C_GAUGE_SERIES not in draft.reasons
                )
    assert converted >= 2 and refused == 0  # one used sum() of a gauge: now manual
