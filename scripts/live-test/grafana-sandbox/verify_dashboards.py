#!/usr/bin/env python3
"""Read-only check: run every NRQL widget query of the converted dashboards with variables filled.

Usage: verify_dashboards.py PAYLOAD_DIR ENVFILE [--since "60 minutes ago"] [--only SUBSTRING]
Variables: each `uniques(...)` query is run in order; a multi-select variable takes all values, a
single-select variable the first value (sorted). Prints per dashboard: widgets with data / widgets
with queries, and for each empty widget the first query and the reason (error text or no rows).
Only NerdGraph NRQL reads are made (NEW_RELIC_API_KEY); nothing is written.
"""

import argparse
import re
import sys
from typing import Any

import feed_dashboards as fd
import nrcommon

META = {"beginTimeSeconds", "endTimeSeconds", "facet", "timestamp", "inspectedCount", "x"}


def has_value(rows: list[dict[str, Any]]) -> bool:
    for r in rows:
        for k, v in r.items():
            if k in META or k == "error":
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return True
            if isinstance(v, dict) and any(isinstance(x, (int, float)) for x in v.values()):
                return True
            if isinstance(v, list) and any(isinstance(x, (int, float)) for x in v):
                return True
    return False


def fill(query: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m[1], "'__unresolved__'"), query)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload_dir")
    ap.add_argument("env")
    ap.add_argument("--since", default="60 minutes ago")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    nrcommon.load_env(args.env)
    nrcommon.account()
    for dash in fd.load_dashboards(args.payload_dir):
        if args.only and args.only.lower() not in dash["name"].lower():
            continue
        values: dict[str, str] = {}
        for var in dash["variables"]:
            q = fill(var["nrqlQuery"]["query"].replace("ACCOUNT_ID_PLACEHOLDER", ""), values) + " SINCE 3 hours ago"
            rows = nrcommon.nrql(q)
            found: list[str] = []
            for r in rows:
                for v in r.values():
                    found += [str(x) for x in v] if isinstance(v, list) else []
            found = sorted(set(found))
            multi = var.get("isMultiSelection")
            pick = found if multi else found[:1]
            values[var["name"]] = ", ".join("'" + x.replace("'", "\\'") + "'" for x in pick) or "'__none__'"
            print(f"  var {var['name']}: {len(found)} choices ({'all' if multi else 'first'})")
        widgets: dict[tuple[int, str], list[str]] = {}
        order = 0
        last = None
        for title, query in dash["queries"]:
            if title != last:
                order += 1
                last = title
            widgets.setdefault((order, title), []).append(query)
        ok, empty = 0, []
        for key, queries in widgets.items():
            good, why = False, ""
            for q in queries:
                full = fill(q, values) + ("" if " SINCE " in q else f" SINCE {args.since}")
                rows = nrcommon.nrql(full)
                if rows and "error" in rows[0]:
                    why = rows[0]["error"][:160]
                elif has_value(rows):
                    good = True
                    break
                else:
                    why = why or "no rows" if not rows else "rows without numeric values"
            if good:
                ok += 1
            else:
                empty.append((key[1], queries[0][:110], why))
        print(f"{dash['name']}: {ok}/{len(widgets)} widgets with data")
        for title, q, why in empty:
            print(f"   EMPTY {title!r}: {why} | {q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
