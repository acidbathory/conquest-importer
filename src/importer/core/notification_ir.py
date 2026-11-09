"""Vendor-neutral notification IR and the plan that turns it into New Relic objects.

A source topic (for example an SNS topic) has channels (email addresses, webhook URLs) that can
be migrated and subscriptions that cannot. The plan is: one destination and one channel per
migrated channel, and ONE workflow per topic that routes the issues of the conditions which used
that topic to those channels. Workflows are always created disabled.
"""

import json
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


class ManualDestination(BaseModel):
    """A destination that needs a person: it takes a secret, an OAuth sign-in or has no match."""

    service: str  # a fixed label for the source service, for example "slack"
    nr_type: str  # the closest New Relic destination type, or "" when there is none
    label: str = ""  # untrusted text from the source (a channel name); escape before printing
    steps: list[str]


class IRTopic(BaseModel):
    source_system: str
    source_key: str  # for example the SNS topic ARN
    name: str
    channels: list[IRChannel]
    unsupported: list[str] = []  # one reason per subscription that cannot be migrated
    # subscriptions that need a person (an AWS-side step); kept in the plan with their steps
    manual_destinations: list["ManualDestination"] = []
    warnings: list[str] = []  # caveats about the channels that did migrate


class PlannedDestination(BaseModel):
    kind: ChannelKind
    name: str
    target: str


# Issue filter operators the provider schema accepts (newrelic_workflow.issues_filter.predicate).
PREDICATE_OPERATORS = frozenset(
    [
        "CONTAINS",
        "DOES_NOT_CONTAIN",
        "DOES_NOT_EQUAL",
        "DOES_NOT_EXACTLY_MATCH",
        "ENDS_WITH",
        "EQUAL",
        "EXACTLY_MATCHES",
        "GREATER_OR_EQUAL",
        "GREATER_THAN",
        "IS",
        "IS_NOT",
        "LESS_OR_EQUAL",
        "LESS_THAN",
        "STARTS_WITH",
    ]
)
NOTIFICATION_TRIGGERS = frozenset(
    ["ACTIVATED", "ACKNOWLEDGED", "INVESTIGATING", "PRIORITY_CHANGED", "CLOSED", "OTHER_UPDATES"]
)
MUTING_HANDLING = frozenset(
    [
        "NOTIFY_ALL_ISSUES",
        "DONT_NOTIFY_FULLY_MUTED_ISSUES",
        "DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES",
    ]
)
FILTER_LIMIT = 4096  # characters of issue filter per workflow; New Relic does not raise it
MAX_ENRICHMENTS = 2
MAX_WORKFLOWS = 1000  # per account, existing workflows included
TAG_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,64}")


class PlannedPredicate(BaseModel):
    attribute: str
    operator: str
    values: list[str]

    @field_validator("operator")
    @classmethod
    def _known_operator(cls, value: str) -> str:
        if value not in PREDICATE_OPERATORS:
            raise ValueError("unknown issue filter operator")
        return value


def tag_predicate(name: str, values: list[str]) -> PlannedPredicate:
    """Match issues that carry the entity tag `name` with one of `values`.

    The attribute is accumulations.tag.NAME (documented in New Relic's workflow examples). The
    spelling tags.NAME belongs to muting rules and title templates, not to issue filters.
    """
    if not TAG_NAME.fullmatch(name):
        raise ValueError("tag name must be 1-64 letters, digits and _ . : -")
    return PlannedPredicate(
        attribute=f"accumulations.tag.{name}", operator="EXACTLY_MATCHES", values=values
    )


class Enrichment(BaseModel):
    """An NRQL query whose result is added to the notification; the name is used in templates."""

    name: str
    query: str


