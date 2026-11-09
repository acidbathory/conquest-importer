"""The coverage planner: panels, data sources, variables and tiers on the research fixtures."""

import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from importer.sources.grafana.dashboards import models as m
from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.datasources import resolve
from importer.sources.grafana.dashboards.load import load_data, load_estate
from importer.sources.grafana.dashboards.panels import TABLE, panel_type
from importer.sources.grafana.dashboards.plan import DashboardPlan, Plan, build_plan
from importer.sources.grafana.dashboards.variables import classify_variable
from importer.sources.grafana.translate import OTHER_DATASOURCES

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-dashboards"


def plan_of(name: str) -> DashboardPlan:
    plan = build_plan(load_estate(FIXTURES / name))
    assert not plan.unreadable
    return plan.dashboards[0]


def by_title(d: DashboardPlan) -> dict[str, Any]:
    return {p.title: p for p in d.panels}


def counts(d: DashboardPlan) -> tuple[int, int, int, int, int]:
    return d.panel_count, d.t1, d.t2, d.t3, d.t3_extra


def test_the_easy_fixture_converts_at_tier_two_as_the_note_says() -> None:
    d = plan_of("easy-node-overview.json")
    assert counts(d) == (5, 4, 5, 5, 5)  # 4 at T1, all 5 at T2
    assert {p.title: p.tier for p in d.panels}["CPU busy by instance"] == 2
    assert [(v.name, v.status) for v in d.variables] == [
        ("datasource", "dropped"),
        ("job", "converts"),
    ]
    assert d.variables_used == ["datasource", "job"] and d.variables_unused == []
    assert d.annotations_dropped == 0  # only the built-in annotation


def test_the_hard_fixture_fails_each_panel_for_its_own_reason() -> None:
    d = plan_of("hard-service-mixed.json")
    panels = by_title(d)
    expected = {
        "Request rate": [r.P_VARIABLE, r.Q_LABEL_FN],  # $service stays manual
        "Latency p95 / p99": [r.P_VARIABLE],
        "Error ratio": [r.P_EXPRESSION],
        "Slow endpoints": [r.P_TRANSFORM],
        "Errors": [r.P_WIDGET_GAP.format(label="logs"), r.P_VARIABLE, r.QL_PARSER],
        "Queue depth (CloudWatch)": [r.P_DS_UNSUPPORTED.format(label="cloudwatch")],
        "CPU by pod (library)": [r.P_LIBRARY],
        "Pod phase": [r.P_NO_WIDGET.format(label="state-timeline")],
        "Retries": [r.P_VARIABLE],
        "Firing alerts": [r.P_NO_WIDGET.format(label="alertlist")],
    }
    assert {t: panels[t].reasons for t in expected} == expected
    assert counts(d) == (10, 0, 0, 0, 0)  # the `service` variable has a lookahead regex
    # single queries still reach their tier: request rate A, latency p95 and p99, slow
    # endpoints, retries
    assert (d.queries, d.query_t2, d.query_manual) == (9, 5, 4)
    assert d.rows == 2 and d.collapsed_rows == 1 and d.row_repeats == 1


def test_the_hard_fixture_variables_are_classified_one_by_one() -> None:
    d = plan_of("hard-service-mixed.json")
    got = {v.name: (v.status, v.reason) for v in d.variables}
    assert got == {
        "env": ("converts", ""),
        "service": ("manual", r.V_QUERY_REGEX),
        "pod": ("manual", r.V_NESTED),
        "window": ("dropped", ""),
        "filters": ("manual", r.V_ADHOC),
        "owner": ("converts", ""),
        "search": ("converts", ""),
    }
    assert d.annotations_dropped == 1 and d.links == 1


def test_the_legacy_fixture_reads_rows_and_marks_the_old_table_transform() -> None:
    d = plan_of("legacy-rows-v12.json")
    assert counts(d) == (3, 0, 2, 2, 2)  # the singlestat has value maps: one tier lower
    assert by_title(d)["Per instance"].reasons == [r.P_TRANSFORM]
    assert by_title(d)["Instances up"].datasources == ["prometheus"]  # through __inputs


def test_the_v2_fixture_converts_two_widgets_and_reports_the_library_panel() -> None:
    d = plan_of("v2-small.json")
    assert counts(d) == (3, 2, 2, 2, 2)
    assert by_title(d)["Shared CPU panel"].reasons == [r.P_LIBRARY]
    assert d.variables_used == ["job"] and d.variables_unused == ["mode"]
    assert [(v.name, v.status) for v in d.variables] == [("job", "converts"), ("mode", "converts")]


