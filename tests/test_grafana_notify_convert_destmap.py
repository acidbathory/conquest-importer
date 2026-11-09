"""The destinations map: strict validation, hostile files, no value echoed in an error."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.grafana.notifications.models import ContactPoint
from importer.sources.grafana.notify_convert import destmap as dm

SECRETS = [
    "https://hooks.slack.com/services/T000/B000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "glsa_AbCdEf123456_TOKENTOKEN",
    "sk-abcdef123456",
    "AKIAABCDEFGHIJKLMNOP",
    "hunter2",  # not upper case: not a logical name
    "a@example.com",
    "NRAK-ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEF",  # no underscore, a long run
    "WITH SPACE_X",
    "",
]


def text(entries: dict[str, Any], **top: Any) -> str:
    return json.dumps({"version": 1, "contact_points": entries, **top})


def test_a_good_map_is_read_and_matches_by_uid_then_name() -> None:
    loaded = dm.parse_map(
        text(
            {
                "uid-1": {"email": ["a@example.com", "b@example.com", "a@example.com"]},
                "Pager": {"pagerduty_key": "TEAM_PD_KEY"},
                "Hook": {"webhook_url": "TEAM_HOOK_URL"},
                "Old": {"skip": True},
            }
        )
    )
    cp = ContactPoint(uid="uid-1", name="Pager", type="email")
    entry = loaded.entry_for(cp)
    assert entry is not None and entry.email == ["a@example.com", "b@example.com"]  # uid wins
    by_name = loaded.entry_for(ContactPoint(uid="zzz", name="Pager", type="pagerduty"))
    assert by_name is not None and by_name.pagerduty_key == "TEAM_PD_KEY"
    assert loaded.entry_for(ContactPoint(uid="q", name="none", type="email")) is None
    assert loaded.unused([cp]) == 2  # Hook and Old match no contact point; uid-1 and Pager do


@pytest.mark.parametrize("value", SECRETS)
@pytest.mark.parametrize("key", ["webhook_url", "pagerduty_key"])
def test_a_secret_or_url_in_place_of_a_reference_is_refused_without_echo(
    key: str, value: str
) -> None:
    with pytest.raises(dm.MapError) as err:
        dm.parse_map(text({"cp": {key: value}}))
    shown = str(err.value)
    assert f"cp.{key}" in shown
    for part in (value, value[:12]):
        if part:
            assert part not in shown


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"email": []},
        {"email": ["not an address"]},
        {"email": "a@example.com"},
        {"email": ["a@example.com"], "skip": True},
        {"skip": False},
        {"skip": "yes"},
        {"colour": "red"},
        {"webhook_url": "AAA_BBB", "pagerduty_key": "CCC_DDD"},
        {"email": [f"u{i}@example.com" for i in range(151)]},
        {"webhook_url": 7},
        {"email": [None]},
    ],
)
def test_bad_entries_are_refused(entry: dict[str, Any]) -> None:
    with pytest.raises(dm.MapError, match="destinations map"):
        dm.parse_map(text({"cp": entry}))


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "{}",
        '{"version": 2, "contact_points": {}}',
        '{"version": 1}',
        '{"version": 1, "contact_points": [], "x": 1}',
        '{"version": 1, "contact_points": {}, "extra": 1}',
        '{"version": "1", "contact_points": {}}',
        '{"version": true, "contact_points": {}}',
        "not json",
        '{"version": 1, "contact_points": {"": {"skip": true}}}',
        '{"version": 1, "contact_points": {"a\\nb": {"skip": true}}}',
        '{"version": 1, "contact_points": {"a": {"skip": true}, "a": {"skip": true}}}',
    ],
)
def test_bad_files_are_refused(raw: str) -> None:
    with pytest.raises(dm.MapError):
        dm.parse_map(raw)


def test_a_key_over_the_limit_is_refused() -> None:
    with pytest.raises(dm.MapError):
        dm.parse_map(text({"k" * 201: {"skip": True}}))


def test_too_many_entries_are_refused() -> None:
    entries = {f"cp{i}": {"skip": True} for i in range(dm.MAX_ENTRIES + 1)}
    with pytest.raises(dm.MapError, match="entries"):
        dm.parse_map(text(entries))


def test_deep_nesting_is_an_error_not_a_crash() -> None:
    raw = '{"version": 1, "contact_points": ' + "[" * 100_000 + "]" * 100_000 + "}"
    with pytest.raises(dm.MapError):
        dm.parse_map(raw)


def test_a_huge_file_is_refused_before_it_is_read(tmp_path: Path) -> None:
    big = tmp_path / "map.json"
    big.write_text(" " * (dm.MAX_MAP_BYTES + 1))
    with pytest.raises(dm.MapError, match="larger"):
        dm.load_map(big)


def test_a_missing_file_and_a_folder_are_errors(tmp_path: Path) -> None:
    for path in (tmp_path / "nope.json", tmp_path):
        with pytest.raises(dm.MapError, match="not a file"):
            dm.load_map(path)


def test_a_duplicate_key_names_the_key_and_not_a_value() -> None:
    raw = (
        '{"version": 1, "contact_points": {"x": {"email": ["SECRET-v@example.com"]}, '
        '"x": {"skip": true}}}'
    )
    with pytest.raises(dm.MapError) as err:
        dm.parse_map(raw)
    assert "twice" in str(err.value) and "SECRET" not in str(err.value)


def test_the_error_for_a_hostile_key_is_printable() -> None:
    raw = text({"bad‮\x1b[31mkey": {"colour": "red"}})
    with pytest.raises(dm.MapError) as err:
        dm.parse_map(raw)
    assert "\x1b" not in str(err.value) and "‮" not in str(err.value)
