"""Name-map hints the engine reads (variables, labels), the planner link and the slide step."""

import json
from pathlib import Path

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from importer.sources.grafana.dashboard_convert.name_map import (
    NameMap,
    NameMapError,
    load_name_map,
    merge_hints,
)
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboards.query_tier import classify_query
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.placeholders import fill_placeholders
from importer.sources.grafana.translate import translate_rule


def write_map(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(data))
    return path


def test_a_variable_default_is_read_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    good = load_name_map(write_map(tmp_path, {"variables": {"CLUSTER": "prod-1.eu"}}))
    assert good.variables == {"CLUSTER": "prod-1.eu"}
    for value in ('a"b', "x}", "$y", "a\\b", "new\nline", "'q'", "x" * 201):
        with pytest.raises(NameMapError):
            load_name_map(write_map(tmp_path, {"variables": {"A": value}}))
    with pytest.raises(NameMapError):
        load_name_map(write_map(tmp_path, {"variables": {"bad name": "x"}}))


def test_labels_are_attribute_names_and_merge_keeps_the_owners_entries() -> None:
    base = NameMap(labels={"m": ["a"]})
    hints = NameMap(labels={"m": ["b"], "n": ["c"]})
    assert merge_hints(base, hints).labels == {"m": ["a"], "n": ["c"]}
    with pytest.raises(ValueError, match="identifier"):
        NameMap(labels={"m": ["bad label"]})


def test_placeholders_are_filled_noted_and_never_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = 'up{cluster="${C}", zone="${Z}"}'
    monkeypatch.delenv("IMPORTER_GRAFANA_NAME_MAP", raising=False)
    assert fill_placeholders(query) == (query, [], ["C", "Z"])
    path = write_map(tmp_path, {"variables": {"C": "a"}})
    monkeypatch.setenv("IMPORTER_GRAFANA_NAME_MAP", str(path))
    text, notes, missing = fill_placeholders(query)
    assert (text, notes, missing) == (query, [], ["Z"])  # one is missing: nothing is changed
    path = write_map(tmp_path, {"variables": {"C": "a", "Z": "b"}})
    monkeypatch.setenv("IMPORTER_GRAFANA_NAME_MAP", str(path))
    text, notes, missing = fill_placeholders(query)
    assert text == 'up{cluster="a", zone="b"}' and missing == []
    assert len(notes) == 2 and "'a'" in notes[0]


def test_the_planner_hands_a_comparison_to_the_engine_and_the_converter_agrees() -> None:
    expr = 'kube_node_status_condition{condition="Ready"} == 1'
    assert classify_query(expr, "prometheus").tier == 3
    opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=False)
    assert "kube_node_status_condition >= 1" in convert_query(expr, "prometheus", opts).nrql


def test_the_planner_still_refuses_what_the_engine_does_not_read() -> None:
    for expr in ("a == on(x) b", "up == 1 or up == 2", "x > y", "histogram_quantile(0.9, x)"):
        assert classify_query(expr, "prometheus").tier is None, expr


def test_an_unreadable_comparison_is_refused_with_the_old_reason() -> None:
    opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=True)
    with pytest.raises(Unconvertible) as stop:
        convert_query("sum(rate(x_total[5m])) > 0", "prometheus", opts)
    assert "shape" in stop.value.reason


def rule(expr: str, interval: int | None) -> GrafanaRule:
    return GrafanaRule(
        title="t",
        interval_seconds=interval,
        for_seconds=300,
        datasource_managed=True,
        expr=expr,
    )


@pytest.mark.parametrize(
    ("interval", "slide"), [(60, 60), (150, 150), (120, None), (20, None), (45, None)]
)
def test_the_condition_slides_by_the_interval_only_when_new_relic_accepts_it(
    interval: int, slide: int | None
) -> None:
    monitor = translate_rule(rule("max_over_time(g[5m]) > 5", interval), "k")
    assert monitor.confidence is Confidence.APPROXIMATE
    assert (monitor.emit.slide_by_seconds if monitor.emit else None) == slide
    assert nrql_for(monitor) == "SELECT max(g) FROM Metric"


def test_without_with_too_many_labels_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    labels = {"r_total": ["a", "b", "c", "d", "e", "f", "g"]}
    monkeypatch.setenv("IMPORTER_GRAFANA_NAME_MAP", str(write_map(tmp_path, {"labels": labels})))
    monitor = translate_rule(rule("sum without (g) (rate(r_total[5m])) > 1", 60), "k")
    assert monitor.confidence is Confidence.MANUAL


def test_an_expression_that_converted_before_is_unchanged_by_the_engine() -> None:
    monitor = translate_rule(
        rule("sum(rate(a_total[5m])) / sum(rate(b_total[5m])) > 0.05", 60), "k"
    )
    assert nrql_for(monitor) == "SELECT sum(a_total) / sum(b_total) FROM Metric"


def test_an_azure_rule_template_placeholder_uses_the_default_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importer.sources.azure.promql import convert_expression

    expression = 'avg({"system.cpu.utilization", env="${ENV}"}) > 90'

    def run() -> tuple[Confidence, list[str]]:
        found, notes = convert_expression(
            expression, key="k", name="n", for_text="PT5M", interval_text="PT1M"
        )
        return found.confidence, notes

    monkeypatch.delenv("IMPORTER_GRAFANA_NAME_MAP", raising=False)
    assert run()[0] is Confidence.MANUAL
    monkeypatch.setenv(
        "IMPORTER_GRAFANA_NAME_MAP", str(write_map(tmp_path, {"variables": {"ENV": "prod"}}))
    )
    confidence, notes = run()
    assert confidence is Confidence.APPROXIMATE
    assert any("${ENV}" in n and "'prod'" in n for n in notes)


def test_the_planner_reads_over_time_and_plain_comparisons_but_not_a_filter_on_an_aggregate() -> (
    None
):
    assert classify_query("max_over_time(up[1h])", "prometheus").tier == 3
    assert classify_query("up > 0", "prometheus").tier == 3
    assert classify_query("avg(up) > bool 1", "prometheus").tier == 3
    assert classify_query("sum(up) > 1", "prometheus").tier is None
