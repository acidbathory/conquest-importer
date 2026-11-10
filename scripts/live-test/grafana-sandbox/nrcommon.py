"""Shared helpers for the counter round trip scripts: env, Metric API post (capped), NRQL read.

Secrets come from the environment (or the repository .env, parsed here, never printed). Every
datapoint carries ckg_test=true. TLS verification stays on (set SSL_CERT_FILE for a corporate CA).
"""

import json
import os
import urllib.request
from pathlib import Path

METRIC_API = "https://metric-api.newrelic.com/metric/v1"
GRAPHQL = "https://api.newrelic.com/graphql"
MAX_POINTS_PER_POST = 2000
MAX_POINTS_TOTAL = 20000


def load_env(path: str) -> None:
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip().isidentifier():
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def account() -> str:
    acct = os.environ["NEW_RELIC_ACCOUNT_ID"]
    allowed = os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "").replace(" ", "").split(",")
    if acct not in allowed:
        raise SystemExit("account not in NEW_RELIC_ALLOWED_ACCOUNT_IDS; stopping")
    return acct


def post_points(points: list[dict]) -> None:
    account()
    if len(points) > MAX_POINTS_TOTAL:
        raise SystemExit("too many points")
    for p in points:
        p["attributes"]["ckg_test"] = "true"
    for i in range(0, len(points), MAX_POINTS_PER_POST):
        body = json.dumps([{"metrics": points[i : i + MAX_POINTS_PER_POST]}]).encode()
        req = urllib.request.Request(
            METRIC_API,
            data=body,
            method="POST",
            headers={
                "Api-Key": os.environ["NEW_RELIC_LICENSE_KEY"],
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("metric api:", resp.status)


def nrql(query: str) -> list[dict]:
    """Read-only NRQL through NerdGraph (query passed as a GraphQL variable)."""
    gql = "query($id:Int!,$q:Nrql!){actor{account(id:$id){nrql(query:$q,timeout:60){results}}}}"
    body = json.dumps({"query": gql, "variables": {"id": int(account()), "q": query}}).encode()
    req = urllib.request.Request(
        GRAPHQL,
        data=body,
        method="POST",
        headers={"API-Key": os.environ["NEW_RELIC_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        out = json.load(resp)
    if out.get("errors"):
        return [{"error": str(out["errors"])[:300]}]
    return out["data"]["actor"]["account"]["nrql"]["results"]
