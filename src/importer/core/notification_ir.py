"""Vendor-neutral notification IR and the plan that turns it into New Relic objects.

A source topic (for example an SNS topic) has channels (email addresses, webhook URLs) that can
be migrated and subscriptions that cannot. The plan is: one destination and one channel per
migrated channel, and ONE workflow per topic that routes the issues of the conditions which used
that topic to those channels. Workflows are always created disabled.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from importer.core.text import printable

MAX_NAME = 120
# ASCII only. The local part is dot-separated atoms without $ { } or a backtick (they could start
# a template), at most 64 characters; the domain has at least two labels that do not start or end
# with a hyphen, and a letters-only last label. \Z, not $, so a trailing newline fails. Every
# part is length-bounded and unambiguous, so a long hostile string cannot make it backtrack.
_ATOM = r"[A-Za-z0-9!#&*+/=?^_|~%-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
EMAIL_ADDRESS = re.compile(
    rf"(?=[^@]{{1,64}}@){_ATOM}(?:\.{_ATOM})*@(?:{_LABEL}\.){{1,10}}[A-Za-z]{{2,24}}\Z"
)


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
    # destinations of any New Relic type; they follow `destinations` in the index space that
    # PlannedWorkflow.destination_indexes uses. Left out of the file when empty.
    typed_destinations: list["NrDestination"] = []
    # the next three are set only by sources whose plan needs a person to finish it; they are
    # left out of the written file when empty, so the AWS plans are unchanged
    needs_review: bool = False
    notes: list[str] = []
    manual_destinations: list[ManualDestination] = []

    @model_serializer(mode="wrap")
    def _omit_new_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Saved plans keep their old shape: fields added later appear only when they are used."""
        data: dict[str, Any] = handler(self)
        if not data.get("typed_destinations"):
            data.pop("typed_destinations", None)
        return data


def plan_json(plan: NotificationPlan) -> str:
    """The plan as written to disk. Fields at their default are left out."""
    return plan.model_dump_json(indent=2, exclude_defaults=True) + "\n"


def _name(text: str) -> str:
    return printable(text, MAX_NAME)


def plan_destinations(
    name: str, channels: list[IRChannel], prefix: str
) -> list[PlannedDestination]:
    """One planned destination (and channel) per migrated channel, named from the topic."""
    planned: list[PlannedDestination] = []
    for i, ch in enumerate(channels):
        suffix = f" {ch.kind.value} {i + 1}"  # the index is never cut off a long title
        head = printable(f"{prefix} {name}", MAX_NAME - len(suffix))
        planned.append(PlannedDestination(kind=ch.kind, name=head + suffix, target=ch.target))
    return planned


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


# ---------------------------------------------------------------------------------------------
# The New Relic destination and channel model. One NrDestination is one destination plus the one
# channel that points at it (a channel can be used by one workflow only, so they are made together).
# Types, property keys and auth shapes come from the provider schema and the New Relic notification
# documentation (docs/research/newrelic-notifications-and-nrql-reference.md, section 1.1).
# ---------------------------------------------------------------------------------------------

SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class DestinationError(ValueError):
    """The destination does not fit its type (unknown property, missing field, wrong auth)."""


class DestinationType(StrEnum):
    WEBHOOK = "WEBHOOK"
    EMAIL = "EMAIL"
    SERVICE_NOW = "SERVICE_NOW"
    SERVICE_NOW_APP = "SERVICE_NOW_APP"
    PAGERDUTY_ACCOUNT_INTEGRATION = "PAGERDUTY_ACCOUNT_INTEGRATION"
    PAGERDUTY_SERVICE_INTEGRATION = "PAGERDUTY_SERVICE_INTEGRATION"
    JIRA = "JIRA"
    SLACK = "SLACK"
    SLACK_COLLABORATION = "SLACK_COLLABORATION"
    SLACK_LEGACY = "SLACK_LEGACY"
    MOBILE_PUSH = "MOBILE_PUSH"
    EVENT_BRIDGE = "EVENT_BRIDGE"
    MICROSOFT_TEAMS = "MICROSOFT_TEAMS"
    WORKFLOW_AUTOMATION = "WORKFLOW_AUTOMATION"


class AuthKind(StrEnum):
    NONE = "none"
    BASIC = "basic"
    TOKEN = "token"  # noqa: S105 - the name of an auth kind, not a password
    CUSTOM_HEADER = "custom_header"
    OAUTH = "oauth"  # a browser sign-in; cannot be scripted


class SecretRef(BaseModel, frozen=True):
    """The name of a secret that a person supplies; the value is never stored in the IR."""

    name: str

    @field_validator("name")
    @classmethod
    def _shape(cls, value: str) -> str:
        if not SECRET_NAME.fullmatch(value):
            raise ValueError("secret name must be upper case letters, digits and underscores")
        return value


class HeaderAuth(BaseModel):
    key: str
    value: SecretRef


