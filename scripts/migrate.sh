#!/usr/bin/env bash
# One command for the whole guided migration (GCP, AWS, Grafana, Dynatrace).
# It loads your .env, then opens the numbered menu. Nothing is created without you typing "yes".
set -euo pipefail
cd "$(dirname "$0")/.."
ENVFILE=".env"
if [ ! -f "$ENVFILE" ]; then                     # a git worktree keeps .env in the main checkout
  ENVFILE="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)/.env"
fi
if [ -f "$ENVFILE" ]; then set -a; . "$ENVFILE"; set +a; fi
[ -f /tmp/q/ca.pem ] && export SSL_CERT_FILE=/tmp/q/ca.pem   # only for corporate TLS inspection
exec uv run --active importer menu
