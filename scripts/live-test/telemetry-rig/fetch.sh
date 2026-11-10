#!/usr/bin/env bash
# Download the official prometheus and node_exporter release tarballs (https), verify the sha256
# published in the same release, unpack into a NEW temp dir. No sudo, no install.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; RIG="$ROOT/out/live-run/rig"; mkdir -p "$RIG"
PV=3.16.0; NV=1.12.1; A=darwin-arm64
T="$(mktemp -d /tmp/ckg-rig.XXXXXX)"; chmod 700 "$T"; mkdir "$T/dl" "$T/bin"; echo "$T" > "$RIG/tmpdir"
cd "$T/dl"
P=https://github.com/prometheus/prometheus/releases/download/v$PV; N=https://github.com/prometheus/node_exporter/releases/download/v$NV
curl -fsSL -o p.tgz "$P/prometheus-$PV.$A.tar.gz"; curl -fsSL -o p.sums "$P/sha256sums.txt"
curl -fsSL -o n.tgz "$N/node_exporter-$NV.$A.tar.gz"; curl -fsSL -o n.sums "$N/sha256sums.txt"
[[ "$(shasum -a 256 p.tgz | cut -d' ' -f1)" == "$(grep "prometheus-$PV.$A.tar.gz" p.sums | cut -d' ' -f1)" ]]
[[ "$(shasum -a 256 n.tgz | cut -d' ' -f1)" == "$(grep "node_exporter-$NV.$A.tar.gz" n.sums | cut -d' ' -f1)" ]]
tar xzf p.tgz -C ../bin --strip-components=1 "prometheus-$PV.$A/prometheus" "prometheus-$PV.$A/promtool"
tar xzf n.tgz -C ../bin --strip-components=1 "node_exporter-$NV.$A/node_exporter"
echo "ok: $T/bin"
