"""Run every NRQL example of the remediation cookbook read-only against the sandbox account.

    SSL_CERT_FILE=/tmp/q/ca.pem uv run python scripts/improve/verify_cookbook.py [--only key]

What it does (reads only; nothing is created, changed or deleted):
  1. every example of kind nrql / nrql-full / nrql-alert goes to NerdGraph `nrql` (the query is a
     GraphQL variable). A query without its own time range gets `SINCE 1 day ago`; a {{variable}}
     is replaced by the example's `subst` literal.
  2. the value checks in VALUE_CHECKS run (two independent queries must agree).
  3. every documentation URL named by a method is fetched once over HTTPS.
The result goes to docs/improvement/cookbook-verification.json; `tests/test_cookbook_verified.py`
reads it, so a status in the data files cannot be claimed without a recorded run.

Keys come from the environment or the repository .env (parsed here, never printed). TLS
verification stays on; set SSL_CERT_FILE for a corporate CA.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from importer.core import cookbook  # noqa: E402

OUT = ROOT / "docs" / "improvement" / "cookbook-verification.json"
GRAPHQL = "https://api.newrelic.com/graphql"
GQL = "query($id:Int!,$q:Nrql!){actor{account(id:$id){nrql(query:$q,timeout:90){results}}}}"
VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
HAS_RANGE = re.compile(r"\bSINCE\b", re.IGNORECASE)


def load_env() -> None:
    env = ROOT.parents[2] / ".env"  # the main checkout holds .env (git-ignored)
    for path in (ROOT / ".env", env):
        if path.is_file():
            for line in path.read_text().splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip().isidentifier():
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def account() -> int:
    acct = os.environ["NEW_RELIC_ACCOUNT_ID"]
    allowed = os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "").replace(" ", "").split(",")
    if acct not in allowed:
        raise SystemExit("account not in NEW_RELIC_ALLOWED_ACCOUNT_IDS; stopping")
    return int(acct)


def nrql(query: str) -> list[dict[str, object]]:
    """Read-only NRQL. Raises RuntimeError with the first GraphQL error text."""
    body = json.dumps({"query": GQL, "variables": {"id": account(), "q": query}}).encode()
    req = urllib.request.Request(  # noqa: S310  (fixed https host)
        GRAPHQL,
        data=body,
        method="POST",
        headers={"API-Key": os.environ["NEW_RELIC_API_KEY"], "Content-Type": "application/json"},
    )
    for attempt in range(3):  # a handshake timeout on the corporate proxy is common
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                out = json.load(resp)
            break
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(3)
    if out.get("errors"):
        raise RuntimeError(str(out["errors"][0].get("message", out["errors"]))[:300])
    results: list[dict[str, object]] = out["data"]["actor"]["account"]["nrql"]["results"]
    return results


def runnable(example: cookbook.Example) -> str:
    """The text that is sent: variables replaced, a time range added when there is none."""
    subst = dict(example.subst)
    text = VARIABLE.sub(lambda m: subst.get(m.group(1), "'x'"), example.text)
    text = " ".join(text.split())
    if HAS_RANGE.search(text):
        return text
    # a fixed TIMESERIES bucket is limited to 366 buckets, so a shorter range keeps it legal
    fixed = re.search(r"TIMESERIES\s+\d", text, re.IGNORECASE)
    return f"{text} SINCE {'6 hours' if fixed else '1 day'} ago"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def number(rows: list[dict[str, object]], key: str) -> float:
    value = rows[0][key]
    assert isinstance(value, int | float)
    return float(value)


# --- value checks: two independent ways of getting the number must agree -----------------------
# Each returns (ok, note). Add one per method that is marked VERIFIED-VALUE.
ValueCheck = Callable[[], tuple[bool, str]]
SINCE = " SINCE 1 day ago"


def _sum_rows(query: str, key: str) -> float:
    return sum(float(r[key]) for r in nrql(query + SINCE) if isinstance(r.get(key), int | float))  # type: ignore[arg-type]


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def _nested_sum() -> tuple[bool, str]:
    got = number(nrql("SELECT sum(v) FROM (SELECT latest(kube_deployment_status_replicas) AS v FROM Metric FACET cluster, namespace, deployment LIMIT MAX)" + SINCE), "sum.v")
    by_hand = _sum_rows("SELECT latest(kube_deployment_status_replicas) FROM Metric FACET cluster, namespace, deployment, instance LIMIT MAX", "latest.kube_deployment_status_replicas")
    plain = number(nrql("SELECT sum(kube_deployment_status_replicas) FROM Metric" + SINCE), "sum.kube_deployment_status_replicas")
    return _close(got, by_hand), f"nested {got}, sum of per-series newest values {by_hand}, plain sum {plain}"


def _avg_card() -> tuple[bool, str]:
    got = nrql("SELECT average(kube_deployment_status_replicas) * cardinality(kube_deployment_status_replicas) FROM Metric" + SINCE)[0]
    value = float(next(iter(got.values())))  # type: ignore[arg-type]
    ref = number(nrql("SELECT sum(v) FROM (SELECT latest(kube_deployment_status_replicas) AS v FROM Metric FACET cluster, namespace, deployment LIMIT MAX)" + SINCE), "sum.v")
    return _close(value, ref), f"average*cardinality {value}, nested {ref} (steady gauge)"


def _where_value() -> tuple[bool, str]:
    ge = number(nrql("SELECT uniqueCount(node) FROM Metric WHERE metricName = 'kube_node_status_condition' AND condition = 'Ready' AND status = 'true' AND kube_node_status_condition >= 1" + SINCE), "uniqueCount.node")
    rows = nrql("SELECT latest(kube_node_status_condition) FROM Metric WHERE condition = 'Ready' AND status = 'true' FACET node LIMIT MAX" + SINCE)
    ready = sum(1 for r in rows if r.get("latest.kube_node_status_condition") == 1)
    return _close(ge, ready), f"WHERE >= 1 counted {ge}; newest value is 1 for {ready} nodes"


def _nested_filter() -> tuple[bool, str]:
    got = number(nrql("SELECT count(*) FROM (SELECT latest(kube_node_status_condition) AS v FROM Metric WHERE condition = 'Ready' AND status = 'true' FACET node LIMIT MAX) WHERE v = 1" + SINCE), "count")
    rows = nrql("SELECT latest(kube_node_status_condition) FROM Metric WHERE condition = 'Ready' AND status = 'true' FACET node LIMIT MAX" + SINCE)
    ready = sum(1 for r in rows if r.get("latest.kube_node_status_condition") == 1)
    return _close(got, ready), f"nested filter {got}, hand count {ready}"


def _filter_agg() -> tuple[bool, str]:
    row = nrql("SELECT filter(uniqueCount(deployment), WHERE kube_deployment_status_replicas_unavailable > 0) AS bad, uniqueCount(deployment) AS total FROM Metric WHERE metricName LIKE 'kube_deployment_status_replicas%'" + SINCE)[0]
    bad = number(nrql("SELECT uniqueCount(deployment) FROM Metric WHERE kube_deployment_status_replicas_unavailable > 0" + SINCE), "uniqueCount.deployment")
    total = number(nrql("SELECT uniqueCount(deployment) FROM Metric WHERE metricName LIKE 'kube_deployment_status_replicas%'" + SINCE), "uniqueCount.deployment")
    return _close(float(row["bad"]), bad) and _close(float(row["total"]), total), f"filter() bad={row['bad']} total={row['total']}; separate queries {bad}/{total}"  # type: ignore[arg-type]


def _join() -> tuple[bool, str]:
    joined = nrql("FROM Metric JOIN (FROM Metric SELECT latest(kube_pod_info) AS info WHERE metricName = 'kube_pod_info' FACET pod, node LIMIT MAX) ON pod SELECT uniqueCount(pod) WHERE metricName = 'kube_pod_status_phase' AND phase = 'Running' AND kube_pod_status_phase >= 1 FACET node LIMIT MAX" + SINCE)
    direct = nrql("SELECT uniqueCount(pod) FROM Metric WHERE metricName = 'kube_pod_info' FACET node LIMIT MAX" + SINCE)
    a = {str(r["node"]): r["uniqueCount.pod"] for r in joined}
    b = {str(r["node"]): r["uniqueCount.pod"] for r in direct}
    return a == b and bool(a), f"JOIN per node {a}; direct on the info metric {b}"


def _facet_total() -> tuple[bool, str]:
    rows = _sum_rows("SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric FACET instance, mode LIMIT MAX", "rate.sum.node_cpu_seconds_total")
    total = number(nrql("SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric" + SINCE), "rate.sum.node_cpu_seconds_total")
    return _close(rows, total), f"sum of facet rates {rows}, unfaceted {total}"


def _bucket_sums() -> tuple[bool, str]:
    whole = number(nrql("SELECT sum(nginx_ingress_controller_requests) FROM Metric" + SINCE), "sum.nginx_ingress_controller_requests")
    parts = _sum_rows("SELECT sum(nginx_ingress_controller_requests) FROM Metric TIMESERIES 1 hour", "sum.nginx_ingress_controller_requests")
    return _close(whole, parts), f"sum over 1 day {whole}, sum of hourly buckets {parts}"


VALUE_CHECKS: dict[str, ValueCheck] = {
    "sum-gauge/nested-latest": _nested_sum,
    "sum-gauge/average-times-cardinality": _avg_card,
    "comparison/where-on-value": _where_value,
    "comparison/nested-then-filter": _nested_filter,
    "comparison/filter-in-aggregate": _filter_agg,
    "joins/nrql-join": _join,
    "without/facet-remaining": _facet_total,
    "raw-counter/rate-of-sum": _bucket_sums,
    "raw-counter/sum-for-increase": _bucket_sums,
}



def fetch_status(url: str) -> int:
    """HTTP status of a GET; waits and retries when the site rate-limits (429)."""
    req = urllib.request.Request(url, headers={"User-Agent": "cookbook-link-check"})  # noqa: S310
    code = 0
    for attempt in range(5):
        time.sleep(2 + 6 * attempt)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
                return int(resp.status)
        except urllib.error.HTTPError as err:
            code = int(err.code)
            if code != 429:
                return code
        except urllib.error.URLError:
            return 0
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="only blockers whose key contains this text")
    parser.add_argument("--no-urls", action="store_true")
    args = parser.parse_args()
    load_env()
    previous = json.loads(OUT.read_text()) if OUT.is_file() else {}
    queries: dict[str, dict[str, object]] = dict(previous.get("nrql", {}))
    values: dict[str, dict[str, object]] = dict(previous.get("values", {}))
    urls: dict[str, int] = dict(previous.get("urls", {}))
    failed = 0
    for blocker in cookbook.blockers():
        if args.only not in blocker.key:
            continue
        for method in blocker.methods:
            for example in method.examples:
                if example.kind not in cookbook.NRQL_KINDS:
                    continue
                sent = runnable(example)
                try:
                    rows = nrql(sent)
                    queries[digest(sent)] = {"ok": True, "rows": len(rows), "where": method.id}
                except (RuntimeError, urllib.error.URLError) as err:
                    queries[digest(sent)] = {"ok": False, "error": str(err), "where": method.id}
                    failed += 1
                    print(f"FAIL {blocker.key}/{method.id}: {err}\n     {sent}")
            check = VALUE_CHECKS.get(f"{blocker.key}/{method.id}")
            if check is not None:
                ok, note = check()
                values[f"{blocker.key}/{method.id}"] = {"ok": ok, "note": note}
                failed += 0 if ok else 1
                print(f"{'ok  ' if ok else 'FAIL'} value {blocker.key}/{method.id}: {note}")
            if not args.no_urls:
                for url in method.sources:
                    if url not in urls or urls[url] != 200:
                        urls[url] = fetch_status(url)
                        if urls[url] != 200:
                            failed += 1
                            print(f"FAIL url {urls[url]}: {url}")
    result = {"checked_on": date.today().isoformat(), "nrql": queries, "values": values}
    OUT.write_text(json.dumps({**result, "urls": urls}, indent=1, sort_keys=True) + "\n")
    print(f"done; {failed} failure(s); wrote {OUT.relative_to(ROOT)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
