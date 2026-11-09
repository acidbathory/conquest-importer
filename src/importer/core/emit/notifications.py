"""Emit notification plans as NerdGraph variables and Terraform. Nothing here writes anywhere.

Names and addresses are untrusted: NerdGraph values travel as GraphQL variables and Terraform
strings go through hcl_string().
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from importer.core.emit.common import CannotEmit, emit_defaults
from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.emit.terraform import hcl_string
from importer.core.ir import IRMonitor
from importer.core.notification_ir import (
    AuthKind,
    ChannelKind,
    DestinationAuth,
    DestinationError,
    DestinationType,
    NotificationPlan,
    NrDestination,
    PlannedDestination,
    PlannedWorkflow,
    SecretRef,
    plan_violations,
    spec_for,
    validate_destination,
    workflow_violations,
)

EMAIL_SUBJECT = "{{ issueTitle }}"
WEBHOOK_PAYLOAD = (
    '{\n  "issueId": {{ json issueId }},\n  "title": {{ json annotations.title.[0] }},\n'
    '  "state": {{ json state }},\n  "priority": {{ json priority }}\n}'
)
# What a preview shows in place of a secret. apply refuses to send it: a missing secret is an error.
SECRET_PLACEHOLDER = "SECRET_NOT_SET"  # noqa: S105 - a label shown in previews, not a password
GRAPHQL_AUTH = {
    AuthKind.BASIC: "BASIC",
    AuthKind.TOKEN: "TOKEN",
    AuthKind.CUSTOM_HEADER: "CUSTOM_HEADERS",
}

CREATE_DESTINATION = (
    "mutation($accountId: Int!, $destination: AiNotificationsDestinationInput!) {\n"
    "  aiNotificationsCreateDestination(accountId: $accountId, destination: $destination) {\n"
    "    destination { id name }\n    error { __typename }\n  }\n}"
)
CREATE_CHANNEL = (
    "mutation($accountId: Int!, $channel: AiNotificationsChannelInput!) {\n"
    "  aiNotificationsCreateChannel(accountId: $accountId, channel: $channel) {\n"
    "    channel { id name }\n    error { __typename }\n  }\n}"
)
CREATE_WORKFLOW = (
    "mutation($accountId: Int!, $data: AiWorkflowsCreateWorkflowInput!) {\n"
    "  aiWorkflowsCreateWorkflow(accountId: $accountId, createWorkflowData: $data) {\n"
    "    workflow { id name }\n    errors { type description }\n  }\n}"
)


def converted_condition_names(monitors: list[IRMonitor]) -> dict[int, str]:
    """source_id -> the condition name used in New Relic, for monitors that can be emitted."""
    names: dict[int, str] = {}
    for m in monitors:
        try:
            names[m.source_id] = nerdgraph_payload(m)["variables"]["condition"]["name"]
        except (CannotEmit, ValueError):
            continue
    return names


def as_nr_destination(dest: PlannedDestination) -> NrDestination:
    """The typed form of an email or webhook destination from a plan."""
    if dest.kind == ChannelKind.EMAIL:
        return NrDestination(
            type=DestinationType.EMAIL,
            name=dest.name,
            properties={"email": dest.target},
            channel_properties={"subject": EMAIL_SUBJECT},
        )
    return NrDestination(
        type=DestinationType.WEBHOOK,
        name=dest.name,
        properties={"url": dest.target},
        channel_properties={"payload": WEBHOOK_PAYLOAD},
    )


def all_destinations(plan: NotificationPlan) -> list[NrDestination]:
    """Every destination of the plan, in the index space the workflow uses."""
    return [as_nr_destination(d) for d in plan.destinations] + plan.typed_destinations


def _creatable(dest: NrDestination) -> None:
    """Raise unless the destination fits its type and New Relic lets a script create it."""
    try:
        validate_destination(dest)
    except DestinationError as exc:
        raise CannotEmit(str(exc)) from None
    spec = spec_for(dest.type)
    if not spec.creatable:
        raise CannotEmit(spec.refuse)


def _secret(value: str | SecretRef, secrets: Mapping[str, str] | None) -> str:
    if isinstance(value, str):
        return value
    if secrets is None:
        return SECRET_PLACEHOLDER
    if value.name not in secrets:
        raise CannotEmit(f"the secret {value.name} was not supplied")
    return secrets[value.name]


def _auth_input(auth: DestinationAuth, secrets: Mapping[str, str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"authType": GRAPHQL_AUTH[auth.kind]}
    if auth.kind == AuthKind.BASIC and auth.password is not None:
        out["basic"] = {"user": auth.user, "password": _secret(auth.password, secrets)}
    elif auth.kind == AuthKind.TOKEN and auth.token is not None:
        out["token"] = {"prefix": auth.prefix, "token": _secret(auth.token, secrets)}
    else:
        out["customHeaders"] = [
            {"key": h.key, "value": _secret(h.value, secrets)} for h in auth.headers
        ]
    return out


def nr_destination_input(
    dest: NrDestination, secrets: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The NerdGraph destination input. `secrets` is None for a preview with placeholders."""
    _creatable(dest)
    if dest.secure_url is not None:
        raise CannotEmit("secure_url is emitted for Terraform only")
    out: dict[str, Any] = {
        "type": dest.type.value,
        "name": dest.name,
        "properties": [
            {"key": k, "value": _secret(v, secrets)} for k, v in dest.properties.items()
        ],
    }
    if dest.auth is not None and dest.auth.kind != AuthKind.NONE:
        out["auth"] = _auth_input(dest.auth, secrets)
    return out


