"""Data round in a New Relic SANDBOX: do the NRQL forms we emit EVALUATE correctly on real data?

The capability session proved New Relic accepts these forms. This sends a few synthetic series and
runs read-only NRQL over them, comparing each result with a value computed here from the same series
(the PromQL answer). What it settles: RLIKE as a full-string match, how a missing attribute (NULL)
behaves under RLIKE and NOT RLIKE, `average(x) * cardinality(x)` and the nested sum of the latest value
per series against the true sum, and the values of `clamp_min` and `if()`.
Already settled by the telemetry rig over real Prometheus remote write (not repeated): `_bucket`
keeps `le`, `_sum` is a summary, bucketPercentile and rate(sum(_sum)) match Prometheus.

Modes:
  plan (default)   prints the series and every query with its expected value. No network.
  --run            sends the points (Metric API), waits for them, then evaluates. Needs
                   --confirm-account <id> that is also in NEW_RELIC_ALLOWED_ACCOUNT_IDS.

Safety: about 90 points in total, every point tagged ckg_test=true and ckg_cap_run=<run id>; metric
names start with ckg_cap_; no condition, dashboard or other object is created; queries are read-only
and filtered to this run. Metric data cannot be deleted; it expires with the account's retention.
Keys come from the environment (NEW_RELIC_LICENSE_KEY to send, NEW_RELIC_API_KEY to query; the
repository .env is read if present) and are never printed. TLS verification stays on.
"""

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from importer.core.apply import GuardError, NewRelicAPIError, NewRelicClient, check_guards
from importer.core.tls import system_tls_context

METRIC_API = "https://metric-api.newrelic.com/metric/v1"
STEP = 15  # seconds between points
HOSTS = ["web-1", "web-10", "xweb-1", "web-"]  # plus one series with no ckg_host at all
GAUGES = {"h1": 10.0, "h2": 20.0, "h3": 30.0, "h4": 40.0}  # steady, 15 s cadence
SLOW = ("h5", 50.0)  # one point per minute
STOPPED = ("h6", 60.0)  # reports for the first 2 minutes of the 4, then stops
RESULTS = Path("out/capability-check/data-round.json")


def series(now_ms: int) -> list[dict[str, Any]]:
    """Every metric point, newest at `now_ms`. Gauges only (type gauge)."""
    pts: list[dict[str, Any]] = []
    for host in [*HOSTS, None]:
        attrs: dict[str, Any] = {} if host is None else {"ckg_host": host}
        for back in (90, 60, 30):
            pts.append(gauge("ckg_cap_rx", 1.0, now_ms - back * 1000, attrs))
    for host, value in GAUGES.items():
        for back in range(240, 0, -STEP):
            pts.append(gauge("ckg_cap_gauge", value, now_ms - back * 1000, {"ckg_host": host}))
    for back in range(240, 0, -60):
        pts.append(gauge("ckg_cap_gauge", SLOW[1], now_ms - back * 1000, {"ckg_host": SLOW[0]}))
    for back in range(240, 120, -STEP):
        pts.append(
            gauge("ckg_cap_gauge", STOPPED[1], now_ms - back * 1000, {"ckg_host": STOPPED[0]})
        )
    return pts


def gauge(name: str, value: float, ts_ms: int, attrs: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "type": "gauge", "value": value, "timestamp": ts_ms, "attributes": attrs}


@dataclass
class Probe:
    id: str
    settles: str
    nrql: str  # {run} is the run id
    expect: Any  # a number, or a sorted list of strings for uniques()
    kind: str = "number"  # number | set
    tolerance: float = 0.0  # relative; 0 means exact


