"""Planner round 2: never overstate. Variables, time overrides, options, reuse, reconciliation."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.grafana.dashboards import models as m
from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.load import load_data, load_estate
from importer.sources.grafana.dashboards.plan import PanelPlan, build_plan
from importer.sources.grafana.dashboards.report import plan_json

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-dashboards"


def panel_plan(panel: dict[str, Any], **dashboard: Any) -> PanelPlan:
    body = {"uid": "u", "title": "t", "panels": [{"id": 1, "title": "p", **panel}], **dashboard}
    return build_plan(load_data(body, "x")).dashboards[0].panels[0]


def stat(expr: str = "up", **extra: Any) -> dict[str, Any]:
    return {"type": "stat", "targets": [{"refId": "A", "expr": expr}], **extra}


def test_a_panel_that_uses_a_manual_variable_is_manual() -> None:
    variables = {"list": [{"name": "svc", "type": "query", "query": "query_result(up)"}]}
    found = panel_plan(stat('up{job="$svc"}'), templating=variables)
    assert (found.tier, found.reasons) == (None, [r.P_VARIABLE])
    fine = {"list": [{"name": "svc", "type": "custom", "query": "a,b"}]}
    assert panel_plan(stat('up{job="$svc"}'), templating=fine).tier == 1


def test_a_variable_regex_with_a_capture_group_or_beyond_plain_words_is_manual() -> None:
    def status(regex: str) -> str:
        body = {
            "uid": "u",
            "title": "t",
            "panels": [],
            "templating": {
                "list": [
                    {"name": "v", "type": "query", "query": "label_values(up, job)", "regex": regex}
                ]
            },
        }
        return build_plan(load_data(body, "x")).dashboards[0].variables[0].status

    assert status("/(.*)-prod/") == "manual"
    assert status("/a.b/") == "manual"
    assert status("/prod-.*/") == "converts"  # a plain prefix, as for a matcher
    assert status("/^prod$/") == "manual"
    assert status("/prod|stage/") == "converts"
    assert status("") == "converts"


@pytest.mark.parametrize("key", ["timeShift", "timeFrom"])
def test_a_time_override_makes_the_panel_manual(key: str) -> None:
    found = panel_plan(stat(**{key: "1d"}))
    assert (found.tier, found.reasons) == (None, [r.P_TIME_OVERRIDE])


@pytest.mark.parametrize(
    ("extra", "expr", "tier", "reasons", "noted"),
    [
        ({"fieldConfig": {"defaults": {"mappings": [{"type": "value"}]}}}, "up", 2, [], True),
        ({"fieldConfig": {"overrides": [{"matcher": {}}]}}, "rate(x_total[5m])", 3, [], True),
        ({"fieldConfig": {"defaults": {"unit": "percentunit"}}}, "up", 2, [], True),
        ({"fieldConfig": {"defaults": {"unit": "short"}}}, "up", 1, [], False),
        ({"fieldConfig": {"defaults": {"unit": "none"}}}, "up", 1, [], False),
        ({"options": {"reduceOptions": {"calcs": ["mean"]}}}, "up", 2, [], True),
        ({"options": {"reduceOptions": {"calcs": ["lastNotNull"]}}}, "up", 1, [], False),
        ({"valueName": "avg"}, "up", 2, [], True),
        (
            {"fieldConfig": {"defaults": {"mappings": [{"type": "value"}]}}},
            "sum(up) / sum(up)",
            None,
            [r.P_OPTIONS],
            False,
        ),
    ],
)
def test_options_that_change_what_a_stat_shows_lower_its_tier_by_one(
    extra: dict[str, Any], expr: str, tier: int | None, reasons: list[str], noted: bool
) -> None:
    found = panel_plan(stat(expr, **extra))
    assert (found.tier, found.reasons) == (tier, reasons)
    assert (r.N_DOWNGRADED in found.notes) is noted


def test_a_time_series_panel_is_not_lowered_by_mappings() -> None:
    plain = {"type": "timeseries", "targets": [{"refId": "A", "expr": "up"}]}
    found = panel_plan({**plain, "fieldConfig": {"defaults": {"mappings": [{"type": "value"}]}}})
    assert found.tier == 1 and found.notes == []


def test_a_table_with_overrides_is_lowered_too() -> None:
    found = panel_plan(
        {
            "type": "table",
            "targets": [{"refId": "A", "expr": "up"}],
            "fieldConfig": {"overrides": [{}]},
        }
    )
    assert found.tier == 2


def test_instant_format_interval_and_legend_text_are_noted_not_ignored() -> None:
    found = panel_plan(
        {
            "type": "timeseries",
            "targets": [
                {
                    "refId": "A",
                    "expr": "up",
                    "instant": True,
                    "interval": "1m",
                    "legendFormat": "pod {{pod}} total",
                },
                {"refId": "B", "expr": "up", "format": "table", "legendFormat": "{{pod}}"},
            ],
        }
    )
    assert found.tier == 1
    assert set(found.notes) == {r.N_INSTANT, r.N_INTERVAL, r.N_LEGEND}
    assert panel_plan(stat()).notes == []


def test_reusing_a_text_panel_is_not_tier_one() -> None:
    body = {
        "uid": "u",
        "title": "t",
        "panels": [
            {"id": 1, "type": "text", "options": {"content": "hi"}},
            {
                "id": 2,
                "type": "stat",
                "datasource": {"uid": "-- Dashboard --"},
                "targets": [{"refId": "A", "datasource": {"uid": "-- Dashboard --"}, "panelId": 1}],
            },
        ],
    }
    found = build_plan(load_data(body, "x")).dashboards[0].panels[1]
    assert (found.tier, found.reasons) == (None, [r.P_REUSE])


def generated_bodies() -> list[dict[str, Any]]:
    exprs = [
        "up",
        "up == 1",
        "rate(x_total[5m])",
        "sum(a) / sum(b)",
        'a{b=~".*"}',
        "sum by (a)(x) / sum by (b)(y)",
        "",
        "nope(",
    ]
    panels: list[dict[str, Any]] = []
    for i, expr in enumerate(exprs):
        panels.append({"id": i, "type": "stat", "targets": [{"refId": "A", "expr": expr}]})
    panels.append(
        {
            "id": 20,
            "type": "timeseries",
            "datasource": {"uid": "-- Dashboard --"},
            "targets": [{"refId": "A", "panelId": 0}, {"refId": "B", "panelId": 99}],
        }
    )
    panels.append(
        {
            "id": 21,
            "type": "gauge",
            "targets": [
                {"refId": "A", "expr": "up"},
                {"refId": "B", "namespace": "n", "metricName": "m"},
            ],
        }
    )
    panels.append({"id": 22, "type": "state-timeline", "targets": [{"refId": "A", "expr": "up"}]})
    return [{"uid": "g", "title": "g", "panels": panels}]


def test_the_query_counts_add_up_for_generated_and_fixture_plans() -> None:
    plans = [build_plan(load_data(generated_bodies(), "g")), build_plan(load_estate(FIXTURES))]
    for plan in plans:
        for d in plan.dashboards:
            assert d.queries == d.query_t1 + d.query_t2 + d.query_t3 + d.query_manual, d.title
        t = plan.totals
        assert t.queries == t.query_t1 + t.query_t2 + t.query_t3 + t.query_manual
        assert t.t1 <= t.t2 <= t.t3 <= t.t3_extra <= t.panels


def test_plan_json_lists_a_bounded_number_of_panels_and_keeps_the_counts() -> None:
    panels = [
        {"id": i, "type": "stat", "targets": [{"refId": "A", "expr": "up"}]}
        for i in range(m.MAX_PANELS)
    ]
    plan = build_plan(load_data({"uid": "u", "title": "big", "panels": panels}, "x"))
    data = json.loads(plan_json(plan))
    d = data["dashboards"][0]
    assert d["panel_count"] == m.MAX_PANELS and d["t1"] == m.MAX_PANELS
    assert len(d["panels"]) == 1000 and d["panels_omitted"] == m.MAX_PANELS - 1000
    assert len(plan_json(plan)) < 1_000_000
    assert data["panel_types"]["stat"] == m.MAX_PANELS  # inventories use every panel


def test_the_research_fixtures_have_these_numbers_after_round_two() -> None:
    expected = {
        "easy-node-overview.json": (5, 4, 5, 5, 5),
        "hard-service-mixed.json": (10, 0, 0, 0, 0),
        "legacy-rows-v12.json": (3, 0, 2, 2, 2),
        "v2-small.json": (3, 2, 2, 2, 2),
    }
    for name, counts in expected.items():
        d = build_plan(load_estate(FIXTURES / name)).dashboards[0]
        assert (d.panel_count, d.t1, d.t2, d.t3, d.t3_extra) == counts, name