def nr_channel_input(dest: NrDestination, destination_id: str) -> dict[str, Any]:
    _creatable(dest)
    return {
        "type": spec_for(dest.type).channel_type,
        "name": dest.name,
        "destinationId": destination_id,
        "product": dest.channel_product,
        "properties": [{"key": k, "value": v} for k, v in dest.channel_properties.items()],
    }


def destination_input(dest: PlannedDestination) -> dict[str, Any]:
    return nr_destination_input(as_nr_destination(dest))


def channel_input(dest: PlannedDestination, destination_id: str) -> dict[str, Any]:
    return nr_channel_input(as_nr_destination(dest), destination_id)


def workflow_input(plan: NotificationPlan, channel_ids: list[str]) -> dict[str, Any]:
    wf = plan.workflow
    if wf is None:
        raise ValueError("plan has no workflow")
    problems = workflow_violations(plan)
    if problems:
        raise CannotEmit(problems[0])
    defaults = emit_defaults()
    triggers = wf.notification_triggers or list(defaults.notification_triggers)
    configs: list[dict[str, Any]] = []
    for i in wf.destination_indexes:
        config: dict[str, Any] = {"channelId": channel_ids[i]}
        if triggers:
            config["notificationTriggers"] = triggers
        configs.append(config)
    data: dict[str, Any] = {
        "name": wf.name,
        "workflowEnabled": False,  # imported disabled; a human enables after review
        "destinationsEnabled": True,
        "mutingRulesHandling": wf.muting_rules_handling or defaults.muting_rules_handling,
        "issuesFilter": {
            "name": wf.name,
            "type": "FILTER",
            "predicates": [
                {"attribute": p.attribute, "operator": p.operator, "values": p.values}
                for p in wf.issue_predicates()
            ],
        },
        "destinationConfigurations": configs,
    }
    if wf.enrichments:
        data["enrichmentsEnabled"] = True
        data["enrichments"] = {
            "nrql": [
                {"name": e.name, "configuration": [{"query": e.query}]} for e in wf.enrichments
            ]
        }
    return data


