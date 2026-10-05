"""Vendor-neutral notification IR and the plan that turns it into New Relic objects.

A source topic (for example an SNS topic) has channels (email addresses, webhook URLs) that can
be migrated and subscriptions that cannot. The plan is: one destination and one channel per
migrated channel, and ONE workflow per topic that routes the issues of the conditions which used
that topic to those channels. Workflows are always created disabled.
"""

from enum import StrEnum

from pydantic import BaseModel

from importer.core.text import printable

MAX_NAME = 120


class ChannelKind(StrEnum):
    EMAIL = "email"
    WEBHOOK = "webhook"


class IRChannel(BaseModel):
    kind: ChannelKind
    target: str  # an email address or an https URL


class IRTopic(BaseModel):
    source_system: str
    source_key: str  # for example the SNS topic ARN
    name: str
    channels: list[IRChannel]
    unsupported: list[str] = []  # one reason per subscription that cannot be migrated


class PlannedDestination(BaseModel):
    kind: ChannelKind
    name: str
    target: str


class PlannedWorkflow(BaseModel):
    name: str
    condition_names: list[str]  # issues of these conditions are routed
    destination_indexes: list[int]  # which planned destinations/channels it notifies


class NotificationPlan(BaseModel):
    topic: str
    destinations: list[PlannedDestination]
    workflow: PlannedWorkflow | None


def _name(text: str) -> str:
    return printable(text, MAX_NAME)


def build_plan(topic: IRTopic, condition_names: list[str], prefix: str) -> NotificationPlan:
    """Plan the New Relic objects for one topic. No workflow if nothing can be routed."""
    destinations = [
        PlannedDestination(
            kind=ch.kind,
            name=_name(f"{prefix} {topic.name} {ch.kind.value} {i + 1}"),
            target=ch.target,
        )
        for i, ch in enumerate(topic.channels)
    ]
    if not destinations or not condition_names:
        return NotificationPlan(topic=topic.name, destinations=[], workflow=None)
    workflow = PlannedWorkflow(
        name=_name(f"{prefix} {topic.name}"),
        condition_names=sorted(set(condition_names)),
        destination_indexes=list(range(len(destinations))),
    )
    return NotificationPlan(topic=topic.name, destinations=destinations, workflow=workflow)
