#!/bin/sh
# Report files changed by more than one branch, relative to a base. Run before merging parallel work.
# Usage: scripts/improve/overlap.sh <base> <branch> <branch> ...
set -eu
base=$1; shift
tmp=$(mktemp -d)
for b in "$@"; do git diff --name-only "$base...$b" | sort > "$tmp/$(echo "$b" | tr / _)"; done
echo "Files changed by two or more of: $*"
cat "$tmp"/* | sort | uniq -d | sed 's/^/  /'
n=$(cat "$tmp"/* | sort | uniq -d | wc -l | tr -d ' ')
rm -rf "$tmp"
echo "$n overlapping file(s)"
