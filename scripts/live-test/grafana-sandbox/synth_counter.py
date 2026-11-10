"""Send a synthetic counter to the Metric API in both forms (delta count and cumulative gauge).

Usage: synth_counter.py ENVFILE [--send]
ckg_synth_requests_total: type count, interval.ms=10000, +10 (code=200) and +2 (code=500) per 10 s.
ckg_synth_requests_gauge_total: gauge holding the cumulative value. 60 steps (10 minutes), ending now.
"""

import sys
import time

import nrcommon

STEP, STEPS = 10, 60
RATES = {"200": 10, "500": 2}


def build(end: int) -> list[dict]:
    pts: list[dict] = []
    for code, inc in RATES.items():
        attrs = {"job": "ckg_synth", "code": code}
        for i in range(STEPS):
            ts = end - (STEPS - 1 - i) * STEP
            pts.append({"name": "ckg_synth_requests_total", "type": "count", "value": inc,
                        "interval.ms": STEP * 1000, "timestamp": ts, "attributes": dict(attrs)})
            pts.append({"name": "ckg_synth_requests_gauge_total", "type": "gauge",
                        "value": inc * (i + 1), "timestamp": ts, "attributes": dict(attrs)})
    return pts


if __name__ == "__main__":
    nrcommon.load_env(sys.argv[1])
    end = int(time.time())
    pts = build(end)
    print(len(pts), "points; end timestamp", end)
    if "--send" in sys.argv:
        nrcommon.post_points(pts)
