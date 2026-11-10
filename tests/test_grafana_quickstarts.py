"""The quickstart map: the data file, the metric-name overlap check and the verdicts."""

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.models import Dashboard, Panel, Target
from importer.sources.grafana.dashboards.quickstart_map import (
    CHANGE,
    RELATED,
    REPLACE,
    entries,
    match_dashboard,
    overlap,
    plan_quickstarts,
    promql_metrics,
    read_gnet_ids,
)

runner = CliRunner()
URL = re.compile(
    r"^https://newrelic\.com/instant-observability/[a-z0-9-]+/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def dash(title: str, *exprs: str, uid: str = "u") -> Dashboard:
    panels = [Panel(id=i, targets=[Target(expr=e)]) for i, e in enumerate(exprs)]
    return Dashboard(uid=uid, title=title, panels=panels)


def by_id(entry_id: str):  # type: ignore[no-untyped-def]
    return next(e for e in entries() if e.id == entry_id)


# the data file


def test_every_entry_is_verified_and_points_at_the_public_repository() -> None:
    seen: set[str] = set()
    for e in entries():
        assert e.id not in seen
        seen.add(e.id)
        assert e.verified and e.evidence
        assert all(u.startswith("https://github.com/newrelic/newrelic-quickstarts/") for u in e.evidence)
        assert e.caveat and e.needs
        if e.quickstart:
            assert URL.match(e.quickstart.url), e.quickstart.url
            assert e.quickstart.config_url.startswith(e.evidence[0][:50])
        else:
            assert e.dashboard_only and e.dashboard_only.url.startswith("https://github.com/")
        if e.data_path == "prometheus-same-names":
            assert e.expected_metrics, e.id


def test_expected_metrics_hold_no_generic_names() -> None:
    for e in entries():
        for name in e.expected_metrics:
            assert not name.startswith(("process_", "go_", "scrape_")), (e.id, name)


# metric extraction


def test_promql_metrics_keeps_metric_names_only() -> None:
    expr = (
        'histogram_quantile(0.9, sum by (le, instance_name) (rate(http_request_duration_seconds_bucket'
        '{job=~"$job",mode!="idle_x"}[$__rate_interval]))) / on(node_name) group_left(pod_name) '
        'label_replace(kube_pod_info, "a_b", "$1", "c_d", "(.*)")'
    )
    assert promql_metrics(expr) == {"http_request_duration_seconds_bucket", "kube_pod_info"}


# the overlap check


def test_overlap_counts_shared_names_and_ignores_generic_ones() -> None:
    o = overlap({"node_load1", "node_load5", "process_cpu_seconds_total", "x_y"}, {"node_load1"})
    assert (o.shared, o.total) == (1, 3) and abs(o.share - 1 / 3) < 1e-9


def test_a_name_collision_is_not_a_signature_match() -> None:
    # three redis_ names the quickstart does not use: the prefix alone must not claim a match
    d = dash("Our cache", "redis_zzz_one", "redis_zzz_two", "redis_zzz_three")
    assert [m for m in match_dashboard(d, None) if m.entry == "redis-prometheus"] == []


def test_a_signature_match_needs_shared_names_with_the_quickstart() -> None:
    names = sorted(by_id("redis-prometheus").expected_metrics)[:4]
    d = dash("Our cache", *names)
    m = next(m for m in match_dashboard(d, None) if m.entry == "redis-prometheus")
    assert m.confidence == "signature match" and m.verdict == REPLACE


def test_an_id_match_with_low_overlap_is_related_not_a_replacement() -> None:
    d = dash("Node Exporter Full", *[f"node_other{i}_total" for i in range(9)], "node_load1")
    m = next(m for m in match_dashboard(d, 1860) if m.entry == "node-exporter")
    assert m.confidence == "exact name match" and m.verdict == RELATED
    assert m.overlap.shared == 1


def test_an_id_match_with_enough_overlap_is_a_replacement() -> None:
    names = sorted(by_id("coredns").expected_metrics)[:4]
    m = next(m for m in match_dashboard(dash("anything", *names), 14981) if m.entry == "coredns")
    assert m.verdict == REPLACE and m.overlap.share == 1.0


def test_a_title_match_without_queries_is_related_because_overlap_is_unknown() -> None:
    m = next(m for m in match_dashboard(dash("Redis Dashboard"), None) if m.entry == "redis-prometheus")
    assert m.verdict == RELATED and m.overlap.total == 0


def test_a_collection_change_entry_needs_a_change_of_collector() -> None:
    d = dash("Whatever", "pg_stat_database_xact_commit", "pg_stat_activity_count", "pg_up_x")
    m = next(m for m in match_dashboard(d, None) if m.entry == "postgresql")
    assert m.verdict == CHANGE and m.confidence == "signature match"


def test_nginx_ingress_names_do_not_match_the_nginx_infrastructure_entry() -> None:
    d = dash("x", "nginx_ingress_controller_a_b", "nginx_ingress_controller_c_d", "nginx_ingress_controller_e_f")
    assert [m for m in match_dashboard(d, None) if m.entry == "nginx"] == []


# the plan


def test_the_plan_counts_dashboards_with_a_native_replacement(tmp_path: Path) -> None:
    good = sorted(by_id("coredns").expected_metrics)[:4]
    estate_dashboards = [
        dash("CoreDNS", *[f'sum(rate({n}{{a="b"}}[5m]))' for n in good], uid="a"),
        dash("Plain", "up_x_y", uid="b"),
    ]
    from importer.sources.grafana.dashboards.models import Estate

    plan = plan_quickstarts(Estate(dashboards=estate_dashboards), {"a": 14981})
    assert plan.coverage == "1 of 2 dashboards have a native replacement"
    rows = {r.uid: r for r in plan.dashboards}
    assert rows["a"].action in {"replace", "both"} and rows["b"].action == "convert"


def test_gnet_ids_are_read_from_exports(tmp_path: Path) -> None:
    (tmp_path / "dashboard-1.json").write_text(
        json.dumps({"dashboard": {"uid": "a", "title": "t", "gnetId": 1860, "panels": []}})
    )
    (tmp_path / "dashboard-2.json").write_text(json.dumps({"uid": "b", "title": "t", "panels": []}))
    assert read_gnet_ids(tmp_path) == {"a": 1860}


# the command


def test_the_command_writes_markdown_and_json(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    names = sorted(by_id("coredns").expected_metrics)[:4]
    body = {
        "uid": "a",
        "title": "CoreDNS",
        "gnetId": 14981,
        "schemaVersion": 39,
        "panels": [
            {"id": i, "type": "timeseries", "title": n, "targets": [{"expr": f"sum(rate({n}[5m]))"}]}
            for i, n in enumerate(names)
        ],
    }
    (src / "dashboard-a.json").write_text(json.dumps(body))
    out = tmp_path / "out"
    result = runner.invoke(app, ["plan-grafana-quickstarts", "--in", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "1 of 1 dashboards have a native replacement" in result.output
    md = (out / "quickstart-plan.md").read_text()
    data = json.loads((out / "quickstart-plan.json").read_text())
    assert "coredns" in md and "names, units and dimensions" in md
    assert data["dashboards"][0]["matches"][0]["quickstart"]["slug"] == "coredns"


def test_the_command_refuses_an_output_folder_inside_the_input(tmp_path: Path) -> None:
    (tmp_path / "d.json").write_text(json.dumps({"uid": "a", "title": "t", "panels": []}))
    result = runner.invoke(
        app, ["plan-grafana-quickstarts", "--in", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1 and "inside" in result.output


def test_a_real_export_folder_loads(tmp_path: Path) -> None:
    assert load_estate  # the planner path the command reuses
