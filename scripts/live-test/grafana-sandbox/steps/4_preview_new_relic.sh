#!/usr/bin/env bash
# STEP 4: show what WOULD be created in New Relic. Sends nothing.
set -euo pipefail; . "$(dirname "$0")/env.sh"
uv run --active importer apply-dashboards --in "$RUN/convert" --account-id "$NEW_RELIC_ACCOUNT_ID"
echo; echo "Next (creates private dashboards): bash scripts/live-test/grafana-sandbox/steps/5_import_to_new_relic.sh"
