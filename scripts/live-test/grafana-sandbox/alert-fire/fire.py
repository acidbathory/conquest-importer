"""Alert firing test helpers. Usage: fire.py ENVFILE MANIFEST show|enable|send|nrql ...

show              read back the conditions listed in the manifest (read-only)
enable            set enabled=true on manifest conditions whose name starts with ckg- (the only write)
send cpu VALUE N  N points (10 s apart, ending now) of gauge ckg_synth_cpu = VALUE
drive LOG LOW_MIN HIGH_MIN RECOVER_MIN   real time, one point per 10 s per metric (cpu 30/95/30,
                  rate 1.2/3.0/1.2 per s); appends phase changes with UTC times to LOG
send rate PER_S N N points (10 s apart) of count ckg_synth_requests_total, interval.ms=10000
nrql "QUERY"      read-only NRQL
Secrets come from the env file / environment and are never printed. TLS verification stays on.
"""

import json
import sys
import time
import urllib.request

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import nrcommon


def gql(query: str, variables: dict) -> dict:
    import os

    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        nrcommon.GRAPHQL,
        data=body,
        method="POST",
        headers={"API-Key": os.environ["NEW_RELIC_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        out = json.load(resp)
    if out.get("errors"):
        raise SystemExit("graphql error: " + str(out["errors"])[:300])
    return out["data"]


def conditions(manifest: str) -> list[dict]:
    objs = json.load(open(manifest))["objects"]
    return [o for o in objs if o["kind"] == "condition" and o["name"].startswith("ckg-")]


SHOW = (
    "query($a:Int!,$id:ID!){actor{account(id:$a){alerts{nrqlCondition(id:$id){"
    "... on AlertsNrqlStaticCondition{id name enabled policyId valueFunction "
    "nrql{query} signal{aggregationWindow aggregationMethod aggregationDelay aggregationTimer "
    "fillOption fillValue slideBy} expiration{expirationDuration openViolationOnExpiration "
    "closeViolationsOnExpiration} violationTimeLimitSeconds "
    "terms{priority operator threshold thresholdDuration thresholdOccurrences}}}}}}}"
)
ENABLE = (
    "mutation($a:Int!,$id:ID!){alertsNrqlConditionStaticUpdate(accountId:$a,id:$id,"
    "condition:{enabled:true}){id name enabled}}"
)


def main() -> None:
    nrcommon.load_env(sys.argv[1])
    manifest, cmd = sys.argv[2], sys.argv[3]
    acct = int(nrcommon.account())
    if cmd == "show":
        for c in conditions(manifest):
            d = gql(SHOW, {"a": acct, "id": c["id"]})
            print(json.dumps(d["actor"]["account"]["alerts"]["nrqlCondition"], indent=1))
    elif cmd == "enable":
        for c in conditions(manifest):
            d = gql(ENABLE, {"a": acct, "id": c["id"]})
            print(d["alertsNrqlConditionStaticUpdate"])
    elif cmd == "send":
        kind, val, n = sys.argv[4], float(sys.argv[5]), int(sys.argv[6])
        end = int(time.time())
        pts = []
        for i in range(n):
            ts = end - (n - 1 - i) * 10
            if kind == "cpu":
                pts.append(
                    {
                        "name": "ckg_synth_cpu",
                        "type": "gauge",
                        "value": val,
                        "timestamp": ts,
                        "attributes": {"job": "ckg_synth"},
                    }
                )
            else:
                pts.append(
                    {
                        "name": "ckg_synth_requests_total",
                        "type": "count",
                        "value": val * 10,
                        "interval.ms": 10000,
                        "timestamp": ts,
                        "attributes": {"job": "ckg_synth", "code": "200"},
                    }
                )
        print(kind, val, n, "points ending", end)
        nrcommon.post_points(pts)
    elif cmd == "drive":
        log, mins = sys.argv[4], [float(x) for x in sys.argv[5:8]]
        phases = [
            ("low", 30, 1.2, mins[0]),
            ("high", 95, 3.0, mins[1]),
            ("recover", 30, 1.2, mins[2]),
        ]
        for name, cpu, rate, minutes in phases:
            with open(log, "a") as f:
                f.write(
                    f"{time.strftime('%H:%M:%S', time.gmtime())}Z phase {name} cpu={cpu} rate={rate}/s\n"
                )
            until = time.time() + minutes * 60
            while time.time() < until:
                ts = int(time.time())
                nrcommon.post_points(
                    [
                        {
                            "name": "ckg_synth_cpu",
                            "type": "gauge",
                            "value": cpu,
                            "timestamp": ts,
                            "attributes": {"job": "ckg_synth"},
                        },
                        {
                            "name": "ckg_synth_requests_total",
                            "type": "count",
                            "value": rate * 10,
                            "interval.ms": 10000,
                            "timestamp": ts,
                            "attributes": {"job": "ckg_synth", "code": "200"},
                        },
                    ]
                )
                time.sleep(10)
        with open(log, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S', time.gmtime())}Z done\n")
    elif cmd == "nrql":
        print(json.dumps(nrcommon.nrql(sys.argv[4]), indent=0))


main()
