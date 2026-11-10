#!/usr/bin/env bash
# STEP 2: list what is in the ckg-importer-test folder, with links to open in your browser.
# Read-only. Does not open a browser itself.
set -euo pipefail; . "$(dirname "$0")/env.sh"
printf 'url = "%s/api/search?type=dash-db&folderUIDs=ckg-importer-test"\nheader = "Authorization: Bearer %s"\nsilent\nmax-time = 30\n' "$GURL" "$GRAFANA_SA_TOKEN" \
 | curl -K - | GURL="$GURL" python3 -c "
import json,os,sys
rows=json.load(sys.stdin)
print(len(rows),'dashboards in folder ckg-importer-test:')
for r in rows: print(' -',r['title'],'\n   ',os.environ['GURL']+r['url'])"
echo; echo "Open the links, look at them, then: bash scripts/live-test/grafana-sandbox/steps/3_extract_and_convert.sh"
