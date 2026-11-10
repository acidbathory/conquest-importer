"""Delete exactly what publish_estate*.py recorded in out/live-estate/manifest.json.

Usage: cleanup_estate.py [--yes]   (without --yes it only prints what it would delete)
Refuses any name, title or uid that does not start with `ckg-`. Deletes, in order: alert rules,
mute timings, contact points, dashboards, then the nested folders (never the root folder, which
older runs also use). Reads GRAFANA_URL and GRAFANA_SA_TOKEN from the environment.
"""

import sys

from estate_common import call, load_manifest

H = ["-H", "X-Disable-Provenance: true"]


def guard(name: str) -> str:
    if not name.startswith("ckg-"):
        raise SystemExit(f"refusing to touch {name!r}: not a ckg- name")
    return name


def main(go: bool) -> int:
    m = load_manifest()
    plan = []
    for r in m.get("grafana_alert_rules", []):
        plan.append(("alert rule", r["title"], f"/api/v1/provisioning/alert-rules/{guard(r['title']) and r['uid']}"))
    for t in m.get("grafana_mute_timings", []):
        plan.append(("mute timing", t["name"], f"/api/v1/provisioning/mute-timings/{guard(t['name'])}"))
    for c in m.get("grafana_contact_points", []):
        plan.append(("contact point", c["name"], f"/api/v1/provisioning/contact-points/{guard(c['name']) and c['uid']}"))
    for d in m.get("grafana_dashboards", []):
        plan.append(("dashboard", d["uid"], f"/api/dashboards/uid/{guard(d['uid'])}"))
    for f in reversed(m.get("grafana_folders", [])):
        if f != "ckg-importer-test":
            plan.append(("folder", f, f"/api/folders/{guard(f)}"))
    failed = 0
    for kind, name, path in plan:
        if not go:
            print("would delete", kind, name)
            continue
        code, text = call("DELETE", path, None, H)
        print(kind, name, code)
        if code not in (200, 202, 204, 404):
            failed += 1
            print("  ", text[:200])
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main("--yes" in sys.argv))
