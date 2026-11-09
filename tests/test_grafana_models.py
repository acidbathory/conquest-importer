"""Grafana rule normalisers: four read paths give one internal rule model."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.core import ids
from importer.sources.grafana.models import GrafanaRule, load_rules, parse_duration

FIXTURES = Path(__file__).parent / "fixtures" / "grafana"


def rules_of(name: str) -> dict[str, GrafanaRule]:
    return {r.title: r for r in load_rules(FIXTURES / name)}


def test_the_provisioning_list_has_seventeen_rules_with_group_and_folder() -> None:
    rules = rules_of("provisioning_alert_rules.json")
    assert len(rules) == 17
    rule = rules["ckg-prom-cpu-gauge"]
    assert (rule.uid, rule.folder, rule.group) == ("ckg-rule-09", "ckg-sandbox", "ckg-sandbox")
    assert rule.for_seconds == 300 and rule.keep_firing_for_seconds == 0
    assert rule.interval_seconds is None  # the provisioning list has no interval
    assert rule.is_paused and rule.condition == "C"
    assert [n.refId for n in rule.data] == ["A", "B", "C"]
    assert rule.data[0].datasourceUid == "grafanacloud-prom"
    assert rule.data[0].relativeTimeRange == {"from": 600, "to": 0}


def test_export_rules_inherit_folder_group_and_interval_from_the_group() -> None:
    rules = rules_of("export_alert_rules.json")
    assert len(rules) == 17
    rule = rules["ckg-prom-cpu-gauge"]
    assert (rule.folder, rule.group, rule.interval_seconds) == ("ckg-sandbox",) * 2 + (60,)
    assert rule.no_data_state == "NoData" and rule.exec_err_state == "Error"


def test_both_real_paths_give_the_same_rule_apart_from_what_one_path_lacks() -> None:
    a, b = rules_of("provisioning_alert_rules.json"), rules_of("export_alert_rules.json")
    assert a.keys() == b.keys()
    for title in a:
        left, right = a[title], b[title]
        assert (left.uid, left.for_seconds, left.condition, left.no_data_state) == (
            right.uid,
            right.for_seconds,
            right.condition,
            right.no_data_state,
        )
        assert [n.model_dump() for n in left.data] == [n.model_dump() for n in right.data]


def test_the_ruler_shape_is_normalised() -> None:
    rule = rules_of("ruler_rules.json")["hm-ruler-loki-errors"]
    assert (rule.uid, rule.folder, rule.group) == (
        "hm-ruler-1",
        "hand-made-folder",
        "hand-made-group",
    )
    assert rule.for_seconds == 600 and rule.keep_firing_for_seconds == 300
    assert rule.interval_seconds == 120
    assert rule.no_data_state == "Alerting" and rule.exec_err_state == "Error"
    assert rule.labels == {"team": "payments"} and not rule.is_paused


def test_a_ruler_recording_rule_is_marked_as_one() -> None:
    assert rules_of("ruler_rules.json")["hm-ruler-recording"].record is not None


def test_the_apis_shape_is_normalised_and_the_source_node_is_the_condition() -> None:
    rule = rules_of("apis_alert_rules.json")["hm-apis-queue-depth"]
    assert (rule.uid, rule.folder, rule.interval_seconds) == ("hm-apis-1", "hand-made-folder", 60)
    assert rule.condition == "C" and rule.is_paused
    assert rule.for_seconds == 180
    assert rule.no_data_state == "KeepLast" and rule.exec_err_state == "Alerting"
    assert sorted(n.refId for n in rule.data) == ["A", "B", "C"]
    assert rule.data[0].datasourceUid == "prom-main"


def test_a_folder_dedupes_the_same_uid_read_two_ways() -> None:
    rules = load_rules(FIXTURES)
    assert len(rules) == 17 + 3 + 1
    assert len({r.uid for r in rules}) == len(rules)


@pytest.mark.parametrize(
    "payload",
    [
        [{"title": "t", "uid": "u", "condition": "A", "data": [], "ruleGroup": "g"}],
        {"title": "t", "uid": "u", "condition": "A", "data": [], "ruleGroup": "g"},
        {"groups": [{"name": "g", "folder": "f", "interval": "1m", "rules": [{"title": "t"}]}]},
        {"name": "g", "folder": "f", "interval": "1m", "rules": [{"title": "t", "data": []}]},
    ],
)
def test_a_file_list_object_envelope_or_group_loads(tmp_path: Path, payload: Any) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps(payload))
    assert [r.title for r in load_rules(path)] == ["t"]


def test_both_spellings_of_the_state_fields_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps(
            [
                {
                    "title": "t",
                    "data": [],
                    "ruleGroup": "g",
                    "keepFiringFor": "2m",
                    "no_data_state": "keeplast",
                    "execErrState": "ok",
                    "is_paused": True,
                }
            ]
        )
    )
    (rule,) = load_rules(path)
    assert rule.keep_firing_for_seconds == 120
    assert (rule.no_data_state, rule.exec_err_state, rule.is_paused) == ("KeepLast", "OK", True)


def test_unknown_fields_of_a_node_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    node = {"refId": "A", "datasourceUid": "u", "model": {}, "somethingNew": 1}
    path.write_text(json.dumps([{"title": "t", "data": [node], "ruleGroup": "g"}]))
    (rule,) = load_rules(path)
    assert rule.data[0].model_extra == {"somethingNew": 1}


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("0s", 0),
        ("5m", 300),
        ("1h30m", 5400),
        ("90s", 90),
        ("1d", 86400),
        ("", 0),
        (None, 0),
        (120, 120),
        ("1m30s", 90),
        ("1w", 604800),
    ],
)
def test_durations(text: Any, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["abc", "5x", "-5m", "5", "1.5m", True, [], "1m 30s", "m5"])
def test_a_bad_duration_is_refused(text: Any) -> None:
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


@pytest.mark.parametrize(
    "payload",
    ["text", 3, {"unrelated": 1}, [1], [{"nothing": "here"}]],
)
def test_an_unknown_shape_is_refused(tmp_path: Path, payload: Any) -> None:
    path = tmp_path / "x.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="alert rule"):
        load_rules(path)


def test_a_file_over_the_size_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importer.sources.grafana import models

    path = tmp_path / "x.json"
    path.write_text("[]" + " " * 200)
    monkeypatch.setattr(models, "MAX_INPUT_BYTES", 100)
    with pytest.raises(ValueError, match="larger than"):
        load_rules(path)
    assert ids.MAX_INPUT_BYTES > 100


def test_the_extractor_folder_layout_is_read_and_inventory_is_not(tmp_path: Path) -> None:
    (tmp_path / "alert-rules").mkdir()
    (tmp_path / "inventory").mkdir()
    (tmp_path / "alert-rules" / "alert-rules.json").write_text(
        json.dumps([{"title": "t", "uid": "u", "data": [], "ruleGroup": "g", "folderUID": "f"}])
    )
    (tmp_path / "inventory" / "inventory.json").write_text(json.dumps({"contact_points": 3}))
    assert [r.title for r in load_rules(tmp_path)] == ["t"]


def test_the_converted_output_inside_the_input_folder_is_not_read_again(tmp_path: Path) -> None:
    (tmp_path / "alerts").mkdir()
    (tmp_path / "alerts" / "x.json").write_text("{}")
    (tmp_path / "r.json").write_text(json.dumps([{"title": "t", "data": [], "ruleGroup": "g"}]))
    assert len(load_rules(tmp_path)) == 1


def test_a_data_source_managed_ruler_rule_is_marked(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text(
        json.dumps(
            {"ns": [{"name": "g", "interval": "1m", "rules": [{"alert": "A", "expr": "up == 0"}]}]}
        )
    )
    (rule,) = load_rules(path)
    assert rule.datasource_managed and rule.title == "A"
