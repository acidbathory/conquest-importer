"""Create converted conditions in a New Relic account via NerdGraph.

This is the only module that WRITES to New Relic. Safety design:
- The plan is rebuilt from the IR files and re-validated; payload files are not trusted.
- Reads are retried; create mutations are never retried (a retry could duplicate a condition).
- Re-running is safe: an existing policy is reused and conditions with the same name are skipped.
- Conditions are created disabled. The one exception is the parts of a compound condition: New
  Relic requires them enabled, so they go in a policy of their own that no workflow routes, and
  the compound condition itself is created disabled (see core/compound.py).
- Only official New Relic API hosts are used, TLS verification uses the OS trust store, and
  error text is redacted before it is shown.
"""

import copy
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, SecretStr

from importer.core.compound import part_keys, parts_policy_name
from importer.core.emit.common import CannotEmit
from importer.core.emit.nerdgraph import (
    ACCOUNT_PLACEHOLDER,
    POLICY_PLACEHOLDER,
    component_placeholder,
    nerdgraph_payload,
)
from importer.core.ir import COMPOUND_ALIASES, IRMonitor
from importer.core.manifest import CreatedObject
from importer.core.secrets import redact
from importer.core.tls import system_tls_context

log = logging.getLogger(__name__)

HOSTS = {"us": "api.newrelic.com", "eu": "api.eu.newrelic.com"}
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_WAIT_SECONDS = 30.0
MAX_PAGES = 20
MAX_ERROR_TEXT = 300

ACCOUNT_QUERY = "query($accountId: Int!) { actor { account(id: $accountId) { id name } } }"
POLICY_SEARCH = (
    "query($accountId: Int!, $name: String!, $cursor: String) { actor { account(id: $accountId) "
    "{ alerts { policiesSearch(searchCriteria: {name: $name}, cursor: $cursor) "
    "{ policies { id name } nextCursor } } } } }"
)
POLICY_CREATE = (
    "mutation($accountId: Int!, $policy: AlertsPolicyInput!) "
    "{ alertsPolicyCreate(accountId: $accountId, policy: $policy) { id name } }"
)
CONDITION_SEARCH = (
    "query($accountId: Int!, $policyId: ID!, $cursor: String) { actor { account(id: $accountId) "
    "{ alerts { nrqlConditionsSearch(searchCriteria: {policyId: $policyId}, cursor: $cursor) "
    "{ nrqlConditions { id name } nextCursor } } } } }"
)
# The read seen live in the sandbox (compound_check.py); it lists the account's compound conditions.
COMPOUND_SEARCH = (
    "query($accountId: Int!) { actor { account(id: $accountId) "
    "{ alerts { compoundConditions { items { id name } } } } } }"
)


class NewRelicAPIError(RuntimeError):
    """A New Relic API call failed."""


class GuardError(RuntimeError):
    """A safety check refused the operation."""


class PlanItem(BaseModel):
    source_id: int
    name: str
    nrql: str  # for a compound condition, its trigger expression
    payload: dict[str, Any]
    source_key: str = ""
    role: Literal["condition", "part", "compound"] = "condition"
    components: list[str] = []  # compound: the source keys of its parts, in alias order


class Plan(BaseModel):
    items: list[PlanItem]
    skipped: int  # monitors that cannot be emitted (manual/unsupported/too long)


class ApplyResult(BaseModel):
    created: list[str] = []
    skipped_existing: list[str] = []
    skipped_review: list[str] = []  # plans marked needs_review that were not created
    failed: list[tuple[str, str]] = []
    objects: list[CreatedObject] = []  # ids of what was created, for the manifest
    enabled_parts: list[str] = []  # parts of compound conditions, created enabled (required)


def _plan_item(monitor: IRMonitor, parts: set[str]) -> PlanItem:
    """The plan entry of one monitor; CannotEmit when New Relic cannot take it."""
    payload = nerdgraph_payload(monitor)
    condition = payload["variables"]["condition"]
    if monitor.compound is not None:
        return PlanItem(
            source_id=monitor.source_id,
            name=condition["name"],
            nrql=monitor.compound.trigger,
            payload=payload,
            source_key=monitor.source_key,
            role="compound",
            components=monitor.compound.components,
        )
    part = monitor.source_key in parts
    if part:
        condition["enabled"] = True  # New Relic refuses a compound condition over disabled parts
    return PlanItem(
        source_id=monitor.source_id,
        name=condition["name"],
        nrql=condition["nrql"]["query"],
        payload=payload,
        source_key=monitor.source_key,
        role="part" if part else "condition",
    )


def _whole_compounds(items: list[PlanItem]) -> list[PlanItem]:
    """The items without a compound condition that misses a part, and without that one's parts
    (a part alone would be an enabled condition that nothing combines)."""
    have = {i.source_key for i in items if i.role == "part"}
    broken = [i for i in items if i.role == "compound" and not set(i.components) <= have]
    dropped = {i.source_key for i in broken} | {k for i in broken for k in i.components}
    kept = [i for i in items if i.source_key not in dropped]
    still_used = {k for i in kept if i.role == "compound" for k in i.components}
    return [i for i in kept if i.role != "part" or i.source_key in still_used]


