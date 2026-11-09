"""Vendor-neutral notification IR and the plan that turns it into New Relic objects.

A source topic (for example an SNS topic) has channels (email addresses, webhook URLs) that can
be migrated and subscriptions that cannot. The plan is: one destination and one channel per
migrated channel, and ONE workflow per topic that routes the issues of the conditions which used
that topic to those channels. Workflows are always created disabled.
"""

import re
from enum import StrEnum

from pydantic import BaseModel

from importer.core.text import printable

MAX_NAME = 120
# no nested quantifiers, and each part is length-bounded, so a long hostile string cannot backtrack
EMAIL_ADDRESS = re.compile(r"^[^@\s,;<>\"'\\]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


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


class ManualDestination(BaseModel):
    """A destination that needs a person: it takes a secret, an OAuth sign-in or has no match."""

    service: str  # a fixed label for the source service, for example "slack"
    nr_type: str  # the closest New Relic destination type, or "" when there is none
    label: str = ""  # untrusted text from the source (a channel name); escape before printing
    steps: list[str]


class PlannedPredicate(BaseModel):
    attribute: str
    operator: str
    values: list[str]


class PlannedWorkflow(BaseModel):
    name: str
    condition_names: list[str]  # issues of these conditions are routed
    destination_indexes: list[int]  # which planned destinations/channels it notifies
    predicates: list[PlannedPredicate] = []  # when set, these replace the condition-name filter

    def issue_predicates(self) -> list[PlannedPredicate]:
        """What the workflow's issue filter tests: its own predicates or the condition names."""
        if self.predicates:
            return self.predicates
        return [
            PlannedPredicate(
                attribute="accumulations.conditionName",
                operator="EXACTLY_MATCHES",
                values=self.condition_names,
            )
        ]


class NotificationPlan(BaseModel):
    topic: str
    destinations: list[PlannedDestination]
    workflow: PlannedWorkflow | None
    # the next three are set only by sources whose plan needs a person to finish it; they are
    # left out of the written file when empty, so the AWS plans are unchanged
    needs_review: bool = False
    notes: list[str] = []
    manual_destinations: list[ManualDestination] = []


def plan_json(plan: NotificationPlan) -> str:
    """The plan as written to disk. Fields at their default are left out."""
    return plan.model_dump_json(indent=2, exclude_defaults=True) + "\n"


def _name(text: str) -> str:
    return printable(text, MAX_NAME)


def plan_destinations(
    name: str, channels: list[IRChannel], prefix: str
) -> list[PlannedDestination]:
    """One planned destination (and channel) per migrated channel, named from the topic."""
    return [
        PlannedDestination(
            kind=ch.kind,
            name=_name(f"{prefix} {name} {ch.kind.value} {i + 1}"),
            target=ch.target,
        )
        for i, ch in enumerate(channels)
    ]


def build_plan(topic: IRTopic, condition_names: list[str], prefix: str) -> NotificationPlan:
    """Plan the New Relic objects for one topic. No workflow if nothing can be routed."""
    destinations = plan_destinations(topic.name, topic.channels, prefix)
    if not destinations or not condition_names:
        return NotificationPlan(topic=topic.name, destinations=[], workflow=None)
    workflow = PlannedWorkflow(
        name=_name(f"{prefix} {topic.name}"),
        condition_names=sorted(set(condition_names)),
        destination_indexes=list(range(len(destinations))),
    )
    return NotificationPlan(topic=topic.name, destinations=destinations, workflow=workflow)
