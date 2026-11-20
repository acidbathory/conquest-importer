"""Compound alert conditions in a New Relic SANDBOX: what does the API accept, reject and store?

A Grafana classic condition that joins tests on several queries with AND has no one-query NRQL
form. New Relic's compound conditions combine up to five standard conditions with AND, OR and NOT
(docs/research/newrelic-grafana-platform-analogues.md). Nothing about them was checked against the
API before. This script settles the questions a converter needs answered. Modes:
  plan (default)  prints every probe. No network, nothing created.
  --apply         creates the probes. Needs --confirm-account <id> that is also listed in
                  NEW_RELIC_ALLOWED_ACCOUNT_IDS.
  --cleanup       deletes everything recorded in the manifest. Same account guard.
Safety: every object is named `ckg-cc-...`; the component conditions must be enabled (the API
requires it), so they query an event type and an app name nothing writes and their threshold is
1e12, which nothing reaches; the policies have no workflow, so nothing is sent anywhere; the
compound conditions are created disabled unless a probe tests `enabled`; the manifest is written
after each creation so a cleanup works after an interruption. The API key comes from
NEW_RELIC_API_KEY (the repository .env is read if present) and is never printed or saved.
Usage (repository root):
  uv run python scripts/live-test/capability-check/compound_check.py                  # plan
  uv run python scripts/live-test/capability-check/compound_check.py --apply --confirm-account 123
  uv run python scripts/live-test/capability-check/compound_check.py --cleanup --confirm-account 123
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient, check_guards

PREFIX = "ckg-cc"
MANIFEST = Path("out/compound-check/manifest.json")
RESULTS = Path("out/compound-check/results.json")
NEVER = "SELECT count(*) FROM CkgCcNothing WHERE appName = 'ckg-cc-none'"
NEVER_FACETED = NEVER + " FACET host"

POLICY_MUTATION = (
    "mutation($accountId: Int!, $policy: AlertsPolicyInput!) {"
    " alertsPolicyCreate(accountId: $accountId, policy: $policy) { id name } }"
)
CONDITION_MUTATION = (
    "mutation($accountId: Int!, $policyId: ID!, $condition: AlertsNrqlConditionStaticInput!) {"
    " alertsNrqlConditionStaticCreate(accountId: $accountId, policyId: $policyId,"
    " condition: $condition) { id name enabled } }"
)
COMPOUND_MUTATION = (
    "mutation($accountId: Int!, $policyId: ID!, $c: AlertsCompoundConditionInput!) {"
    " alertsCompoundConditionCreate(accountId: $accountId, policyId: $policyId,"
    " compoundAlertCondition: $c) { id name enabled triggerExpression facetMatchingBehavior"
    " thresholdDuration policyId componentConditions { id alias } } }"
)
COMPOUND_DELETE = (
    "mutation($accountId: Int!, $id: ID!) {"
    " alertsCompoundConditionDelete(accountId: $accountId, id: $id) { id } }"
)
POLICY_DELETE = (
    "mutation($accountId: Int!, $id: ID!) { alertsPolicyDelete(accountId: $accountId, id: $id) {"
    " id } }"
)
LEFTOVERS = (
    "query($a: Int!) { actor { account(id: $a) { alerts {"
    ' nrqlConditionsSearch(searchCriteria: {queryLike: "CkgCcNothing"}) { nrqlConditions { id } }'
    ' policiesSearch(searchCriteria: {name: "ckg-cc"}) { policies { id name } }'
    " compoundConditions { items { id name } } } } } }"
)


@dataclass(frozen=True)
class Component:
    key: str
    nrql: str
    enabled: bool = True
    policy: str = "main"


@dataclass(frozen=True)
class Probe:
    id: str
    question: str
    components: tuple[str, ...]
    trigger: str
    facets: str = "FACETS_IGNORED"
    enabled: bool = False
    duration: int | None = 300
    expect: str = "accepted"


COMPONENTS = [
    Component("a", NEVER),
    Component("b", NEVER),
    Component("c", NEVER),
    Component("d", NEVER),
    Component("e", NEVER),
    Component("f", NEVER),
    Component("fa", NEVER_FACETED),
    Component("fb", NEVER_FACETED),
    Component("off", NEVER, enabled=False),
    Component("other", NEVER, policy="other"),
]

PROBES = [
    Probe("and", "two conditions joined by AND", ("a", "b"), "A AND B"),
    Probe("or", "two conditions joined by OR", ("a", "b"), "A OR B"),
    Probe("andnot", "AND NOT", ("a", "b"), "A AND NOT B"),
    Probe("paren", "parentheses, mixed AND/OR", ("a", "b", "c"), "(A AND B) OR C"),
    Probe(
        "five",
        "five components (the documented maximum)",
        ("a", "b", "c", "d", "e"),
        "A AND B AND C AND D AND E",
    ),
    Probe(
        "six",
        "six components",
        ("a", "b", "c", "d", "e", "f"),
        "A AND B AND C AND D AND E AND F",
        expect="rejected",
    ),
    Probe(
        "facets",
        "faceted components with FACETS_MATCH",
        ("fa", "fb"),
        "A AND B",
        facets="FACETS_MATCH",
    ),
    Probe(
        "mixedfacets",
        "a faceted and an unfaceted component with FACETS_MATCH",
        ("fa", "a"),
        "A AND B",
        facets="FACETS_MATCH",
    ),
    Probe("enabled", "the compound condition itself enabled", ("a", "b"), "A AND B", enabled=True),
    Probe("nodur", "no threshold duration", ("a", "b"), "A AND B", duration=None),
    Probe("disabledpart", "a disabled component", ("a", "off"), "A AND B", expect="rejected"),
    Probe("otherpolicy", "a component from another policy", ("a", "other"), "A AND B"),
    Probe("reuse", "the same component in a second compound condition", ("a", "b"), "A AND B"),
]


def component_input(c: Component) -> dict[str, Any]:
    return {
        "name": f"{PREFIX}-part-{c.key}",
        "enabled": c.enabled,
        "nrql": {"query": c.nrql},
        "signal": {
            "aggregationWindow": 60,
            "aggregationMethod": "EVENT_FLOW",
            "aggregationDelay": 120,
        },
        "valueFunction": "SINGLE_VALUE",
        "terms": [
            {
                "priority": "CRITICAL",
                "operator": "ABOVE",
                "threshold": 1e12,
                "thresholdDuration": 300,
                "thresholdOccurrences": "ALL",
            }
        ],
    }


def compound_input(p: Probe, ids: dict[str, str]) -> dict[str, Any]:
    aliases = "ABCDEFGHIJ"  # New Relic accepts only A to E (seen live)
    body: dict[str, Any] = {
        "name": f"{PREFIX}-{p.id}",
        "enabled": p.enabled,
        "triggerExpression": p.trigger,
        "facetMatchingBehavior": p.facets,
        "componentConditions": [
            {"id": ids[k], "alias": aliases[i]} for i, k in enumerate(p.components)
        ],
    }
    if p.duration is not None:
        body["thresholdDuration"] = p.duration
    return body


def print_plan() -> None:
    print("components (standard NRQL conditions, never reach their threshold):")
    for c in COMPONENTS:
        print(f"  {PREFIX}-part-{c.key}: enabled={c.enabled} policy={c.policy} {c.nrql}")
    print("compound probes:")
    for p in PROBES:
        print(f"  {p.id}: {p.question}: '{p.trigger}' {p.facets} expect {p.expect}")


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
    try:
        return True, client.run(query, variables, is_write=write)
    except NewRelicAPIError as exc:
        return False, str(exc)[:400]


def apply(client: NewRelicClient, account_id: int) -> None:
    if MANIFEST.exists():
        raise SystemExit("a manifest exists: run --cleanup first")
    manifest: dict[str, Any] = {
        "account": account_id,
        "policies": {},
        "components": {},
        "compounds": [],
    }
    for name in ("main", "other"):
        ok, data = attempt(
            client,
            POLICY_MUTATION,
            {
                "accountId": account_id,
                "policy": {"name": f"{PREFIX}-{name}", "incidentPreference": "PER_CONDITION"},
            },
            True,
        )
        if not ok:
            raise SystemExit(f"policy not created: {data}")
        manifest["policies"][name] = data["alertsPolicyCreate"]["id"]
        save(MANIFEST, manifest)
    for c in COMPONENTS:
        ok, data = attempt(
            client,
            CONDITION_MUTATION,
            {
                "accountId": account_id,
                "policyId": manifest["policies"][c.policy],
                "condition": component_input(c),
            },
            True,
        )
        if not ok:
            raise SystemExit(f"component {c.key} not created: {data}")
        manifest["components"][c.key] = data["alertsNrqlConditionStaticCreate"]["id"]
        save(MANIFEST, manifest)
    results = []
    for p in PROBES:
        ok, data = attempt(
            client,
            COMPOUND_MUTATION,
            {
                "accountId": account_id,
                "policyId": manifest["policies"]["main"],
                "c": compound_input(p, manifest["components"]),
            },
            True,
        )
        stored = data["alertsCompoundConditionCreate"] if ok else None
        if stored:
            manifest["compounds"].append({"id": stored["id"], "name": stored["name"]})
            save(MANIFEST, manifest)
        outcome = "accepted" if ok else "rejected"
        results.append(
            {
                "probe": p.id,
                "question": p.question,
                "expected": p.expect,
                "outcome": outcome,
                "stored": stored,
                "error": None if ok else data,
            }
        )
        mark = "as expected" if outcome == p.expect else "NOT AS EXPECTED"
        print(f"{p.id}: {outcome} ({mark})" + ("" if ok else f": {str(data)[:200]}"))
    save(RESULTS, results)
    print(f"results in {RESULTS}; run --cleanup next")


def gone(ok: bool, data: Any) -> bool:
    """Deleted now, or already gone (deleting a policy removes its compound conditions: seen live)."""
    return ok or "not found" in str(data).lower()


def cleanup(client: NewRelicClient, account_id: int) -> None:
    if not MANIFEST.exists():
        raise SystemExit("no manifest: nothing recorded to delete")
    manifest = json.loads(MANIFEST.read_text())
    if manifest.get("account") != account_id:
        raise SystemExit("the manifest belongs to another account; refusing")
    if any(not c["name"].startswith(PREFIX) for c in manifest["compounds"]):
        raise SystemExit("refusing to delete a non-probe object")
    clean = True
    for c in manifest["compounds"]:
        ok, data = attempt(client, COMPOUND_DELETE, {"accountId": account_id, "id": c["id"]}, True)
        print(
            f"delete compound {c['name']}: {'ok' if ok else 'already gone' if gone(ok, data) else data}"
        )
        clean = clean and gone(ok, data)
    for name, pid in manifest["policies"].items():
        ok, data = attempt(client, POLICY_DELETE, {"accountId": account_id, "id": pid}, True)
        print(
            f"delete policy {PREFIX}-{name} (and its conditions): {'ok' if ok else 'already gone' if gone(ok, data) else data}"
        )
        clean = clean and gone(ok, data)
    ok, data = attempt(client, LEFTOVERS, {"a": account_id}, False)
    if ok:
        alerts = data["actor"]["account"]["alerts"]
        conds = alerts["nrqlConditionsSearch"]["nrqlConditions"]
        pols = [p for p in alerts["policiesSearch"]["policies"] if p["name"].startswith(PREFIX)]
        comps = [c for c in alerts["compoundConditions"]["items"] if c["name"].startswith(PREFIX)]
        print(f"leftovers: conditions {len(conds)}, policies {len(pols)}, compounds {len(comps)}")
        clean = clean and not conds and not pols and not comps
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
        check_guards(
            account_id, args.confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
        )
    except GuardError as exc:
        raise SystemExit(f"guard: {exc}") from None
    with NewRelicClient(SecretStr(key), args.region) as client:
        (cleanup if args.cleanup else apply)(client, account_id)


if __name__ == "__main__":
    sys.exit(main())
