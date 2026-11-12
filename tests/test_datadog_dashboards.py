"""Datadog dashboards (steps 1 and 2): widgets, groups, template variables, placeholders.

A converted widget is always approximate; a widget the converter cannot do is a labelled
placeholder with a reason that has manual steps, and is never counted as converted.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.dashboard_ir import (
    MAX_WIDGETS,
    IRDashboard,
    IRWidget,
    Visualization,
    WidgetKind,
)
from importer.core.emit.dashboard import emittable_widgets, nerdgraph_dashboard
from importer.core.ir import Confidence
from importer.core.playbook import playbook_for
from importer.core.remediation import remedies_for, steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.datadog.dashboards.measure import measure

FIXTURES = Path(__file__).parent / "fixtures" / "datadog"
REAL = FIXTURES / "real" / "dashboards"


def widget(kind: str, **fields: Any) -> dict[str, Any]:
    return {"definition": {"type": kind, "title": "w", **fields}}


def ts(q: str, **fields: Any) -> dict[str, Any]:
    return widget("timeseries", requests=[{"q": q}], **fields)


def formula_widget(
    formulas: str | list[str],
    queries: list[str],
    names: list[str] | None = None,
    kind: str = "timeseries",
    alias: str | None = None,
) -> dict[str, Any]:
    names = names or [f"query{i + 1}" for i in range(len(queries))]
    items = [formulas] if isinstance(formulas, str) else formulas
    fs = [{"formula": f, **({"alias": alias} if alias else {})} for f in items]
    return widget(
        kind,
        requests=[
            {
                "queries": [
                    {"data_source": "metrics", "name": n, "query": q}
                    for n, q in zip(names, queries, strict=True)
                ],
                "formulas": fs,
            }
        ],
    )


def convert(
    tmp_path: Path,
    widgets: list[dict[str, Any]],
    variables: list[dict[str, Any]] | None = None,
    name_map: dict[str, Any] | None = None,
) -> IRDashboard:
    body = {"title": "D", "layout_type": "ordered", "template_variables": variables or []}
    (tmp_path / "d.json").write_text(json.dumps({**body, "widgets": widgets}))
    if name_map is not None:
        (tmp_path / "name-map.json").write_text(json.dumps(name_map))
    (found,) = DASHBOARD_REGISTRY["datadog"].convert(tmp_path)
    return found


def one(tmp_path: Path, w: dict[str, Any], **kw: Any) -> tuple[IRDashboard, IRWidget, list[str]]:
    d = convert(tmp_path, [w], **kw)
    (item,) = [x for x in d.widgets if x.source_type != "group"]
    emits = d.confidence is not Confidence.MANUAL
    queries = emittable_widgets(d)[0].queries if emits else []
    return d, item, queries


def test_datadog_is_a_dashboard_source() -> None:
    assert "datadog" in DASHBOARD_REGISTRY


@pytest.mark.parametrize(
    ("w", "viz", "nrql"),
    [
        (
            ts("avg:system.cpu.user{*}"),
            Visualization.LINE,
            "SELECT average(system.cpu.user) FROM Metric TIMESERIES AUTO",
        ),
        (
            widget("timeseries", requests=[{"q": "max:a.b{env:prod,!host:x} by {az}"}]),
            Visualization.LINE,
            "SELECT max(a.b) FROM Metric WHERE env = 'prod' AND host != 'x' FACET az "
            "TIMESERIES AUTO",
        ),
        (
            widget("timeseries", requests=[{"q": "min:a.b{*}", "display_type": "area"}]),
            Visualization.AREA,
            "SELECT min(a.b) FROM Metric TIMESERIES AUTO",
        ),
        (
            widget("timeseries", requests=[{"q": "avg:a.b{*}", "display_type": "bars"}]),
            Visualization.AREA,
            "SELECT average(a.b) FROM Metric TIMESERIES AUTO",
        ),
        (
            widget("query_value", requests=[{"q": "avg:a.b{*}", "aggregator": "max"}]),
            Visualization.BILLBOARD,
            "SELECT max(a.b) FROM Metric",
        ),
        (
            widget("query_value", requests=[{"q": "avg:a.b{*}"}]),
            Visualization.BILLBOARD,
            "SELECT average(a.b) FROM Metric",
        ),
        (
            widget("toplist", requests=[{"q": "avg:a.b{*} by {host}"}]),
            Visualization.BAR,
            "SELECT average(a.b) FROM Metric FACET host",
        ),
        (
            widget("query_table", requests=[{"q": "avg:a.b{*} by {host}"}]),
            Visualization.TABLE,
            "SELECT average(a.b) FROM Metric FACET host",
        ),
        (
            widget(
                "timeseries",
                requests=[
                    {
                        "queries": [
                            {"data_source": "metrics", "name": "q1", "query": "avg:a.b{*}"}
                        ],
                        "formulas": [{"formula": "q1"}],
                    }
                ],
            ),
            Visualization.LINE,
            "SELECT average(a.b) FROM Metric TIMESERIES AUTO",
        ),
    ],
)
def test_plain_metric_widgets(
    tmp_path: Path, w: dict[str, Any], viz: Visualization, nrql: str
) -> None:
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART
    assert item.visualization is viz
    assert item.confidence is Confidence.APPROXIMATE  # exact is never claimed
    assert queries == [nrql]


def test_values_are_escaped_and_the_name_map_is_used(tmp_path: Path) -> None:
    nm = {
        "metrics": {"a.b": {"event": "Metric", "attribute": "x.b", "kind": "count"}},
        "attributes": {"env": "environment"},
    }
    _, item, queries = one(tmp_path, ts("sum:a.b{env:it's,host:a\\b}"), name_map=nm)
    assert queries == [
        "SELECT sum(x.b) FROM Metric WHERE environment = 'it\\'s' AND host = 'a\\\\b' "
        "TIMESERIES AUTO"
    ]
    assert item.data_path == "name-map"


def test_unmapped_names_say_so(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, ts("avg:a.b{*}"))
    assert item.data_path == "datadog-same-names"
    assert any("UNVERIFIED" in r for r in item.reasons)


def test_sum_of_a_gauge_carries_a_warning(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, ts("sum:a.b{*}"))
    assert any(r.startswith("dashboard sum adds every data point") for r in item.reasons)


def test_widget_time_span_is_reported(tmp_path: Path) -> None:
    _, item, _ = one(
        tmp_path, widget("query_value", requests=[{"q": "avg:a.b{*}"}], time={"live_span": "1h"})
    )
    assert any(r.startswith("dashboard widget has its own time range") for r in item.reasons)


@pytest.mark.parametrize(
    ("w", "start"),
    [
        (ts("timeshift(avg:a.b{*}, 3600)"), "dashboard query function"),
        (ts("derivative(avg:a.b{*})"), "dashboard query function"),
        (ts("avg:a.b{*}.weighted()"), "dashboard query function"),
        (ts("avg:a.b{*} / avg:a.c{*}"), "dashboard query uses arithmetic"),
        (ts("avg:a.b{*"), "dashboard query could not be read"),
        (ts("p95:a.b{*}"), "query function"),
        (
            widget("toplist", requests=[{"q": "avg:a.b{*}"}, {"q": "avg:a.c{*}"}]),
            "dashboard widget has several queries",
        ),
        (
            widget("query_value", requests=[{"q": "avg:a.b{x:1,y:2}, avg:a.c{*}"}]),
            "dashboard widget has several queries",
        ),
        (
            formula_widget("a / b", ["avg:a.b{*} by {h}", "avg:a.c{*}"], names=["a", "b"]),
            "dashboard widget uses a formula over queries that group by different tags",
        ),
        (
            widget(
                "timeseries",
                requests=[
                    {
                        "queries": [
                            {
                                "data_source": "logs",
                                "name": "a",
                                "compute": {"aggregation": "count"},
                            }
                        ],
                        "formulas": [{"formula": "a"}],
                    }
                ],
            ),
            "dashboard widget reads logs",
        ),
        (
            widget("query_value", requests=[{"q": "avg:a.b{*}", "aggregator": "last"}]),
            "dashboard query_value aggregator",
        ),
        (
            widget("query_value", requests=[{"q": "avg:a.b{*} by {host}"}]),
            "dashboard query_value groups",
        ),
        (widget("check_status", check="x"), "dashboard widget type"),
        (widget("heatmap", requests=[{"q": "avg:a.b{*}"}]), "dashboard widget type"),
        (widget("hostmap"), "dashboard widget type"),
        (widget("timeseries", requests=[]), "dashboard widget has no query"),
        (ts("avg:a.b{host:$nope}"), "dashboard template variable is not defined"),
        (
            ts(
                "avg:a.b{*} by {$g}",
            ),
            "dashboard template variable is not defined",
        ),
        (ts("avg:a.b{env:prod OR host:a}"), "scope cannot be written"),
        (ts("avg:bad name{*}"), "dashboard query could not be read"),
    ],
)
def test_placeholders_are_labelled_with_a_reason_that_has_steps(
    tmp_path: Path, w: dict[str, Any], start: str
) -> None:
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED, item.reasons
    assert item.reasons[0].lower().startswith(start.lower()), item.reasons
    assert item.confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED)
    assert steps_for(item.reasons)
    assert classify(item.reasons[0]) is not Category.OTHER


def test_a_note_is_markdown(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, widget("note", content="# Hello\n\n* a"))
    assert item.kind is WidgetKind.MARKDOWN
    assert item.text == "# Hello\n\n* a"
    assert item.confidence is Confidence.EXACT


def test_a_free_text_is_markdown(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, widget("free_text", text="Section"))
    assert item.kind is WidgetKind.MARKDOWN and item.text == "Section"


def test_a_group_becomes_a_page_and_children_keep_their_place(tmp_path: Path) -> None:
    inner = [
        {
            "definition": ts("avg:a.b{*}")["definition"],
            "layout": {"x": 0, "y": 0, "width": 4, "height": 3},
        },
        {
            "definition": ts("avg:a.c{*}")["definition"],
            "layout": {"x": 4, "y": 0, "width": 4, "height": 3},
        },
    ]
    g1 = widget("group", title="One", widgets=inner)
    g2 = widget("group", title="Two", widgets=inner[:1])
    d = convert(tmp_path, [g1, g2])
    assert [(w.page, w.layout.column, w.layout.row) for w in d.widgets] == [
        ("One", 1, 1),
        ("One", 5, 1),
        ("Two", 1, 1),
    ]


def test_one_group_among_loose_widgets_is_a_section_heading(tmp_path: Path) -> None:
    g = widget("group", title="Sec", widgets=[ts("avg:a.b{*}")])
    d = convert(tmp_path, [ts("avg:a.c{*}"), g])
    assert all(w.page is None for w in d.widgets)
    heading = [w for w in d.widgets if w.source_type == "group"]
    assert len(heading) == 1 and heading[0].text and "Sec" in heading[0].text
    assert len(d.widgets) == 3


def test_nested_groups_are_headings_on_the_parent_page(tmp_path: Path) -> None:
    deep = widget("group", title="Deep", widgets=[ts("avg:a.b{*}")])
    top = widget("group", title="Top", widgets=[deep])
    d = convert(tmp_path, [top, widget("group", title="B", widgets=[ts("avg:a.c{*}")])])
    top_page = [w for w in d.widgets if w.page == "Top"]
    assert [w.source_type for w in top_page] == ["group", "timeseries"]


# -- step 2: template variables -------------------------------------------------------------------
HOST = {"name": "host", "prefix": "host", "default": "*"}
SCOPE = {"name": "scope", "prefix": None, "default": "*"}


def test_a_tag_variable_becomes_a_dashboard_variable(tmp_path: Path) -> None:
    d, item, queries = one(tmp_path, ts("avg:a.b{$host,env:prod} by {az}"), variables=[HOST])
    assert queries == [
        "SELECT average(a.b) FROM Metric WHERE env = 'prod' AND host IN ({{host}}) FACET az "
        "TIMESERIES AUTO"
    ]
    (v,) = d.variables
    assert (v.name, v.multi_select, v.default_values) == ("host", True, ["*"])
    assert v.nrql == "SELECT uniques(host, 200) FROM Metric WHERE metricName = 'a.b'"
    assert item.kind is WidgetKind.CHART
    nerdgraph_dashboard(d)  # emits


def test_explicit_key_and_negation(tmp_path: Path) -> None:
    v = {"name": "h", "prefix": None, "default": "*"}
    _, _, queries = one(
        tmp_path,
        ts("avg:a.b{host:$h,!az:$az}"),
        variables=[v, {"name": "az", "prefix": "az", "default": "x"}],
    )
    assert queries == [
        "SELECT average(a.b) FROM Metric WHERE host IN ({{h}}) AND az NOT IN ({{az}}) "
        "TIMESERIES AUTO"
    ]


def test_a_variable_with_a_default_value(tmp_path: Path) -> None:
    d, _, _ = one(
        tmp_path,
        ts("avg:a.b{$host}"),
        variables=[{"name": "host", "prefix": "host", "default": "web-1"}],
    )
    assert d.variables[0].default_values == ["web-1"]


def test_unused_variables_are_not_created(tmp_path: Path) -> None:
    d, _, _ = one(tmp_path, ts("avg:a.b{*}"), variables=[HOST])
    assert d.variables == []


def test_a_variable_without_a_tag_key_uses_its_default_and_says_so(tmp_path: Path) -> None:
    d, item, queries = one(tmp_path, ts("avg:a.b{$scope}"), variables=[SCOPE])
    assert d.variables == []
    assert queries == ["SELECT average(a.b) FROM Metric TIMESERIES AUTO"]
    assert any(r.startswith("dashboard template variable has no tag key") for r in item.reasons)


def test_a_scope_variable_default_that_is_a_filter(tmp_path: Path) -> None:
    v = {"name": "scope", "prefix": None, "default": "env:prod"}
    _, _, queries = one(tmp_path, ts("avg:a.b{$scope}"), variables=[v])
    assert queries == ["SELECT average(a.b) FROM Metric WHERE env = 'prod' TIMESERIES AUTO"]


@pytest.mark.parametrize(
    ("q", "variables", "start"),
    [
        ("avg:a.b{$scope.value}", [SCOPE], "dashboard template variable is used in a form"),
        ("avg:a.b{*} by {$host}", [HOST], "dashboard template variable is used in a form"),
        ("avg:a.b{host IN ($host)}", [HOST], "dashboard template variable is used in a form"),
        ("avg:a.b{host:$host*}", [HOST], "dashboard template variable is used in a form"),
        ("avg:a.b{$host OR env:x}", [HOST], "scope cannot be written"),
        (
            "avg:a.b{$host}",
            [{"name": "host", "prefix": "az bad", "default": "*"}],
            "tag cannot be written",
        ),
        ("avg:a.b{env:$host}", [HOST], "dashboard template variable stands for"),
        (
            "avg:a.b{$scope}",
            [{"name": "scope", "prefix": None, "default": "a OR b"}],
            "dashboard template variable default",
        ),
    ],
)
def test_variable_forms_that_are_not_converted(
    tmp_path: Path, q: str, variables: list[dict[str, Any]], start: str
) -> None:
    d, item, _ = one(tmp_path, ts(q), variables=variables)
    assert item.kind is WidgetKind.UNSUPPORTED, item.reasons
    assert item.reasons[0].lower().startswith(start.lower()), item.reasons
    assert d.variables == []
    assert steps_for(item.reasons)


def test_a_variable_name_that_is_not_a_new_relic_name_is_renamed_and_says_so(
    tmp_path: Path,
) -> None:
    v = {"name": "replica-set", "prefix": "replica_set", "default": "*"}
    d, item, queries = one(tmp_path, ts("avg:a.b{$replica-set}"), variables=[v])
    assert queries == [
        "SELECT average(a.b) FROM Metric WHERE replica_set IN ({{replica_set}}) TIMESERIES AUTO"
    ]
    assert [x.name for x in d.variables] == ["replica_set"]
    assert item.kind is WidgetKind.CHART
    assert any("replica-set" in r and "replica_set" in r for r in item.reasons), item.reasons
    nerdgraph_dashboard(d)


def test_renamed_variables_that_would_collide_get_distinct_names(tmp_path: Path) -> None:
    vs = [
        {"name": "a-b", "prefix": "x", "default": "*"},
        {"name": "a_b", "prefix": "y", "default": "*"},
    ]
    d = convert(tmp_path, [ts("avg:a.b{$a-b,$a_b}")], variables=vs)
    assert len({x.name for x in d.variables}) == 2


def test_a_variable_used_on_two_tags_is_refused(tmp_path: Path) -> None:
    v = {"name": "x", "prefix": None, "default": "*"}
    d = convert(tmp_path, [ts("avg:a.b{host:$x}"), ts("avg:a.b{az:$x}")], variables=[v])
    assert [w.kind for w in d.widgets] == [WidgetKind.CHART, WidgetKind.UNSUPPORTED]
    assert [x.name for x in d.variables] == ["x"]


@pytest.mark.parametrize("scope", ["env:x') FROM Log --", 'env:"x\'y"', "env:a\\"])
def test_hostile_text_does_not_reach_a_query(tmp_path: Path, scope: str) -> None:
    v = {"name": "h", "prefix": "host", "default": "*' OR 1=1"}
    d, item, queries = one(tmp_path, ts("avg:a.b{$h," + scope + "}"), variables=[v])
    for q in queries:
        assert q.count(" FROM ") == 1 and "1=1" not in q and "Log" not in q
    if item.kind is WidgetKind.CHART:
        assert d.variables[0].default_values == ["*' OR 1=1"]  # only in the variable's default
        nerdgraph_dashboard(d)


# -- the whole thing ------------------------------------------------------------------------------
def test_every_dashboard_converts_and_emits() -> None:
    found = DASHBOARD_REGISTRY["datadog"].convert(REAL)
    assert len(found) == 30
    for d in found:
        if d.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
            nerdgraph_dashboard(d)


def test_every_reason_on_the_real_dashboards_is_classified_and_every_placeholder_has_steps() -> (
    None
):
    for d in DASHBOARD_REGISTRY["datadog"].convert(REAL):
        for w in d.widgets:
            for reason in w.reasons:
                assert classify(reason) is not Category.OTHER, reason
            if w.kind is WidgetKind.UNSUPPORTED:
                assert steps_for(w.reasons), w.reasons
                (first, *_) = remedies_for(w.reasons)
                assert playbook_for(first.title), first.title


def test_the_synthetic_dashboards_convert_without_error() -> None:
    synthetic = FIXTURES / "synthetic"
    found = DASHBOARD_REGISTRY["datadog"].convert(synthetic)
    assert len(found) == 3
    for d in found:
        assert d.widgets
        assert all(
            w.kind is WidgetKind.UNSUPPORTED
            or w.confidence is not Confidence.EXACT
            or w.kind is WidgetKind.MARKDOWN
            for w in d.widgets
        )


def test_the_cli_converts_datadog_dashboards(tmp_path: Path) -> None:
    out = tmp_path / "out"
    r = CliRunner().invoke(
        app, ["convert-dashboards", "--source", "datadog", "--in", str(REAL), "--out", str(out)]
    )
    assert r.exit_code == 0, r.output
    assert "Read 30 dashboards" in r.output
    assert (out / "report.md").is_file()


def test_the_monitor_loader_still_skips_dashboards() -> None:
    from importer.sources import REGISTRY

    assert REGISTRY["datadog"].load(REAL) == []


def test_a_dashboard_over_the_page_limit_says_how_many_widgets_were_left_out(
    tmp_path: Path,
) -> None:
    d = convert(tmp_path, [ts("avg:a.b{*}") for _ in range(MAX_WIDGETS + 7)])
    assert len(d.widgets) == MAX_WIDGETS
    assert any(r.startswith("7 widgets are beyond") for r in d.reasons)


def test_overlapping_boxes_are_moved_and_counted(tmp_path: Path) -> None:
    box = {"x": 0, "y": 0, "width": 6, "height": 3}
    d = convert(
        tmp_path, [{**ts("avg:a.b{*}"), "layout": box}, {**ts("avg:a.c{*}"), "layout": box}]
    )
    assert sorted(w.layout.row for w in d.widgets) == [1, 4]
    assert any("overlapped" in r for r in d.reasons)


# -- step 3B: formulas and several queries --------------------------------------------------------
@pytest.mark.parametrize(
    ("w", "nrqls"),
    [
        (
            formula_widget("query1 / query2 * 100", ["sum:a.b{*} by {h}", "sum:a.c{*} by {h}"]),
            ["SELECT sum(a.b) / sum(a.c) * 100 FROM Metric FACET h TIMESERIES AUTO"],
        ),
        (
            formula_widget("(query1 - query2) / query1", ["avg:a.b{*}", "max:a.c{*}"]),
            ["SELECT (average(a.b) - max(a.c)) / average(a.b) FROM Metric TIMESERIES AUTO"],
        ),
        (
            formula_widget(["query1", "query2 - query1"], ["sum:a.b{*}", "sum:a.c{*}"]),
            [
                "SELECT sum(a.b) FROM Metric TIMESERIES AUTO",
                "SELECT sum(a.c) - sum(a.b) FROM Metric TIMESERIES AUTO",
            ],
        ),
        (
            formula_widget("query1 + query2", ["avg:a.b{env:x}", "avg:a.c{env:y}"]),
            [
                "SELECT filter(average(a.b), WHERE env = 'x') + filter(average(a.c), "
                "WHERE env = 'y') FROM Metric TIMESERIES AUTO"
            ],
        ),
        (
            formula_widget("query1 + query2", ["avg:a.b{env:x}", "avg:a.c{env:x}"], kind="toplist"),
            ["SELECT average(a.b) + average(a.c) FROM Metric WHERE env = 'x'"],
        ),
        (
            widget("timeseries", requests=[{"q": "avg:a.b{*}"}, {"q": "max:a.c{*} by {h}"}]),
            [
                "SELECT average(a.b) FROM Metric TIMESERIES AUTO",
                "SELECT max(a.c) FROM Metric FACET h TIMESERIES AUTO",
            ],
        ),
        (
            ts("avg:a.b{*}, avg:a.c{*}"),
            [
                "SELECT average(a.b) FROM Metric TIMESERIES AUTO",
                "SELECT average(a.c) FROM Metric TIMESERIES AUTO",
            ],
        ),
        (
            formula_widget("query1 / query2", ["sum:a.b{*}", "sum:a.c{*}"], alias="Ratio"),
            ["SELECT sum(a.b) / sum(a.c) AS 'Ratio' FROM Metric TIMESERIES AUTO"],
        ),
    ],
)
def test_formulas_and_several_queries(tmp_path: Path, w: dict[str, Any], nrqls: list[str]) -> None:
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert item.confidence is Confidence.APPROXIMATE
    assert queries == nrqls


@pytest.mark.parametrize(
    ("formula", "nrql"),
    [
        (
            "top(query1, 25, 'max', 'desc')",
            "SELECT average(a.b) FROM Metric FACET h ORDER BY max(a.b) DESC LIMIT 25 "
            "TIMESERIES AUTO",
        ),
        (
            "top(query1, 5, 'mean', 'desc') * 8",
            "SELECT average(a.b) * 8 FROM Metric FACET h ORDER BY average(a.b) DESC LIMIT 5 "
            "TIMESERIES AUTO",
        ),
    ],
)
def test_a_formula_that_is_a_top_of_one_query(tmp_path: Path, formula: str, nrql: str) -> None:
    w = formula_widget(formula, ["avg:a.b{*} by {h}"])
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == [nrql]


def test_a_top_formula_over_a_query_that_cannot_be_ranked_is_refused(tmp_path: Path) -> None:
    _, item, _ = one(
        tmp_path, formula_widget("top(query1, 5, 'l2norm', 'desc')", ["avg:a.b{*} by {h}"])
    )
    assert item.kind is WidgetKind.UNSUPPORTED
    assert item.reasons[0].startswith("dashboard query function: top()")


def test_a_formula_with_a_variable_scope_hoists_it_and_keeps_the_variable(tmp_path: Path) -> None:
    w = formula_widget("query1 / query2", ["sum:a.b{$host} by {az}", "sum:a.c{$host} by {az}"])
    d, item, queries = one(tmp_path, w, variables=[HOST])
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == [
        "SELECT sum(a.b) / sum(a.c) FROM Metric WHERE host IN ({{host}}) FACET az TIMESERIES AUTO"
    ]
    assert [v.name for v in d.variables] == ["host"]
    nerdgraph_dashboard(d)


def test_a_formula_carries_each_metrics_unit_scale(tmp_path: Path) -> None:
    nm = {"metrics": {"a.b": {"event": "Metric", "attribute": "a.b", "unit_scale": 8}}}
    _, item, queries = one(
        tmp_path, formula_widget("query1 / query2", ["sum:a.b{*}", "sum:a.c{*}"]), name_map=nm
    )
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == ["SELECT sum(a.b) * 8 / sum(a.c) FROM Metric TIMESERIES AUTO"]


def test_a_formula_over_queries_says_it_is_a_ratio_of_window_aggregates(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, formula_widget("query1 / query2", ["sum:a.b{*}", "sum:a.c{*}"]))
    assert any(r.startswith("dashboard formula") for r in item.reasons), item.reasons


@pytest.mark.parametrize(
    ("w", "start"),
    [
        (
            formula_widget("default_zero(query1) + query2", ["sum:a.b{*}", "sum:a.c{*}"]),
            "dashboard widget uses a formula with the function default_zero()",
        ),
        (
            formula_widget("top(query1, 5, 'mean', 'desc') + query2", ["sum:a.b{*}", "sum:a.c{*}"]),
            "dashboard widget uses a formula with the function top()",
        ),
        (
            formula_widget("week_before(query1)", ["sum:a.b{*}", "sum:a.c{*}"]),
            "dashboard widget uses a formula with the function week_before()",
        ),
        (
            formula_widget("query1 + nope", ["sum:a.b{*}", "sum:a.c{*}"]),
            "dashboard widget uses a formula that names a query that does not exist",
        ),
        (
            formula_widget("query1 +", ["sum:a.b{*}", "sum:a.c{*}"]),
            "dashboard widget uses a formula that could not be read",
        ),
        (
            formula_widget("query1 / query2", ["sum:a.b{*} by {h}", "sum:a.c{*} by {g}"]),
            "dashboard widget uses a formula over queries that group by different tags",
        ),
        (
            formula_widget("query1 / query2", ["sum:a.b{*}.as_rate()", "sum:a.c{*}.as_rate()"]),
            "dashboard widget uses a formula over a query with",
        ),
        (
            formula_widget(
                "query1 / query2", ["top(sum:a.b{*} by {h}, 5, 'max', 'desc')", "sum:a.c{*}"]
            ),
            "dashboard widget uses a formula over a query with",
        ),
        (
            formula_widget(["query1", "query2"], ["sum:a.b{*}", "sum:a.c{*}"], kind="query_table"),
            "dashboard widget has several queries",
        ),
    ],
)
def test_formulas_that_cannot_be_one_select_stay_placeholders(
    tmp_path: Path, w: dict[str, Any], start: str
) -> None:
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED, item.reasons
    assert item.reasons[0].startswith(start), item.reasons
    assert steps_for(item.reasons)
    assert classify(item.reasons[0]) is not Category.OTHER


def test_formulas_over_different_events_are_refused(tmp_path: Path) -> None:
    nm = {
        "metrics": {
            "a.b": {"event": "Metric", "attribute": "a.b"},
            "a.c": {"event": "Other", "attribute": "a.c"},
        }
    }
    _, item, _ = one(
        tmp_path, formula_widget("query1 / query2", ["sum:a.b{*}", "sum:a.c{*}"]), name_map=nm
    )
    assert item.kind is WidgetKind.UNSUPPORTED
    assert item.reasons[0].startswith("dashboard widget uses a formula over metrics on different")


def test_a_failing_series_makes_the_whole_widget_a_placeholder(tmp_path: Path) -> None:
    w = widget("timeseries", requests=[{"q": "avg:a.b{*}"}, {"q": "weighted(avg:a.c{*})"}])
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED


def test_hostile_formula_text_does_not_reach_a_query(tmp_path: Path) -> None:
    w = formula_widget("query1 / query2", ["sum:a.b{*}", "sum:a.c{*}"], alias="x' ; DROP")
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == ["SELECT sum(a.b) / sum(a.c) AS 'x\\' ; DROP' FROM Metric TIMESERIES AUTO"]


# -- step 3: the function table -------------------------------------------------------------------
COUNT_MAP = {"metrics": {"a.b": {"event": "Metric", "attribute": "a.b", "kind": "count"}}}
GAUGE_MAP = {"metrics": {"a.b": {"event": "Metric", "attribute": "a.b", "kind": "gauge"}}}


@pytest.mark.parametrize(
    ("w", "nrql"),
    [
        (
            ts("top(avg:a.b{*} by {host}, 10, 'mean', 'desc')"),
            "SELECT average(a.b) FROM Metric FACET host LIMIT 10 TIMESERIES AUTO",
        ),
        (
            ts("top(avg:a.b{*} by {host}, 5, 'max', 'desc')"),
            "SELECT average(a.b) FROM Metric FACET host ORDER BY max(a.b) DESC LIMIT 5 "
            "TIMESERIES AUTO",
        ),
        (
            ts("top(max:a.b{*} by {host}, 5, 'min', 'asc')"),
            "SELECT max(a.b) FROM Metric FACET host ORDER BY min(a.b) ASC LIMIT 5 TIMESERIES AUTO",
        ),
        (
            ts("top(avg:a.b{*} by {host}, 5, 'last', 'desc')"),
            "SELECT average(a.b) FROM Metric FACET host ORDER BY latest(a.b) DESC LIMIT 5 "
            "TIMESERIES AUTO",
        ),
        (
            widget("toplist", requests=[{"q": "top(avg:a.b{*} by {host}, 7, 'mean', 'desc')"}]),
            "SELECT average(a.b) FROM Metric FACET host LIMIT 7",
        ),
        (
            ts("avg:a.b{*} by {host}.fill(0)"),
            "SELECT average(a.b) FROM Metric FACET host TIMESERIES AUTO",
        ),
        (
            ts("avg:a.b{*}.rollup(avg, 300)"),
            "SELECT average(a.b) FROM Metric TIMESERIES AUTO",
        ),
        (
            ts("max:a.b{*}.rollup(60)"),
            "SELECT max(a.b) FROM Metric TIMESERIES AUTO",
        ),
        (
            ts("sum:a.b{*} by {host}.rollup(sum, daily)"),
            "SELECT sum(a.b) FROM Metric FACET host TIMESERIES 1 day",
        ),
    ],
)
def test_the_function_table_converts(tmp_path: Path, w: dict[str, Any], nrql: str) -> None:
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert item.confidence is Confidence.APPROXIMATE
    assert queries == [nrql]


def test_top_with_a_variable_keeps_the_variable_in_the_filter(tmp_path: Path) -> None:
    _, item, queries = one(
        tmp_path, ts("top(avg:a.b{$host} by {az}, 3, 'mean', 'desc')"), variables=[HOST]
    )
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == [
        "SELECT average(a.b) FROM Metric WHERE host IN ({{host}}) FACET az LIMIT 3 TIMESERIES AUTO"
    ]


def test_top_does_not_warn_about_the_ten_group_limit(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, ts("top(avg:a.b{*} by {host}, 30, 'mean', 'desc')"))
    assert not any(r.startswith("New Relic shows at most") for r in item.reasons)


@pytest.mark.parametrize(
    ("q", "nrql"),
    [
        (
            "sum:a.b{*} by {host}.as_count()",
            "SELECT sum(a.b) FROM Metric FACET host TIMESERIES AUTO",
        ),
        (
            "avg:a.b{*} by {host}.as_count()",
            "SELECT sum(a.b) FROM Metric FACET host TIMESERIES AUTO",
        ),
        (
            "sum:a.b{*} by {host}.as_rate()",
            "SELECT rate(sum(a.b), 1 second) FROM Metric FACET host TIMESERIES AUTO",
        ),
        (
            "per_second(avg:a.b{env:x} by {host})",
            "SELECT rate(sum(a.b), 1 second) FROM Metric WHERE env = 'x' FACET host "
            "TIMESERIES AUTO",
        ),
        (
            "sum:a.b{*}.as_rate().rollup(10)",
            "SELECT rate(sum(a.b), 1 second) FROM Metric TIMESERIES AUTO",
        ),
    ],
)
def test_counts_and_rates(tmp_path: Path, q: str, nrql: str) -> None:
    _, item, queries = one(tmp_path, ts(q), name_map=COUNT_MAP)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert item.confidence is Confidence.APPROXIMATE
    assert queries == [nrql]


def test_a_rate_over_a_replaced_space_aggregator_says_so(tmp_path: Path) -> None:
    _, item, _ = one(tmp_path, ts("avg:a.b{*}.as_rate()"), name_map=COUNT_MAP)
    assert any(
        r.startswith("the space aggregator avg was replaced by sum") for r in item.reasons
    ), item.reasons


def test_a_rate_keeps_the_unit_scale_outside_rate(tmp_path: Path) -> None:
    nm = {
        "metrics": {
            "a.b": {"event": "Metric", "attribute": "a.b", "kind": "count", "unit_scale": 8}
        }
    }
    _, item, queries = one(tmp_path, ts("sum:a.b{*}.as_rate()"), name_map=nm)
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == ["SELECT rate(sum(a.b), 1 second) * 8 FROM Metric TIMESERIES AUTO"]


@pytest.mark.parametrize(
    ("w", "start"),
    [
        (ts("sum:a.b{*}.weighted()"), "dashboard query function has no New Relic form: weighted()"),
        (ts("autosmooth(avg:a.b{*})"), "dashboard query function"),
        (ts("top(avg:a.b{*}, 5, 'mean', 'desc')"), "dashboard query function: top() needs"),
        (ts("top(avg:a.b{*} by {h}, 5, 'l2norm', 'desc')"), "dashboard query function: top()"),
        (ts("top(avg:a.b{*} by {h}, 0, 'mean', 'desc')"), "dashboard query function: top()"),
        (ts("top(avg:a.b{*} by {h}, 5, 'mean')"), "dashboard query function: top()"),
        (
            ts("top(timeshift(avg:a.b{*} by {h}, 40), 5, 'mean', 'desc')"),
            "dashboard query function is not converted yet: timeshift()",
        ),
        (ts("sum:a.b{*}.rollup(sum, 60)"), "dashboard query function: rollup()"),
        (ts("sum:a.b{*}.rollup(sum, monthly)"), "dashboard query function: rollup()"),
        (ts("derivative(avg:a.b{*})"), "dashboard query function is not converted yet"),
    ],
)
def test_functions_without_a_correct_form_stay_placeholders(
    tmp_path: Path, w: dict[str, Any], start: str
) -> None:
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED, item.reasons
    assert item.reasons[0].startswith(start), item.reasons
    assert steps_for(item.reasons)
    assert classify(item.reasons[0]) is not Category.OTHER


def test_rates_of_gauges_are_refused(tmp_path: Path) -> None:
    for q in ("avg:a.b{*}.as_rate()", "per_second(avg:a.b{*})", "avg:a.b{*}.as_count()"):
        _, item, _ = one(tmp_path, ts(q), name_map=GAUGE_MAP)
        assert item.kind is WidgetKind.UNSUPPORTED, q
        assert steps_for(item.reasons)


def test_a_query_value_with_a_function_still_converts(tmp_path: Path) -> None:
    _, item, queries = one(
        tmp_path,
        widget("query_value", requests=[{"q": "sum:a.b{*}.as_count()", "aggregator": "sum"}]),
        name_map=COUNT_MAP,
    )
    assert item.kind is WidgetKind.CHART, item.reasons
    assert queries == ["SELECT sum(a.b) FROM Metric"]


def test_the_real_dashboards_measure_is_pinned() -> None:
    """Data widgets exclude notes. Before this converter nothing converted (0 of 454)."""
    m = measure(DASHBOARD_REGISTRY["datadog"].convert(REAL))
    assert (m.dashboards, m.data_widgets, m.converted, m.placeholders) == (30, 454, 264, 190)
    assert (m.notes, m.note_placeholders) == (62, 0)
    assert m.converted + m.placeholders == m.data_widgets
    assert sum(m.by_reason.values()) == m.placeholders


# -- community corpus: image, treemap, sunburst ----------------------------------------------
def test_an_https_image_becomes_approximate_markdown(
    tmp_path: Path,
) -> None:
    w = widget("image", url="https://example.com/logo.png")
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.MARKDOWN
    assert item.confidence is Confidence.APPROXIMATE
    assert item.text == "![w](https://example.com/logo.png)"
    assert any(r.startswith("dashboard image") for r in item.reasons)


@pytest.mark.parametrize(
    "url",
    [
        "/static/images/logo.png",
        "http://example.com/a.png",
        "javascript:alert(1)",
        "https://example.com/a b.png",
        "https://example.com/a).png",
        "https://example.com/a<b>.png",
        None,
    ],
)
def test_an_image_without_a_safe_https_address_is_a_placeholder(
    tmp_path: Path, url: str | None
) -> None:
    _, item, _ = one(tmp_path, widget("image", url=url))
    assert item.kind is WidgetKind.UNSUPPORTED
    assert steps_for(item.reasons)


def test_a_treemap_is_a_bar_chart_over_a_facet_and_says_so(tmp_path: Path) -> None:
    w = formula_widget("query1", ["avg:a.b{*} by {resource}"], kind="treemap")
    _, item, queries = one(tmp_path, w)
    assert item.visualization is Visualization.BAR
    assert item.confidence is Confidence.APPROXIMATE
    assert " FACET resource" in queries[0] and "TIMESERIES" not in queries[0]
    assert any(r.startswith("dashboard treemap") for r in item.reasons)


def test_a_sunburst_is_a_pie_chart_over_a_facet_and_says_so(tmp_path: Path) -> None:
    w = formula_widget("query1", ["sum:a.b{*} by {resource}"], kind="sunburst")
    _, item, queries = one(tmp_path, w)
    assert item.visualization is Visualization.PIE
    assert " FACET resource" in queries[0]
    assert any(r.startswith("dashboard sunburst") for r in item.reasons)


def test_a_treemap_without_a_group_is_a_placeholder(tmp_path: Path) -> None:
    w = formula_widget("query1", ["avg:a.b{*}"], kind="treemap")
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED
