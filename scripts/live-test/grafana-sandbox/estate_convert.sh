#!/usr/bin/env bash
# Extract (read-only) and convert the ckg- test estate. Usage: estate_convert.sh OUT_DIR
# Needs GRAFANA_URL and GRAFANA_SA_TOKEN in the environment (source .env first).
set -euo pipefail
O=${1:?out dir}
importer extract-grafana --out "$O/rules-all" >/dev/null
importer extract-grafana-notifications --out "$O/notif" >/dev/null
importer extract-grafana-dashboards --out "$O/dash-all" >/dev/null
python3 -I - "$O" <<'PY'
import json, shutil, sys
from pathlib import Path
o = Path(sys.argv[1])
shutil.copytree(o / "rules-all", o / "rules", dirs_exist_ok=True)
f = o / "rules/alert-rules/alert-rules.json"
rules = [r for r in json.loads(f.read_text()) if r["folderUID"] == "ckg-estate-alerts"]
f.write_text(json.dumps(rules, indent=2))
(o / "dash/dashboards").mkdir(parents=True, exist_ok=True)
for p in (o / "dash-all/dashboards").glob("dashboard-*.json"):
    d = json.loads(p.read_text())
    if str(d["dashboard"].get("uid", "")).startswith("ckg-"):
        shutil.copy(p, o / "dash/dashboards" / p.name)
for n in ("folders.json", "library-panels.json"):
    shutil.copy(o / "dash-all" / n, o / "dash" / n)
print(len(rules), "rules;", len(list((o / "dash/dashboards").glob("*.json"))), "dashboards")
PY
importer convert --source grafana --in "$O/rules" --out "$O/alerts" | tail -1
importer convert-grafana-dashboards --in "$O/dash" --out "$O/dash-conv" | tail -2
importer convert-grafana-notifications --in "$O/notif" --alerts "$O/alerts" --rules "$O/rules" --out "$O/notif-plans" | tail -1
mkdir -p "$O/all"; for d in alerts dash-conv notif-plans; do ln -sfn "../$d" "$O/all/$d"; done
importer plan-remediation --in "$O/all" --out "$O/remediation" | tail -1
importer plan-grafana-datapath --out "$O/datapath" --export "$O/notif" --alerts "$O/alerts" --dashboards "$O/dash-conv" | tail -3
