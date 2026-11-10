"""Publish the ckg- test estate dashboards to Grafana (create-only).

Usage: publish_estate.py SRC_DIR   (SRC_DIR holds d<id>.json files from grafana.com)
Folders: ckg-importer-test (exists) with nested folders ckg-estate-<group>. Dashboards get the
title prefix `ckg-`, uid `ckg-<id>`, and every datasource points at grafanacloud-prom (Loki
dashboards: grafanacloud-logs). An existing uid is never overwritten. Uids go to
out/live-estate/manifest.json.
"""

import json
import sys
from pathlib import Path

from estate_common import LOKI, PROM, ROOT, call, load_manifest, save_manifest

GROUPS = {
    "infra": [1860, 11074, 10619, 10826, 6671],
    "kubernetes": [
        15661,
        315,
        13332,
        15757,
        15758,
        15759,
        15760,
        16364,
        16368,
        16361,
        15762,
        15761,
        6417,
        747,
    ],
    "data": [7362, 12485, 9628, 11835, 2583, 14191, 6483, 10991, 7651],
    "edge-apps": [
        12693,
        9614,
        14900,
        17346,
        4701,
        11378,
        13659,
        14981,
        7645,
        7636,
        14584,
        19993,
        15987,
        12904,
    ],
    "observability": [9578, 3662, 19105, 14055, 15141, 15983, 11176],
}


def rewrite_ds(node):
    """Point every non-variable datasource reference at the stack's data sources."""
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k == "datasource":
                if (
                    isinstance(v, str)
                    and v
                    and not v.startswith(("$", "-- ", "${"))
                    and v != "null"
                ):
                    node[k] = {"type": "prometheus", "uid": LOKI if "oki" in v else PROM}
                elif isinstance(v, dict) and not str(v.get("uid", "")).startswith("$"):
                    t = v.get("type")
                    if t == "prometheus":
                        node[k] = {"type": t, "uid": PROM}
                    elif t == "loki":
                        node[k] = {"type": t, "uid": LOKI}
            else:
                rewrite_ds(v)
    elif isinstance(node, list):
        for v in node:
            rewrite_ds(v)


def ensure_folder(uid: str, parent: str | None) -> bool:
    code, _ = call("GET", f"/api/folders/{uid}")
    if code == 200:
        return True
    body = {"title": uid, "uid": uid}
    if parent:
        body["parentUid"] = parent
    code, text = call("POST", "/api/folders", body)
    print("folder", uid, code, "" if code == 200 else text[:200])
    return code == 200


def main(src: Path) -> int:
    m = load_manifest()
    m.setdefault("grafana_folders", [])
    dash = m.setdefault("grafana_dashboards", [])
    done = {d["uid"] for d in dash}
    failed = 0
    for group, ids in GROUPS.items():
        fuid = f"ckg-estate-{group}"
        if not ensure_folder(fuid, ROOT):
            fuid = ROOT
            print("  nested folder not possible; using", ROOT)
        elif fuid not in m["grafana_folders"]:
            m["grafana_folders"].append(fuid)
        for gid in ids:
            path = src / f"d{gid}.json"
            uid = f"ckg-{gid}"
            if uid in done or not path.exists():
                continue
            d = json.loads(path.read_text())
            d.pop("id", None)
            d["uid"], d["title"] = uid, f"ckg-{d.get('title', gid)}"
            d["tags"] = sorted(set(d.get("tags", [])) | {"ckg-estate"})
            inputs = []
            for i in d.get("__inputs", []):
                val = i.get("value", "")
                if i["type"] == "datasource":
                    val = LOKI if i.get("pluginId") == "loki" else PROM
                inputs.append(
                    {
                        "name": i["name"],
                        "type": i["type"],
                        "pluginId": i.get("pluginId"),
                        "value": val,
                    }
                )
            if d.get("__inputs"):
                body = {"dashboard": d, "overwrite": False, "folderUid": fuid, "inputs": inputs}
                code, text = call("POST", "/api/dashboards/import", body)
            else:
                rewrite_ds(d)
                body = {"dashboard": d, "overwrite": False, "folderUid": fuid}
                code, text = call("POST", "/api/dashboards/db", body)
            if code == 200:
                dash.append({"uid": uid, "title": d["title"], "source_id": gid, "folder": fuid})
                print("created", uid)
            else:
                failed += 1
                print("FAIL", uid, code, text[:200])
            save_manifest(m)
    save_manifest(m)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