def _slug(plan: NotificationPlan, n: int = 1) -> str:
    """Ten hex characters of the topic's hash; a repeat of the topic hashes `#n` too."""
    key = plan.topic if n == 1 else f"{plan.topic}#{n}"
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def _slugs(plans: list[NotificationPlan]) -> list[str]:
    """One resource-name slug per plan. Two plans with one topic (two SNS topics of one name in
    different regions) get different slugs; a topic that occurs once keeps its old slug."""
    seen: dict[str, int] = {}
    slugs: list[str] = []
    for plan in plans:
        seen[plan.topic] = seen.get(plan.topic, 0) + 1
        slugs.append(_slug(plan, seen[plan.topic]))
    return slugs


def secret_variable(name: str) -> str:
    """The Terraform variable for a secret. The prefix keeps it clear of reserved words (count,
    source, version...) and of the variables of a module this file is placed in."""
    return "nr_secret_" + name.lower()


def _hcl_value(value: str | SecretRef) -> str:
    """A string through hcl_string, or a reference to a sensitive Terraform variable."""
    return hcl_string(value) if isinstance(value, str) else f"var.{secret_variable(value.name)}"


def _property_blocks(items: Mapping[str, str | SecretRef]) -> str:
    """`property` blocks. The provider needs at least one; an empty key and value is its idiom."""
    pairs = list(items.items()) or [("", "")]
    return "\n".join(
        f"  property {{\n    key   = {hcl_string(k)}\n    value = {_hcl_value(v)}\n  }}\n"
        for k, v in pairs
    )


def _auth_blocks(dest: NrDestination) -> str:
    auth = dest.auth
    text = ""
    if dest.secure_url is not None:
        text += (
            f"\n  secure_url {{\n    prefix        = {hcl_string(dest.secure_url.prefix)}\n"
            f"    secure_suffix = {_hcl_value(dest.secure_url.suffix)}\n  }}\n"
        )
    if auth is None:
        return text
    if auth.kind == AuthKind.BASIC and auth.password is not None:
        text += (
            f"\n  auth_basic {{\n    user     = {hcl_string(auth.user)}\n"
            f"    password = {_hcl_value(auth.password)}\n  }}\n"
        )
    elif auth.kind == AuthKind.TOKEN and auth.token is not None:
        text += (
            f"\n  auth_token {{\n    prefix = {hcl_string(auth.prefix)}\n"
            f"    token  = {_hcl_value(auth.token)}\n  }}\n"
        )
    for header in auth.headers if auth.kind == AuthKind.CUSTOM_HEADER else []:
        text += (
            f"\n  auth_custom_header {{\n    key   = {hcl_string(header.key)}\n"
            f"    value = {_hcl_value(header.value)}\n  }}\n"
        )
    return text


def _destination_text(dest: NrDestination, slug: str, i: int) -> str:
    """The destination resource and the channel resource that points at it."""
    _creatable(dest)
    spec = spec_for(dest.type)
    return (
        f'resource "newrelic_notification_destination" "n_{slug}_{i}" {{\n'
        f"  name = {hcl_string(dest.name)}\n"
        f'  type = "{dest.type.value}"\n\n'
        f"{_property_blocks(dest.properties)}{_auth_blocks(dest)}}}\n\n"
        f'resource "newrelic_notification_channel" "n_{slug}_{i}" {{\n'
        f"  name           = {hcl_string(dest.name)}\n"
        f'  type           = "{spec.channel_type}"\n'
        f"  destination_id = newrelic_notification_destination.n_{slug}_{i}.id\n"
        f"  product        = {hcl_string(dest.channel_product)}\n\n"
        f"{_property_blocks(dest.channel_properties)}}}\n"
    )


def secret_names(dests: list[NrDestination]) -> list[str]:
    names: set[str] = set()
    for d in dests:
        names |= {v.name for v in d.properties.values() if isinstance(v, SecretRef)}
        if d.secure_url is not None:
            names.add(d.secure_url.suffix.name)
        if d.auth is not None:
            names |= {r.name for r in (d.auth.password, d.auth.token) if r is not None}
            names |= {h.value.name for h in d.auth.headers}
    return sorted(names)


def _variable_block(name: str) -> str:
    return f'variable "{secret_variable(name)}" {{\n  type      = string\n  sensitive = true\n}}\n'


