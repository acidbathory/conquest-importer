"""Undo a migration: delete exactly the objects listed in its manifest, and nothing else.

Each object is re-read first and deleted only if its id still has the name that was recorded, so
an id that was reused or an object someone renamed is left alone. A policy is deleted only if it
is empty afterwards (something added to it later is not ours to remove). Writes are sent once.
"""

from typing import Any

from pydantic import BaseModel

from importer.core.apply import NewRelicAPIError, NewRelicClient
from importer.core.manifest import CreatedObject, Manifest

READ: dict[str, tuple[str, str]] = {
    # kind -> (query, path to the object inside the response)
    "condition": (
        "query($a: Int!, $id: ID!) { actor { account(id: $a) { alerts "
        "{ nrqlCondition(id: $id) { id name } } } } }",
        "actor.account.alerts.nrqlCondition",
    ),
    "policy": (
        "query($a: Int!, $id: ID!) { actor { account(id: $a) { alerts "
        "{ policy(id: $id) { id name } } } } }",
        "actor.account.alerts.policy",
    ),
    "dashboard": (
        "query($g: EntityGuid!) { actor { entity(guid: $g) { guid name } } }",
        "actor.entity",
    ),
    "destination": (
        "query($a: Int!, $id: ID!) { actor { account(id: $a) { aiNotifications "
        "{ destinations(filters: {id: $id}) { entities { id name } } } } } }",
        "actor.account.aiNotifications.destinations.entities.0",
    ),
    "channel": (
        "query($a: Int!, $id: ID!) { actor { account(id: $a) { aiNotifications "
        "{ channels(filters: {id: $id}) { entities { id name } } } } } }",
        "actor.account.aiNotifications.channels.entities.0",
    ),
    "workflow": (
        "query($a: Int!, $id: ID!) { actor { account(id: $a) { aiWorkflows "
        "{ workflows(filters: {id: $id}) { entities { id name } } } } } }",
        "actor.account.aiWorkflows.workflows.entities.0",
    ),
}
DELETE: dict[str, tuple[str, str]] = {
    # kind -> (mutation, name of the variable that carries the id)
    "condition": (
        "mutation($a: Int!, $id: ID!) { alertsConditionDelete(accountId: $a, id: $id) { id } }",
        "id",
    ),
    "policy": (
        "mutation($a: Int!, $id: ID!) { alertsPolicyDelete(accountId: $a, id: $id) { id } }",
        "id",
    ),
    "dashboard": (
        "mutation($g: EntityGuid!) { dashboardDelete(guid: $g) { status errors { description } } }",
        "g",
    ),
    "destination": (
        "mutation($a: Int!, $id: ID!) { aiNotificationsDeleteDestination("
        "accountId: $a, destinationId: $id) { ids error { __typename } } }",
        "id",
    ),
    "channel": (
        "mutation($a: Int!, $id: ID!) { aiNotificationsDeleteChannel("
        "accountId: $a, channelId: $id) { ids error { __typename } } }",
        "id",
    ),
    "workflow": (
        "mutation($a: Int!, $id: ID!) { aiWorkflowsDeleteWorkflow("
        "accountId: $a, id: $id, deleteChannels: false) { id errors { description } } }",
        "id",
    ),
}
POLICY_CONDITIONS = (
    "query($a: Int!, $p: ID!) { actor { account(id: $a) { alerts "
    "{ nrqlConditionsSearch(searchCriteria: {policyId: $p}) { nrqlConditions { id } } } } } }"
)


class RollbackResult(BaseModel):
    deleted: list[CreatedObject] = []
    skipped: list[tuple[CreatedObject, str]] = []
    failed: list[tuple[CreatedObject, str]] = []


def _dig(data: Any, path: str) -> Any:
    for part in path.split("."):
        if data is None:
            return None
        if part.isdigit():
            data = data[int(part)] if isinstance(data, list) and len(data) > int(part) else None
        else:
            data = data.get(part) if isinstance(data, dict) else None
    return data


def current(client: NewRelicClient, account_id: int, obj: CreatedObject) -> dict[str, Any] | None:
    query, path = READ[obj.kind]
    variables: dict[str, Any] = {"a": account_id, "id": obj.id}
    if obj.kind == "dashboard":
        variables = {"g": obj.id}
    found = _dig(client.run(query, variables, is_write=False), path)
    return found if isinstance(found, dict) else None


def foreign_conditions(
    client: NewRelicClient, account_id: int, policy_id: str, ours: set[str]
) -> list[str]:
    """Ids of conditions in the policy that this manifest did not create."""
    data = client.run(POLICY_CONDITIONS, {"a": account_id, "p": policy_id}, is_write=False)
    found = _dig(data, "actor.account.alerts.nrqlConditionsSearch.nrqlConditions") or []
    return [str(c["id"]) for c in found if str(c["id"]) not in ours]


def rollback(
    client: NewRelicClient, account_id: int, manifest: Manifest, *, apply: bool
) -> RollbackResult:
    result = RollbackResult()
    our_conditions = {o.id for o in manifest.objects if o.kind == "condition"}
    for obj in manifest.in_delete_order():
        try:
            now = current(client, account_id, obj)
        except NewRelicAPIError as exc:
            result.failed.append((obj, str(exc)))
            continue
        if now is None:
            result.skipped.append((obj, "no longer exists"))
            continue
        if now.get("name") != obj.name:
            result.skipped.append((obj, "name changed since it was created; left alone"))
            continue
        if obj.kind == "policy" and foreign_conditions(client, account_id, obj.id, our_conditions):
            result.skipped.append(
                (obj, "policy still has conditions that are not ours; left alone")
            )
            continue
        if not apply:
            result.deleted.append(obj)  # would be deleted
            continue
        mutation, id_var = DELETE[obj.kind]
        variables = {id_var: obj.id} if obj.kind == "dashboard" else {"a": account_id, "id": obj.id}
        try:
            client.run(mutation, variables, is_write=True)
            still_there = current(client, account_id, obj) is not None
        except NewRelicAPIError as exc:
            result.failed.append((obj, str(exc)))
            continue
        if still_there:
            # The call did not raise, but the object is still there: do not report it as gone.
            result.failed.append((obj, "delete was accepted but the object still exists"))
            continue
        result.deleted.append(obj)
    return result