def test_the_hostile_fixture_plans_without_error_and_no_reason_carries_its_text() -> None:
    plan = build_plan(load_estate(FIXTURES / "hostile.json"))
    d = plan.dashboards[0]
    every = [reason for p in d.panels for reason in p.reasons] + [
        v.reason for v in d.variables if v.reason
    ]
    assert every
    for reason in every:
        assert reason.startswith("dashboard ")
        assert not any(bad in reason for bad in ("DROP", "evil", "${", "jndi", "SELECT", "pwn"))
    assert d.variables_undefined == []


def test_reasons_come_only_from_the_fixed_tables() -> None:
    known = {
        v
        for k, v in vars(r).items()
        if k.isupper() and isinstance(v, str) and v.startswith("dashboard ")
    }
    labels = {"other", "grafana", *TABLE, *OTHER_DATASOURCES.values()}  # the only fillers
    reasons_seen: set[str] = set()
    for name in sorted(p.name for p in FIXTURES.glob("*.json")):
        d = plan_of(name)
        reasons_seen.update(x for p in d.panels for x in p.reasons)
        reasons_seen.update(v.reason for v in d.variables if v.reason)
    for reason in reasons_seen:
        assert any(
            reason == t or reason in {t.replace("{label}", lab) for lab in labels} for t in known
        ), reason


PANEL_TYPES = [
    ("timeseries", "timeseries", "core", "viz.line"),
    ("graph", "timeseries", "core", "viz.line"),
    ("stat", "stat", "core", "viz.billboard"),
    ("singlestat", "stat", "core", "viz.billboard"),
    ("grafana-singlestat-panel", "stat", "core", "viz.billboard"),
    ("table", "table", "core", "viz.table"),
    ("table-old", "table", "core", "viz.table"),
    ("piechart", "piechart", "core", "viz.pie"),
    ("grafana-piechart-panel", "piechart", "core", "viz.pie"),
    ("text", "text", "core", "viz.markdown"),
    ("gauge", "gauge", "extra", "gauge"),
    ("bargauge", "bargauge", "extra", "viz.bullet"),
    ("barchart", "barchart", "extra", "viz.bar"),
    ("heatmap", "heatmap", "extra", "viz.heatmap"),
    ("histogram", "histogram", "extra", "viz.histogram"),
    ("logs", "logs", "extra", "log table"),
    ("logstable", "logstable", "extra", "log table"),
    ("trend", "trend", "extra", "viz.line"),
    ("state-timeline", "state-timeline", "none", ""),
    ("status-history", "status-history", "none", ""),
    ("natel-discrete-panel", "state-timeline", "none", ""),
    ("news", "news", "none", ""),
    ("alertlist", "alertlist", "none", ""),
    ("dashlist", "dashlist", "none", ""),
    ("geomap", "geomap", "none", ""),
    ("grafana-worldmap-panel", "geomap", "none", ""),
    ("nodeGraph", "nodeGraph", "none", ""),
    ("traces", "traces", "none", ""),
    ("flamegraph", "flamegraph", "none", ""),
    ("canvas", "canvas", "none", ""),
    ("xychart", "xychart", "none", ""),
    ("candlestick", "candlestick", "none", ""),
    ("annolist", "annolist", "none", ""),
    ("debug", "debug", "none", ""),
    ("live", "live", "none", ""),
    ("grafana-polystat-panel", "other", "none", ""),
    ("", "other", "none", ""),
    ("x" * 500, "other", "none", ""),
    ("<script>", "other", "none", ""),
]


@pytest.mark.parametrize(("raw", "label", "group", "widget"), PANEL_TYPES)
def test_every_panel_type_in_the_table_has_a_widget_group_and_a_safe_label(
    raw: str, label: str, group: str, widget: str
) -> None:
    found = panel_type(raw)
    assert (found.label, found.group, found.widget) == (label, group, widget)


def ds(kind: str = "", uid: str = "") -> m.DsRef:
    return m.DsRef(type=kind, uid=uid)


def dash(**kw: Any) -> m.Dashboard:
    return m.Dashboard(**kw)


