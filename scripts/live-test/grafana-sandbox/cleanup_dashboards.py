"""Delete exactly the dashboards listed in a publish manifest. Refuses any name not starting ckg-.

Usage: cleanup_dashboards.py MANIFEST [--yes]    (without --yes it only prints what it would delete)

MANIFEST is out/live-grafana/manifest.json, with
  grafana_dashboards:   [{uid, title}]   deleted from Grafana (GRAFANA_URL, GRAFANA_SA_TOKEN)
  new_relic_dashboards: [{id, name}]     deleted from New Relic with dashboardDelete
                                         (NEW_RELIC_API_KEY, NEW_RELIC_ACCOUNT_ID must be in
                                         NEW_RELIC_ALLOWED_ACCOUNT_IDS; region from NEW_RELIC_REGION, default us)
The Grafana folder is deleted last and only when it is then empty. Nothing outside the manifest is
touched and no secret is printed.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

PREFIX = "ckg-"


def request(url: str, headers: dict[str, str], method: str, body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def grafana(manifest: dict, yes: bool) -> int:
    entries = manifest.get("grafana_dashboards", [])
    bad = [e for e in entries if not str(e.get("title", "")).startswith(PREFIX)]
    if bad:
        print("REFUSED: grafana entries without the ckg- prefix:", [e.get("title") for e in bad])
        return 1
    if not entries:
        return 0
    base = os.environ["GRAFANA_URL"].rstrip("/")
    base = base if base.startswith("http") else "https://" + base
    headers = {"Authorization": f"Bearer {os.environ['GRAFANA_SA_TOKEN']}"}
    failed = 0
    for e in entries:
        print("grafana delete", e["uid"], e["title"])
        if yes:
            code, text = request(f"{base}/api/dashboards/uid/{e['uid']}", headers, "DELETE")
            if code not in (200, 404):
                failed += 1
                print("  FAIL", code, text[:200])
    folder = manifest.get("grafana_folder")
    if yes and folder and not failed and folder.startswith(PREFIX):
        code, text = request(f"{base}/api/search?folderUIDs={folder}", headers, "GET")
        if code == 200 and json.loads(text) == []:
            code, text = request(f"{base}/api/folders/{folder}", headers, "DELETE")
            print("folder delete", folder, code)
        else:
            print("folder not empty or unreadable; left in place:", folder)
    return 1 if failed else 0


def new_relic(manifest: dict, yes: bool) -> int:
    entries = manifest.get("new_relic_dashboards", [])
    bad = [e for e in entries if not str(e.get("name", "")).startswith(PREFIX)]
    if bad:
        print("REFUSED: new relic entries without the ckg- prefix:", [e.get("name") for e in bad])
        return 1
    if not entries:
        return 0
    allowed = os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "").replace(" ", "").split(",")
    if os.environ.get("NEW_RELIC_ACCOUNT_ID") not in allowed:
        print("REFUSED: NEW_RELIC_ACCOUNT_ID is not in NEW_RELIC_ALLOWED_ACCOUNT_IDS")
        return 1
    host = "api.eu.newrelic.com" if os.environ.get("NEW_RELIC_REGION", "us") == "eu" else "api.newrelic.com"
    headers = {"API-Key": os.environ["NEW_RELIC_API_KEY"], "Content-Type": "application/json"}
    query = "mutation($g: EntityGuid!) { dashboardDelete(guid: $g) { status errors { description } } }"
    failed = 0
    for e in entries:
        print("new relic delete", e["id"], e["name"])
        if yes:
            code, text = request(f"https://{host}/graphql", headers, "POST",
                                 {"query": query, "variables": {"g": e["id"]}})
            answer = (json.loads(text).get("data") or {}).get("dashboardDelete") or {}
            if code != 200 or answer.get("errors") or answer.get("status") != "SUCCESS":
                failed += 1
                print("  FAIL", code, text[:200])
    return 1 if failed else 0


def main(path: Path, yes: bool) -> int:
    manifest = json.loads(path.read_text())
    if not yes:
        print("DRY RUN: add --yes to delete")
    return new_relic(manifest, yes) | grafana(manifest, yes)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--yes"]
    sys.exit(main(Path(args[0]), "--yes" in sys.argv))
