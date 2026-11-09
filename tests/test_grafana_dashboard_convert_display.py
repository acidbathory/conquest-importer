"""Display options: units, thresholds by colour family, axis limits, legend and stacking."""

from typing import Any

import pytest

from importer.core.dashboard_ir import Threshold, ThresholdSeverity, Visualization
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.display import (
    family,
    new_relic_unit,
    number,
    read_display,
    thresholds_for,
)

S = ThresholdSeverity


def stat(steps: list[Any], **defaults: Any) -> dict[str, Any]:
    return {
        "type": "stat",
        "fieldConfig": {
            "defaults": {"thresholds": {"mode": "absolute", "steps": steps}, **defaults}
        },
    }


@pytest.mark.parametrize(
    ("color", "expected"),
    [
        ("green", "green"),
        ("dark-green", "green"),
        ("semi-dark-red", "red"),
        ("super-light-yellow", "yellow"),
        ("dark-orange", "orange"),
        ("blue", "other"),
        ("text", "other"),
        ("", "other"),
        ("#73BF69", "green"),
        ("#F2495C", "red"),
        ("#FF9830", "orange"),
        ("#FADE2A", "yellow"),
        ("#808080", "other"),
        ("rgba(245, 54, 54, 0.9)", "red"),
        ("rgb(50, 172, 45)", "green"),
    ],
)
def test_colour_families(color: str, expected: str) -> None:
    assert family(color) == expected


@pytest.mark.parametrize(
    ("unit", "expected", "notes"),
    [
        ("percent", "PERCENTAGE", []),
        ("bytes", "BYTES", []),
        ("reqps", "REQUESTS_PER_SECOND", []),
        ("short", None, []),
        ("", None, []),
        ("percentunit", None, [r.N_PERCENTUNIT]),
        ("µs", None, [r.N_UNIT]),
        ("dtdurations", None, [r.N_UNIT]),
    ],
)
def test_units(unit: str, expected: str | None, notes: list[str]) -> None:
    assert new_relic_unit(unit) == (expected, notes)


def test_a_stat_with_red_and_yellow_steps_becomes_critical_and_warning() -> None:
    steps = [
        {"value": None, "color": "green"},
        {"value": 70, "color": "orange"},
        {"value": 90, "color": "red"},
    ]
    found, notes = thresholds_for(read_display(stat(steps)), Visualization.BILLBOARD)
    assert found == [
        Threshold(severity=S.WARNING, from_value=70),
        Threshold(severity=S.CRITICAL, from_value=90),
    ]
    assert notes == []


def test_a_billboard_keeps_the_lowest_step_of_each_severity() -> None:
    steps = [
        {"value": None, "color": "green"},
        {"value": 50, "color": "red"},
        {"value": 80, "color": "dark-red"},
    ]
    found, _ = thresholds_for(read_display(stat(steps)), Visualization.BILLBOARD)
    assert found == [Threshold(severity=S.CRITICAL, from_value=50)]


def test_thresholds_that_go_from_bad_to_good_are_left_out_with_a_note() -> None:
    steps = [
        {"value": None, "color": "red"},
        {"value": 10, "color": "yellow"},
        {"value": 90, "color": "green"},
    ]
    found, notes = thresholds_for(read_display(stat(steps)), Visualization.BILLBOARD)
    assert found == [] and notes == [r.N_THRESHOLD_ORDER]


def test_other_colours_are_left_out_with_a_note_and_percentage_mode_too() -> None:
    steps = [{"value": None, "color": "green"}, {"value": 5, "color": "purple"}]
    found, notes = thresholds_for(read_display(stat(steps)), Visualization.BILLBOARD)
    assert found == [] and notes == [r.N_THRESHOLD_COLORS]
    body = stat(steps)
    body["fieldConfig"]["defaults"]["thresholds"]["mode"] = "percentage"
    assert thresholds_for(read_display(body), Visualization.BILLBOARD) == ([], [r.N_THRESHOLD_MODE])


def test_stat_thresholds_count_only_when_the_colour_mode_uses_them() -> None:
    steps = [{"value": None, "color": "green"}, {"value": 5, "color": "red"}]
    fixed = stat(steps, color={"mode": "fixed", "fixedColor": "blue"})
    assert thresholds_for(read_display(fixed), Visualization.BILLBOARD) == ([], [])
    assert thresholds_for(read_display(stat(steps)), Visualization.BILLBOARD)[0] != []


def test_time_series_default_thresholds_are_not_drawn_and_so_not_converted() -> None:
    steps = [{"value": None, "color": "green"}, {"value": 80, "color": "red"}]
    body = {"type": "timeseries", **stat(steps)}
    assert thresholds_for(read_display(body), Visualization.LINE) == ([], [])
    body["fieldConfig"]["defaults"]["custom"] = {"thresholdsStyle": {"mode": "line"}}
    found, notes = thresholds_for(read_display(body), Visualization.LINE)
    assert found == [
        Threshold(severity=S.SUCCESS, to_value=80),
        Threshold(severity=S.CRITICAL, from_value=80),
    ]
    assert notes == []
    assert thresholds_for(read_display(body), Visualization.AREA) == ([], [r.N_THRESHOLD_TYPE])


def test_a_legacy_singlestat_threshold_string_is_read() -> None:
    body = {"type": "singlestat", "thresholds": "70,90", "colors": ["green", "orange", "red"]}
    found, _ = thresholds_for(read_display(body), Visualization.BILLBOARD)
    assert [(t.severity, t.from_value) for t in found] == [(S.WARNING, 70), (S.CRITICAL, 90)]
    bad = {"thresholds": "70,x", "colors": ["green", "orange", "red"]}
    assert read_display(bad).steps == []


def test_axis_legend_stacking_and_unit_are_read_from_modern_and_legacy_panels() -> None:
    modern = {
        "fieldConfig": {
            "defaults": {
                "unit": "bytes",
                "decimals": 2,
                "min": 0,
                "max": 100,
                "custom": {"stacking": {"mode": "normal"}, "fillOpacity": 30, "drawStyle": "bars"},
            }
        },
        "options": {"legend": {"showLegend": False}},
    }
    d = read_display(modern)
    assert (d.unit, d.decimals, d.low, d.high) == ("bytes", 2, 0.0, 100.0)
    assert (d.legend, d.stacking, d.fill, d.draw) == (False, "normal", 30, "bars")
    old = {
        "type": "graph",
        "stack": True,
        "fill": 1,
        "bars": True,
        "lines": False,
        "legend": {"show": True},
        "yaxes": [{"format": "ms", "min": "0", "max": 5, "decimals": 1}],
    }
    d = read_display(old)
    assert (d.unit, d.stacking, d.draw, d.legend, d.high, d.decimals) == (
        "ms",
        "normal",
        "bars",
        True,
        5.0,
        1,
    )


@pytest.mark.parametrize("value", [True, "3", None, float("nan"), float("inf"), 1e30, [1], {}])
def test_numbers_must_be_finite_real_numbers_in_range(value: object) -> None:
    assert number(value) is None


def test_hostile_values_do_not_raise() -> None:
    body = {
        "fieldConfig": {"defaults": {"unit": 5, "min": "x", "thresholds": {"steps": "no"}}},
        "options": {"legend": "yes", "content": 7},
        "yaxes": "x",
        "colors": {},
    }
    d = read_display(body)
    assert d.unit == "" and d.low is None and d.steps == [] and d.content == ""
