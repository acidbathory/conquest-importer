"""Create converted dashboards in a New Relic account via NerdGraph (`dashboardCreate`).

Same safety design as apply.py: the plan is rebuilt from the IR files, writes are sent once and
never retried, an existing dashboard with the same name is skipped (never updated), dashboards are
created PRIVATE, and the guid of everything created is returned for the manifest.
"""

import copy
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from importer.core.apply import ApplyResult, GuardError, NewRelicAPIError, NewRelicClient
from importer.core.dashboard_ir import IRDashboard
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import ACCOUNT_PLACEHOLDER, nerdgraph_dashboard
from importer.core.emit.nrql import quote_string
from importer.core.manifest import CreatedObject

DASHBOARD_SEARCH = (
    "query($q: String!) { actor { entitySearch(query: $q) "
    "{ results { entities { guid name } } } } }"
)


class DashboardItem(BaseModel):
    name: str
    widgets: int
    payload: dict[str, Any]


class DashboardPlan(BaseModel):
    items: list[DashboardItem]
    skipped: int


def load_dashboard_plan(ir_dir: Path) -> DashboardPlan:
    items: list[DashboardItem] = []
    skipped = 0
    for path in sorted(ir_dir.glob("*.json")):
        dashboard = IRDashboard.model_validate_json(path.read_text())
        try:
            payload = nerdgraph_dashboard(dashboard)
        except CannotEmit:
            skipped += 1
            continue
        page = payload["variables"]["dashboard"]["pages"][0]
        items.append(
            DashboardItem(
                name=payload["variables"]["dashboard"]["name"],
                widgets=len(page["widgets"]),
                payload=payload,
            )
        )
    return DashboardPlan(items=items, skipped=skipped)


def dashboard_exists(client: NewRelicClient, name: str) -> bool:
    query = f"type = 'DASHBOARD' AND name = {quote_string(name)}"
    data = client.run(DASHBOARD_SEARCH, {"q": query}, is_write=False)
    entities = data["actor"]["entitySearch"]["results"]["entities"]
    return any(e["name"] == name for e in entities)


def with_account(payload: dict[str, Any], account_id: int) -> dict[str, Any]:
    """Return a copy with the account placeholder replaced; refuse anything unexpected."""
    out = copy.deepcopy(payload)
    variables = out["variables"]
    if variables.get("accountId") != ACCOUNT_PLACEHOLDER:
        raise GuardError("payload has an unexpected account value; refusing to send")
    variables["accountId"] = account_id
    for page in variables["dashboard"]["pages"]:
        for widget in page["widgets"]:
            for q in widget["rawConfiguration"].get("nrqlQueries", []):
                if q.get("accountIds") != [ACCOUNT_PLACEHOLDER]:
                    raise GuardError("widget has an unexpected account value; refusing to send")
                q["accountIds"] = [account_id]
    for variable in variables["dashboard"].get("variables", []):
        query = variable.get("nrqlQuery")
        if query is None:
            continue
        # A variable pinned to a source account keeps its integer ids; only the placeholder moves.
        ids = query["accountIds"]
        if ids == [ACCOUNT_PLACEHOLDER]:
            query["accountIds"] = [account_id]
        elif not all(isinstance(i, int) for i in ids):
            raise GuardError("variable has an unexpected account value; refusing to send")
    return out


def apply_dashboards(client: NewRelicClient, account_id: int, plan: DashboardPlan) -> ApplyResult:
    result = ApplyResult()
    for item in plan.items:
        if dashboard_exists(client, item.name):
            result.skipped_existing.append(item.name)
            continue
        payload = with_account(item.payload, account_id)
        try:
            data = client.run(payload["query"], payload["variables"], is_write=True)
        except NewRelicAPIError as exc:
            result.failed.append((item.name, str(exc)))
            continue
        created = data.get("dashboardCreate") or {}
        errors = created.get("errors") or []
        if errors:
            text = "; ".join(str(e.get("description", e)) for e in errors)[:300]
            result.failed.append((item.name, text))
            continue
        result.created.append(item.name)
        guid = (created.get("entityResult") or {}).get("guid")
        if guid:
            result.objects.append(CreatedObject(kind="dashboard", id=str(guid), name=item.name))
    return result