DATASOURCES = [
    (ds("prometheus", "p1"), "", {}, "prometheus"),
    (ds("loki", "l1"), "", {}, "loki"),
    (ds("grafana-azure-monitor-datasource", "a"), "", {}, "azure monitor"),
    (ds("mysql", "x"), "", {}, "sql"),
    (ds("stackdriver", "x"), "", {}, "google cloud monitoring"),
    (ds("", "-- Mixed --"), "", {}, "mixed"),
    (ds("datasource", "-- Dashboard --"), "", {}, "dashboard"),
    (ds("grafana", "-- Grafana --"), "", {}, "grafana"),
    (ds("__expr__", "__expr__"), "", {}, "expression"),
    (ds("", "-100"), "", {}, "expression"),
    (ds("", "${DS_PROMETHEUS}"), "", {"inputs": {"DS_PROMETHEUS": "prometheus"}}, "prometheus"),
    (ds("", "$DS_PROMETHEUS"), "", {"inputs": {"DS_PROMETHEUS": "loki"}}, "loki"),
    (ds("prometheus", "${datasource}"), "", {}, "prometheus"),
    (
        ds("", "${ds}"),
        "",
        {"variables": [m.Variable(name="ds", kind="datasource", query="loki")]},
        "loki",
    ),
    (
        ds("", "[[ds]]"),
        "",
        {"variables": [m.Variable(name="ds", kind="datasource", query="mysql")]},
        "sql",
    ),
    (ds("", "${unknown}"), "", {}, ""),
    (ds("", "prom-main"), "", {}, "prometheus"),
    (ds("", "loki-main"), "", {}, "loki"),
    (ds("", "mimir-eu"), "", {}, "prometheus"),
    (None, "", {}, ""),
    (None, "prometheus", {}, "prometheus"),
    (None, "cloudwatch", {}, "cloudwatch"),
    (ds("some-plugin-ds", "x"), "", {}, "other"),
    (ds("x" * 300, "y"), "", {}, "other"),
]


@pytest.mark.parametrize(("ref", "hint", "extra", "expected"), DATASOURCES)
def test_a_data_source_reference_resolves_to_a_family(
    ref: m.DsRef | None, hint: str, extra: dict[str, Any], expected: str
) -> None:
    assert resolve(ref, hint, dash(**extra)) == expected


def test_a_target_reference_beats_the_panel_one_and_mixed_falls_back_to_the_hint() -> None:
    d = dash()
    assert resolve(ds("loki"), "", d) == "loki"
    assert resolve(None, "prometheus", d) == "prometheus"


def var(**kw: Any) -> m.Variable:
    return m.Variable(**{"name": "v", **kw})


VARIABLES = [
    (
        var(kind="query", query="label_values(node_uname_info, job)"),
        "converts",
        "",
        "nrql variable",
    ),
    (var(kind="query", query="label_values(job)"), "converts", "", "nrql variable"),
    (var(kind="query", query='label_values(up{a="b"}, instance)'), "converts", "", "nrql variable"),
    (
        var(kind="query", query="label_values(up, job)", regex="/^(?!canary).*/"),
        "manual",
        r.V_QUERY_REGEX,
        "",
    ),
    (
        var(kind="query", query="label_values(up, job)", regex="/prod-.*/"),
        "converts",
        "",
        "nrql variable",
    ),
    (var(kind="query", query='label_values(up{s=~"$service"}, pod)'), "manual", r.V_NESTED, ""),
    (var(kind="query", query="query_result(up)"), "manual", r.V_QUERY_SHAPE, ""),
    (var(kind="query", query="label_names()"), "manual", r.V_QUERY_SHAPE, ""),
    (var(kind="query", query="x", ds=m.DsRef(type="loki")), "manual", r.V_QUERY_DS, ""),
    (
        var(kind="query", query="dimension_values(a,b)", ds=m.DsRef(type="cloudwatch")),
        "manual",
        r.V_QUERY_DS,
        "",
    ),
    (var(kind="custom", query="a,b,c"), "converts", "", "enum variable"),
    (var(kind="constant", query="team-a"), "converts", "", "substituted into the queries"),
    (var(kind="textbox"), "converts", "", "string variable"),
    (var(kind="interval", query="1m,5m"), "dropped", "", ""),
    (var(kind="datasource", query="prometheus"), "dropped", "", ""),
    (var(kind="adhoc"), "manual", r.V_ADHOC, ""),
    (var(kind="switch"), "converts", "", "enum variable"),
    (var(kind="whatever"), "manual", r.V_KIND, ""),
    (var(kind=""), "manual", r.V_KIND, ""),
    (var(name="x' OR 1=1 --", kind="custom"), "manual", r.V_NAME, ""),
    (var(name="", kind="custom"), "manual", r.V_NAME, ""),
    (var(name="a" * 65, kind="custom"), "manual", r.V_NAME, ""),
]