class PlannedWorkflow(BaseModel):
    name: str
    condition_names: list[str]  # issues of these conditions are routed
    destination_indexes: list[int]  # which planned destinations/channels it notifies
    predicates: list[PlannedPredicate] = []  # when set, these replace the condition-name filter
    # the next three are options; at their default they are left out of the written file and the
    # emitters use the defaults in emit/common.py (EmitDefaults)
    notification_triggers: list[str] = []
    muting_rules_handling: str = ""
    enrichments: list["Enrichment"] = []

    @field_validator("notification_triggers")
    @classmethod
    def _known_triggers(cls, value: list[str]) -> list[str]:
        if not NOTIFICATION_TRIGGERS.issuperset(value):
            raise ValueError("unknown notification trigger")
        return value

    @field_validator("muting_rules_handling")
    @classmethod
    def _known_handling(cls, value: str) -> str:
        if value and value not in MUTING_HANDLING:
            raise ValueError("unknown muting rules handling")
        return value

    @model_serializer(mode="wrap")
    def _omit_options(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name in ("notification_triggers", "muting_rules_handling", "enrichments"):
            if not data.get(name):
                data.pop(name, None)
        return data

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


def build_plan(
    topic: IRTopic,
    condition_names: list[str],
    prefix: str,
    muting_rules_handling: str = "",
) -> NotificationPlan:
    """Plan the New Relic objects for one topic. No workflow if nothing can be routed."""
    destinations = plan_destinations(topic.name, topic.channels, prefix)
    extras: dict[str, Any] = {}
    if topic.manual_destinations:
        extras = {"needs_review": True, "manual_destinations": topic.manual_destinations}
    if topic.warnings:
        extras["notes"] = topic.warnings
    if not destinations or not condition_names:
        return NotificationPlan(topic=topic.name, destinations=[], workflow=None, **extras)
    workflow = PlannedWorkflow(
        name=_name(f"{prefix} {topic.name}"),
        condition_names=sorted(set(condition_names)),
        destination_indexes=list(range(len(destinations))),
        muting_rules_handling=muting_rules_handling,
    )
    return NotificationPlan(
        topic=topic.name, destinations=destinations, workflow=workflow, **extras
    )


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


def issue_filter_size(wf: PlannedWorkflow) -> int:
    """Characters in the workflow's issue filter, counted as the JSON that carries it.

    This is the upper bound of any reading of New Relic's 4,096-character limit (UNVERIFIED which
    one it measures), so a filter that passes here passes there.
    """
    return len(
        json.dumps(
            [p.model_dump() for p in wf.issue_predicates()],
            separators=(",", ":"),
            ensure_ascii=False,
        )
    )


def workflow_violations(plan: NotificationPlan) -> list[str]:
    """What stops this plan's workflow from being created. Fixed wording: no source text."""
    wf = plan.workflow
    if wf is None:
        return []
    problems: list[str] = []
    count = len(plan.destinations) + len(plan.typed_destinations)
    seen: set[int] = set()
    for index in wf.destination_indexes:
        if not 0 <= index < count:
            problems.append(f"destination index {index} does not exist in the plan")
        elif index in seen:
            problems.append(
                f"destination {index + 1} is listed more than once; a channel can be used once "
                "in a workflow"
            )
        seen.add(index)
    if not wf.destination_indexes:
        problems.append("the workflow notifies no destination")
    size = issue_filter_size(wf)
    if size > FILTER_LIMIT:
        problems.append(
            f"the issue filter is {size} characters; New Relic allows {FILTER_LIMIT} per workflow"
        )
    if len(wf.enrichments) > MAX_ENRICHMENTS:
        problems.append(
            f"the workflow has {len(wf.enrichments)} enrichments; "
            f"New Relic allows {MAX_ENRICHMENTS}"
        )
    if len({e.name for e in wf.enrichments}) != len(wf.enrichments):
        problems.append("enrichment names must be unique")
    return problems


def plan_violations(plans: list[NotificationPlan]) -> list[str]:
    """Problems across a set of plans: the account limit and objects that would be shared."""
    problems: list[str] = []
    made = [p for p in plans if p.workflow is not None]
    if len(made) > MAX_WORKFLOWS:
        problems.append(
            f"{len(made)} workflows are planned; New Relic allows {MAX_WORKFLOWS} per account, "
            "counting the ones that exist"
        )
    topics = [p.topic for p in plans]
    if len(set(topics)) != len(topics):
        problems.append(
            "two plans have the same topic, so they would create the same objects; a channel "
            "can serve one workflow only"
        )
    for n, plan in enumerate(plans, start=1):
        problems += [f"plan {n}: {p}" for p in workflow_violations(plan)]
    return problems
