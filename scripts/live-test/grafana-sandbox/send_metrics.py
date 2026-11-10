"""Copy a short window of Prometheus series from Grafana to the New Relic Metric API, re-stamped to now.

Usage: send_metrics.py --datasource UID --metric NAME[:gauge|:count] [--metric ...] [--send]

Reads GRAFANA_URL, GRAFANA_SA_TOKEN, NEW_RELIC_LICENSE_KEY, NEW_RELIC_ACCOUNT_ID and
NEW_RELIC_ALLOWED_ACCOUNT_IDS from the environment; never prints a secret. Read-only against
Grafana (instant-range query through the datasource proxy). Without --send nothing is posted.

Mapping (docs/research/grafana-notifications-datapath.md): each label becomes an attribute of the
same name; a label called metricName is renamed metric_name; a counter is sent as a delta `count`
(with interval.ms = step); everything else is a `gauge`. Every point gets ckg_test=true and
prometheus_server. Timestamps are shifted so the newest sample is "now". TLS verification stays on
(set SSL_CERT_FILE for a corporate CA bundle).
"""

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

MAX_SERIES = 20
MAX_POINTS = 120
STEP = 60
METRIC_API = "https://metric-api.newrelic.com/metric/v1"


def grafana_range(datasource: str, expr: str, window: int) -> list[dict]:
    base = os.environ["GRAFANA_URL"].rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    now = int(time.time())
    query = urllib.parse.urlencode({"query": expr, "start": now - window, "end": now, "step": STEP})
    req = urllib.request.Request(
        f"{base}/api/datasources/proxy/uid/{urllib.parse.quote(datasource)}/api/v1/query_range?{query}",
        headers={"Authorization": "Bearer " + os.environ["GRAFANA_SA_TOKEN"]},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["data"]["result"]


def to_points(name: str, mtype: str, series: list[dict], now: int) -> list[dict]:
    out: list[dict] = []
    for s in series[:MAX_SERIES]:
        attrs = {k: v for k, v in s["metric"].items() if k != "__name__"}
        if "metricName" in attrs:
            attrs["metric_name"] = attrs.pop("metricName")
        attrs["ckg_test"] = "true"
        attrs["prometheus_server"] = "ckg-sandbox"
        values = [(float(t), float(v)) for t, v in s["values"]][-MAX_POINTS:]
        if not values:
            continue
        shift = now - int(values[-1][0])
        prev: float | None = None
        for ts, val in values:
            point: dict = {"name": name, "attributes": dict(attrs), "timestamp": int(ts) + shift}
            if mtype == "count":
                delta = val if prev is None or val < prev else val - prev
                prev = val
                point.update(type="count", value=delta, **{"interval.ms": STEP * 1000})
            else:
                point.update(type="gauge", value=val)
            out.append(point)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasource", required=True)
    ap.add_argument("--metric", action="append", required=True)
    ap.add_argument("--window", type=int, default=3600)
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()

    account = os.environ["NEW_RELIC_ACCOUNT_ID"]
    allowed = os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "").replace(" ", "").split(",")
    if account not in allowed:
        print("account not in NEW_RELIC_ALLOWED_ACCOUNT_IDS; stopping")
        return 2
    now = int(time.time())
    points: list[dict] = []
    for spec in args.metric:
        name, _, mtype = spec.partition(":")
        series = grafana_range(args.datasource, name, min(args.window, MAX_POINTS * STEP))
        got = to_points(name, mtype or "gauge", series, now)
        print(f"{name}: {len(series)} series, {len(got)} points")
        points += got
    if not points:
        print("nothing to send")
        return 1
    if not args.send:
        print("dry run; add --send to post")
        return 0
    body = json.dumps([{"metrics": points}]).encode()
    req = urllib.request.Request(
        METRIC_API,
        data=body,
        method="POST",
        headers={"Api-Key": os.environ["NEW_RELIC_LICENSE_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        print("metric api:", resp.status, resp.read().decode()[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