@pytest.mark.parametrize(("variable", "status", "reason", "target"), VARIABLES)
def test_a_variable_is_classified_by_type_and_query(
    variable: m.Variable, status: str, reason: str, target: str
) -> None:
    found = classify_variable(variable, dash(variables=[variable]), {"service"})
    assert (found.status, found.reason, found.target) == (status, reason, target)


def test_a_variable_name_used_twice_is_manual_the_second_time() -> None:
    first, second = var(name="a", kind="custom"), var(name="a", kind="custom")
    d = dash(variables=[first, second])
    assert classify_variable(first, d, {"a"}, seen=set()).status == "converts"
    assert classify_variable(second, d, {"a"}, seen={"a"}).reason == r.V_DUPLICATE


def test_templating_usage_lists_used_unused_and_undefined_variables() -> None:
    body = {
        "uid": "u",
        "title": "t",
        "templating": {"list": [{"name": "a", "type": "custom"}, {"name": "b", "type": "custom"}]},
        "panels": [
            {
                "id": 1,
                "type": "stat",
                "targets": [{"refId": "A", "expr": 'up{x="$a", y="$ghost"}'}],
            }
        ],
    }
    d = build_plan(load_data(body, "x")).dashboards[0]
    assert (d.variables_used, d.variables_unused) == (["a"], ["b"])


def reuse_body(source_expr: str, reuse_id: int = 1) -> dict[str, Any]:
    return {
        "uid": "u",
        "title": "t",
        "panels": [
            {"id": 1, "type": "timeseries", "targets": [{"refId": "A", "expr": source_expr}]},
            {
                "id": 2,
                "type": "stat",
                "datasource": {"type": "datasource", "uid": "-- Dashboard --"},
                "targets": [
                    {"refId": "A", "datasource": {"uid": "-- Dashboard --"}, "panelId": reuse_id}
                ],
            },
        ],
    }


@pytest.mark.parametrize(
    ("source", "reuse_id", "reasons", "tier"),
    [
        ("up", 1, [], 1),
        ("rate(x_total[5m])", 1, [], 2),
        ("up == 1", 1, [r.P_REUSE], None),
        ("up", 99, [r.P_REUSE], None),
        ("up", 2, [r.P_REUSE], None),
    ],
)
def test_a_dashboard_data_source_panel_converts_only_if_its_source_does(
    source: str, reuse_id: int, reasons: list[str], tier: int | None
) -> None:
    d = build_plan(load_data(reuse_body(source, reuse_id), "x")).dashboards[0]
    found = d.panels[1]
    assert (found.reasons, found.tier) == (reasons, tier)


def test_a_mixed_panel_converts_only_when_every_visible_target_does() -> None:
    def panel(second: dict[str, Any]) -> dict[str, Any]:
        return {
            "uid": "u",
            "title": "t",
            "panels": [
                {
                    "id": 1,
                    "type": "timeseries",
                    "datasource": {"uid": "-- Mixed --"},
                    "targets": [
                        {"refId": "A", "datasource": {"type": "prometheus"}, "expr": "up"},
                        second,
                    ],
                }
            ],
        }

    ok = build_plan(
        load_data(panel({"refId": "B", "datasource": {"type": "prometheus"}, "expr": "up"}), "x")
    )
    assert ok.dashboards[0].panels[0].tier == 1
    hidden = build_plan(
        load_data(panel({"refId": "B", "hide": True, "namespace": "n", "metricName": "m"}), "x")
    )
    assert hidden.dashboards[0].panels[0].tier == 1
    bad = build_plan(load_data(panel({"refId": "B", "namespace": "n", "metricName": "m"}), "x"))
    assert bad.dashboards[0].panels[0].reasons == [r.P_DS_UNSUPPORTED.format(label="cloudwatch")]


