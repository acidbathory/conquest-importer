"""Baseline: what New Relic's own PromQL translator does with the PromQL of the public community dashboards.

Usage (project environment, repository root):
  uv run python scripts/live-test/grafana-sandbox/promql_baseline.py SRCDIR [--out DIR] [--offline]

SRCDIR holds the community dashboard JSON (d1860.json ...). Distinct expressions are made literal
(variables replaced by neutral values), wrapped one per timeseries panel in a synthetic dashboard and
sent, in batches, to the documented translation endpoint:
  POST https://promql-gateway.service.newrelic.com/api/v1/translate/dashboard/grafana   (header api-key)
The endpoint only returns JSON; nothing is created (creating is a separate NerdGraph call we never make).
Only expression text is sent. Keys come from the environment (NEW_RELIC_API_KEY, NEW_RELIC_ACCOUNT_ID,
NEW_RELIC_ALLOWED_ACCOUNT_IDS; the repository .env is read if present) and are never printed or saved.
TLS verification stays on; set SSL_CERT_FILE for a corporate CA. Results land in OUT/translated.json and
OUT/comparison.json. --offline skips the network and reuses OUT/translated.json.
"""

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

ENDPOINT = "https://promql-gateway.service.newrelic.com/api/v1/translate/dashboard/grafana"
MAX_EXPRESSIONS = 400
MAX_REQUESTS = 20
BATCH = 40
MAX_EXPR_LEN = 4000

BUILTIN = {
    "__rate_interval": "5m",
    "__interval": "1m",
    "__range": "1h",
    "__range_s": "3600",
    "__range_ms": "3600000",
    "__interval_ms": "60000",
    "__from": "0",
    "__to": "0",
}
TOKEN = re.compile(
    r'"(?:[^"\\]|\\.)*"|`[^`]*`|\[[^\]]*\]|\$\{(\w+)(?::\w+)?\}|\[\[(\w+)\]\]|\$(\w+)'
)


def literal(expr: str) -> str:
    """Replace every Grafana variable by a neutral literal, keeping the expression's shape."""

    def name_of(m: re.Match[str]) -> str | None:
        return m.group(1) or m.group(2) or m.group(3)

    def fill(name: str, mode: str) -> str:
        if name in BUILTIN:
            return BUILTIN[name]
        return {"range": "5m", "regex": ".+", "string": "ckg_value", "number": "1"}[mode]

    def sub_text(text: str, mode: str) -> str:
        return re.sub(
            r"\$\{(\w+)(?::\w+)?\}|\[\[(\w+)\]\]|\$(\w+)",
            lambda m: fill(name_of(m) or "", mode),
            text,
        )

    def repl(m: re.Match[str]) -> str:
        whole = m.group(0)
        if whole.startswith("["):
            return (
                sub_text(whole, "range")
                if not whole.startswith("[[")
                else fill(name_of(m) or "", "number")
            )
        if whole[0] in '"`':
            return whole  # string literals are handled with their matcher below
        return fill(name_of(m) or "", "number")

    # string literals: variables inside a regex matcher become '.+', elsewhere a plain value
    pieces: list[str] = []
    last = 0
    for m in TOKEN.finditer(expr):
        pieces.append(expr[last : m.start()])
        whole = m.group(0)
        if whole[0] == '"':
            before = expr[: m.start()].rstrip()
            mode = "regex" if before.endswith(("=~", "!~")) else "string"
            pieces.append(sub_text(whole, mode))
        else:
            pieces.append(repl(m))
        last = m.end()
    pieces.append(expr[last:])
    return "".join(pieces)


