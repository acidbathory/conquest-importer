"""Read the ckg- estate back with READ-ONLY GETs and save sanitised real responses as fixtures.

Uses the list endpoints and api-versions of docs/research/azure-monitor-alerting.md, plus a few
older versions to see what differs. Only ckg- objects are kept from a list. The subscription id,
tenant id, principal ids and every other GUID become placeholders; query values named sig, code,
token, key or sv-sig are masked.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from arm import Arm, ArmError, load_env

GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
ZERO = "00000000-0000-0000-0000-000000000000"
SECRET_QUERY = re.compile(r"([?&](?:sig|code|token|key|sv-sig|signature)=)[^&\"\s]+", re.IGNORECASE)

# kind -> (list path template, api-version from the research doc, alternative versions to compare)
LISTS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "metric_alerts": ("Microsoft.Insights/metricAlerts", "2026-01-01", ("2018-03-01",)),
    "scheduled_query_rules": (
        "Microsoft.Insights/scheduledQueryRules",
        "2026-03-01",
        ("2023-12-01", "2021-08-01"),
    ),
    "activity_log_alerts": (
        "Microsoft.Insights/activityLogAlerts",
        "2026-01-01",
        ("2020-10-01",),
    ),
    "alert_processing_rules": (
        "Microsoft.AlertsManagement/actionRules",
        "2021-08-08",
        ("2019-05-05-preview",),
    ),
    "action_groups": ("Microsoft.Insights/actionGroups", "2023-01-01", ("2021-09-01",)),
    "prometheus_rule_groups": (
        "Microsoft.AlertsManagement/prometheusRuleGroups",
        "2023-03-01",
        (),
    ),
    "smart_detector_alert_rules": (
        "microsoft.alertsManagement/smartDetectorAlertRules",
        "2021-04-01",
        (),
    ),
    "storage_accounts": ("Microsoft.Storage/storageAccounts", "2023-05-01", ()),
    "workspaces": ("Microsoft.OperationalInsights/workspaces", "2022-10-01", ()),
}


def only_ours(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [i for i in items if str(i.get("name", "")).startswith(("ckg-", "ckgstore"))]


def sanitise(doc: Any, sub: str, tenant: str) -> Any:
    """Return a copy with ids replaced by placeholders and secret-looking query values masked."""
    seen: dict[str, str] = {}

    def guid(match: re.Match[str]) -> str:
        value = match.group(0)
        if value == ZERO:
            return value
        return seen.setdefault(value.lower(), f"GUID-{len(seen) + 1:02d}")

    def text(s: str) -> str:
        s = re.sub(re.escape(sub), "SUBSCRIPTION_ID", s, flags=re.IGNORECASE)
        s = re.sub(re.escape(tenant), "TENANT_ID", s, flags=re.IGNORECASE)
        s = re.sub(r"(?i)/resourceGroups/azure-signup-rg", "/resourceGroups/RESOURCE_GROUP", s)
        s = SECRET_QUERY.sub(r"\1MASKED", s)
        return GUID.sub(guid, s)

    def walk(o: Any) -> Any:
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return text(o) if isinstance(o, str) else o

    return walk(doc)


def save(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")


def read_all(arm: Arm, tenant: str, out: Path) -> dict[str, Any]:
    sub = arm.subscription
    meta: dict[str, Any] = {"calls": []}
    for kind, (tail, api, alts) in LISTS.items():
        for version in (api, *alts):
            path = f"/subscriptions/{sub}/providers/{tail}"
            record: dict[str, Any] = {"kind": kind, "api": version, "path": tail}
            try:
                page = arm.get(path, version)
            except ArmError as err:
                record.update(
                    status=err.status, code=err.code, message=sanitise(str(err), sub, tenant)
                )
                meta["calls"].append(record)
                continue
            raw = page.get("value", [])
            record.update(
                status=200,
                items_total=len(raw),
                items_ckg=len(only_ours(raw)),
                keys=sorted(page),
                has_next_link="nextLink" in page,
            )
            meta["calls"].append(record)
            kept = {**page, "value": only_ours(raw)}
            save(out / kind / f"{version}__list.json", sanitise(kept, sub, tenant))
            if version == api and kept["value"]:
                first = kept["value"][0]
                single = arm.get(first["id"], version)
                save(
                    out / kind / f"{version}__get_{first['name']}.json",
                    sanitise(single, sub, tenant),
                )
    save(out / "_meta.json", meta)
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    ap.add_argument("--out", type=Path, default=Path("tests/fixtures/azure-live"))
    a = ap.parse_args(argv)
    env = load_env(a.env_file)
    arm = Arm(env)
    try:
        meta = read_all(arm, env["AZURE_TENANT_ID"], a.out)
    finally:
        arm.close()
    for c in meta["calls"]:
        print(c["kind"], c["api"], c.get("status"), c.get("items_ckg", c.get("code", "")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
