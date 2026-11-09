"""Muting rules: operators the provider cannot take, boundaries, alignment and exact lines."""

from typing import Any

import pytest

from hcl_schema import fmt_errors, schema_errors
from importer.core.emit.common import CannotEmit
from importer.core.emit.muting import muting_payload, muting_terraform
from importer.core.muting_ir import (
    MAX_CONDITIONS,
    MAX_VALUES,
    NO_VALUES,
    MutingCondition,
    MutingGroup,
    MutingOperator,
)
from importer.core.remediation import remedies_for
from test_nr_muting import cond, rule, schedule

WITH_VALUES = [op for op in MutingOperator if op not in NO_VALUES]


@pytest.mark.parametrize("op", WITH_VALUES)
def test_every_operator_that_takes_values_passes_the_schema_check(op: MutingOperator) -> None:
    r = rule(condition=MutingGroup(operator="AND", conditions=[cond(operator=op.value)]))
    tf = muting_terraform([r])
    assert f'operator  = "{op.value}"' in tf and schema_errors(tf) == []


@pytest.mark.parametrize("op", ["NOT_STARTS_WITH", "NOT_ENDS_WITH", "NOT_CONTAINS", "NOT_IN"])
def test_negated_spellings_are_written_exactly(op: str) -> None:
    tf = muting_terraform(
        [rule(condition=MutingGroup(operator="OR", conditions=[cond(operator=op)]))]
    )
    assert f'"{op}"' in tf and "DOES_NOT" not in tf


@pytest.mark.parametrize("op", sorted(o.value for o in NO_VALUES))
def test_operators_without_values_cannot_go_to_terraform_yet(op: str) -> None:
    r = rule(condition=MutingGroup(operator="AND", conditions=[cond(operator=op, values=[])]))
    with pytest.raises(CannotEmit, match="takes no values") as info:
        muting_terraform([r])
    assert remedies_for([f"not emitted: {info.value}"]) or remedies_for([str(info.value)])
    body = muting_payload(r)["variables"]["rule"]["condition"]["conditions"][0]
    assert body == {
        "attribute": "conditionName",
        "operator": op,
        "values": [],
    }  # NerdGraph keeps it


def test_the_schema_checker_flags_an_empty_required_list() -> None:
    bad = muting_terraform([rule()]).replace('values    = ["x"]', "values    = []")
    assert any("empty list" in e for e in schema_errors(bad))


def test_the_schema_checker_flags_missing_required_parts() -> None:
    tf = muting_terraform([rule()])
    assert any("time_zone" in e for e in schema_errors(tf.replace("time_zone", "tz")))
    assert any("conditions" in e for e in schema_errors(tf.replace("conditions {", "cond {")))


@pytest.mark.parametrize(("count", "ok"), [(MAX_CONDITIONS, True), (MAX_CONDITIONS + 1, False)])
def test_condition_count_boundary(count: int, ok: bool) -> None:
    def build() -> MutingGroup:
        return MutingGroup(operator="AND", conditions=[cond()] * count)

    if ok:
        assert len(build().conditions) == count
    else:
        with pytest.raises(ValueError, match="conditions"):
            build()


@pytest.mark.parametrize(("count", "ok"), [(MAX_VALUES, True), (MAX_VALUES + 1, False)])
def test_value_count_boundary(count: int, ok: bool) -> None:
    if ok:
        assert len(cond(values=["v"] * count).values) == count
    else:
        with pytest.raises(ValueError, match="values"):
            cond(values=["v"] * count)


@pytest.mark.parametrize(
    ("end", "ok"),
    [
        ("2026-12-02T02:00:00", True),
        ("2026-12-01T22:00:01", True),
        ("2026-12-01T22:00:00", False),
        ("2026-12-01T21:59:59", False),
    ],
)
def test_the_window_must_end_after_it_starts(end: str, ok: bool) -> None:
    if ok:
        assert schedule(end_time=end).end_time == end
    else:
        with pytest.raises(ValueError, match="end"):
            schedule(end_time=end)


def test_repeat_count_is_written_as_given() -> None:
    r = rule(schedule=schedule(repeat="DAILY", repeat_count=3))
    assert "repeat_count = 3\n" in muting_terraform(
        [r]
    ) or "repeat_count        = 3\n" in muting_terraform([r])
    assert muting_payload(r)["variables"]["rule"]["schedule"]["repeatCount"] == 3
    assert (
        muting_payload(rule(schedule=schedule(repeat="DAILY", repeat_count=1)))["variables"][
            "rule"
        ]["schedule"]["repeatCount"]
        == 1
    )


def test_muting_terraform_is_aligned_like_terraform_fmt() -> None:
    full: dict[str, Any] = {
        "description": "d",
        "action_on_window_ended": "DO_NOTHING",
        "schedule": schedule(repeat="WEEKLY", weekly_repeat_days=["MONDAY"], repeat_count=3),
    }
    assert fmt_errors(muting_terraform([rule(**full), rule(schedule=None)])) == []


def test_the_condition_models_still_reject_a_bad_group_operator() -> None:
    with pytest.raises(ValueError, match="operator"):
        MutingGroup.model_validate(
            {
                "operator": "NAND",
                "conditions": [
                    MutingCondition(
                        attribute="policyId", operator=MutingOperator.EQUALS, values=["1"]
                    )
                ],
            }
        )