def load_plan(ir_dir: Path) -> Plan:
    monitors = [IRMonitor.model_validate_json(p.read_text()) for p in sorted(ir_dir.glob("*.json"))]
    parts = part_keys(monitors)
    items: list[PlanItem] = []
    skipped = 0
    for monitor in monitors:
        try:
            items.append(_plan_item(monitor, parts))
        except CannotEmit:
            skipped += 1
    whole = _whole_compounds(items)
    return Plan(items=whole, skipped=skipped + len(items) - len(whole))


def check_guards(account_id: int, confirm_account: int | None, allowed: str | None) -> None:
    allowed_ids = {part.strip() for part in (allowed or "").split(",") if part.strip()}
    if not allowed_ids:
        raise GuardError(
            "NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set. List the TEST account ids that may be "
            "written to, for example NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567"
        )
    if str(account_id) not in allowed_ids:
        raise GuardError(f"account {account_id} is not in NEW_RELIC_ALLOWED_ACCOUNT_IDS")
    if confirm_account != account_id:
        raise GuardError("--confirm-account must repeat the account id to apply")


class NewRelicClient:
    def __init__(
        self,
        api_key: SecretStr,
        region: str = "us",
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if region not in HOSTS:
            raise GuardError(f"unknown region {region!r}; use one of {sorted(HOSTS)}")
        self._secrets = [api_key]
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=f"https://{HOSTS[region]}",
            headers={"API-Key": api_key.get_secret_value(), "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            transport=transport,
            verify=system_tls_context(),
        )

    def add_secrets(self, values: list[str]) -> None:
        """Have error text scrubbed of these values too (for example destination secrets)."""
        self._secrets.extend(SecretStr(v) for v in values if v)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "NewRelicClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        attempts = 1 if is_write else self._max_retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                resp = self._http.post("/graphql", json={"query": query, "variables": variables})
            except httpx.TransportError as exc:
                if last:
                    raise NewRelicAPIError(
                        redact(f"network error calling New Relic: {exc}", self._secrets)
                    ) from None
                self._wait(attempt)
                continue
            if resp.status_code == 200:
                return self._data(resp)
            if resp.status_code in RETRYABLE_STATUS and not last:
                self._wait(attempt)
                continue
            body = redact(resp.text[:MAX_ERROR_TEXT], self._secrets)
            raise NewRelicAPIError(f"New Relic returned HTTP {resp.status_code}: {body}")
        raise NewRelicAPIError("retries exhausted")  # pragma: no cover

    def _data(self, resp: httpx.Response) -> dict[str, Any]:
        body = resp.json()
        if body.get("errors"):
            messages = "; ".join(str(e.get("message", e)) for e in body["errors"])
            raise NewRelicAPIError(redact(messages[:MAX_ERROR_TEXT], self._secrets))
        data = body.get("data")
        if not isinstance(data, dict):
            raise NewRelicAPIError("response had no data")
        return data

    def _wait(self, attempt: int) -> None:
        delay = min(2.0**attempt, MAX_WAIT_SECONDS)
        log.warning("New Relic request failed; retrying in %.1fs", delay)
        self._sleep(delay)


def get_account_name(client: NewRelicClient, account_id: int) -> str:
    data = client.run(ACCOUNT_QUERY, {"accountId": account_id}, is_write=False)
    account = data["actor"]["account"]
    if not account:
        raise NewRelicAPIError(f"account {account_id} is not visible to this API key")
    return str(account["name"])


def ensure_policy(
    client: NewRelicClient,
    account_id: int,
    name: str,
    created: list[CreatedObject] | None = None,
) -> str:
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        data = client.run(
            POLICY_SEARCH,
            {"accountId": account_id, "name": name, "cursor": cursor},
            is_write=False,
        )
        search = data["actor"]["account"]["alerts"]["policiesSearch"]
        for policy in search["policies"]:
            if policy["name"] == name:
                return str(policy["id"])
        cursor = search.get("nextCursor")
        if not cursor:
            break
    data = client.run(
        POLICY_CREATE,
        {"accountId": account_id, "policy": {"name": name, "incidentPreference": "PER_CONDITION"}},
        is_write=True,
    )
    policy_id = str(data["alertsPolicyCreate"]["id"])
    if created is not None:
        created.append(CreatedObject(kind="policy", id=policy_id, name=name))
    return policy_id


def existing_conditions(client: NewRelicClient, account_id: int, policy_id: str) -> dict[str, str]:
    """Name -> id of the NRQL conditions in a policy."""
    found: dict[str, str] = {}
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        data = client.run(
            CONDITION_SEARCH,
            {"accountId": account_id, "policyId": policy_id, "cursor": cursor},
            is_write=False,
        )
        search = data["actor"]["account"]["alerts"]["nrqlConditionsSearch"]
        found.update((c["name"], str(c["id"])) for c in search["nrqlConditions"])
        cursor = search.get("nextCursor")
        if not cursor:
            break
    return found


def existing_condition_names(client: NewRelicClient, account_id: int, policy_id: str) -> set[str]:
    return set(existing_conditions(client, account_id, policy_id))


def existing_compound_names(client: NewRelicClient, account_id: int) -> set[str]:
    """Names of the account's compound conditions. A name found anywhere is skipped: skipping is
    the safe side of create-only."""
    data = client.run(COMPOUND_SEARCH, {"accountId": account_id}, is_write=False)
    items = data["actor"]["account"]["alerts"]["compoundConditions"]["items"] or []
    return {str(c["name"]) for c in items}


def _send(
    client: NewRelicClient, account_id: int, policy_id: str, item: PlanItem
) -> dict[str, Any]:
    """Fill in the account and policy and send the create mutation once (never retried)."""
    payload = copy.deepcopy(item.payload)
    variables = payload["variables"]
    if (variables["accountId"], variables["policyId"]) != (
        ACCOUNT_PLACEHOLDER,
        POLICY_PLACEHOLDER,
    ):
        raise GuardError("payload has unexpected account or policy values; refusing to send")
    variables["accountId"] = account_id
    variables["policyId"] = policy_id
    return client.run(payload["query"], variables, is_write=True)


def _create_condition(
    client: NewRelicClient,
    account_id: int,
    policy_id: str,
    item: PlanItem,
    existing: dict[str, str],
    result: ApplyResult,
) -> str | None:
    """Create one NRQL condition unless its name exists; its id (the existing one if skipped)."""
    if item.name in existing:
        result.skipped_existing.append(item.name)
        return existing[item.name]
    try:
        data = _send(client, account_id, policy_id, item)
    except NewRelicAPIError as exc:
        result.failed.append((item.name, str(exc)))
        return None
    result.created.append(item.name)
    if item.role == "part":
        result.enabled_parts.append(item.name)
    made = (
        data.get("alertsNrqlConditionStaticCreate")
        or data.get("alertsNrqlConditionBaselineCreate")
        or {}
    )
    if made.get("id") is None:
        return None
    result.objects.append(CreatedObject(kind="condition", id=str(made["id"]), name=item.name))
    return str(made["id"])


def _with_part_ids(item: PlanItem, ids: dict[str, str]) -> PlanItem | str:
    """The compound item with its parts' ids in place of the placeholders, or why it cannot be."""
    payload = copy.deepcopy(item.payload)
    components = payload["variables"]["condition"]["componentConditions"]
    if len(components) != len(item.components):
        raise GuardError("compound payload does not match its parts; refusing to send")
    for alias, entry, key in zip(COMPOUND_ALIASES, components, item.components, strict=False):
        if entry != {"id": component_placeholder(alias), "alias": alias}:
            raise GuardError("compound payload has unexpected part values; refusing to send")
        if key not in ids:
            return f"part {alias} of the compound condition was not created"
        entry["id"] = ids[key]
    return item.model_copy(update={"payload": payload})


def _create_compounds(
    client: NewRelicClient,
    account_id: int,
    policy_id: str,
    items: list[PlanItem],
    ids: dict[str, str],
    result: ApplyResult,
) -> None:
    """Create the compound conditions in the routed policy, after all their parts exist."""
    existing = existing_compound_names(client, account_id)
    for item in items:
        if item.name in existing:
            result.skipped_existing.append(item.name)
            continue
        filled = _with_part_ids(item, ids)
        if isinstance(filled, str):
            result.failed.append((item.name, filled))
            continue
        try:
            data = _send(client, account_id, policy_id, filled)
        except NewRelicAPIError as exc:
            result.failed.append((item.name, str(exc)))
            continue
        result.created.append(item.name)
        made = data.get("alertsCompoundConditionCreate") or {}
        if made.get("id") is not None:
            result.objects.append(
                CreatedObject(kind="compound", id=str(made["id"]), name=item.name)
            )


def apply_plan(
    client: NewRelicClient, account_id: int, policy_name: str, plan: Plan
) -> ApplyResult:
    """Create the plan's conditions: plain ones in `policy_name`, parts of compound conditions
    (enabled) in its "(compound parts)" policy, then the compound conditions in `policy_name`."""
    result = ApplyResult()
    policy_id = ensure_policy(client, account_id, policy_name, result.objects)
    existing = existing_conditions(client, account_id, policy_id)
    parts = [i for i in plan.items if i.role == "part"]
    ids: dict[str, str] = {}
    if parts:
        parts_id = ensure_policy(client, account_id, parts_policy_name(policy_name), result.objects)
        parts_existing = existing_conditions(client, account_id, parts_id)
        for item in parts:
            made = _create_condition(client, account_id, parts_id, item, parts_existing, result)
            if made is not None:
                ids[item.source_key] = made
    for item in plan.items:
        if item.role == "condition":
            _create_condition(client, account_id, policy_id, item, existing, result)
    compounds = [i for i in plan.items if i.role == "compound"]
    if compounds:
        _create_compounds(client, account_id, policy_id, compounds, ids, result)
    return result
