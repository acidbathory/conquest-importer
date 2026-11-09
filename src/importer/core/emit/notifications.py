"""Emit notification plans as NerdGraph variables and Terraform. Nothing here writes anywhere.

Names and addresses are untrusted: NerdGraph values travel as GraphQL variables and Terraform
strings go through hcl_string().
"""

import hashlib
from typing import Any

from importer.core.emit.terraform import hcl_string
from importer.core.notification_ir import (
    ChannelKind,
    NotificationPlan,
    PlannedDestination,
    PlannedWorkflow,
)

EMAIL_SUBJECT = "{{ issueTitle }}"
WEBHOOK_PAYLOAD = (
    '{\n  "issueId": {{ json issueId }},\n  "title": {{ json annotations.title.[0] }},\n'
    '  "state": {{ json state }},\n  "priority": {{ json priority }}\n}'
)
NR_TYPE = {ChannelKind.EMAIL: "EMAIL", ChannelKind.WEBHOOK: "WEBHOOK"}

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


def destination_input(dest: PlannedDestination) -> dict[str, Any]:
    key = "email" if dest.kind == ChannelKind.EMAIL else "url"
    return {
        "type": NR_TYPE[dest.kind],
        "name": dest.name,
        "properties": [{"key": key, "value": dest.target}],
    }


def channel_input(dest: PlannedDestination, destination_id: str) -> dict[str, Any]:
    props = (
        {"key": "subject", "value": EMAIL_SUBJECT}
        if dest.kind == ChannelKind.EMAIL
        else {"key": "payload", "value": WEBHOOK_PAYLOAD}
    )
    return {
        "type": NR_TYPE[dest.kind],
        "name": dest.name,
        "destinationId": destination_id,
        "product": "IINT",
        "properties": [props],
    }


def workflow_input(plan: NotificationPlan, channel_ids: list[str]) -> dict[str, Any]:
    wf = plan.workflow
    if wf is None:
        raise ValueError("plan has no workflow")
    return {
        "name": wf.name,
        "workflowEnabled": False,  # imported disabled; a human enables after review
        "destinationsEnabled": True,
        "mutingRulesHandling": "NOTIFY_ALL_ISSUES",
        "issuesFilter": {
            "name": wf.name,
            "type": "FILTER",
            "predicates": [
                {"attribute": p.attribute, "operator": p.operator, "values": p.values}
                for p in wf.issue_predicates()
            ],
        },
        "destinationConfigurations": [
            {"channelId": channel_ids[i]} for i in wf.destination_indexes
        ],
    }


def _slug(plan: NotificationPlan) -> str:
    return hashlib.sha256(plan.topic.encode()).hexdigest()[:10]


def _destination_blocks(plan: NotificationPlan, slug: str) -> list[str]:
    blocks: list[str] = []
    for i, dest in enumerate(plan.destinations):
        key = "email" if dest.kind == ChannelKind.EMAIL else "url"
        props = EMAIL_SUBJECT if dest.kind == ChannelKind.EMAIL else WEBHOOK_PAYLOAD
        pkey = "subject" if dest.kind == ChannelKind.EMAIL else "payload"
        blocks.append(
            f'resource "newrelic_notification_destination" "n_{slug}_{i}" {{\n'
            f"  name = {hcl_string(dest.name)}\n"
            f'  type = "{NR_TYPE[dest.kind]}"\n\n'
            f"  property {{\n    key   = {hcl_string(key)}\n"
            f"    value = {hcl_string(dest.target)}\n  }}\n}}\n\n"
            f'resource "newrelic_notification_channel" "n_{slug}_{i}" {{\n'
            f"  name           = {hcl_string(dest.name)}\n"
            f'  type           = "{NR_TYPE[dest.kind]}"\n'
            f"  destination_id = newrelic_notification_destination.n_{slug}_{i}.id\n"
            f'  product        = "IINT"\n\n'
            f"  property {{\n    key   = {hcl_string(pkey)}\n"
            f"    value = {hcl_string(props)}\n  }}\n}}\n"
        )
    return blocks


def _workflow_block(wf: PlannedWorkflow, slug: str) -> str:
    predicates = "".join(
        f"\n    predicate {{\n"
        f"      attribute = {hcl_string(p.attribute)}\n"
        f"      operator  = {hcl_string(p.operator)}\n"
        f"      values    = [{', '.join(hcl_string(v) for v in p.values)}]\n    }}\n"
        for p in wf.issue_predicates()
    )
    dests = "".join(
        f"\n  destination {{\n    channel_id = newrelic_notification_channel.n_{slug}_{i}.id\n"
        f"  }}\n"
        for i in wf.destination_indexes
    )
    return (
        f'resource "newrelic_workflow" "n_{slug}" {{\n'
        f"  name                  = {hcl_string(wf.name)}\n"
        f'  muting_rules_handling = "NOTIFY_ALL_ISSUES"\n'
        f"  enabled               = false\n\n"
        f"  issues_filter {{\n    name = {hcl_string(wf.name)}\n"
        f'    type = "FILTER"\n{predicates}  }}\n{dests}}}\n'
    )


def notifications_terraform(plans: list[NotificationPlan]) -> str:
    """Terraform for every plan: its destinations and channels, and its workflow if it has one.

    Workflows are created disabled. A plan with destinations but no workflow (a Dynatrace plan
    whose filter could not be mapped) still gets its destinations, so a person only adds the
    workflow.
    """
    blocks: list[str] = []
    for plan in plans:
        slug = _slug(plan)
        blocks += _destination_blocks(plan, slug)
        if plan.workflow is not None:
            blocks.append(_workflow_block(plan.workflow, slug))
    return "\n".join(blocks)
