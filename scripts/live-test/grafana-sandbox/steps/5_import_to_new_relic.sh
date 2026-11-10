#!/usr/bin/env bash
# STEP 5: create the converted dashboards in New Relic (private, names start with "ckg-"). Asks first.
set -euo pipefail; . "$(dirname "$0")/env.sh"
echo "This creates dashboards in New Relic account $NEW_RELIC_ACCOUNT_ID."
read -r -p "Type yes to continue: " ok; [ "$ok" = yes ] || { echo "Nothing created."; exit 1; }
uv run --active importer apply-dashboards --in "$RUN/convert" --account-id "$NEW_RELIC_ACCOUNT_ID" \
  --apply --confirm-account "$NEW_RELIC_ACCOUNT_ID"
echo; echo "Record of what was created: $RUN/convert/manifest.json"
echo "Undo later: bash scripts/live-test/grafana-sandbox/steps/6_undo.sh"
