#!/bin/sh
# The quality gate for the improvement loop. Run from the repository root.
# Exit 0 only if every check passes, no golden file was touched, and nothing regressed.
set -u
fail=0
run() { printf '== %s\n' "$*"; "$@" || { echo "FAILED: $*"; fail=1; }; }

run uv run ruff check .
run uv run ruff format --check .
run uv run mypy --strict src tests
run uv run vulture src --min-confidence 80
run uv run pytest -q
run uv run importer improve-measure --baseline docs/improvement/baseline.json --out /tmp/improve-measure.json

printf '== golden files untouched\n'
if git diff --name-only HEAD -- tests/golden | grep -q .; then
  echo "FAILED: tests/golden changed; a human must review golden files before they change"
  fail=1
fi

if [ "$fail" -eq 0 ]; then echo "GATE PASSED"; else echo "GATE FAILED"; fi
exit "$fail"
