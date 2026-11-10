#!/usr/bin/env bash
# STEP 6 (optional): remove what these steps created. Previews first, deletes only listed ckg- objects.
set -euo pipefail; . "$(dirname "$0")/env.sh"
echo "== New Relic: preview"; uv run --active importer rollback --manifest "$RUN/convert/manifest.json" --account-id "$NEW_RELIC_ACCOUNT_ID" || true
read -r -p "Delete those from New Relic? Type yes: " a
[ "$a" = yes ] && uv run --active importer rollback --manifest "$RUN/convert/manifest.json" --account-id "$NEW_RELIC_ACCOUNT_ID" --apply --confirm-account "$NEW_RELIC_ACCOUNT_ID"
echo "== Grafana: preview"; python3 - "$RUN" <<'P'
import json,sys
m=json.load(open(sys.argv[1]+"/manifest.json")); print(len(m.get("grafana_dashboards",[])),"dashboards listed in the Grafana manifest")
P
read -r -p "Delete the ckg- dashboards from Grafana? Type yes: " b
[ "$b" = yes ] && python3 scripts/live-test/grafana-sandbox/cleanup_dashboards.py "$RUN/manifest.json" --yes
echo done