@pytest.mark.parametrize(
    ("panel", "reasons", "tier", "extra"),
    [
        ({"type": "text"}, [], 1, None),
        ({"type": "stat", "targets": []}, [r.P_NO_QUERY], None, None),
        ({"type": "stat", "targets": [{"refId": "A", "expr": ""}]}, [r.P_EMPTY_QUERY], None, None),
        ({"type": "stat", "targets": [{"refId": "A", "expr": 5}]}, [r.P_EMPTY_QUERY], None, None),
        (
            {"type": "stat", "targets": [{"refId": "A", "hide": True, "expr": "up"}]},
            [r.P_NO_QUERY],
            None,
            None,
        ),
        (
            {"type": "gauge", "targets": [{"refId": "A", "expr": "up"}]},
            [r.P_WIDGET_GAP.format(label="gauge")],
            None,
            1,
        ),
        (
            {"type": "bargauge", "targets": [{"refId": "A", "expr": "sum(a) / sum(b)"}]},
            [r.P_WIDGET_GAP.format(label="bargauge")],
            None,
            3,
        ),
        (
            {"type": "heatmap", "targets": [{"refId": "A", "expr": "up == 1"}]},
            [r.P_WIDGET_GAP.format(label="heatmap"), r.Q_COMPARE],
            None,
            None,
        ),
        (
            {
                "type": "logs",
                "datasource": {"type": "loki"},
                "targets": [{"refId": "A", "expr": '{a="b"} |= "x"'}],
            },
            [r.P_WIDGET_GAP.format(label="logs")],
            None,
            1,
        ),
        (
            {"type": "state-timeline", "targets": [{"refId": "A", "expr": "up"}]},
            [r.P_NO_WIDGET.format(label="state-timeline")],
            None,
            None,
        ),
        (
            {
                "type": "stat",
                "transformations": [{"id": "organize"}],
                "targets": [{"refId": "A", "expr": "up"}],
            },
            [r.P_TRANSFORM],
            None,
            None,
        ),
        (
            {"type": "graph", "datasource": "${DS_X}", "targets": [{"refId": "A", "expr": "up"}]},
            [r.P_DS_UNRESOLVED],
            None,
            None,
        ),
        (
            {
                "type": "graph",
                "datasource": {"type": "mysql"},
                "targets": [{"refId": "A", "rawSql": "select 1"}],
            },
            [r.P_DS_UNSUPPORTED.format(label="sql")],
            None,
            None,
        ),
        (
            {
                "type": "graph",
                "datasource": {"type": "prometheus"},
                "targets": [{"refId": "A", "expr": "up"}, {"refId": "B", "expr": "up == 1"}],
            },
            [r.Q_COMPARE],
            None,
            None,
        ),
    ],
)
def test_a_panel_gets_every_reason_that_applies_in_a_fixed_order(
    panel: dict[str, Any], reasons: list[str], tier: int | None, extra: int | None
) -> None:
    body = {"uid": "u", "title": "t", "panels": [{"id": 1, "title": "p", **panel}]}
    found = build_plan(load_data(body, "x")).dashboards[0].panels[0]
    assert (found.reasons, found.tier, found.extra_tier) == (reasons, tier, extra)


def test_totals_add_up_across_dashboards_and_carry_the_unreadable_items() -> None:
    estate = load_estate(FIXTURES)
    plan = build_plan(estate)
    assert isinstance(plan, Plan)
    totals = plan.totals
    assert totals.dashboards == len(estate.dashboards) >= 5
    assert totals.unreadable == len(plan.unreadable) == len(estate.unreadable)
    assert (totals.panels, totals.t1, totals.t2, totals.t3) == (
        sum(d.panel_count for d in plan.dashboards),
        sum(d.t1 for d in plan.dashboards),
        sum(d.t2 for d in plan.dashboards),
        sum(d.t3 for d in plan.dashboards),
    )
    assert totals.t1 <= totals.t2 <= totals.t3 <= totals.t3_extra <= totals.panels
    assert plan.panel_types["timeseries"] >= 4 and plan.datasources["prometheus"] > 0
    assert sum(plan.reasons.values()) > 0


def test_a_dashboard_of_ten_thousand_panels_is_planned_quickly_and_in_bounded_memory() -> None:
    panels = [
        {
            "id": i,
            "type": "timeseries",
            "title": f"p{i}",
            "gridPos": {"x": 0, "y": i, "w": 12, "h": 4},
            "targets": [{"refId": "A", "expr": f'sum(rate(m{i}_total{{job=~"$job"}}[5m]))'}],
        }
        for i in range(m.MAX_PANELS)
    ]
    body = {
        "uid": "u",
        "title": "big",
        "panels": panels,
        "templating": {"list": [{"name": "job", "type": "custom", "query": "a,b"}]},
    }
    estate = load_data(body, "big")
    del panels, body
    tracemalloc.start()
    start = time.perf_counter()
    plan = build_plan(estate)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert plan.dashboards[0].t2 == m.MAX_PANELS
    assert elapsed < 20 and peak < 200 * 1024 * 1024
