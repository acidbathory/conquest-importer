"""Muting rules: the IR, its checks, and the Terraform and NerdGraph output."""

import json
from typing import Any

import pytest

from hcl_schema import resource_count, schema_errors
from importer.core.emit.muting import MUTATION, muting_payload, muting_terraform
from importer.core.muting_ir import (
    MAX_CONDITIONS,
    MAX_VALUES,
    MutingCondition,
    MutingGroup,
    MutingRule,
    MutingSchedule,
)


def cond(**kw: Any) -> MutingCondition:
    base: dict[str, Any] = {"attribute": "conditionName", "operator": "EQUALS", "values": ["x"]}
    return MutingCondition.model_validate({**base, **kw})


def schedule(**kw: Any) -> MutingSchedule:
    base: dict[str, Any] = {
        "start_time": "2026-12-01T22:00:00",
        "end_time": "2026-12-02T02:00:00",
        "time_zone": "Europe/Berlin",
    }
    return MutingSchedule.model_validate({**base, **kw})


def rule(**kw: Any) -> MutingRule:
    base: dict[str, Any] = {
        "name": "Nightly maintenance",
        "condition": MutingGroup(operator="AND", conditions=[cond()]),
        "schedule": schedule(),
    }
    return MutingRule.model_validate({**base, **kw})


@pytest.mark.parametrize(
    "attribute",
    ["accountId", "conditionName", "policyName", "entity.guid", "tags.team", "tags.tags.team"],
)
def test_known_attributes_are_accepted(attribute: str) -> None:
    assert cond(attribute=attribute).attribute == attribute


@pytest.mark.parametrize("attribute", ["", "tags.", "tags.a b", "colour", "tags.a'b", "Tags.x"])
def test_unknown_attributes_are_refused(attribute: str) -> None:
    with pytest.raises(ValueError, match="attribute"):
        cond(attribute=attribute)


def test_operator_spellings_are_the_provider_ones() -> None:
    for op in ("EQUALS", "NOT_EQUALS", "IN", "NOT_IN", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH"):
        assert cond(operator=op).operator == op
    for op in ("DOES_NOT_EQUALS", "DOES_NOT_CONTAINS", "IS_ANY", "equals"):
        with pytest.raises(ValueError, match="operator"):
            cond(operator=op)


def test_values_rules() -> None:
    with pytest.raises(ValueError, match="values"):
        cond(values=[])
    assert cond(operator="IS_BLANK", values=[]).values == []
    with pytest.raises(ValueError, match="values"):
        cond(operator="IS_BLANK", values=["x"])
    with pytest.raises(ValueError, match="values"):
        cond(values=["v"] * (MAX_VALUES + 1))


def test_group_limits() -> None:
    with pytest.raises(ValueError, match="condition"):
        MutingGroup(operator="AND", conditions=[])
    with pytest.raises(ValueError, match="condition"):
        MutingGroup(operator="OR", conditions=[cond()] * (MAX_CONDITIONS + 1))
    with pytest.raises(ValueError, match="operator"):
        MutingGroup.model_validate({"operator": "XOR", "conditions": [cond()]})


@pytest.mark.parametrize(
    "change",
    [
        {"repeat": "DAILY"},
        {"repeat": "WEEKLY", "weekly_repeat_days": ["MONDAY", "FRIDAY"], "repeat_count": 4},
        {"repeat": "MONTHLY", "end_repeat": "2027-06-01T00:00:00"},
        {},
    ],
)
def test_valid_schedules(change: dict[str, Any]) -> None:
    schedule(**change)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"end_time": "2026-12-01T21:00:00"}, "end"),
        ({"time_zone": "Mars/Base"}, "time zone"),
        ({"start_time": "2026-12-01T22:00:00Z"}, "local"),
        ({"start_time": "2026-12-01 22:00"}, "local"),
        ({"weekly_repeat_days": ["MONDAY"]}, "WEEKLY"),
        ({"repeat": "DAILY", "weekly_repeat_days": ["MONDAY"]}, "WEEKLY"),
        ({"repeat": "WEEKLY", "weekly_repeat_days": ["FUNDAY"]}, "weekly_repeat_days"),
        ({"repeat": "DAILY", "repeat_count": 2, "end_repeat": "2027-01-01T00:00:00"}, "not both"),
        ({"repeat": "DAILY", "repeat_count": 0}, "repeat count"),
        ({"repeat_count": 3}, "repeat"),
        ({"repeat": "HOURLY"}, "repeat"),
    ],
)
def test_invalid_schedules_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        schedule(**change)


def test_terraform_matches_the_provider_schema() -> None:
    full = rule(
        description="d",
        action_on_window_ended="DO_NOTHING",
        schedule=schedule(repeat="WEEKLY", weekly_repeat_days=["MONDAY"], repeat_count=3),
        condition=MutingGroup(
            operator="OR",
            conditions=[cond(), cond(attribute="tags.team", operator="IN", values=["a", "b"])],
        ),
    )
    tf = muting_terraform([full, rule(schedule=None)])
    assert schema_errors(tf) == [] and resource_count(tf) == 2
    assert 'weekly_repeat_days = ["MONDAY"]' in tf and "repeat_count" in tf
    assert 'time_zone  = "Europe/Berlin"' in tf or 'time_zone = "Europe/Berlin"' in tf
    assert "enabled     = false" in tf


def test_terraform_escapes_hostile_text() -> None:
    hostile = rule(
        name='x"\n${evil}',
        description="%{bad}",
        condition=MutingGroup(
            operator="AND", conditions=[cond(values=['a"', "${v}"], operator="IN")]
        ),
    )
    tf = muting_terraform([hostile])
    assert "${evil}" not in tf.replace("$${evil}", "")
    assert "${v}" not in tf.replace("$${v}", "") and "%{bad}" not in tf.replace("%%{bad}", "")
    assert schema_errors(tf) == []


def test_nerdgraph_payload() -> None:
    payload = muting_payload(
        rule(schedule=schedule(repeat="WEEKLY", weekly_repeat_days=["MONDAY"], repeat_count=2))
    )
    assert payload["query"] == MUTATION and "alertsMutingRuleCreate" in MUTATION
    assert "$rule" in MUTATION and "Nightly" not in MUTATION
    variables = payload["variables"]
    assert variables["accountId"] == "ACCOUNT_ID_PLACEHOLDER"
    body = variables["rule"]
    assert body["enabled"] is False and body["name"] == "Nightly maintenance"
    assert body["condition"] == {
        "operator": "AND",
        "conditions": [{"attribute": "conditionName", "operator": "EQUALS", "values": ["x"]}],
    }
    assert body["schedule"] == {
        "startTime": "2026-12-01T22:00:00",
        "endTime": "2026-12-02T02:00:00",
        "timeZone": "Europe/Berlin",
        "repeat": "WEEKLY",
        "repeatCount": 2,
        "weeklyRepeatDays": ["MONDAY"],
    }
    json.dumps(payload)  # serialisable


def test_a_rule_without_a_schedule_has_no_schedule_key() -> None:
    body = muting_payload(rule(schedule=None))["variables"]["rule"]
    assert "schedule" not in body and "actionOnMutingRuleWindowEnded" not in body