def probes(all_values: list[float]) -> list[Probe]:
    true_sum = (
        sum(GAUGES.values()) + SLOW[1] + STOPPED[1]
    )  # latest per series, PromQL 5 min lookback
    avg = statistics.fmean(all_values)
    rx = "FROM Metric WHERE metricName = 'ckg_cap_rx' AND ckg_cap_run = '{run}'"
    g = "FROM Metric WHERE metricName = 'ckg_cap_gauge' AND ckg_cap_run = '{run}'"
    return [
        Probe(
            "r1-rlike-full",
            "RLIKE is a full-string match (web-1 only, not web-10 or xweb-1)",
            f"SELECT uniques(ckg_host) {rx} AND ckg_host RLIKE 'web-.' SINCE 10 minutes ago",
            ["web-1"],
            "set",
        ),
        Probe(
            "r2-not-rlike-hosts",
            "NOT RLIKE returns the other hosts",
            f"SELECT uniques(ckg_host) {rx} AND ckg_host NOT RLIKE 'web-.' SINCE 10 minutes ago",
            sorted(["web-", "web-10", "xweb-1"]),
            "set",
        ),
        Probe(
            "r3-null-exists",
            "the series without the attribute has NULL rows (3 points)",
            f"SELECT count(*) {rx} AND ckg_host IS NULL SINCE 10 minutes ago",
            3.0,
        ),
        Probe(
            "r4-not-rlike-null",
            "does NOT RLIKE keep NULL rows? 15 means yes (as Prometheus does), 12 means no",
            f"SELECT count(*) {rx} AND ckg_host NOT RLIKE 'zzz' SINCE 10 minutes ago",
            15.0,
        ),
        Probe(
            "r5-rlike-all-null",
            "does RLIKE '.*' match NULL rows? 12 means no (as Prometheus), 15 means yes",
            f"SELECT count(*) {rx} AND ckg_host RLIKE '.*' SINCE 10 minutes ago",
            12.0,
        ),
        Probe(
            "g1-cardinality-5m",
            "average * cardinality vs the true sum (5 minute window)",
            f"SELECT average(ckg_cap_gauge) * cardinality(ckg_cap_gauge) {g} SINCE 5 minutes ago",
            true_sum,
            tolerance=0.15,
        ),
        Probe(
            "g2-cardinality-1m",
            "the same form over 1 minute: the stopped series is missing",
            f"SELECT average(ckg_cap_gauge) * cardinality(ckg_cap_gauge) {g} SINCE 1 minute ago",
            true_sum,
            tolerance=0.30,
        ),
        Probe(
            "g3-nested-sum",
            "nested sum of the latest per series vs the true sum",
            "SELECT sum(v) FROM (SELECT latest(ckg_cap_gauge) AS v FROM Metric WHERE "
            "metricName = 'ckg_cap_gauge' AND ckg_cap_run = '{run}' FACET ckg_host) "
            "SINCE 5 minutes ago",
            true_sum,
        ),
        Probe(
            "g4-clamp-min",
            "clamp_min(average(x), 100) returns the larger value",
            f"SELECT clamp_min(average(ckg_cap_gauge), 100) {g} SINCE 5 minutes ago",
            max(avg, 100.0),
            tolerance=0.05,
        ),
        Probe(
            "g5-if",
            "if(average(x) > 25, 1, 0)",
            f"SELECT if(average(ckg_cap_gauge) > 25, 1, 0) {g} SINCE 5 minutes ago",
            1.0 if avg > 25 else 0.0,
        ),
    ]


def print_plan() -> None:
    now_ms = int(time.time() * 1000)
    pts = series(now_ms)
    values = [p["value"] for p in pts if p["name"] == "ckg_cap_gauge"]
    print(
        f"PLAN (nothing is sent in this mode)\n\n{len(pts)} gauge points in 2 metrics, 15 s cadence:"
    )
    print(f"  ckg_cap_rx     hosts {HOSTS} + one series with no ckg_host, 3 points each, value 1")
    print(
        f"  ckg_cap_gauge  {list(GAUGES)} steady at {list(GAUGES.values())} every {STEP} s for 4 min;"
    )
    print(
        f"                 {SLOW[0]}={SLOW[1]} once a minute; {STOPPED[0]}={STOPPED[1]} for the first 2 minutes then stops"
    )
    print(
        "  true sum (PromQL, 5 minute lookback) = latest of each series = "
        f"{sum(GAUGES.values()) + SLOW[1] + STOPPED[1]}"
    )
    print("\nRead-only queries (each filtered to this run), with the value computed here:")
    for p in probes(values):
        print(
            f"  {p.id}: {p.settles}\n      {p.nrql}\n      expect {p.expect}"
            + (f" (+/- {p.tolerance:.0%})" if p.tolerance else "")
        )
    print("\nNo object is created. Points carry ckg_test=true and ckg_cap_run=<run id>.")


