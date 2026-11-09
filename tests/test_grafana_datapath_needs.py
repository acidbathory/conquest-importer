"""What the converted rules and tiles need from a data path, and the verdict for each."""

from pathlib import Path

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import IRMonitor
from importer.sources import REGISTRY
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.translate import convert_all, load_items
from importer.sources.grafana.datapath import needs

FIX = Path(__file__).parent / "fixtures"


def rules() -> list[IRMonitor]:
    return REGISTRY["grafana"].convert(FIX / "grafana")


def tiles() -> list[IRDashboard]:
    dashboards, _ = convert_all(load_items(FIX / "grafana-dashboard-convert"), NameMap())
    return dashboards


def test_metric_type_follows_the_suffix_rules() -> None:
    assert needs.metric_type("http_requests_total") == "count"
    assert needs.metric_type("lat_bucket") == "count"
    assert needs.metric_type("lat_count") == "count"
    assert needs.metric_type("lat_sum") == "summary"
    assert needs.metric_type("node_load1") == "gauge"


def test_label_attribute_mapping_flags_the_metricname_collision() -> None:
    assert needs.label_attribute("job") == ("job", "")
    attribute, note = needs.label_attribute("metricName")
    assert "collides" in note and attribute != "metricName"
    assert "le" in needs.label_attribute("le")[0]


def test_nrql_metric_names_and_labels_are_read_from_the_select_and_filters() -> None:
    nrql = (
        "SELECT rate(sum(http_requests_total), 1 second) AS 'sum(fake_in_alias)' FROM Metric "
        "WHERE job IN ({{job}}) AND code != 'x FROM y' FACET instance LIMIT MAX TIMESERIES AUTO"
    )
    metrics, labels, source = needs.parse_nrql(nrql)
    assert (metrics, source) == (("http_requests_total",), "prometheus")
    assert labels == ("job", "code", "instance")
    metrics, labels, source = needs.parse_nrql("SELECT count(*) FROM Log WHERE app = 'x'")
    assert (metrics, labels, source) == ((), ("app",), "loki")


def test_a_tile_with_a_metric_gets_the_metrics_steps() -> None:
    judged = needs.judge(needs.tile_items(tiles()), [])
    by_name = {j.item.name: j for j in judged}
    cpu = next(j for n, j in by_name.items() if "node_cpu_seconds_total" in j.item.metrics)
    assert cpu.verdict is needs.Verdict.DATA_PATH_KNOWN
    assert cpu.steps == (needs.STEP_KEY, needs.STEP_METRICS)
    assert "after step 1 and 2" in cpu.text()


def test_placeholders_are_not_converted_and_log_tiles_need_the_log_step() -> None:
    judged = needs.judge(needs.tile_items(tiles()), [])
    assert any(j.verdict is needs.Verdict.NOT_CONVERTED for j in judged)
    logs = [j for j in judged if j.item.source == "loki"]
    assert logs and all(j.steps == (needs.STEP_KEY, needs.STEP_LOGS) for j in logs)


def test_rules_are_judged_and_manual_rules_are_not_counted_as_known() -> None:
    judged = needs.judge(needs.rule_items(rules()), [])
    kinds = {j.verdict for j in judged}
    assert needs.Verdict.DATA_PATH_KNOWN in kinds
    assert needs.Verdict.NOT_CONVERTED in kinds
    cov = needs.coverage(judged)
    assert cov.known < cov.converted <= cov.total


def test_a_recording_rule_name_and_a_metricname_label_need_a_check_first() -> None:
    items = [
        needs.Item("rule", "rec", True, "prometheus", ("job:lat:mean5m",), ("job",)),
        needs.Item("rule", "coll", True, "prometheus", ("up",), ("metricName",)),
    ]
    judged = needs.judge(items, [])
    assert [j.verdict for j in judged] == [needs.Verdict.CHECK_FIRST] * 2
    assert "colon" in judged[0].text() and "metricName" in judged[1].text()


def test_a_converted_item_without_any_metric_has_no_known_path() -> None:
    judged = needs.judge([needs.Item("tile", "t", True, "prometheus", (), ())], [])
    assert judged[0].verdict is needs.Verdict.NO_PATH


def test_families_merge_uses_and_labels_across_rules_and_tiles() -> None:
    items = needs.rule_items(rules()) + needs.tile_items(tiles())
    fam = {f.name: f for f in needs.families(items)}
    up = fam["up"]
    assert up.nr_type == "gauge" and len(up.uses) > 1
    assert "job" in up.labels
    assert fam["http_requests_total"].nr_type == "count"


def test_coverage_section_counts_converted_and_known() -> None:
    judged = needs.judge(needs.tile_items(tiles()), [])
    cov = needs.coverage(judged)
    text = needs.coverage_section({"tiles": cov})
    assert text.startswith(needs.SECTION_HEADING)
    assert f"{cov.known} of {cov.converted}" in text
    assert needs.replace_section("# r\n\nbody\n", text).count(needs.SECTION_HEADING) == 1
    twice = needs.replace_section(needs.replace_section("# r\n", text), text)
    assert twice.count(needs.SECTION_HEADING) == 1 and twice.startswith("# r")
