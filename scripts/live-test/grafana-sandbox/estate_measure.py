"""Measure a converted ckg- estate: per dashboard, per alert, ranked blockers.

Usage: estate_measure.py RUN_DIR   (the folder estate_convert.sh wrote). Prints JSON to stdout.
Data widgets exclude rows and text boxes, which always pass and would inflate the score.
"""

import collections
import json
import re
import sys
from pathlib import Path

NON_DATA = {"row", "text"}


def norm(reason: str) -> str:
    r = reason.split("; query:")[0].split(" query:")[0]
    r = re.sub(r"\d+", "N", r)
    return r.strip()[:140]


def main(run: Path) -> dict:
    dashes = []
    blockers = collections.Counter()
    blocker_dash = collections.defaultdict(set)
    for p in sorted((run / "dash-conv/dashboard-ir").glob("*.json")):
        d = json.loads(p.read_text())
        c = collections.Counter()
        for w in d["widgets"]:
            if w["source_type"] in NON_DATA or w["kind"] in NON_DATA:
                c["chrome"] += 1
                continue
            c[w["confidence"]] += 1
            if w["confidence"] == "manual":
                for r in w["reasons"]:
                    blockers[norm(r)] += 1
                    blocker_dash[norm(r)].add(d["source_key"])
        n = sum(c[k] for k in ("exact", "approximate", "manual", "unsupported"))
        dashes.append(
            {
                "uid": d["source_key"],
                "name": d["name"],
                "data_widgets": n,
                "exact": c["exact"],
                "approx": c["approximate"],
                "manual": c["manual"] + c["unsupported"],
                "chrome": c["chrome"],
                "score": round((c["exact"] + 0.5 * c["approximate"]) / n * 100) if n else 0,
                "converted_pct": round((c["exact"] + c["approximate"]) / n * 100) if n else 0,
                "variables": len(d.get("variables", [])),
            }
        )
    alerts = []
    ablock = collections.Counter()
    for p in sorted((run / "alerts/ir").glob("*.json")):
        d = json.loads(p.read_text())
        alerts.append(
            {
                "name": d["name"],
                "confidence": d["confidence"],
                "reason": norm(d["reasons"][0])
                if d["reasons"] and d["confidence"] == "manual"
                else "",
            }
        )
        if d["confidence"] == "manual":
            for r in d["reasons"]:
                ablock[norm(r)] += 1
    return {
        "dashboards": dashes,
        "dash_blockers": [(k, v, len(blocker_dash[k])) for k, v in blockers.most_common()],
        "alerts": sorted(alerts, key=lambda a: a["name"]),
        "alert_blockers": ablock.most_common(),
    }


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1])), indent=1))
