#!/usr/bin/env bash
# Start the telemetry rig. Needs a temp dir made by fetch.sh (path in out/live-run/rig/tmpdir).
# Secrets: the licence key is read from the repository .env into a 0600 file; never printed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RIG="$ROOT/out/live-run/rig"; mkdir -p "$RIG"
T="$(cat "$RIG/tmpdir")"; [[ "$T" == /tmp/ckg-rig.* && -x "$T/bin/prometheus" ]] || { echo "run fetch.sh first"; exit 1; }
ENVFILE="${ENVFILE:-$ROOT/../../../.env}"; [[ -f "$ENVFILE" ]] || ENVFILE="/Users/abal/Documents/Projects/killer-app/.env"
HERE="$ROOT/scripts/live-test/telemetry-rig"
umask 077
python3 -I - "$ENVFILE" "$T/nr.token" <<'PY'
import sys
for line in open(sys.argv[1]):
    k, s, v = line.partition("=")
    if s and k.strip() == "NEW_RELIC_LICENSE_KEY":
        open(sys.argv[2], "w").write(v.strip().strip("'\""))
        break
else:
    raise SystemExit("NEW_RELIC_LICENSE_KEY not found")
PY
chmod 600 "$T/nr.token"
sed "s#__TMP__#$T#g" "$HERE/prometheus.yml.tmpl" > "$T/prometheus.yml"; cp "$HERE/rules.yml" "$T/rules.yml"
"$T/bin/promtool" check config "$T/prometheus.yml" >"$RIG/promtool.log" 2>&1 || { cat "$RIG/promtool.log"; exit 1; }
date -u +%FT%TZ > "$RIG/started_at"; echo $(( $(date +%s) + 5400 )) > "$RIG/ends_at_epoch"
nohup python3 -I "$HERE/exporter.py" --ttl 5400 >"$RIG/exporter.log" 2>&1 & echo $! > "$RIG/exporter.pid"
nohup "$T/bin/node_exporter" --web.listen-address=127.0.0.1:9100 >"$RIG/node_exporter.log" 2>&1 & echo $! > "$RIG/node_exporter.pid"
sleep 2
nohup "$T/bin/prometheus" --config.file="$T/prometheus.yml" --web.listen-address=127.0.0.1:9090 \
  --storage.tsdb.path="$T/data" --storage.tsdb.retention.time=1d >"$RIG/prometheus.log" 2>&1 & echo $! > "$RIG/prometheus.pid"
nohup python3 -I "$HERE/watchdog.py" "$RIG" >"$RIG/watchdog.log" 2>&1 & echo $! > "$RIG/watchdog.pid"
# The Mac sleeps one minute after the display turns off, which stops the scrapes and leaves holes in
# both Prometheus and New Relic. Hold off idle sleep for as long as the watchdog lives (no sudo).
nohup caffeinate -i -w "$(cat "$RIG/watchdog.pid")" >/dev/null 2>&1 & echo $! > "$RIG/caffeinate.pid"
echo "started; ends $(date -u -r "$(cat "$RIG/ends_at_epoch")" +%FT%TZ); stop with: bash $HERE/stop.sh"
