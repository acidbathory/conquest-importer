"""Capability checks in a New Relic SANDBOX: what does New Relic accept, reject or store?

Our conversions rest on documentation and offline tests. This script asks New Relic itself, with
disposable objects, the questions the documentation left open (see docs/research/
nr-capability-audit.md). It has three modes:

  plan (default)  prints every probe and what it would settle. No network, nothing created.
  --apply         creates the probes. Needs --confirm-account <id> that is also listed in
                  NEW_RELIC_ALLOWED_ACCOUNT_IDS.
  --cleanup       deletes everything recorded in the manifest. Same account guard.

Safety: every object is named `ckg-cap-...`; alert conditions are created DISABLED; the dashboard is
PRIVATE; no metric data is sent; the manifest is written after each creation so a cleanup works even
after an interruption; the read-only NRQL probes only run syntax checks over an empty window.
The API key is read from NEW_RELIC_API_KEY (the repository .env is read if present) and is never
printed or saved. TLS verification stays on (set SSL_CERT_FILE for a corporate CA).

Usage (repository root):
  uv run python scripts/live-test/capability-check/run.py                      # plan
  uv run python scripts/live-test/capability-check/run.py --apply --confirm-account 123
  uv run python scripts/live-test/capability-check/run.py --cleanup --confirm-account 123
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient, check_guards

PREFIX = "ckg-cap"
POLICY_NAME = f"{PREFIX}-policy"
MANIFEST = Path("out/capability-check/manifest.json")
RESULTS = Path("out/capability-check/results.json")
METRIC = "ckg_cap_gauge"
BUCKET = "ckg_cap_latency_bucket"

CONDITION_MUTATION = (
    "mutation($accountId: Int!, $policyId: ID!, $condition: AlertsNrqlConditionStaticInput!) {"
    " alertsNrqlConditionStaticCreate(accountId: $accountId, policyId: $policyId,"
    " condition: $condition) { id name } }"
)
POLICY_MUTATION = (
    "mutation($accountId: Int!, $policy: AlertsPolicyInput!) {"
    " alertsPolicyCreate(accountId: $accountId, policy: $policy) { id name } }"
)
DASHBOARD_MUTATION = (
    "mutation($accountId: Int!, $dashboard: DashboardInput!) {"
    " dashboardCreate(accountId: $accountId, dashboard: $dashboard) {"
    " entityResult { guid name } errors { description type } } }"
)
DASHBOARD_READ = (
    "query($guid: EntityGuid!) { actor { entity(guid: $guid) {"
    " ... on DashboardEntity { name pages { name widgets { title rawConfiguration } } } } } }"
)
NRQL_READ = (
    "query($accountId: Int!, $nrql: Nrql!) { actor { account(id: $accountId) {"
    " nrql(query: $nrql) { results } } } }"
)
LEFTOVERS = (
    "query($a: Int!) { actor { account(id: $a) { alerts {"
    ' nrqlConditionsSearch(searchCriteria: {queryLike: "ckg-cap"}) { nrqlConditions { id name } }'
    ' policiesSearch(searchCriteria: {name: "ckg-cap-policy"}) { policies { id } } } } } }'
)
POLICY_DELETE = (
    "mutation($accountId: Int!, $id: ID!) { alertsPolicyDelete(accountId: $accountId, id: $id) {"
    " id } }"
)
DASHBOARD_DELETE = (
    "mutation($guid: EntityGuid!) { dashboardDelete(guid: $guid) { status errors {"
    " description } } }"
)


@dataclass
class Probe:
    id: str
    kind: str  # condition | nrql | dashboard
    settles: str
    expect: str
    nrql: str = ""
    operator: str = "ABOVE"
    options: dict[str, Any] = field(default_factory=dict)


def conditions() -> list[Probe]:
    g, b = METRIC, BUCKET
    nested = f"SELECT sum(v) FROM (SELECT latest({g}) AS v FROM Metric FACET ckg_host)"
    return [
        Probe(
            "c00-control",
            "condition",
            "the method itself works",
            "accepted",
            f"SELECT average({g}) FROM Metric",
        ),
        Probe(
            "c01-nested-sum",
            "condition",
            "nested sum of latest per series in a condition",
            "accepted",
            nested,
        ),
        Probe(
            "c02-bucketpercentile",
            "condition",
            "bucketPercentile in a condition",
            "unknown (docs silent)",
            f"SELECT bucketPercentile({b}, 95) FROM Metric",
        ),
        Probe(
            "c03-clamp-min",
            "condition",
            "clamp_min in a condition",
            "unknown (docs silent)",
            f"SELECT clamp_min(average({g}), 1) FROM Metric",
        ),
        Probe(
            "c04-if",
            "condition",
            "if(cond, a, b) in a condition",
            "unknown (docs silent)",
            f"SELECT if(average({g}) > 5, 1, 0) FROM Metric",
        ),
        Probe(
            "c05-derivative",
            "condition",
            "derivative in a condition",
            "unknown (docs silent)",
            f"SELECT derivative(sum({g}), 1 minute) FROM Metric",
        ),
        Probe(
            "c06-rlike",
            "condition",
            "RLIKE in a WHERE of a condition",
            "accepted",
            f"SELECT average({g}) FROM Metric WHERE ckg_host RLIKE 'web-.*'",
        ),
        Probe(
            "c07-not-rlike",
            "condition",
            "NOT RLIKE in a WHERE of a condition",
            "accepted",
            f"SELECT average({g}) FROM Metric WHERE ckg_host NOT RLIKE 'web-.*'",
        ),
        Probe(
            "c08-equals",
            "condition",
            "operator EQUALS",
            "unknown (docs show ABOVE/BELOW)",
            f"SELECT average({g}) FROM Metric",
            operator="EQUALS",
        ),
        Probe(
            "c09-not-equals",
            "condition",
            "operator NOT_EQUALS",
            "unknown",
            f"SELECT average({g}) FROM Metric",
            operator="NOT_EQUALS",
        ),
        Probe(
            "c10-above-or-equals",
            "condition",
            "operator ABOVE_OR_EQUALS",
            "unknown",
            f"SELECT average({g}) FROM Metric",
            operator="ABOVE_OR_EQUALS",
        ),
        Probe(
            "c11-below-or-equals",
            "condition",
            "operator BELOW_OR_EQUALS",
            "unknown",
            f"SELECT average({g}) FROM Metric",
            operator="BELOW_OR_EQUALS",
        ),
        Probe(
            "c12-cardinality",
            "condition",
            "cardinality in a condition (docs: unsupported)",
            "REJECTED",
            f"SELECT average({g}) * cardinality({g}) FROM Metric",
        ),
        Probe(
            "c13-compare-with",
            "condition",
            "COMPARE WITH in a condition (docs: incompatible)",
            "REJECTED",
            f"SELECT average({g}) FROM Metric COMPARE WITH 1 week ago",
        ),
        Probe(
            "c14-negative-control",
            "condition",
            "a malformed query is reported as rejected",
            "REJECTED",
            "SELECT average( FROM Metric",
        ),
    ]


def nrql_probes() -> list[Probe]:
    g, b = METRIC, BUCKET
    window = "SINCE 5 minutes ago"
    return [
        Probe(
            "n01-clamp-min",
            "nrql",
            "clamp_min in a dashboard query",
            "accepted",
            f"SELECT clamp_min(average({g}), 1) FROM Metric {window}",
        ),
        Probe(
            "n02-nested-widget",
            "nrql",
            "nested aggregation in a dashboard query",
            "accepted",
            f"SELECT sum(v) FROM (SELECT latest({g}) AS v FROM Metric FACET ckg_host) {window}",
        ),
        Probe(
            "n03-cardinality",
            "nrql",
            "average * cardinality in a dashboard query",
            "accepted",
            f"SELECT average({g}) * cardinality({g}) FROM Metric FACET ckg_host {window}",
        ),
        Probe(
            "n04-bucketpercentile",
            "nrql",
            "bucketPercentile in a dashboard query",
            "accepted",
            f"SELECT bucketPercentile({b}, 95) FROM Metric {window}",
        ),
        Probe(
            "n05-rlike",
            "nrql",
            "RLIKE in a dashboard query",
            "accepted",
            f"SELECT average({g}) FROM Metric WHERE ckg_host RLIKE 'web-.*' {window}",
        ),
        Probe(
            "n06-if",
            "nrql",
            "if() in a dashboard query",
            "accepted",
            f"SELECT if(average({g}) > 5, 1, 0) FROM Metric {window}",
        ),
        Probe(
            "n07-quoted-wildcard",
            "nrql",
            "LIKE with a % wildcard (what a variable would fill)",
            "accepted",
            f"SELECT average({g}) FROM Metric WHERE ckg_host LIKE '%web%' {window}",
        ),
    ]


def dashboard_probe() -> Probe:
    return Probe(
        "d01-widget-options",
        "dashboard",
        "ignore-time-picker field name on a widget, variable field shapes, a variable inside "
        "quotes, and whether our emitter's variable and widget shapes are accepted",
        "unknown: this reads the dashboard back and lists which keys New Relic kept",
    )


def condition_input(p: Probe) -> dict[str, Any]:
    return {
        "name": f"{PREFIX}-{p.id}",
        "enabled": False,
        "nrql": {"query": p.nrql},
        "signal": {
            "aggregationWindow": 60,
            "aggregationMethod": "EVENT_FLOW",
            "aggregationDelay": 120,
        },
        "valueFunction": "SINGLE_VALUE",
        "terms": [
            {
                "priority": "CRITICAL",
                "operator": p.operator,
                "threshold": 1.0,
                "thresholdDuration": 300,
                "thresholdOccurrences": "ALL",
            }
        ],
    }


def dashboard_input(account_id: int) -> dict[str, Any]:
    q = f"SELECT average({METRIC}) FROM Metric"
    widgets = [
        ("since-plain", f"{q} SINCE 5 minutes ago", {}),
        (
            "since-ignore-time-picker",
            f"{q} SINCE 5 minutes ago",
            {"platformOptions": {"ignoreTimeRange": True}},
        ),
        ("since-ignore-top-level", f"{q} SINCE 5 minutes ago", {"ignoreTimeRange": True}),
        ("variable-in-quotes", f"{q} WHERE ckg_host LIKE '%{{{{v}}}}%'", {}),
        ("variable-whole", f"{q} WHERE ckg_host = {{{{v}}}}", {}),
    ]
    out_widgets = []
    for n, (title, query, extra) in enumerate(widgets):
        raw: dict[str, Any] = {"nrqlQueries": [{"accountIds": [account_id], "query": query}]}
        raw.update(extra)
        out_widgets.append(
            {
                "title": f"{PREFIX}-{title}",
                "layout": {
                    "column": 1 + (n % 3) * 4,
                    "row": 1 + (n // 3) * 3,
                    "width": 4,
                    "height": 3,
                },
                "visualization": {"id": "viz.billboard"},
                "rawConfiguration": raw,
            }
        )
    return {
        "name": f"{PREFIX}-dashboard",
        "description": "Disposable capability probe. Safe to delete.",
        "permissions": "PRIVATE",
        "pages": [{"name": "probe", "widgets": out_widgets}],
        "variables": [
            {
                "name": "v",
                "title": "v",
                "type": "STRING",
                "isMultiSelection": False,
                "replacementStrategy": "STRING",
                "defaultValues": [{"value": {"string": "*"}}],
            }
        ],
    }


def print_plan() -> None:
    print("PLAN (nothing is sent in this mode)\n")
    print(f"1. one empty alert policy named {POLICY_NAME}")
    for p in conditions():
        print(f"   - {PREFIX}-{p.id}  [{p.operator}, DISABLED]  settles: {p.settles}")
        print(f"       query: {p.nrql}")
        print(f"       expect: {p.expect}")
    print("\n2. read-only NRQL syntax checks over an empty 5 minute window (nothing is created)")
    for p in nrql_probes():
        print(f"   - {p.id}  settles: {p.settles}\n       query: {p.nrql}")
    d = dashboard_probe()
    print(f"\n3. one PRIVATE dashboard {PREFIX}-dashboard with 5 widgets and one variable")
    print(f"   settles: {d.settles}")
    print("   widgets: " + ", ".join(w["title"] for w in dashboard_input(0)["pages"][0]["widgets"]))
    print("\n4. read the dashboard back and report which widget keys New Relic kept")
    print("\nCleanup: --cleanup deletes the conditions, the policy and the dashboard.")
    print("No metric data is sent. No existing object is read for change or touched.")


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().isidentifier():
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def attempt(client: NewRelicClient, query: str, variables: dict[str, Any], write: bool) -> Any:
    """(accepted, data or error text). New Relic's reason is kept; secrets are scrubbed."""
    try:
        return True, client.run(query, variables, is_write=write)
    except NewRelicAPIError as exc:
        return False, str(exc)[:400]


