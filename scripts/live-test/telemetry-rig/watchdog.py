"""Rig watchdog: stops everything after 90 minutes or when the remote-write volume passes the cap."""

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CAP = 1_400_000
rig = Path(sys.argv[1])
end = int((rig / "ends_at_epoch").read_text())
stop = Path(__file__).with_name("stop.sh")


def sent() -> float:
    """Samples sent to the remote endpoint so far, from Prometheus's own /metrics page."""
    with urllib.request.urlopen("http://127.0.0.1:9090/metrics", timeout=10) as r:
        text = r.read().decode()
    # samples_in_total counts everything handed to remote storage and survives a config reload
    # (the per-queue counters restart when the queue is renamed)
    return sum(
        float(ln.rsplit(" ", 1)[1])
        for ln in text.splitlines()
        if ln.startswith("prometheus_remote_storage_samples_in_total ")
    )


while time.time() < end:
    time.sleep(30)
    try:
        n = sent()
        (rig / "samples_sent.txt").write_text(f"{int(time.time())} {int(n)}\n")
        if n > CAP:
            print("sample cap reached", n, flush=True)
            break
    except Exception as e:  # prometheus not up yet or gone
        print("poll:", type(e).__name__, flush=True)
print("stopping", flush=True)
subprocess.run(["bash", str(stop)], check=False)
