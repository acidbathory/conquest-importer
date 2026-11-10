"""Create the ckg- estate alert rules (PAUSED), contact points and mute timings in Grafana.

Create-only. Rules live in folder ckg-estate-alerts (nested under ckg-importer-test). Contact
points use unroutable example.invalid addresses. The notification policy tree is NOT touched.
Uids are appended to out/live-estate/manifest.json.
"""

import json
import sys

from estate_common import LOKI, PROM, ROOT, call, load_manifest, save_manifest
from estate_rules import SIMPLE, multi

FOLDER = "ckg-estate-alerts"
H = ["-H", "X-Disable-Provenance: true"]


def q(ref, uid, expr, secs=600):
    return {"refId": ref, "datasourceUid": uid, "relativeTimeRange": {"from": secs, "to": 0},
            "model": {"refId": ref, "expr": expr, "instant": True, "intervalMs": 1000, "maxDataPoints": 43200}}


def ex(ref, typ, **kw):
    return {"refId": ref, "datasourceUid": "__expr__", "relativeTimeRange": {"from": 0, "to": 0},
            "model": {"refId": ref, "type": typ, **kw}}


def reduce(ref, src, fn="last"):
    return ex(ref, "reduce", expression=src, reducer=fn, settings={"mode": "dropNN"})


def thresh(ref, src, kind, *params):
    return ex(ref, "threshold", expression=src, conditions=[{"evaluator": {"type": kind, "params": list(params)}}])


def rules() -> list[dict]:
    out = []
    for title, group, expr, op, thr, for_, nodata, err, sev in SIMPLE:
        ds = LOKI if group == "loki-logs" else PROM
        out.append({"title": title, "group": group, "for": for_, "noDataState": nodata, "execErrState": err,
                    "labels": {"severity": sev, "origin": "ckg-estate"},
                    "annotations": {"summary": title[4:], "description": f"{title[4:]} on {{{{ $labels.instance }}}}",
                                    "runbook_url": f"https://runbooks.prometheus-operator.dev/runbooks/{group}/{title[4:].lower()}"},
                    "condition": "C", "data": [q("A", ds, expr), reduce("B", "A"), thresh("C", "B", op, thr)]})
    for m in multi():
        t, k = m["title"], m["kind"]
        a = q("A", PROM, m["a"])
        if k == "and":
            data = [a, q("B", PROM, m["b"]), reduce("C", "A"), reduce("D", "B"), thresh("E", "C", "gt", 1),
                    thresh("F", "D", "gt", 100), ex("G", "math", expression="$E && $F")]
            cond = "G"
        elif k == "math":
            data = [a, q("B", PROM, m["b"]), reduce("C", "A", "mean"), reduce("D", "B", "mean"),
                    ex("E", "math", expression="$C / $D * 100"), thresh("F", "E", "gt", 5)]
            cond = "F"
        elif k == "range":
            data = [a, reduce("B", "A"), thresh("C", "B", "within_range", 20, 30)]
            cond = "C"
        elif k == "mean":
            data = [a, reduce("B", "A", "mean"), thresh("C", "B", "gt", 4)]
            cond = "C"
        else:
            data = [a, q("B", LOKI, m["b"]), ex("C", "math", expression="$A + $B"), thresh("D", "C", "gt", 10)]
            cond = "D"
        out.append({"title": t, "group": m["group"], "for": m["for"], "noDataState": m["noData"],
                    "execErrState": "KeepLast" if m["noData"] == "KeepLast" else "Error",
                    "labels": {"severity": "warning", "origin": "ckg-estate"},
                    "annotations": {"summary": t[4:]}, "condition": cond, "data": data})
    return out


CONTACTS = [
    ("ckg-webhook-ops", "webhook", {"url": "https://hooks.example.invalid/ckg/ops", "httpMethod": "POST"}),
    ("ckg-email-oncall", "email", {"addresses": "ckg-oncall@example.invalid;ckg-team@example.invalid", "singleEmail": True}),
    ("ckg-slack-alerts", "slack", {"url": "https://hooks.example.invalid/slack/ckg", "recipient": "#ckg-alerts", "title": "{{ template \"default.title\" . }}"}),
    ("ckg-pagerduty-crit", "pagerduty", {"integrationKey": "ckg-not-a-real-key", "severity": "critical"}),
    ("ckg-teams-platform", "teams", {"url": "https://hooks.example.invalid/teams/ckg"}),
    ("ckg-opsgenie-sre", "opsgenie", {"apiKey": "ckg-not-a-real-key", "apiUrl": "https://api.example.invalid/v2/alerts"}),
]
MUTES = [
    ("ckg-weekends", [{"weekdays": ["saturday", "sunday"]}]),
    ("ckg-nightly-maintenance", [{"times": [{"start_time": "01:00", "end_time": "03:00"}], "weekdays": ["monday:friday"]}]),
    ("ckg-quarter-end-freeze", [{"months": ["3", "6", "9", "12"], "days_of_month": ["28:31"]}]),
]


def main() -> int:
    m = load_manifest()
    code, _ = call("GET", f"/api/folders/{FOLDER}")
    if code != 200:
        code, text = call("POST", "/api/folders", {"title": FOLDER, "uid": FOLDER, "parentUid": ROOT})
        if code != 200:
            print("folder", code, text[:200])
            return 1
    m.setdefault("grafana_folders", [])
    if FOLDER not in m["grafana_folders"]:
        m["grafana_folders"].append(FOLDER)
    cps = m.setdefault("grafana_contact_points", [])
    for name, typ, settings in CONTACTS:
        if any(c["name"] == name for c in cps):
            continue
        code, text = call("POST", "/api/v1/provisioning/contact-points", {"name": name, "type": typ, "settings": settings}, H)
        if code in (200, 201, 202):
            cps.append({"name": name, "uid": json.loads(text).get("uid"), "type": typ})
        else:
            print("contact FAIL", name, code, text[:200])
    mts = m.setdefault("grafana_mute_timings", [])
    for name, ivs in MUTES:
        if any(c["name"] == name for c in mts):
            continue
        code, text = call("POST", "/api/v1/provisioning/mute-timings", {"name": name, "time_intervals": ivs}, H)
        if code in (200, 201, 202):
            mts.append({"name": name})
        else:
            print("mute FAIL", name, code, text[:200])
    save_manifest(m)
    ar = m.setdefault("grafana_alert_rules", [])
    have = {r["title"] for r in ar}
    recv = [c[0] for c in CONTACTS]
    for i, r in enumerate(rules()):
        if r["title"] in have:
            continue
        group = "ckg-" + r.pop("group")
        body = {"orgID": 1, "folderUID": FOLDER, "ruleGroup": group, "isPaused": True, **r}
        if i % 3 == 0:
            body["notification_settings"] = {"receiver": recv[i % len(recv)]}
            if i % 2 == 0:
                body["notification_settings"]["mute_time_intervals"] = [MUTES[i % len(MUTES)][0]]
        code, text = call("POST", "/api/v1/provisioning/alert-rules", body, H)
        if code not in (200, 201) and "notification_settings" in body:
            body.pop("notification_settings")
            code, text = call("POST", "/api/v1/provisioning/alert-rules", body, H)
        if code in (200, 201):
            ar.append({"title": r["title"], "uid": json.loads(text)["uid"], "group": group})
        else:
            print("rule FAIL", r["title"], code, text[:250])
        save_manifest(m)
    print(f"rules {len(ar)}, contact points {len(cps)}, mute timings {len(mts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
