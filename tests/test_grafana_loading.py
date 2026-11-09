"""Grafana loading: paused flags, duplicates, per-rule validation, nesting limits."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.models import load_rules


def write(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "x.json"
    path.write_text(json.dumps(payload))
    return path


def rule_json(**extra: Any) -> dict[str, Any]:
    return {"title": "t", "uid": "u", "condition": "A", "data": [], "ruleGroup": "g", **extra}


@pytest.mark.parametrize(
    ("value", "paused", "unknown"),
    [
        (True, True, False),
        (False, False, False),
        ("true", True, False),
        ("TRUE", True, False),
        ("false", False, False),
        ("False", False, False),
        (None, False, False),
        ("0", True, True),
        ("no", True, True),
        ("yes", True, True),
        (0, True, True),
        (1, True, True),
        ([], True, True),
    ],
)
def test_the_paused_flag_is_read_as_a_boolean_and_anything_else_is_paused_and_flagged(
    tmp_path: Path, value: Any, paused: bool, unknown: bool
) -> None:
    for key in ("isPaused", "is_paused", "paused"):
        (rule,) = load_rules(write(tmp_path, [rule_json(**{key: value})]))
        assert (rule.is_paused, getattr(rule, "paused_unknown", None)) == (paused, unknown), key


def test_a_missing_paused_flag_is_not_paused(tmp_path: Path) -> None:
    (rule,) = load_rules(write(tmp_path, [rule_json()]))
    assert (rule.is_paused, getattr(rule, "paused_unknown", None)) == (False, False)


def test_identical_copies_are_merged_and_counted(tmp_path: Path) -> None:
    rules = load_rules(write(tmp_path, [rule_json(), rule_json(), rule_json()]))
    assert len(rules) == 1
    assert getattr(rules[0], "copies_merged", None) == 2
    assert not getattr(rules[0], "duplicate_uid", True)


def test_two_rules_with_one_uid_and_different_content_both_survive_and_are_flagged(
    tmp_path: Path,
) -> None:
    other = rule_json(**{"for": "5m"})
    rules = load_rules(write(tmp_path, [rule_json(), other, rule_json(title="renamed")]))
    assert len(rules) == 3
    assert all(getattr(r, "duplicate_uid", False) for r in rules)
    assert all(getattr(r, "copies_merged", None) == 0 for r in rules)


@pytest.mark.parametrize(
    "changed",
    [
        {"title": "other"},
        {"folderUID": "f2"},
        {"ruleGroup": "g2"},
        {"for": "1m"},
        {"data": [{"refId": "A", "datasourceUid": "x", "model": {"expr": "up"}}]},
    ],
)
def test_each_part_of_the_dedupe_key_keeps_a_different_rule(
    tmp_path: Path, changed: dict[str, Any]
) -> None:
    assert len(load_rules(write(tmp_path, [rule_json(), rule_json(**changed)]))) == 2


MALFORMED = [
    {"data": [{"refId": "A", "model": "not a mapping"}]},
    {"data": [{"refId": 5}]},
    {"data": [{"refId": "A", "datasourceUid": None}]},
    {"for": -60},
    {"for": 1.5},
    {"for": "-1m"},
    {"for": "500ms"},
    {"keep_firing_for": "soon"},
]


@pytest.mark.parametrize("bad", MALFORMED)
def test_one_malformed_rule_does_not_stop_the_others(tmp_path: Path, bad: dict[str, Any]) -> None:
    good = rule_json(uid="good", title="good")
    rules = load_rules(write(tmp_path, [good, rule_json(uid="bad", title="bad", **bad), good]))
    by_uid = {r.uid: r for r in rules}
    assert by_uid["good"].title == "good" and not getattr(by_uid["good"], "unreadable", False)
    assert getattr(by_uid["bad"], "unreadable", False) and by_uid["bad"].title == "bad"


def test_a_malformed_group_interval_makes_only_its_rules_unreadable(tmp_path: Path) -> None:
    payload = {
        "groups": [
            {"name": "bad", "folder": "f", "interval": -1, "rules": [{"title": "a", "data": []}]},
            {"name": "ok", "folder": "f", "interval": "1m", "rules": [{"title": "b", "data": []}]},
        ]
    }
    rules = {r.title: r for r in load_rules(write(tmp_path, payload))}
    assert getattr(rules["a"], "unreadable", False) and not getattr(rules["b"], "unreadable", False)


def test_the_title_of_an_unreadable_rule_is_kept_only_when_it_is_text(tmp_path: Path) -> None:
    rules = load_rules(write(tmp_path, [rule_json(title=5, **{"for": -1}), rule_json(uid="v")]))
    assert rules[0].title == "" and getattr(rules[0], "unreadable", False)


def test_a_deeply_nested_file_is_a_clean_error_not_a_recursion_error(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ValueError, match="nested"):
        load_rules(path)


def test_deep_nesting_inside_a_rule_is_a_clean_error_or_loads(tmp_path: Path) -> None:
    deep: Any = {}
    for _ in range(900):
        deep = {"a": deep}
    node = {"refId": "A", "datasourceUid": "x", "model": deep}
    path = write(tmp_path, [rule_json(data=[node]), rule_json(data=[node], uid="v")])
    try:
        rules = load_rules(path)
    except ValueError:
        with pytest.raises(ValueError, match="nested"):
            load_rules(path)
    else:
        assert len(rules) == 2


def test_the_cli_turns_deep_nesting_into_an_error_message(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.mkdir()
    (path / "deep.json").write_text("[" * 100_000 + "]" * 100_000)
    result = CliRunner().invoke(
        app, ["convert", "--source", "grafana", "--in", str(path), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1 and "nested" in result.output
    assert not isinstance(result.exception, RecursionError)


def test_the_cli_survives_a_malformed_rule_among_good_ones(tmp_path: Path) -> None:
    path = tmp_path / "in"
    path.mkdir()
    bad = rule_json(uid="bad", data=[{"refId": 5}])
    (path / "r.json").write_text(json.dumps([bad, rule_json(uid="ok", title="ok")]))
    out = tmp_path / "o"
    result = CliRunner().invoke(
        app, ["convert", "--source", "grafana", "--in", str(path), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert len(list((out / "ir").glob("*.json"))) == 2


def test_a_rules_list_without_a_name_is_a_plain_rule_list_not_a_namespace(tmp_path: Path) -> None:
    (rule,) = load_rules(write(tmp_path, {"rules": [{"title": "t", "data": []}]}))
    assert rule.title == "t" and rule.folder == ""


def test_a_group_without_a_name_inside_a_namespace_keeps_the_namespace(tmp_path: Path) -> None:
    payload = {"ns1": [{"rules": [{"grafana_alert": {"title": "t", "data": []}}]}]}
    (rule,) = load_rules(write(tmp_path, payload))
    assert rule.title == "t" and rule.folder == "ns1"