class DestinationAuth(BaseModel):
    """Authentication of a destination. Every secret part is a reference."""

    kind: AuthKind
    user: str = ""
    password: SecretRef | None = None
    prefix: str = ""
    token: SecretRef | None = None
    headers: list[HeaderAuth] = []

    @model_validator(mode="after")
    def _complete(self) -> "DestinationAuth":
        complete = {
            AuthKind.BASIC: bool(self.user and self.password),
            AuthKind.TOKEN: self.token is not None,
            AuthKind.CUSTOM_HEADER: bool(self.headers),
            AuthKind.NONE: True,
            AuthKind.OAUTH: True,
        }[self.kind]
        if not complete:
            raise ValueError(f"auth kind {self.kind.value} is missing its fields")
        return self


class SecureUrl(BaseModel):
    """A webhook URL kept encrypted after its prefix (Terraform `secure_url` block)."""

    prefix: str
    suffix: SecretRef


class NrDestination(BaseModel):
    type: DestinationType
    name: str
    properties: dict[str, str | SecretRef] = {}
    auth: DestinationAuth | None = None
    secure_url: SecureUrl | None = None
    channel_properties: dict[str, str] = {}
    channel_product: str = "IINT"


NotificationPlan.model_rebuild()


@dataclass(frozen=True)
class PropertySpec:
    key: str
    required: bool = False
    secret: bool = False


@dataclass(frozen=True)
class DestinationSpec:
    channel_type: str
    properties: tuple[PropertySpec, ...] = ()
    channel_properties: tuple[PropertySpec, ...] = ()
    auth: tuple[AuthKind, ...] = (AuthKind.NONE,)
    auth_required: bool = False
    token_prefix: str = ""  # the prefix New Relic expects with a token, when it is fixed
    header_key: str = ""  # the one custom header key that is required, when it is fixed
    products: tuple[str, ...] = ("IINT",)
    human_steps: tuple[str, ...] = ()  # a person must do these before or after the API calls
    creatable: bool = True
    refuse: str = ""  # why it cannot be created, when `creatable` is false


def _props(*keys: str, required: tuple[str, ...] = ()) -> tuple[PropertySpec, ...]:
    return tuple(PropertySpec(k, required=k in required) for k in keys)


_SLACK_STEPS = (
    "Create the Slack destination in the New Relic UI and sign in with Slack (OAuth); it cannot "
    "be created from a script or Terraform.",
    "Then create a channel on it with the Slack channel id.",
)
_PD_PREFIX = "Token token="
SPECS: dict[DestinationType, DestinationSpec] = {
    DestinationType.WEBHOOK: DestinationSpec(
        "WEBHOOK",
        properties=_props("url", "two_way_integration", "source", required=("url",)),
        channel_properties=_props("payload", "headers", required=("payload",)),
        auth=(AuthKind.NONE, AuthKind.BASIC, AuthKind.TOKEN, AuthKind.CUSTOM_HEADER),
    ),
    DestinationType.EMAIL: DestinationSpec(
        "EMAIL",
        properties=_props("email", required=("email",)),
        channel_properties=_props("subject", "customDetailsEmail"),
    ),
    DestinationType.SERVICE_NOW: DestinationSpec(
        "SERVICENOW_INCIDENTS",
        properties=_props("url", "two_way_integration", required=("url",)),
        channel_properties=_props("description", "short_description"),
        auth=(AuthKind.BASIC,),
        auth_required=True,
    ),
    DestinationType.SERVICE_NOW_APP: DestinationSpec(
        "SERVICE_NOW_APP",
        properties=_props("url", required=("url",)),
        auth=(AuthKind.BASIC,),
        auth_required=True,
        human_steps=(
            "A ServiceNow administrator installs the New Relic application from the ServiceNow "
            "store and creates a user with the role x_newre_core.inbound_api.",
            "Some New Relic accounts must ask New Relic to enable this destination.",
        ),
    ),
    DestinationType.PAGERDUTY_ACCOUNT_INTEGRATION: DestinationSpec(
        "PAGERDUTY_ACCOUNT_INTEGRATION",
        properties=_props("two_way_integration"),
        channel_properties=_props(
            "summary", "service", "email", "customDetails", required=("summary", "service", "email")
        ),
        auth=(AuthKind.TOKEN,),
        auth_required=True,
        token_prefix=_PD_PREFIX,
    ),
    DestinationType.PAGERDUTY_SERVICE_INTEGRATION: DestinationSpec(
        "PAGERDUTY_SERVICE_INTEGRATION",
        channel_properties=_props("summary", "customDetails", required=("summary",)),
        auth=(AuthKind.TOKEN,),
        auth_required=True,
        token_prefix=_PD_PREFIX,
    ),
    DestinationType.JIRA: DestinationSpec(
        "JIRA_CLASSIC",
        properties=_props("url", "two_way_integration", required=("url",)),
        channel_properties=_props(
            "project", "issuetype", "summary", "description", required=("project", "issuetype")
        ),
        auth=(AuthKind.BASIC,),
        auth_required=True,
    ),
    DestinationType.SLACK: DestinationSpec(
        "SLACK",
        channel_properties=_props("channelId", "customDetailsSlack", required=("channelId",)),
        auth=(AuthKind.OAUTH,),
        human_steps=_SLACK_STEPS,
        creatable=False,
        refuse="a Slack destination needs an OAuth sign-in in the New Relic UI",
    ),
    DestinationType.SLACK_COLLABORATION: DestinationSpec(
        "SLACK_COLLABORATION",
        auth=(AuthKind.OAUTH,),
        products=("DISCUSSIONS",),
        human_steps=_SLACK_STEPS,
        creatable=False,
        refuse="a Slack destination needs an OAuth sign-in in the New Relic UI",
    ),
    DestinationType.SLACK_LEGACY: DestinationSpec(
        "SLACK_LEGACY",
        creatable=False,
        refuse="New Relic tells users to move off the legacy Slack destination; use SLACK",
    ),
    DestinationType.MOBILE_PUSH: DestinationSpec(
        "MOBILE_PUSH", properties=_props("userId", required=("userId",))
    ),
    DestinationType.EVENT_BRIDGE: DestinationSpec(
        "EVENT_BRIDGE",
        properties=_props("AWSAccountId", "AWSRegion", required=("AWSAccountId", "AWSRegion")),
        channel_properties=_props("eventSource", "eventContent", required=("eventSource",)),
        human_steps=(
            "In AWS, associate the New Relic partner event source with an event bus and add a "
            "rule that sends events to the target (a Lambda function, SQS queue or Firehose).",
        ),
    ),
    DestinationType.MICROSOFT_TEAMS: DestinationSpec(
        "MICROSOFT_TEAMS",
        properties=(PropertySpec("securityCode", required=True, secret=True),),
        channel_properties=_props(
            "teamId", "channelId", "customDetails", required=("teamId", "channelId")
        ),
        human_steps=(
            "Install the New Relic app in Microsoft Teams and add it to the channel that should "
            "receive alerts.",
            "Read the security code the app posts in the channel; New Relic asks for it when the "
            "destination is created.",
            "Find the team id and the channel id (channelSuggestions in NerdGraph, or the UI).",
        ),
    ),
    DestinationType.WORKFLOW_AUTOMATION: DestinationSpec(
        "WORKFLOW_AUTOMATION",
        channel_properties=_props("workflowAutomation", required=("workflowAutomation",)),
        auth=(AuthKind.CUSTOM_HEADER,),
        auth_required=True,
        header_key="Api-Key",
    ),
}


