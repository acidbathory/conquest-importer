"""Turn comparison JSON files into markdown tables (stdout).

Usage: report.py COMPARE_A.json [COMPARE_B.json]
The root causes below are the analysis of the 2026-11-10 run; they are text, not computed.
"""

import json
import sys
from pathlib import Path

CAUSE = {
    "rate_cpu_mode": "none; counters named _total arrive as delta counts",
    "rate_cpu_busy": "none",
    "rate_cadvisor_cpu": "5m Prometheus window smooths; bucket is 60 s",
    "rate_cadvisor_rx": "5m Prometheus window smooths; bucket is 60 s",
    "rate_reset_container": "reset handled on both sides (New Relic stores the new value as the delta)",
    "rate_nginx_total_cpu": "none",
    "irate_nginx_requests": "counter without _total is stored as a cumulative gauge; rate(sum()) of it is wrong",
    "rate_nginx_by_ingress_ok": "same: cumulative gauge",
    "ratio_nginx_success": "matches only because both operands are cumulative gauges and the ratio cancels",
    "irate_pg_commit": "same: cumulative gauge (name has no _total)",
    "irate_pg_bgwriter": "none (name ends _total); stat differs: irate is the last two samples, NRQL is the window mean",
    "rate_pg_reset": "same: cumulative gauge; the reset also drops the value",
    "increase_http_count": "stat extrapolates the window rate to 10 minutes (existing note); chart matches",
    "rate_http_sum_latency": "none; _sum is a summary, _count a count",
    "avg_over_time_conn": "refused: sum over avg_over_time of a multi-series gauge",
    "avg_rss": "none",
    "sum_alloc_cpu": "refused by design: sum() of a multi-series gauge",
    "sum_pods_running": "refused by design: sum() of a multi-series gauge",
    "ratio_requests_alloc": "refused by design: sum() of a multi-series gauge",
    "jvm_heap_by_app": "refused by design: sum() of a multi-series gauge",
    "mem_used_pct": "series merged (two instances); add a FACET (existing note)",
    "hq_nginx_p95": "none; _bucket arrives as cumulativeCount and bucketPercentile reads it",
    "hq_nginx_p50_ingress": "none",
    "hq_http_p99_app": "none",
    "topk_tx": "NRQL ranks over the whole range, Prometheus at each step (existing note); stat matches",
    "changes_restarts": "not supported: changes()",
    "join_group_left": "not supported: on()/group_left",
    "up_by_job": "refused by design: sum() of a multi-series gauge",
    "up_flaky_avg": "not supported: avg_over_time of a gauge",
    "alert_5xx_ratio": "cumulative gauge (name has no _total) in both operands",
    "alert_5xx_ratio_gt": "comparison to a number is not written",
    "alert_target_down": "comparison filter on a gauge is not written",
    "recording_name": "fixed in this run: colon names are now quoted; two instances merge (add a FACET)",
    "uptime_days": "not supported: time()",
    "absent_failed_jobs": "not supported: absent()",
    "count_cmp_evicted": "not supported: count of a comparison",
}


def short(text: object, n: int) -> str:
    text = " ".join(str(text).split())
    return (text[: n - 1] + "…" if len(text) > n else text).replace("|", "\\|")


def ratio(row: dict) -> str:
    rs = [x.get("mean_ratio") for x in (row.get("series") or []) if x.get("mean_ratio") is not None]
    return f" ({min(rs):.2f}-{max(rs):.2f})" if rs else ""


def load(path: str) -> dict[tuple[str, str], dict]:
    data = json.loads(Path(path).read_text())
    rows: dict[tuple[str, str], dict] = {}
    for r in data["results"]:
        rows.setdefault((r["variant"], r["id"]), {})[r["mode"]] = r
    return rows


def main() -> None:
    rows = load(sys.argv[1])
    print(
        "| case | PromQL | converter NRQL | chart vs dashboard range (mean ratio NR/Prom) | chart vs 1 m range | stat | root cause |"
    )
    print("|---|---|---|---|---|---|---|")
    for (variant, cid), m in rows.items():
        if variant != "plain" or "@" in cid:
            continue
        c, s = m.get("chart", {}), m.get("stat", {})
        nrql = c.get("nrql") or c.get("detail", "")
        print(
            f"| {cid} | `{short(c.get('promql', ''), 80)}` | `{short(nrql, 110)}` | {c.get('status')}{ratio(c)} "
            f"| {c.get('status_1m') or ''} | {s.get('status')} | {CAUSE.get(cid, '')} |"
        )
    print(
        "\nType experiment (same PromQL, `prometheus_server` untyped vs forced by `newrelic_metric_type`):\n"
    )
    print("| case | untyped chart / 1 m / stat | typed chart / 1 m / stat |")
    print("|---|---|---|")
    seen = []
    for (variant, cid), m in rows.items():
        if variant == "plain" and cid.endswith("@ckg-local") and cid.startswith("t_"):
            base = cid.split("@")[0]
            seen.append(base)
            u, t = m, rows.get((variant, base + "@ckg-local-typed"), {})

            def trio(x: dict) -> str:
                c, s = x.get("chart", {}), x.get("stat", {})
                return f"{c.get('status')} / {c.get('status_1m') or '-'} / {s.get('status')}"

            print(f"| {base} | {trio(u)} | {trio(t)} |")
    diffs = []
    for (variant, cid), m in rows.items():
        if variant == "hints":
            p = rows.get(("plain", cid), {})
            if any(
                m.get(k, {}).get("status") != p.get(k, {}).get("status") for k in ("chart", "stat")
            ):
                diffs.append(cid)
    print(
        f"\nWith hints (types and series keys read from the local Prometheus by the project's own `read_hints`), the verdict changed for: {', '.join(diffs) or 'no case'}."
    )
    if len(sys.argv) > 2:
        other = load(sys.argv[2])
        print("\nSecond window verdicts (plain, chart / stat):\n")
        print("| case | window A | window B |")
        print("|---|---|---|")
        for (variant, cid), m in rows.items():
            if variant != "plain" or "@" in cid:
                continue
            o = other.get((variant, cid), {})
            fa = f"{m.get('chart', {}).get('status')} / {m.get('stat', {}).get('status')}"
            fb = f"{o.get('chart', {}).get('status')} / {o.get('stat', {}).get('status')}"
            print(f"| {cid} | {fa} | {fb} |")


if __name__ == "__main__":
    main()
