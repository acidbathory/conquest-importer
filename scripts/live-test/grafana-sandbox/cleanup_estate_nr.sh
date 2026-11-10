#!/usr/bin/env bash
# Roll back the ckg- estate objects created in the New Relic sandbox (dashboards, then conditions
# and the ckg-estate-policy). Preview by default; pass --apply to delete. Run from the repo root
# with the environment active and .env loaded (NEW_RELIC_API_KEY, NEW_RELIC_ACCOUNT_ID).
# Each object is deleted only if its id still has the recorded name.
set -euo pipefail
cd "$(dirname "$0")/../../.."
MODE=()
if [ "${1:-}" = "--apply" ]; then MODE=(--apply --confirm-account "$NEW_RELIC_ACCOUNT_ID"); fi
for m in out/live-estate/nr-manifest.json out/live-estate/nr-manifest-alerts.json; do
  importer rollback --manifest "$m" --account-id "$NEW_RELIC_ACCOUNT_ID" ${MODE[@]+"${MODE[@]}"}
done
