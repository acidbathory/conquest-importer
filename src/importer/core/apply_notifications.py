"""Create notification destinations, channels and workflows in New Relic via NerdGraph.

Same safety design as apply.py: writes are sent once and never retried, a topic whose workflow
already exists is skipped (nothing is updated), workflows are created DISABLED, and every object
created is returned for the manifest even when a later step fails, so a partial run can be undone.
"""

from collections.abc import Mapping

from importer.core.apply import ApplyResult, NewRelicAPIError, NewRelicClient
from importer.core.emit.common import CannotEmit
from importer.core.emit.notifications import (
    CREATE_CHANNEL,
    CREATE_DESTINATION,
    CREATE_WORKFLOW,
    all_destinations,
    nr_channel_input,
    nr_destination_input,
    workflow_input,
)
from importer.core.manifest import CreatedObject
from importer.core.notification_ir import NotificationPlan

WORKFLOW_SEARCH = (
    "query($accountId: Int!, $name: String!) { actor { account(id: $accountId) "
    "{ aiWorkflows { workflows(filters: {name: $name}) { entities { id name } } } } } }"
)


def workflow_exists(client: NewRelicClient, account_id: int, name: str) -> bool:
    data = client.run(WORKFLOW_SEARCH, {"accountId": account_id, "name": name}, is_write=False)
    entities = data["actor"]["account"]["aiWorkflows"]["workflows"]["entities"] or []
    return any(e["name"] == name for e in entities)


def _error_text(error: object) -> str:
    return str(error)[:300]


def apply_notifications(
    client: NewRelicClient,
    account_id: int,
    plans: list[NotificationPlan],
    secrets: Mapping[str, str] | None = None,
) -> ApplyResult:
    """Create every plan. `secrets` maps a secret name to its value; a destination that needs a
    secret that is not in it is reported as failed before anything is created for its plan."""
    result = ApplyResult()
    supplied = secrets or {}
    for plan in plans:
        wf = plan.workflow
        if wf is None:
            continue
        if workflow_exists(client, account_id, wf.name):
            result.skipped_existing.append(wf.name)
            continue
        dests = all_destinations(plan)
        try:
            inputs = [nr_destination_input(d, supplied) for d in dests]
            for d in dests:
                nr_channel_input(d, "0")  # the same checks, before the first write
        except CannotEmit as exc:
            result.failed.append((wf.name, str(exc)))
            continue
        channel_ids: list[str] = []
        try:
            for dest, destination in zip(dests, inputs, strict=True):
                data = client.run(
                    CREATE_DESTINATION,
                    {"accountId": account_id, "destination": destination},
                    is_write=True,
                )["aiNotificationsCreateDestination"]
                if data.get("error") or not data.get("destination"):
                    raise NewRelicAPIError(f"destination: {_error_text(data.get('error'))}")
                destination_id = str(data["destination"]["id"])
                result.objects.append(
                    CreatedObject(kind="destination", id=destination_id, name=dest.name)
                )
                data = client.run(
                    CREATE_CHANNEL,
                    {"accountId": account_id, "channel": nr_channel_input(dest, destination_id)},
                    is_write=True,
                )["aiNotificationsCreateChannel"]
                if data.get("error") or not data.get("channel"):
                    raise NewRelicAPIError(f"channel: {_error_text(data.get('error'))}")
                channel_ids.append(str(data["channel"]["id"]))
                result.objects.append(
                    CreatedObject(kind="channel", id=channel_ids[-1], name=dest.name)
                )
            data = client.run(
                CREATE_WORKFLOW,
                {"accountId": account_id, "data": workflow_input(plan, channel_ids)},
                is_write=True,
            )["aiWorkflowsCreateWorkflow"]
            if data.get("errors") or not data.get("workflow"):
                raise NewRelicAPIError(f"workflow: {_error_text(data.get('errors'))}")
            result.objects.append(
                CreatedObject(kind="workflow", id=str(data["workflow"]["id"]), name=wf.name)
            )
        except NewRelicAPIError as exc:
            result.failed.append((wf.name, str(exc)))
            continue
        result.created.append(wf.name)
    return result
