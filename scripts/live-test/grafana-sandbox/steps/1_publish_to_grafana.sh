#!/usr/bin/env bash
# STEP 1: put the 6 community dashboards into your Grafana, folder "ckg-importer-test".
# Creates only; a dashboard that already exists is left alone. Names start with "ckg-".
set -euo pipefail; . "$(dirname "$0")/env.sh"
ls "$SRC"/d*.json >/dev/null 2>&1 || { echo "No dashboard JSON in $SRC"; exit 1; }
echo "Publishing to $GURL (folder ckg-importer-test)"
python3 scripts/live-test/grafana-sandbox/publish_dashboards.py "$SRC" "$RUN/manifest.json"
echo; echo "Next: bash scripts/live-test/grafana-sandbox/steps/2_show_in_grafana.sh"