def apply(client: NewRelicClient, account_id: int) -> None:
    manifest: dict[str, Any] = {
        "account": account_id,
        "policy": None,
        "conditions": [],
        "dashboards": [],
    }
    results: list[dict[str, Any]] = []
    ok, data = attempt(
        client,
        POLICY_MUTATION,
        {
            "accountId": account_id,
            "policy": {"name": POLICY_NAME, "incidentPreference": "PER_CONDITION"},
        },
        True,
    )
    if not ok:
        raise SystemExit(f"could not create the policy: {data}")
    policy_id = str(data["alertsPolicyCreate"]["id"])
    manifest["policy"] = policy_id
    save(MANIFEST, manifest)
    for p in conditions():
        ok, data = attempt(
            client,
            CONDITION_MUTATION,
            {"accountId": account_id, "policyId": policy_id, "condition": condition_input(p)},
            True,
        )
        row = {"probe": p.id, "settles": p.settles, "expected": p.expect, "accepted": ok}
        if ok:
            created = data["alertsNrqlConditionStaticCreate"]
            manifest["conditions"].append({"id": str(created["id"]), "name": created["name"]})
            save(MANIFEST, manifest)
        else:
            row["reason"] = data
        results.append(row)
        print(f"{p.id:24} {'ACCEPTED' if ok else 'REJECTED'}  (expected {p.expect})")
    for p in nrql_probes():
        ok, data = attempt(client, NRQL_READ, {"accountId": account_id, "nrql": p.nrql}, False)
        row = {"probe": p.id, "settles": p.settles, "expected": p.expect, "accepted": ok}
        if not ok:
            row["reason"] = data
        results.append(row)
        print(f"{p.id:24} {'ACCEPTED' if ok else 'REJECTED'}  (expected {p.expect})")
    ok, data = attempt(
        client,
        DASHBOARD_MUTATION,
        {"accountId": account_id, "dashboard": dashboard_input(account_id)},
        True,
    )
    errors = (data or {}).get("dashboardCreate", {}).get("errors") if ok else None
    drow: dict[str, Any] = {"probe": "d01-widget-options", "accepted": ok and not errors}
    if ok and not errors:
        guid = data["dashboardCreate"]["entityResult"]["guid"]
        manifest["dashboards"].append({"guid": guid, "name": f"{PREFIX}-dashboard"})
        save(MANIFEST, manifest)
        ok2, back = attempt(client, DASHBOARD_READ, {"guid": guid}, False)
        if ok2:
            kept = []
            for w in back["actor"]["entity"]["pages"][0]["widgets"]:
                keys = sorted(w["rawConfiguration"].keys())
                kept.append({"widget": w["title"], "kept_keys": keys, "raw": w["rawConfiguration"]})
            drow["read_back"] = kept
            for k in kept:
                print(f"d01 {k['widget']:34} kept keys: {k['kept_keys']}")
        else:
            drow["read_back_error"] = back
    else:
        drow["reason"] = errors or data
    results.append(drow)
    print(f"d01-widget-options       {'ACCEPTED' if drow['accepted'] else 'REJECTED'}")
    save(RESULTS, results)
    print(f"\nresults: {RESULTS}   manifest (for --cleanup): {MANIFEST}")