def panels_of(dash: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(items: list[dict[str, Any]]) -> None:
        for p in items:
            out.append(p)
            walk(p.get("panels", []))

    walk(dash.get("panels", []))
    for row in dash.get("rows", []):
        walk(row.get("panels", []))
    return out


def collect(src: Path, cap: int) -> list[dict[str, Any]]:
    """Distinct literal expressions with where they came from; evenly thinned to `cap`."""
    seen: dict[str, dict[str, Any]] = {}
    for f in sorted(src.glob("d*.json")):
        dash = json.loads(f.read_text())
        for p in panels_of(dash):
            for t in p.get("targets", []):
                raw = t.get("expr")
                if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_EXPR_LEN:
                    continue
                lit = literal(" ".join(raw.split()))
                entry = seen.setdefault(
                    lit,
                    {
                        "expr": lit,
                        "raw": raw,
                        "dashboard": f.stem,
                        "panel": p.get("title", ""),
                        "type": p.get("type", ""),
                        "uses": 0,
                    },
                )
                entry["uses"] += 1
    items = list(seen.values())
    if len(items) > cap:  # thin the largest dashboard evenly so every dashboard stays represented
        by = Counter(i["dashboard"] for i in items)
        big = by.most_common(1)[0][0]
        big_items = [i for i in items if i["dashboard"] == big]
        keep = len(big_items) - (len(items) - cap)
        step = len(big_items) / keep
        picked = {id(big_items[int(k * step)]) for k in range(keep)}
        items = [i for i in items if i["dashboard"] != big or id(i) in picked]
    for n, i in enumerate(items):
        i["id"] = n
    return items


def post(body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"api-key": os.environ["NEW_RELIC_API_KEY"], "Content-Type": "application/json"},
    )
    ctx = ssl.create_default_context()  # verification on
    with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
        return json.load(resp)  # type: ignore[no-any-return]


def synthetic(batch: list[dict[str, Any]], account: int, n: int) -> dict[str, Any]:
    panels = [
        {
            "id": i["id"] + 1,
            "type": "timeseries",
            "title": f"e{i['id']}",
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8 * k},
            "datasource": {"type": "prometheus", "uid": "ckgprom"},
            "targets": [
                {
                    "refId": "A",
                    "expr": i["expr"],
                    "datasource": {"type": "prometheus", "uid": "ckgprom"},
                }
            ],
        }
        for k, i in enumerate(batch)
    ]
    dash = {
        "title": f"ckg-promql-baseline-{n}",
        "schemaVersion": 39,
        "templating": {"list": []},
        "panels": panels,
    }
    return {"accountId": account, "dashboard": dash}


FAIL = "// This query couldn't be converted automatically"


def read_response(resp: dict[str, Any]) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for page in resp.get("dashboard", {}).get("pages", []):
        for w in page.get("widgets", []):
            m = re.fullmatch(r"e(\d+)", w.get("title", ""))
            if not m:
                continue
            qs = [q.get("query", "") for q in w.get("rawConfiguration", {}).get("nrqlQueries", [])]
            if not qs:
                out[int(m[1])] = {
                    "status": "rejected",
                    "error": "no query in widget",
                    "viz": w.get("visualization", {}).get("id"),
                }
            elif any(q.startswith(FAIL) for q in qs):
                err = next(
                    (ln[5:] for q in qs for ln in q.splitlines() if ln.startswith("// - ")), ""
                )
                out[int(m[1])] = {
                    "status": "rejected",
                    "error": err[:400],
                    "viz": w.get("visualization", {}).get("id"),
                }
            else:
                out[int(m[1])] = {
                    "status": "translated",
                    "nrql": qs,
                    "viz": w.get("visualization", {}).get("id"),
                }
    return out


def load_secrets() -> Any:
    """Import nrcommon after reading the repository .env (also found from a worktree) into the environment."""
    import nrcommon

    root = Path(os.environ.get("CKG_ENV_FILE", ""))
    for cand in (root, HERE.parents[2] / ".env", HERE.parents[5] / ".env"):
        if cand.is_file():
            nrcommon.load_env(str(cand))
            break
    return nrcommon


