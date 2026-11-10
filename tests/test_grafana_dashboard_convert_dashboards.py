"""Whole dashboards: widgets, layout, placeholders, variables and what the emitters accept."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import IRDashboard, IRWidget, ThresholdSeverity, WidgetKind
from importer.core.emit.dashboard import (
    dashboards_terraform,
    nerdgraph_dashboard,
)
from importer.core.ir import Confidence
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.translate import convert_all, load_items

FIXTURES = Path(__file__).parent / "fixtures"
MINE = FIXTURES / "grafana-dashboard-convert"
THEIRS = FIXTURES / "grafana-dashboards"
SCHEMA: dict[str, Any] = json.loads((FIXTURES / "newrelic-one-dashboard-schema.json").read_text())
ALL_FOLDERS = [MINE, THEIRS]


def convert(folder: Path, name: str) -> IRDashboard:
    found = DASHBOARD_REGISTRY["grafana"].convert(folder / name)
    assert len(found) == 1
    return found[0]


def by_title(d: IRDashboard) -> dict[str, IRWidget]:
    return {w.title: w for w in d.widgets}


def queries(w: IRWidget) -> list[str]:
    return [q.nrql for q in w.raw_series]


_BLOCK = re.compile(r'^(\w+)(?: "[^"]*")* \{$')
_ATTR = re.compile(r"^(\w+)\s*=")


def schema_errors(hcl: str) -> list[str]:
    """Names in the generated Terraform that the provider schema does not have."""
    errors: list[str] = []
    stack: list[dict[str, Any]] = []
    for raw in hcl[hcl.index("resource ") :].splitlines():
        line = raw.strip()
        if m := _BLOCK.match(line):
            if not stack:
                stack.append(SCHEMA)
            else:
                child = stack[-1]["blocks"].get(m.group(1))
                if child is None:
                    errors.append(f"unknown block {m.group(1)}")
                    child = {"attributes": {}, "blocks": {}}
                stack.append(child)
        elif line == "}":
            if stack:
                stack.pop()
        elif stack and (m := _ATTR.match(line)) and m.group(1) not in stack[-1]["attributes"]:
            errors.append(f"unknown attribute {m.group(1)}")
    return errors


def all_dashboards() -> list[IRDashboard]:
    found: list[IRDashboard] = []
    for folder in ALL_FOLDERS:
        found += DASHBOARD_REGISTRY["grafana"].convert(folder)
    return found


# --- the registry and the data note ---


def test_the_grafana_dashboard_source_is_registered_for_convert_dashboards() -> None:
    source = DASHBOARD_REGISTRY["grafana"]
    assert source.name == "grafana" and "extract-grafana-dashboards" in source.input_help


def test_every_dashboard_says_the_data_must_arrive_first() -> None:
    dashboards = all_dashboards()
    assert len(dashboards) >= 12
    read = [d for d in dashboards if not d.source_key.startswith("unreadable:")]
    assert len(read) >= 12 and all(d.reasons[0] == r.D_DATA for d in read)


# --- the node exporter style dashboard ---


def test_rows_become_headings_and_collapsed_rows_are_opened() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    headings = [w.title for w in d.widgets if w.source_type == "row"]
    assert headings == ["CPU and load", "Memory", "Network", "Disk", "Detail"]
    assert "1 collapsed rows were opened" in d.reasons
    titles = [w.title for w in d.widgets]
    assert titles.index("Network") < titles.index("Network receive") < titles.index("Disk")


def test_the_layout_is_twelve_columns_and_nothing_overlaps() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    boxes = [(w.layout.column, w.layout.row, w.layout.width, w.layout.height) for w in d.widgets]
    for column, _, width, _ in boxes:
        assert column >= 1 and column + width - 1 <= 12
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            apart = (
                a[0] + a[2] <= b[0]
                or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1]
                or b[1] + b[3] <= a[1]
            )
            assert apart, (a, b)
    cpu = by_title(d)["CPU busy"].layout
    assert (cpu.column, cpu.width, cpu.height) == (1, 8, 4)  # x 0 w 16 h 8 on the 24 grid
    load = by_title(d)["Load average"].layout
    assert (load.column, load.width) == (9, 4)


def test_a_section_starts_under_the_one_before() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    rows = {w.title: w.layout for w in d.widgets if w.source_type == "row"}
    assert rows["CPU and load"].row == 1
    cpu = by_title(d)["CPU busy"].layout
    assert cpu.row == 2  # right under its heading
    assert rows["Memory"].row == cpu.row + cpu.height


def test_stacked_memory_is_an_area_with_alias_and_unit_and_the_gauge_is_a_billboard() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    mem = by_title(d)["Memory"]
    assert mem.source_type == "timeseries" and mem.visualization is not None
    assert mem.visualization.value == "viz.area" and mem.options.unit == "BYTES"
    assert mem.options.stacked is True and mem.options.legend is False
    assert queries(mem)[0].startswith("SELECT average(node_memory_MemTotal_bytes) - ")
    assert queries(mem)[0].endswith("AS 'Used' FROM Metric WHERE job IN ({{job}}) TIMESERIES AUTO")
    gauge = by_title(d)["Memory used"]
    assert gauge.visualization is not None and gauge.visualization.value == "viz.billboard"
    assert [(t.severity, t.from_value) for t in gauge.thresholds] == [
        (ThresholdSeverity.WARNING, 70.0),
        (ThresholdSeverity.CRITICAL, 90.0),
    ]
    assert gauge.options.unit is None  # 0 to 100 percent must not become PERCENTAGE
    assert r.N_PERCENT in gauge.reasons


def test_the_cpu_panel_has_a_threshold_line_only_because_the_style_is_set() -> None:
    cpu = by_title(convert(MINE, "node-exporter-rows.json"))["CPU busy"]
    assert [(t.severity, t.to_value, t.from_value) for t in cpu.thresholds] == [
        (ThresholdSeverity.SUCCESS, 0.8, None),
        (ThresholdSeverity.CRITICAL, None, 0.8),
    ]
    assert (cpu.options.y_min, cpu.options.y_max) == (0.0, 1.0)


def test_a_table_and_a_top_five_bar_and_a_markdown_panel() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    table, bar, notes = (
        by_title(d)[t] for t in ("Filesystems", "Largest filesystems used", "Notes")
    )
    assert table.visualization is not None and table.visualization.value == "viz.table"
    assert "TIMESERIES" not in queries(table)[0]
    assert queries(bar) == [
        "SELECT sum(node_filesystem_size_bytes) FROM Metric WHERE job IN ({{job}}) "
        "FACET mountpoint LIMIT 5"
    ]
    assert notes.kind == WidgetKind.MARKDOWN
    assert notes.text == "Metrics come from node_exporter.\n\nSee the runbook."


def test_the_job_variable_is_an_nrql_multi_select_that_selects_all() -> None:
    d = convert(MINE, "node-exporter-rows.json")
    assert [v.name for v in d.variables] == ["job", "instance"]
    job, instance = d.variables
    assert job.nrql == "FROM Metric SELECT uniques(job) WHERE metricName = 'node_uname_info'"
    assert job.multi_select and job.default_values == ["*"]
    # `instance` depends on `job`: a nested variable, listed after its parent
    assert instance.nrql == (
        "FROM Metric SELECT uniques(instance) WHERE metricName = 'node_uname_info' "
        "AND job IN ({{job}})"
    )
    assert not any("were not converted" in x for x in d.reasons)
    assert any(x.startswith("1 variables use another variable") for x in d.reasons)


# --- placeholders ---


def test_a_manual_panel_is_a_visible_placeholder_with_reason_and_query() -> None:
    made, _ = convert_all(load_items(MINE / "hostile-convert.json"))
    placeholder = next(w for m in made for w in m.widgets if w.kind == WidgetKind.UNSUPPORTED)
    assert placeholder.confidence == Confidence.MANUAL
    assert any(x.startswith("query: ") for x in placeholder.reasons)
    terraform = dashboards_terraform([(1, made[0])])
    assert "**Not migrated:**" in terraform


def test_placeholder_query_text_is_escaped_and_capped() -> None:
    d = convert(MINE, "hostile-convert.json")
    long_one = by_title(d)["Long query"]
    shown = next(x for x in long_one.reasons if x.startswith("query: "))
    assert len(shown) <= len("query: ") + 100
    quoted = by_title(d)["Metric with quote"]
    text = next(x for x in quoted.reasons if x.startswith("query: "))
    assert not any(ch in text for ch in "|`<>[]")


def test_a_dashboard_where_nothing_converts_is_not_emitted_but_is_listed() -> None:
    items = load_items(THEIRS / "hostile" / "wrong-types.json")
    made, _ = convert_all(items)
    assert all(m.confidence != Confidence.EXACT for m in made)


# --- variables, repeats, logs, thresholds on other fixtures ---


def test_kubernetes_variables_constants_and_intervals() -> None:
    d = convert(MINE, "kubernetes-variables.json")
    kinds = {v.name: v.type.value for v in d.variables}
    assert kinds == {"namespace": "nrql", "cluster": "enum", "jobfilter": "string", "pod": "nrql"}
    assert [v.name for v in d.variables].index("namespace") < [v.name for v in d.variables].index(
        "pod"
    )
    pod = next(v for v in d.variables if v.name == "pod")
    assert pod.nrql is not None and "namespace IN ({{namespace}})" in pod.nrql
    assert d.variables[1].default_values == ["prod-eu"]
    w = by_title(d)
    assert queries(w["Region note"]) == [
        "SELECT sum(up) AS 'up in' FROM Metric WHERE region = 'eu-west-1' "
        "AND job IN ({{jobfilter}}) TIMESERIES AUTO"
    ]
    assert "interval" not in {v.name for v in d.variables}  # dropped, [$interval] is the bucket
    assert queries(w["Restarts"])[0].startswith("SELECT sum(kube_pod_container_status_restarts")
    assert "2 interval or data source variables were dropped" in d.reasons


def test_the_p99_panel_uses_a_percentile_with_a_cluster_variable_and_a_facet() -> None:
    q = queries(by_title(convert(MINE, "kubernetes-variables.json"))["Request latency p99"])
    assert q == [
        "SELECT bucketPercentile(http_request_duration_seconds_bucket, 99) FROM Metric "
        "WHERE namespace IN ({{namespace}}) AND cluster IN ({{cluster}}) "
        "FACET service LIMIT MAX TIMESERIES AUTO"
    ]


def test_loki_panels_are_counts_rates_and_a_log_table() -> None:
    d = convert(MINE, "loki-logs.json")
    w = by_title(d)
    assert queries(w["Checkout logs"]) == [
        "SELECT timestamp, message FROM Log WHERE app = 'checkout' "
        "AND env IN ('prod', 'staging') AND message LIKE '%timeout%' LIMIT 200"
    ]
    assert w["Checkout logs"].visualization is not None
    assert w["Checkout logs"].visualization.value == "viz.table"
    assert w["Latency from logs"].kind == WidgetKind.UNSUPPORTED  # json and unwrap
    assert w["Parsed logs"].kind == WidgetKind.UNSUPPORTED


def test_stat_thresholds_and_the_legacy_singlestat() -> None:
    w = by_title(convert(MINE, "sre-stat-thresholds.json"))
    p95 = w["p95 latency"]
    assert p95.options.unit == "SECONDS"
    assert [(t.severity, t.from_value) for t in p95.thresholds] == [
        (ThresholdSeverity.WARNING, 0.5),
        (ThresholdSeverity.CRITICAL, 1.0),
    ]
    saturation = w["Saturation"]
    assert [(t.severity, t.from_value) for t in saturation.thresholds] == [
        (ThresholdSeverity.WARNING, 4.0),
        (ThresholdSeverity.CRITICAL, 8.0),
    ]
    assert w["Legacy uptime"].thresholds == []  # red to green: bad at low values
    assert any("go from bad to good" in x for x in w["Legacy uptime"].reasons)
    assert w["Weekly delta"].kind == WidgetKind.UNSUPPORTED  # offset


def test_a_repeated_panel_is_one_widget_with_a_facet_on_the_repeat_label() -> None:
    w = by_title(convert(MINE, "repeated-panels.json"))
    cpu = w["CPU of $instance"]
    assert queries(cpu) == [
        "SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric "
        "WHERE instance IN ({{instance}}) AND mode = 'user' FACET instance LIMIT MAX "
        "TIMESERIES AUTO"
    ]
    assert r.N_REPEAT in cpu.reasons
    assert r.N_REPEAT_FLAT in w["Repeats with no label"].reasons
    assert w["Shared library panel"].kind == WidgetKind.UNSUPPORTED


# --- the emitters accept everything ---


@pytest.mark.parametrize("folder", ALL_FOLDERS, ids=lambda p: p.name)
def test_every_converted_dashboard_emits_and_matches_the_provider_schema(folder: Path) -> None:
    emitted = 0
    for d in DASHBOARD_REGISTRY["grafana"].convert(folder):
        if d.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
            continue
        payload = nerdgraph_dashboard(d)["variables"]["dashboard"]
        assert payload["permissions"] == "PRIVATE"
        hcl = dashboards_terraform([(1, d)])
        assert schema_errors(hcl) == []
        assert 'permissions = "private"' in hcl
        emitted += 1
    assert emitted >= 1


def test_hostile_text_never_leaves_its_string_in_the_terraform() -> None:
    d = convert(MINE, "hostile-convert.json")
    hcl = dashboards_terraform([(1, d)])
    assert schema_errors(hcl) == []
    assert hcl.count('resource "newrelic_one_dashboard"') == 1
    assert "null_resource" not in hcl.replace('\\"null_resource\\"', "")
    for widget in nerdgraph_dashboard(d)["variables"]["dashboard"]["pages"][0]["widgets"]:
        for q in widget["rawConfiguration"].get("nrqlQueries", []):
            assert "\n" not in q["query"] and ";" not in q["query"].replace("\\;", "")


def test_widget_options_the_ir_refuses_do_not_stop_the_dashboard() -> None:
    d = convert(MINE, "hostile-convert.json")
    first = d.widgets[0]
    assert first.kind == WidgetKind.CHART
    assert first.options.y_min is None and first.options.y_max is None  # 1e300 and -1e300
    assert first.thresholds == []


def test_unreadable_files_are_listed_as_dashboards_that_did_not_convert() -> None:
    items = load_items(THEIRS / "hostile")
    made, _ = convert_all(items)
    broken = [m for m in made if m.source_key.startswith("unreadable:")]
    assert broken and all(m.confidence == Confidence.UNSUPPORTED for m in broken)
    assert all(m.reasons[0].startswith("the file could not be read: ") for m in broken)


def test_two_dashboards_with_the_same_uid_get_different_keys() -> None:
    items = load_items(MINE) + load_items(MINE / "kubernetes-variables.json")
    made, _ = convert_all(items)
    keys = [m.source_key for m in made]
    assert len(keys) == len(set(keys)) and "k8s-vars#2" in keys


def test_html_in_a_text_panel_is_reduced_to_text() -> None:
    text = by_title(convert(MINE, "hostile-convert.json"))["html <script>alert(1)</script>"]
    assert text.text == "{{var}} bold & text"
    assert text.confidence == Confidence.APPROXIMATE and r.N_HTML in text.reasons


def _one_panel(**panel: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": 1,
        "type": "timeseries",
        "title": "p",
        "datasource": {"type": "prometheus", "uid": "p"},
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 8},
        "targets": [{"refId": "A", "expr": "sum(up)"}],
    }
    body.update(panel)
    return {"uid": "d", "title": "d", "schemaVersion": 39, "panels": [body]}


def _convert_data(data: dict[str, Any], tmp_path: Path) -> IRDashboard:
    (tmp_path / "d.json").write_text(json.dumps(data))
    return DASHBOARD_REGISTRY["grafana"].convert(tmp_path)[0]


@pytest.mark.parametrize(
    ("interval", "tail"),
    [
        ("1m", "TIMESERIES 60 SECONDS"),
        ("2h", "TIMESERIES 7200 SECONDS"),
        ("100h", "TIMESERIES AUTO"),
        ("0s", "TIMESERIES AUTO"),
        ("$__interval", "TIMESERIES AUTO"),
        ("1d", "TIMESERIES AUTO"),
    ],
)
def test_a_fixed_query_interval_is_the_bucket_only_when_it_is_a_sane_literal(
    interval: str, tail: str, tmp_path: Path
) -> None:
    data = _one_panel(targets=[{"refId": "A", "expr": "sum(up)", "interval": interval}])
    widget = _convert_data(data, tmp_path).widgets[0]
    assert queries(widget)[0].endswith(tail)
    assert (r.N_FIXED_INTERVAL in widget.reasons) == (tail != "TIMESERIES AUTO")


def test_axis_limits_that_cross_are_dropped_and_ordinary_ones_kept(tmp_path: Path) -> None:
    crossed = _one_panel(fieldConfig={"defaults": {"min": 10, "max": 5}})
    widget = _convert_data(crossed, tmp_path).widgets[0]
    assert (widget.options.y_min, widget.options.y_max) == (None, None)
    fine = _one_panel(fieldConfig={"defaults": {"min": 0, "max": 5}})
    widget = _convert_data(fine, tmp_path).widgets[0]
    assert (widget.options.y_min, widget.options.y_max) == (0.0, 5.0)


def test_a_panel_that_shows_another_panels_result_uses_that_panels_query(tmp_path: Path) -> None:
    data = _one_panel()
    data["panels"].append(
        {
            "id": 2,
            "type": "stat",
            "title": "copy",
            "gridPos": {"x": 0, "y": 8, "w": 24, "h": 4},
            "datasource": {"type": "datasource", "uid": "-- Dashboard --"},
            "targets": [{"refId": "A", "panelId": 1, "datasource": {"uid": "-- Dashboard --"}}],
        }
    )
    widgets = _convert_data(data, tmp_path).widgets
    assert queries(widgets[1]) == ["SELECT sum(up) FROM Metric"]
    assert widgets[1].visualization is not None
    assert widgets[1].visualization.value == "viz.billboard"


def test_every_variable_of_a_chain_gets_the_real_account_when_applied() -> None:
    from importer.core.apply_dashboards import with_account

    d = convert(MINE, "node-exporter-rows.json")
    sent = with_account(nerdgraph_dashboard(d), 4242)
    variables = sent["variables"]["dashboard"]["variables"]
    assert [v["name"] for v in variables] == ["job", "instance"]
    assert [v["nrqlQuery"]["accountIds"] for v in variables] == [[4242], [4242]]
    assert "ACCOUNT_ID_PLACEHOLDER" not in json.dumps(sent)
    assert "{{job}}" in variables[1]["nrqlQuery"]["query"]
