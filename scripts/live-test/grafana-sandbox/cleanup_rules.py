"""Delete the alert rules (and optionally a folder) that an earlier session recorded in a manifest.

Usage: cleanup_rules.py CREATED_JSON [--folder UID] [--yes]
Without --yes it only prints what it would delete. CREATED_JSON is the file written by
create_rules.py: {"created": [{"title": "ckg-...", "uid": "..."}]}. Refuses any title or folder uid
that does not start with `ckg-`. Reads GRAFANA_URL and GRAFANA_SA_TOKEN from the environment.
"""

import json
import sys
from pathlib import Path

from estate_common import call

HEADERS = ["-H", "X-Disable-Provenance: true"]


def guard(name: str) -> str:
    if not name.startswith("ckg-"):
        raise SystemExit(f"refusing to touch {name!r}: not a ckg- name")
    return name


def main(created: Path, folder: str | None, go: bool) -> int:
    rules = json.loads(created.read_text()).get("created", [])
    for r in rules:
        guard(r["title"])
    if folder:
        guard(folder)
    print("" if go else "DRY RUN: add --yes to delete")
    failed = 0
    for r in rules:
        print(f"delete alert rule {r['title']}")
        if go:
            code, text = call(
                "DELETE", f"/api/v1/provisioning/alert-rules/{r['uid']}", extra=HEADERS
            )
            if code not in (200, 202, 204, 404):
                failed += 1
                print("  FAIL", code, text[:200])
    if folder:
        print(f"delete folder {folder}")
        if go:
            code, text = call("DELETE", f"/api/folders/{folder}?forceDeleteRules=false")
            if code not in (200, 404):
                failed += 1
                print("  FAIL", code, text[:200])
    return 1 if failed else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--yes"]
    folder_uid = None
    if "--folder" in args:
        i = args.index("--folder")
        folder_uid = args[i + 1]
        del args[i : i + 2]
    sys.exit(main(Path(args[0]), folder_uid, "--yes" in sys.argv))
