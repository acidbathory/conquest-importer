# Sourced by every step. Loads .env from the repository root; never prints secrets.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
ENVFILE="${CKG_ENV_FILE:-$ROOT/.env}"
if [ ! -f "$ENVFILE" ]; then   # a git worktree keeps .env in the main checkout
  MAIN="$(cd "$ROOT" && cd "$(git rev-parse --git-common-dir)/.." && pwd)"; ENVFILE="$MAIN/.env"
fi
[ -f "$ENVFILE" ] || { echo "No .env found at $ENVFILE (set CKG_ENV_FILE to its path)"; exit 1; }
set -a; . "$ENVFILE"; set +a
: "${GRAFANA_URL:?Set GRAFANA_URL in .env}" "${GRAFANA_SA_TOKEN:?Set GRAFANA_SA_TOKEN in .env}"
: "${NEW_RELIC_ACCOUNT_ID:?Set NEW_RELIC_ACCOUNT_ID in .env}"
[ -f /tmp/q/ca.pem ] && export SSL_CERT_FILE=/tmp/q/ca.pem   # corporate TLS inspection
GURL="${GRAFANA_URL%/}"; case "$GURL" in http*) ;; *) GURL="https://$GURL";; esac
RUN="$ROOT/out/live-run"; mkdir -p "$RUN"
SRC="${CKG_SRC:-$ROOT/out/live-grafana/src}"   # community dashboard JSON (d<id>.json)
cd "$ROOT"
