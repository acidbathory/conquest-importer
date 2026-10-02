"""Create converted conditions in a New Relic account via NerdGraph.

This is the only module that WRITES to New Relic. Safety design:
- The plan is rebuilt from the IR files and re-validated; payload files are not trusted.
- Reads are retried; create mutations are never retried (a retry could duplicate a condition).
- Re-running is safe: an existing policy is reused and conditions with the same name are skipped.
- Conditions are created disabled.
- Only official New Relic API hosts are used, TLS verification uses the OS trust store, and
  error text is redacted before it is shown.
"""

import copy
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from importer.emit.common import CannotEmit
from importer.emit.nerdgraph import ACCOUNT_PLACEHOLDER, POLICY_PLACEHOLDER, nerdgraph_payload
from importer.models.ir import IRMonitor
from importer.secrets import redact
from importer.tls import system_tls_context

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


class NewRelicAPIError(RuntimeError):
    """A New Relic API call failed."""


class GuardError(RuntimeError):
    """A safety check refused the operation."""


class PlanItem(BaseModel):
    source_id: int
    name: str
    nrql: str
    payload: dict[str, Any]


class Plan(BaseModel):
    items: list[PlanItem]
    skipped: int  # monitors that cannot be emitted (manual/unsupported/too long)


class ApplyResult(BaseModel):
    created: list[str] = []
    skipped_existing: list[str] = []
    failed: list[tuple[str, str]] = []


def printable(text: str, limit: int = 80) -> str:
    """Make untrusted text safe to print to a terminal (no control or escape characters)."""
    return "".join(c if c.isprintable() else "?" for c in text)[:limit]


def load_plan(ir_dir: Path) -> Plan:
    items: list[PlanItem] = []
    skipped = 0
    for path in sorted(ir_dir.glob("*.json")):
        monitor = IRMonitor.model_validate_json(path.read_text())
        try:
            payload = nerdgraph_payload(monitor)
        except CannotEmit:
            skipped += 1
            continue
        items.append(
            PlanItem(
                source_id=monitor.source_id,
                name=monitor.name,
                nrql=payload["variables"]["condition"]["nrql"]["query"],
                payload=payload,
            )
        )
    return Plan(items=items, skipped=skipped)


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


def ensure_policy(client: NewRelicClient, account_id: int, name: str) -> str:
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
    return str(data["alertsPolicyCreate"]["id"])


def existing_condition_names(client: NewRelicClient, account_id: int, policy_id: str) -> set[str]:
    names: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        data = client.run(
            CONDITION_SEARCH,
            {"accountId": account_id, "policyId": policy_id, "cursor": cursor},
            is_write=False,
        )
        search = data["actor"]["account"]["alerts"]["nrqlConditionsSearch"]
        names.update(c["name"] for c in search["nrqlConditions"])
        cursor = search.get("nextCursor")
        if not cursor:
            break
    return names


def apply_plan(
    client: NewRelicClient, account_id: int, policy_name: str, plan: Plan
) -> ApplyResult:
    result = ApplyResult()
    policy_id = ensure_policy(client, account_id, policy_name)
    existing = existing_condition_names(client, account_id, policy_id)
    for item in plan.items:
        if item.name in existing:
            result.skipped_existing.append(item.name)
            continue
        payload = copy.deepcopy(item.payload)
        variables = payload["variables"]
        if (variables["accountId"], variables["policyId"]) != (
            ACCOUNT_PLACEHOLDER,
            POLICY_PLACEHOLDER,
        ):
            raise GuardError("payload has unexpected account or policy values; refusing to send")
        variables["accountId"] = account_id
        variables["policyId"] = policy_id
        try:
            client.run(payload["query"], variables, is_write=True)
        except NewRelicAPIError as exc:
            result.failed.append((item.name, str(exc)))
            continue
        result.created.append(item.name)
    return result
