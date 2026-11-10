#!/usr/bin/env bash
# Stop every rig process and delete the temp dir (binaries, TSDB, token file). Logs stay in out/live-run/rig.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; RIG="$ROOT/out/live-run/rig"
for n in caffeinate prometheus node_exporter exporter watchdog; do
  [[ -f "$RIG/$n.pid" ]] && { kill "$(cat "$RIG/$n.pid")" 2>/dev/null; rm -f "$RIG/$n.pid"; }
done
sleep 2
if [[ -f "$RIG/tmpdir" ]]; then T="$(cat "$RIG/tmpdir")"; [[ "$T" == /tmp/ckg-rig.* ]] && rm -rf "$T" && echo "deleted $T"; rm -f "$RIG/tmpdir"; fi
date -u +%FT%TZ > "$RIG/stopped_at"; echo stopped