def cleanup(client: NewRelicClient, account_id: int) -> None:
    """Delete the dashboard and the policy (deleting a policy removes its conditions: seen live),
    then search for leftovers. The manifest is kept unless the search comes back clean."""
    if not MANIFEST.exists():
        raise SystemExit("no manifest: nothing recorded to delete")
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("account") != account_id:
        raise SystemExit("the manifest belongs to another account; refusing")
    for item in [*manifest["conditions"], *manifest["dashboards"]]:
        if not item["name"].startswith(PREFIX):
            raise SystemExit(f"refusing to delete a non-probe object: {item['name']}")
    clean = True
    for d in manifest["dashboards"]:
        ok, data = attempt(client, DASHBOARD_DELETE, {"guid": d["guid"]}, True)
        print(f"delete dashboard {d['name']}: {'ok' if ok else data}")
        clean = clean and ok
    if manifest.get("policy"):
        ok, data = attempt(
            client, POLICY_DELETE, {"accountId": account_id, "id": manifest["policy"]}, True
        )
        print(f"delete policy {POLICY_NAME} (and its conditions): {'ok' if ok else data}")
        clean = clean and ok
    ok, data = attempt(client, LEFTOVERS, {"a": account_id}, False)
    if ok:
        alerts = data["actor"]["account"]["alerts"]
        left = alerts["nrqlConditionsSearch"]["nrqlConditions"]
        pols = alerts["policiesSearch"]["policies"]
        print(f"leftover {PREFIX} conditions: {len(left)}; policies: {len(pols)}")
        clean = clean and not left and not pols
    else:
        print(f"could not check for leftovers: {data}")
        clean = False
    if clean:
        MANIFEST.unlink()
        print("cleanup complete; manifest removed")
    else:
        print("cleanup NOT complete; manifest kept so it can be run again")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--confirm-account", type=int)
    ap.add_argument("--region", default="us")
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    args = ap.parse_args()
    if not (args.apply or args.cleanup):
        print_plan()
        return
    load_env(args.env_file)
    key = os.environ.get("NEW_RELIC_API_KEY")
    account = os.environ.get("NEW_RELIC_ACCOUNT_ID")
    if not key or not account:
        raise SystemExit("NEW_RELIC_API_KEY and NEW_RELIC_ACCOUNT_ID must be set")
    account_id = int(account)
    try:
        allowed = os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
        check_guards(account_id, args.confirm_account, allowed)
    except GuardError as exc:
        raise SystemExit(f"guard: {exc}") from None
    with NewRelicClient(SecretStr(key), args.region) as client:
        (cleanup if args.cleanup else apply)(client, account_id)


if __name__ == "__main__":
    sys.exit(main())