def translate(items: list[dict[str, Any]], max_requests: int) -> list[dict[str, Any]]:
    account = int(load_secrets().account())  # must be in NEW_RELIC_ALLOWED_ACCOUNT_IDS
    batches = [items[i : i + BATCH] for i in range(0, len(items), BATCH)]
    if len(batches) > max_requests:
        raise SystemExit(f"{len(batches)} requests needed, cap is {max_requests}")
    results: dict[int, dict[str, Any]] = {}
    for n, batch in enumerate(batches):
        try:
            got = read_response(post(synthetic(batch, account, n)))
        except urllib.error.HTTPError as e:
            got = {
                i["id"]: {"status": "error", "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
                for i in batch
            }
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            got = {
                i["id"]: {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}"}
                for i in batch
            }
        for i in batch:
            results[i["id"]] = got.get(
                i["id"], {"status": "error", "error": "widget missing from response"}
            )
        print(f"request {n + 1}/{len(batches)} done", file=sys.stderr)
    return [{**i, **results[i["id"]]} for i in items]


def validate(rows: list[dict[str, Any]], cap: int) -> None:
    """Run each translated NRQL read-only (NerdGraph, one query each, capped) and record ok or the error."""
    nrcommon = load_secrets()
    nrcommon.account()
    done = 0
    for r in rows:
        if r.get("status") != "translated" or "check" in r:
            continue
        if done >= cap:
            break
        done += 1
        try:
            out = nrcommon.nrql(r["nrql"][0])
        except (urllib.error.URLError, TimeoutError) as e:
            r["check"] = f"transport: {type(e).__name__}"
            continue
        err = out[0].get("error") if out and isinstance(out[0], dict) else None
        r["check"] = f"error: {err[:300]}" if err else "ok"


def ours(expr: str) -> dict[str, Any]:
    from importer.sources.grafana.dashboard_convert.name_map import NameMap
    from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
    from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

    opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=True)
    try:
        c = convert_query(expr, "prometheus", opts)
    except Unconvertible as e:
        return {"ours": "refused", "ours_reason": str(e.args[0]) if e.args else ""}
    except Exception as e:
        return {"ours": "crash", "ours_reason": f"{type(e).__name__}: {e}"}
    return {"ours": "converted", "ours_nrql": c.nrql, "ours_notes": c.notes}


def classify(rows: list[dict[str, Any]]) -> dict[str, Any]:
    for r in rows:
        r.update(ours(r["expr"]))
        nr = r["status"] == "translated"
        us = r["ours"] == "converted"
        r["class"] = "both" if nr and us else "nr_only" if nr else "ours_only" if us else "neither"
    return {
        "expressions": len(rows),
        "status": dict(Counter(r["status"] for r in rows)),
        "class": dict(Counter(r["class"] for r in rows)),
        "by_dashboard": {
            d: dict(Counter(r["class"] for r in rows if r["dashboard"] == d))
            for d in sorted({r["dashboard"] for r in rows})
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out/promql-baseline"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--max-expressions", type=int, default=MAX_EXPRESSIONS)
    ap.add_argument(
        "--validate", type=int, default=0, help="run up to N translated NRQL statements read-only"
    )
    ap.add_argument("--max-requests", type=int, default=MAX_REQUESTS)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    cache = a.out / "translated.json"
    if a.offline:
        rows = json.loads(cache.read_text())
    else:
        items = collect(a.src, min(a.max_expressions, MAX_EXPRESSIONS))
        print(f"{len(items)} distinct expressions", file=sys.stderr)
        rows = translate(items, min(a.max_requests, MAX_REQUESTS))
        cache.write_text(json.dumps(rows, indent=1))
    if a.validate:
        validate(rows, min(a.validate, MAX_EXPRESSIONS))
        cache.write_text(json.dumps(rows, indent=1))
    summary = classify(rows)
    summary["nrql_check"] = dict(
        Counter(str(r.get("check", "not run"))[:7] for r in rows if r["status"] == "translated")
    )
    (a.out / "comparison.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