def _destination_blocks(
    dests: list[NrDestination], slug: str
) -> tuple[list[str], list[str], list[NrDestination]]:
    """(resource texts, comments for destinations that cannot be emitted, the ones emitted)."""
    blocks: list[str] = []
    skipped: list[str] = []
    emitted: list[NrDestination] = []
    for i, dest in enumerate(dests):
        try:
            blocks.append(_destination_text(dest, slug, i))
            emitted.append(dest)
        except CannotEmit as exc:
            skipped.append(f"# not emitted: destination {i + 1} of {slug}: {exc}\n")
    return blocks, skipped, emitted


def _enrichment_blocks(wf: PlannedWorkflow) -> str:
    if not wf.enrichments:
        return ""
    nrql = "".join(
        f"\n    nrql {{\n      name = {hcl_string(e.name)}\n\n"
        f"      configuration {{\n        query = {hcl_string(e.query)}\n      }}\n    }}\n"
        for e in wf.enrichments
    )
    return f"\n  enrichments {{{nrql}  }}\n"


def _workflow_block(wf: PlannedWorkflow, slug: str) -> str:
    defaults = emit_defaults()
    triggers = wf.notification_triggers or list(defaults.notification_triggers)
    predicates = "".join(
        f"\n    predicate {{\n"
        f"      attribute = {hcl_string(p.attribute)}\n"
        f"      operator  = {hcl_string(p.operator)}\n"
        f"      values    = [{', '.join(hcl_string(v) for v in p.values)}]\n    }}\n"
        for p in wf.issue_predicates()
    )
    if triggers:
        listed = ", ".join(hcl_string(t) for t in triggers)
        extra = f"    notification_triggers = [{listed}]\n"
        pad = " " * 11  # lines channel_id up with notification_triggers
    else:
        extra, pad = "", ""
    dests = "".join(
        f"\n  destination {{\n    channel_id{pad} = newrelic_notification_channel.n_{slug}_{i}.id\n"
        f"{extra}  }}\n"
        for i in wf.destination_indexes
    )
    handling = wf.muting_rules_handling or defaults.muting_rules_handling
    return (
        f'resource "newrelic_workflow" "n_{slug}" {{\n'
        f"  name                  = {hcl_string(wf.name)}\n"
        f"  muting_rules_handling = {hcl_string(handling)}\n"
        f"  enabled               = false\n\n"
        f"  issues_filter {{\n    name = {hcl_string(wf.name)}\n"
        f'    type = "FILTER"\n{predicates}  }}\n{dests}{_enrichment_blocks(wf)}}}\n'
    )


@dataclass(frozen=True)
class TerraformResult:
    text: str
    violations: list[str]  # plan_violations(plans): what the caller should show


def notifications_terraform_result(plans: list[NotificationPlan]) -> TerraformResult:
    """Terraform for every plan: its destinations and channels, and its workflow if it has one.

    Workflows are created disabled. A plan with destinations but no workflow (a Dynatrace plan
    whose filter could not be mapped) still gets its destinations, so a person only adds the
    workflow. A variable is declared for exactly the secrets the emitted blocks refer to.
    """
    blocks: list[str] = []
    emitted: list[NrDestination] = []
    for plan, slug in zip(plans, _slugs(plans), strict=True):
        texts, skipped, done = _destination_blocks(all_destinations(plan), slug)
        blocks += texts + skipped
        emitted += done
        if plan.workflow is None:
            continue
        problems = workflow_violations(plan)
        if skipped or problems:
            why = "a destination could not be emitted" if skipped else problems[0]
            blocks.append(f"# not emitted: workflow of {slug}: {why}\n")
        else:
            blocks.append(_workflow_block(plan.workflow, slug))
    declared = [_variable_block(n) for n in secret_names(emitted)]
    return TerraformResult("\n".join(declared + blocks), plan_violations(plans))


def notifications_terraform(plans: list[NotificationPlan]) -> str:
    return notifications_terraform_result(plans).text