def spec_for(kind: DestinationType) -> DestinationSpec:
    return SPECS[kind]


def needs_human_step(kind: DestinationType) -> bool:
    return bool(SPECS[kind].human_steps)


def _check_properties(
    where: str,
    given: dict[str, str] | dict[str, str | SecretRef],
    specs: tuple[PropertySpec, ...],
    skip: set[str],
) -> None:
    known = {p.key: p for p in specs}
    for key, value in given.items():
        spec = known.get(key)
        if spec is None:
            raise DestinationError(f"unknown {where}property {key!r}")
        if spec.secret and not isinstance(value, SecretRef):
            raise DestinationError(f"{where}property {key} must be a secret reference")
        if not spec.secret and isinstance(value, SecretRef):
            raise DestinationError(f"{where}property {key} is not a secret field")
    for p in specs:
        if p.required and p.key not in given and p.key not in skip:
            raise DestinationError(f"requires {where}property {p.key}")


def _check_auth(dest: NrDestination, spec: DestinationSpec) -> None:
    auth = dest.auth or DestinationAuth(kind=AuthKind.NONE)
    if auth.kind not in spec.auth:
        raise DestinationError(f"{dest.type.value} does not accept auth {auth.kind.value}")
    if spec.auth_required and auth.kind == AuthKind.NONE:
        raise DestinationError(f"{dest.type.value} requires auth")
    if spec.token_prefix and auth.prefix != spec.token_prefix:
        raise DestinationError(f"{dest.type.value} auth prefix must be {spec.token_prefix!r}")
    if spec.header_key and [h.key for h in auth.headers] != [spec.header_key]:
        raise DestinationError(f"{dest.type.value} needs the custom header {spec.header_key}")


def validate_destination(dest: NrDestination) -> None:
    """Raise DestinationError unless the destination fits its type. Unknown parts are refused."""
    spec = SPECS[dest.type]
    if dest.secure_url is not None and (
        dest.type != DestinationType.WEBHOOK or "url" in dest.properties
    ):
        raise DestinationError("secure_url belongs to a WEBHOOK destination without a plain url")
    skip = {"url"} if dest.secure_url is not None else set()
    _check_properties("", dest.properties, spec.properties, skip)
    _check_properties("channel ", dest.channel_properties, spec.channel_properties, set())
    _check_auth(dest, spec)
    if dest.channel_product not in spec.products:
        raise DestinationError(f"{dest.type.value} channels use product {spec.products[0]}")
