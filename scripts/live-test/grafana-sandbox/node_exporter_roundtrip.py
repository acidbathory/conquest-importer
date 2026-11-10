"""Scrape a local node_exporter (127.0.0.1 only), convert to Metric API points, optionally send.

Usage:
  node_exporter_roundtrip.py scrape OUTFILE SECONDS INTERVAL    # raw scrapes as JSON lines
  node_exporter_roundtrip.py convert RAWFILE [--send ENVFILE]   # datapath rules; dry run without --send

Datapath rules (docs/research/grafana-notifications-datapath.md): counters become a delta `count`
with interval.ms from consecutive scrapes (a drop means a reset: the new value is the delta),
gauges stay `gauge`, labels become attributes, a label named metricName becomes metric_name.
Only the families a Node Exporter Full dashboard reads are kept, capped in series and points.
"""

import json
import re
import sys
import time
import urllib.request

import nrcommon

URL = "http://127.0.0.1:9100/metrics"
KEEP = re.compile(
    r"node_(cpu_seconds_total|memory_\w+|load(1|5|15)|network_(receive|transmit)_bytes_total|"
    r"filesystem_(avail|size|free)_bytes|boot_time_seconds|time_seconds)$"
)
SAMPLE = re.compile(r'([A-Za-z_:][\w:]*)(?:\{(.*)\})? (\S+)')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
MAX_SERIES = 400


def scrape(out: str, seconds: int, interval: int) -> None:
    end = time.time() + seconds
    with open(out, "w") as fh:
        while time.time() < end:
            t = time.time()
            text = urllib.request.urlopen(URL, timeout=10).read().decode()  # noqa: S310
            fh.write(json.dumps({"ts": t, "text": text}) + "\n")
            fh.flush()
            time.sleep(max(0.0, interval - (time.time() - t)))


def parse(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, float]]:
    types: dict[str, str] = {}
    out: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, float]] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split()[:4]
            types[name] = kind
        elif line and not line.startswith("#"):
            m = SAMPLE.fullmatch(line.strip())
            if not m or not KEEP.fullmatch(m[1]):
                continue
            labels = tuple(sorted(LABEL.findall(m[2] or "")))
            out[(m[1], labels)] = (types.get(m[1], "untyped"), float(m[3]))
    return out


def convert(raw: str) -> list[dict]:
    scrapes = [json.loads(x) for x in open(raw)]
    points: list[dict] = []
    prev: dict | None = None
    prev_ts = 0.0
    for s in scrapes:
        cur = parse(s["text"])
        if len(cur) > MAX_SERIES:
            raise SystemExit("too many series")
        for key, (kind, val) in cur.items():
            name, labels = key
            attrs = {k: v for k, v in labels}
            if "metricName" in attrs:
                attrs["metric_name"] = attrs.pop("metricName")
            attrs["job"] = "ckg_node"
            attrs["instance"] = "127.0.0.1:9100"
            ts = int(s["ts"] * 1000)
            if kind == "counter":
                if prev is None or key not in prev:
                    continue
                before = prev[key][1]
                delta = val if val < before else val - before
                points.append({"name": name, "type": "count", "value": delta, "timestamp": ts,
                               "interval.ms": int((s["ts"] - prev_ts) * 1000), "attributes": attrs})
            else:
                points.append({"name": name, "type": "gauge", "value": val, "timestamp": ts,
                               "attributes": attrs})
        prev, prev_ts = cur, s["ts"]
    return points


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "scrape":
        scrape(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    else:
        pts = convert(sys.argv[2])
        print(len(pts), "points,", len({p["name"] for p in pts}), "metrics")
        if "--send" in sys.argv:
            nrcommon.load_env(sys.argv[sys.argv.index("--send") + 1])
            nrcommon.post_points(pts)
