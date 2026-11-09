"""Loading Settings 2.0 objects: shapes, per-object validation, caps, and dropped secrets."""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.models import load_objects, parse_object

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-classic"
EVENT = {
    "enabled": True,
    "summary": "s",
    "queryDefinition": {"type": "METRIC_KEY", "metricKey": "k", "aggregation": "AVG"},
    "modelProperties": {
        "type": "STATIC_THRESHOLD",
        "threshold": 1,
        "alertCondition": "ABOVE",
        "violatingSamples": 1,
        "samples": 1,
    },
}


def wrapped(value: dict[str, Any], schema: str = models.METRIC_EVENTS) -> dict[str, Any]:
    return {"objectId": "id1", "schemaId": schema, "scope": "environment", "value": value}


def test_every_docs_fixture_loads() -> None:
    files = sorted(FIXTURES.glob("*.json"))
    assert len(files) >= 21
    for file in files:
        assert load_objects(file), file.name


def test_a_file_a_response_and_a_folder_give_the_same_objects(tmp_path: Path) -> None:
    item = wrapped(EVENT)
    (tmp_path / "one.json").write_text(json.dumps(item))
    (tmp_path / "list.json").write_text(json.dumps([item, item]))
    (tmp_path / "resp.json").write_text(json.dumps({"items": [item], "nextPageKey": None}))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "x.json").write_text(json.dumps({"items": [item]}))
    assert len(load_objects(tmp_path / "one.json")) == 1
    assert len(load_objects(tmp_path / "list.json")) == 2
    assert len(load_objects(tmp_path / "resp.json")) == 1
    assert len(load_objects(tmp_path)) == 5


def test_one_bad_object_does_not_stop_the_rest(tmp_path: Path) -> None:
    bad = wrapped({**EVENT, "modelProperties": {"type": "STATIC_THRESHOLD"}})
    nothing = {"objectId": "n", "value": {}}
    (tmp_path / "a.json").write_text(json.dumps([wrapped(EVENT), bad, "text", nothing]))
    good, broken, text, no_schema = load_objects(tmp_path / "a.json")
    assert not good.problem and broken.problem and text.problem and no_schema.problem
    assert "modelProperties.violatingSamples is missing" in broken.problem


def test_a_problem_names_fields_and_never_repeats_a_value() -> None:
    secret = "SECRET-VALUE-9"  # noqa: S105 - a planted test value, not a credential
    obj = parse_object(wrapped({**EVENT, "enabled": secret}))
    assert obj.problem and secret not in obj.problem
    assert secret not in obj.model_dump_json()


@pytest.mark.parametrize("number", ["1e999", "-1e999", "NaN"])
def test_a_number_that_is_not_finite_is_a_problem(tmp_path: Path, number: str) -> None:
    text = json.dumps(wrapped(EVENT)).replace('"threshold": 1', f'"threshold": {number}')
    text = text.replace('"threshold":1', f'"threshold":{number}')
    (tmp_path / "n.json").write_text(text)
    (obj,) = load_objects(tmp_path / "n.json")
    assert "modelProperties.threshold is not a finite number" in obj.problem


def test_notifications_keep_only_the_safe_subset() -> None:
    raw = wrapped(
        {
            "enabled": True,
            "type": "WEBHOOK",
            "displayName": "Bridge",
            "alertingProfile": "p1",
            "webHookNotification": {"url": "https://h/?token=ZZTOP", "secretUrl": "MASKED"},
            "emailNotification": {"toRecipients": ["jane@example.invalid"]},
        },
        models.NOTIFICATIONS,
    )
    obj = parse_object(raw)
    assert obj.value == {
        "enabled": True,
        "type": "WEBHOOK",
        "displayName": "Bridge",
        "alertingProfile": "p1",
    }
    dumped = obj.model_dump_json()
    assert "ZZTOP" not in dumped and "jane@" not in dumped and "MASKED" not in dumped


def test_every_notification_fixture_loses_its_secrets() -> None:
    texts = [
        o.model_dump_json() for f in FIXTURES.glob("notification-*.json") for o in load_objects(f)
    ]
    assert texts
    for text in texts:
        for planted in ("SHOULD-NEVER-BE-PRINTED", "MASKED", "example.invalid", "idp."):
            assert planted not in text


def test_deep_nesting_inside_one_object_is_a_problem_not_a_crash() -> None:
    deep: Any = "x"
    for _ in range(models.MAX_DEPTH + 5):
        deep = {"a": deep}
    obj = parse_object(wrapped({**EVENT, "extra": deep}))
    assert "nested" in obj.problem


def test_a_file_nested_beyond_the_json_parser_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "deep.json").write_text("[" * 200_000 + "]" * 200_000)
    started = time.monotonic()
    with pytest.raises(ValueError, match="nested too deeply"):
        load_objects(tmp_path / "deep.json")
    assert time.monotonic() - started < 5


def test_a_large_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(models, "MAX_INPUT_BYTES", 10)
    (tmp_path / "big.json").write_text(json.dumps(wrapped(EVENT)))
    with pytest.raises(ValueError, match="larger than"):
        load_objects(tmp_path / "big.json")


def test_too_many_files_or_objects_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "MAX_FILES", 1)
    (tmp_path / "a.json").write_text("[]")
    (tmp_path / "b.json").write_text("[]")
    with pytest.raises(ValueError, match="more than 1 JSON files"):
        load_objects(tmp_path)
    monkeypatch.setattr(models, "MAX_ITEMS", 2)
    (tmp_path / "c.json").write_text(json.dumps([wrapped(EVENT)] * 3))
    with pytest.raises(ValueError, match="more than 2 objects"):
        load_objects(tmp_path / "c.json")


def test_a_file_that_is_not_json_or_not_objects_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "t.json").write_text("not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_objects(tmp_path / "t.json")
    (tmp_path / "n.json").write_text("3")
    with pytest.raises(ValueError, match="expected a JSON object"):
        load_objects(tmp_path / "n.json")
