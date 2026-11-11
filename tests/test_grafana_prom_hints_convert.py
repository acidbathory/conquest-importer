"""The converter reads a metric's exact type from the hints when there is one."""

import json
from pathlib import Path

import pytest

from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import NameMap, NameMapError, load_name_map
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboards.models import Dashboard, DsRef, Panel, Target
from importer.sources.grafana.dashboards.plan import plan_dashboard


def opts(names: NameMap, timeseries: bool = True) -> QueryOptions:
    return QueryOptions(variables={}, name_map=names, timeseries=timeseries)


def nrql(expr: str, names: NameMap) -> str:
    return convert_query(expr, "prometheus", opts(names)).nrql


def test_types_are_optional_and_checked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert NameMap().types == {}
    good = tmp_path / "h.json"
    good.write_text(
        '{"types": {"up": "gauge", "x_total": "counter", "l_bucket": "histogram_bucket"}}'
    )
    assert load_name_map(good).types["x_total"] == "counter"
    bad = tmp_path / "b.json"
    bad.write_text('{"types": {"up": "dial"}}')
    with pytest.raises(NameMapError):
        load_name_map(bad)


def test_gauge_with_counter_suffix_converts_when_hinted() -> None:
    with pytest.raises(Unconvertible):
        nrql("process_open_fds_total", NameMap())
    names = NameMap(types={"process_open_fds_total": "gauge"})
    assert "average(process_open_fds_total)" in nrql("process_open_fds_total", names)


def test_counter_without_suffix_is_refused_bare_when_hinted() -> None:
    assert "average(requests)" in nrql("requests", NameMap())
    with pytest.raises(Unconvertible):
        nrql("requests", NameMap(types={"requests": "counter"}))
    # through rate it still converts
    assert "rate(sum(requests), 1 second)" in nrql(
        "sum(rate(requests[5m]))", NameMap(types={"requests": "counter"})
    )


def test_histogram_percentile_needs_a_bucket_type_when_hinted() -> None:
    expr = "histogram_quantile(0.9, sum by (le) (rate(lat_bucket[5m])))"
    assert "bucketPercentile(lat_bucket, 90)" in nrql(expr, NameMap())
    assert "bucketPercentile" in nrql(expr, NameMap(types={"lat_bucket": "histogram_bucket"}))
    with pytest.raises(Unconvertible):
        nrql(expr, NameMap(types={"lat_bucket": "gauge"}))


def test_series_hint_unlocks_gauge_sum_as_before() -> None:
    names = NameMap(series={"node_load1": ["instance"]})
    assert "average(node_load1)" in nrql("sum by (instance) (node_load1)", names)
    with pytest.raises(Unconvertible) as stop:
        nrql("sum(node_load1)", names)
    assert str(stop.value) == r.C_GAUGE_SERIES


def plan_reason(expr: str, names: NameMap) -> str:
    dash = Dashboard(
        uid="d",
        title="t",
        panels=[
            Panel(
                id=1,
                type="timeseries",
                title="p",
                targets=[Target(ref_id="A", expr=expr, ds=DsRef(type="prometheus", uid="p"))],
            )
        ],
    )
    plan = plan_dashboard(dash, names.types)
    return plan.panels[0].reasons[0] if plan.panels[0].reasons else ""


def test_planner_follows_the_hints() -> None:
    assert "counter" in plan_reason("process_open_fds_total", NameMap())
    gauge = NameMap(types={"process_open_fds_total": "gauge"})
    assert plan_reason("process_open_fds_total", gauge) == ""
    assert "counter" in plan_reason("requests", NameMap(types={"requests": "counter"}))
    assert plan_reason("requests", NameMap()) == ""


def test_the_command_reads_a_hints_file_next_to_a_name_map(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    source = Path(__file__).parent / "fixtures" / "grafana-dashboard-convert"
    hints = tmp_path / "hints.json"
    hints.write_text(json.dumps({"types": {"node_load1": "counter"}}))
    args = ["convert-grafana-dashboards", "--in", str(source / "node-exporter-rows.json")]
    plain = CliRunner().invoke(app, [*args, "--out", str(tmp_path / "a")])
    hinted = CliRunner().invoke(app, [*args, "--out", str(tmp_path / "b"), "--hints", str(hints)])
    assert plain.exit_code == 0 and hinted.exit_code == 0, hinted.output
    assert "Hints loaded: 1 types" in hinted.output
    assert "average(node_load1)" in "".join(
        p.read_text() for p in (tmp_path / "a").glob("nerdgraph/*")
    )
    assert "average(node_load1)" not in "".join(
        p.read_text() for p in (tmp_path / "b").glob("nerdgraph/*")
    )


def test_hinted_type_that_new_relic_would_infer_differently_gets_a_note() -> None:
    """Measured with remote write: a counter named without _total is stored as a gauge and a gauge
    named *_count is stored as a count, unless newrelic_metric_type is set."""
    counter = convert_query(
        "sum(rate(requests[5m]))", "prometheus", opts(NameMap(types={"requests": "counter"}))
    )
    assert any("newrelic_metric_type=counter" in n and "requests" in n for n in counter.notes)
    gauge = convert_query(
        "sum(open_count)",
        "prometheus",
        opts(NameMap(types={"open_count": "gauge"}, series={"open_count": []})),
    )
    assert any("newrelic_metric_type=gauge" in n and "open_count" in n for n in gauge.notes)


def test_hinted_type_that_matches_the_name_rule_has_no_type_note() -> None:
    names = NameMap(types={"x_total": "counter", "up": "gauge"})
    for expr in ("sum(rate(x_total[5m]))", "up"):
        assert not any(
            "newrelic_metric_type" in n
            for n in convert_query(expr, "prometheus", opts(names)).notes
        )
    # without hints nothing is known, so nothing is said
    assert not any(
        "newrelic_metric_type" in n
        for n in convert_query("sum(rate(requests_total[5m]))", "prometheus", opts(NameMap())).notes
    )
