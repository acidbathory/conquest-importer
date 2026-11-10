"""Import community dashboards into a ckg- Grafana folder (create-only) and record their uids.

Usage: publish_dashboards.py SRC_DIR MANIFEST   (SRC_DIR holds d<id>.json from grafana.com)
Reads GRAFANA_URL and GRAFANA_SA_TOKEN from the environment; never prints the token.
Creates the folder `ckg-importer-test` if absent, then imports each dashboard with the title
prefixed `ckg-`, the uid `ckg-<id>`, and every datasource input pointed at grafanacloud-prom.
An existing uid is never overwritten.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ["GRAFANA_URL"].rstrip("/")
if not URL.startswith("http"):
    URL = "https://" + URL
TOKEN = os.environ["GRAFANA_SA_TOKEN"]
FOLDER_UID = "ckg-importer-test"
DATASOURCE = "grafanacloud-prom"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:500]


def main(src: Path, manifest_path: Path) -> int:
    code, _ = call("GET", f"/api/folders/{FOLDER_UID}")
    if code != 200:
        code, text = call("POST", "/api/folders", {"title": FOLDER_UID, "uid": FOLDER_UID})
        print("folder create:", code)
        if code != 200:
            print(text[:300])
            return 1
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    manifest.setdefault("grafana_folder", FOLDER_UID)
    created = manifest.setdefault("grafana_dashboards", [])
    done = {c["uid"] for c in created}
    failed = 0
    for path in sorted(src.glob("d*.json")):
        gid = path.stem[1:]
        uid = f"ckg-{gid}"
        if uid in done:
            continue
        dash = json.loads(path.read_text())
        dash.pop("id", None)
        dash["uid"] = uid
        dash["title"] = f"ckg-{dash.get('title', gid)}"
        inputs = [
            {
                "name": i["name"],
                "type": i["type"],
                "pluginId": i.get("pluginId"),
                "value": DATASOURCE if i["type"] == "datasource" else i.get("value", DATASOURCE),
            }
            for i in dash.get("__inputs", [])
        ]
        body = {"dashboard": dash, "overwrite": False, "folderUid": FOLDER_UID, "inputs": inputs}
        code, text = call("POST", "/api/dashboards/import", body)
        if code == 200:
            out = json.loads(text)
            created.append({"uid": out.get("uid", uid), "title": dash["title"], "source_id": gid})
            print("created", uid)
        else:
            failed += 1
            print("FAIL", uid, code, text[:300])
        manifest_path.write_text(json.dumps(manifest, indent=2))
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
