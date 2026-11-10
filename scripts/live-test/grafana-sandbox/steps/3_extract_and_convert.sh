#!/usr/bin/env bash
# STEP 3: read the ckg- dashboards back from Grafana (read-only) and convert them. Nothing is sent to New Relic.
set -euo pipefail; . "$(dirname "$0")/env.sh"
rm -rf "$RUN/extract-all" "$RUN/extract-ckg" "$RUN/convert"
uv run --active importer extract-grafana-dashboards --out "$RUN/extract-all"
python3 - "$RUN" <<'P'
import json,shutil,sys
from pathlib import Path
run=Path(sys.argv[1]); out=run/"extract-ckg"/"dashboards"; out.mkdir(parents=True)
n=0
for f in sorted((run/"extract-all"/"dashboards").glob("*.json")):
    d=json.loads(f.read_text()); t=(d.get("dashboard") or d).get("title","")
    if t.startswith("ckg-"): shutil.copy(f,out/f.name); n+=1
for extra in ("folders.json","library-panels.json"):
    if (run/"extract-all"/extra).exists(): shutil.copy(run/"extract-all"/extra, run/"extract-ckg"/extra)
print(f"kept {n} ckg- dashboards of the tenant export")
P
uv run --active importer convert-grafana-dashboards --in "$RUN/extract-ckg" --out "$RUN/convert"
echo; echo "Report: $RUN/convert/report.md   (not opened automatically)"
echo "Next: bash scripts/live-test/grafana-sandbox/steps/4_preview_new_relic.sh"