def load_env(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip().isidentifier():
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


NRQL = (
    "query($a: Int!, $q: Nrql!) { actor { account(id: $a) { nrql(query: $q, timeout: 60) {"
    " results } } } }"
)


def send(points: list[dict[str, Any]], run: str, license_key: str) -> None:
    for p in points:
        p["attributes"] = {**p["attributes"], "ckg_test": "true", "ckg_cap_run": run}
    body = [{"metrics": points}]
    with httpx.Client(verify=system_tls_context(), timeout=60) as http:
        r = http.post(METRIC_API, json=body, headers={"Api-Key": license_key})
    print(f"metric api: HTTP {r.status_code}")
    if r.status_code >= 300:
        raise SystemExit("the Metric API refused the points")


def query(client: NewRelicClient, account: int, text: str) -> list[dict[str, Any]]:
    data = client.run(NRQL, {"a": account, "q": text}, is_write=False)
    return list(data["actor"]["account"]["nrql"]["results"])


def first_value(rows: list[dict[str, Any]]) -> Any:
    row = rows[0] if rows else {}
    for key, value in row.items():
        if key not in ("beginTimeSeconds", "endTimeSeconds"):
            return value
    return None


def evaluate(
    client: NewRelicClient, account: int, run: str, values: list[float]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in probes(values):
        text = p.nrql.replace("{run}", run)
        try:
            rows = query(client, account, text)
            err = ""
        except NewRelicAPIError as exc:
            rows, err = [], str(exc)[:200]
        got: Any
        if p.kind == "set":
            got = sorted(rows[0].get("uniques.ckg_host", [])) if rows else []
            verdict = "MATCH" if got == p.expect else "DIFFERS"
        else:
            got = first_value(rows)
            if err or got is None:
                verdict = "NO VALUE"
            else:
                diff = abs(float(got) - float(p.expect)) / (abs(float(p.expect)) or 1.0)
                verdict = (
                    "MATCH"
                    if diff <= 1e-6
                    else "WITHIN TOLERANCE"
                    if diff <= p.tolerance
                    else f"DIFFERS ({diff:.1%})"
                )
        out.append(
            {
                "probe": p.id,
                "settles": p.settles,
                "expected": p.expect,
                "got": got,
                "verdict": verdict,
                "error": err,
            }
        )
        print(
            f"{p.id:20} {verdict:18} expected {p.expect}  got {got}" + (f"  [{err}]" if err else "")
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--confirm-account", type=int)
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    args = ap.parse_args()
    if not args.run:
        print_plan()
        return
    load_env(args.env_file)
    api_key, lic = os.environ.get("NEW_RELIC_API_KEY"), os.environ.get("NEW_RELIC_LICENSE_KEY")
    account = os.environ.get("NEW_RELIC_ACCOUNT_ID")
    if not (api_key and lic and account):
        raise SystemExit(
            "NEW_RELIC_API_KEY, NEW_RELIC_LICENSE_KEY and NEW_RELIC_ACCOUNT_ID must be set"
        )
    try:
        check_guards(
            int(account), args.confirm_account, os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS")
        )
    except GuardError as exc:
        raise SystemExit(f"guard: {exc}") from None
    now_ms = int(time.time() * 1000)
    run = str(now_ms // 1000)
    pts = series(now_ms)
    values = [p["value"] for p in pts if p["name"] == "ckg_cap_gauge"]
    send(pts, run, lic)
    expected = len(pts)
    with NewRelicClient(SecretStr(api_key), "us") as client:
        for _ in range(16):  # up to about 4 minutes for the points to become queryable
            time.sleep(15)
            got = first_value(
                query(
                    client,
                    int(account),
                    f"SELECT count(*) FROM Metric WHERE ckg_cap_run = '{run}' SINCE 15 minutes ago",
                )
            )
            print(f"waiting: {got} of {expected} points visible")
            if got == expected:
                break
        results = evaluate(client, int(account), run, values)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(
        json.dumps({"run": run, "sent": expected, "results": results}, indent=2, default=str)
    )
    print(f"\nresults: {RESULTS}")


if __name__ == "__main__":
    sys.exit(main())
