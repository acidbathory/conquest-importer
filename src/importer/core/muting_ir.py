"""Vendor-neutral muting rule IR: stop notifications for matching alert events in a time window.

A New Relic muting rule is a group of conditions on alert-event attributes plus an optional
schedule (one window, repeating daily, weekly or monthly). The alert event is still recorded; a
workflow whose muting handling is not NOTIFY_ALL_ISSUES withholds the notification.

Operator spellings are those of the Terraform provider documentation (`newrelic_alert_muting_rule`).
The offline provider schema does not list them and New Relic's own page spells some negatives
differently, so which set NerdGraph accepts is UNVERIFIED (one live call settles it). See
docs/research/newrelic-notifications-and-nrql-reference.md, section 1.3.
"""

import re
from datetime import datetime
from enum import StrEnum
from functools import lru_cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator, model_validator

MAX_CONDITIONS = 20
MAX_VALUES = 500
MAX_NAME = 255
LOCAL_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
TAG_ATTRIBUTE = re.compile(r"tags\.[A-Za-z0-9_.:-]{1,64}")
ATTRIBUTES = frozenset(
    [
        "accountId",
        "conditionId",
        "conditionName",
        "conditionRunbookUrl",
        "conditionType",
        "entity.guid",
        "nrqlEventType",
        "nrqlQuery",
        "policyId",
        "policyName",
        "product",
        "targetId",
        "targetName",
        "compoundAlertId",
        "compoundAlertName",
    ]
)


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


class MutingOperator(StrEnum):
    ANY = "ANY"
    CONTAINS = "CONTAINS"
    ENDS_WITH = "ENDS_WITH"
    EQUALS = "EQUALS"
    IN = "IN"
    IS_BLANK = "IS_BLANK"
    IS_NOT_BLANK = "IS_NOT_BLANK"
    NOT_CONTAINS = "NOT_CONTAINS"
    NOT_ENDS_WITH = "NOT_ENDS_WITH"
    NOT_EQUALS = "NOT_EQUALS"
    NOT_IN = "NOT_IN"
    NOT_STARTS_WITH = "NOT_STARTS_WITH"
    STARTS_WITH = "STARTS_WITH"


NO_VALUES = frozenset({MutingOperator.ANY, MutingOperator.IS_BLANK, MutingOperator.IS_NOT_BLANK})


class Repeat(StrEnum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class Weekday(StrEnum):
    MONDAY = "MONDAY"
    TUESDAY = "TUESDAY"
    WEDNESDAY = "WEDNESDAY"
    THURSDAY = "THURSDAY"
    FRIDAY = "FRIDAY"
    SATURDAY = "SATURDAY"
    SUNDAY = "SUNDAY"


class MutingCondition(BaseModel):
    attribute: str
    operator: MutingOperator
    values: list[str] = []

    @field_validator("attribute")
    @classmethod
    def _known_attribute(cls, value: str) -> str:
        if value not in ATTRIBUTES and not TAG_ATTRIBUTE.fullmatch(value):
            raise ValueError("unknown muting rule attribute")
        return value

    @model_validator(mode="after")
    def _values_fit_operator(self) -> "MutingCondition":
        if self.operator in NO_VALUES:
            if self.values:
                raise ValueError(f"operator {self.operator.value} takes no values")
        elif not 1 <= len(self.values) <= MAX_VALUES:
            raise ValueError(f"a condition needs 1 to {MAX_VALUES} values")
        return self


class MutingGroup(BaseModel):
    operator: Literal["AND", "OR"]
    conditions: list[MutingCondition]

    @field_validator("conditions")
    @classmethod
    def _count(cls, value: list[MutingCondition]) -> list[MutingCondition]:
        if not 1 <= len(value) <= MAX_CONDITIONS:
            raise ValueError(f"a rule needs 1 to {MAX_CONDITIONS} conditions")
        return value


class MutingSchedule(BaseModel):
    """One window in the rule's time zone, optionally repeating. Times carry no offset."""

    start_time: str
    end_time: str
    time_zone: str
    repeat: Repeat | None = None
    end_repeat: str | None = None  # when the repetition stops; or use repeat_count
    repeat_count: int | None = None  # counts the original window: 2 means one repeat
    weekly_repeat_days: list[Weekday] = []

    @field_validator("start_time", "end_time", "end_repeat")
    @classmethod
    def _local_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not LOCAL_TIME.fullmatch(value):
            raise ValueError("times are local ISO 8601 with no offset, like 2026-12-01T22:00:00")
        datetime.fromisoformat(value)  # rejects month 13 and the like
        return value

    @field_validator("time_zone")
    @classmethod
    def _zone(cls, value: str) -> str:
        if value not in _zones():
            raise ValueError("unknown time zone (use an IANA name such as Europe/Berlin)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "MutingSchedule":
        if self.end_time <= self.start_time:
            raise ValueError("the window must end after it starts")
        if self.weekly_repeat_days and self.repeat != Repeat.WEEKLY:
            raise ValueError("weekday names need repeat WEEKLY")
        if self.repeat is None and (self.end_repeat or self.repeat_count is not None):
            raise ValueError("an end for the repetition needs a repeat")
        if self.end_repeat and self.repeat_count is not None:
            raise ValueError("give an end date or a repeat count, not both")
        if self.repeat_count is not None and self.repeat_count < 1:
            raise ValueError("the repeat count is at least 1")
        return self


class MutingRule(BaseModel):
    name: str
    description: str = ""
    enabled: bool = False  # imported disabled, like conditions and workflows
    condition: MutingGroup
    schedule: MutingSchedule | None = None  # none: active whenever it is enabled
    # CLOSE_ISSUES_ON_INACTIVE (New Relic's default) or DO_NOTHING; None leaves it to New Relic
    action_on_window_ended: Literal["CLOSE_ISSUES_ON_INACTIVE", "DO_NOTHING"] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not 1 <= len(value) <= MAX_NAME:
            raise ValueError(f"the name is 1 to {MAX_NAME} characters")
        return value
