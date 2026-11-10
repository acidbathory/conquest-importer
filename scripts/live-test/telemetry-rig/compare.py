"""Compare local Prometheus (reference) with New Relic for the converter's NRQL, same window.

Usage (project environment, repository root; the rig must be running or have run for a while):
  SSL_CERT_FILE=... uv run python scripts/live-test/telemetry-rig/compare.py ENVFILE OUTJSON [MINUTES]

Read-only. Prometheus: query_range on 127.0.0.1:9090. New Relic: NerdGraph NRQL (NEW_RELIC_API_KEY).
Each case is evaluated twice: as a chart (TIMESERIES 60 SECONDS) and as a stat (no TIMESERIES, whole
window; Prometheus side rewrites the range selector to the window and reads one instant).
The PromQL for New Relic carries ckg_test="true" in each selector, which the converter turns into a
WHERE; Prometheus is queried without it. Both sides see `prometheus_server=ckg-local` data only
through that attribute filter, because the converter has no way to add it (ckg_test is unique to the rig).
"""

import json
import math
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "grafana-sandbox"))
import nrcommon  # noqa: E402

from importer.sources.grafana.dashboard_convert.name_map import NameMap  # noqa: E402
from importer.sources.grafana.dashboard_convert.promql import (  # noqa: E402
    QueryOptions,
    Unconvertible,
    convert_query,
)
from importer.sources.grafana.prom_hints import read_hints  # noqa: E402

PROM = "http://127.0.0.1:9090"
STEP = 60
SKIP = 1  # buckets dropped at the start of the window (edge bucket)

# id, group, PromQL (plain; no ckg_test), chart-only?
CASES: list[tuple[str, str, str]] = [
    (
        "rate_cpu_mode",
        "rate",
        'sum by (mode) (rate(node_cpu_seconds_total{job="node-extras"}[5m]))',
    ),
    (
        "rate_cpu_busy",
        "rate",
        'sum by (instance) (rate(node_cpu_seconds_total{job="node-extras",mode!="idle"}[5m]))',
    ),
    (
        "rate_cadvisor_cpu",
        "rate",
        'sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+"}[5m])) * 100',
    ),
    (
        "rate_cadvisor_rx",
        "rate",
        'sum by (name) (rate(container_network_receive_bytes_total{name=~".+"}[5m]))',
    ),
    (
        "rate_reset_container",
        "reset",
        'sum by (name) (rate(container_cpu_usage_seconds_total{instance="127.0.0.1:9131",name="worker"}[5m]))',
    ),
    (
        "rate_nginx_total_cpu",
        "rate",
        "sum(rate(nginx_ingress_controller_nginx_process_cpu_seconds_total[2m]))",
    ),
    (
        "irate_nginx_requests",
        "irate-gauge-stored",
        "sum(irate(nginx_ingress_controller_requests[2m]))",
    ),
    (
        "rate_nginx_by_ingress_ok",
        "rate-gauge-stored",
        'sum by (ingress) (rate(nginx_ingress_controller_requests{status!~"[4-5].*"}[2m]))',
    ),
    (
        "ratio_nginx_success",
        "ratio",
        'sum(rate(nginx_ingress_controller_requests{status!~"[4-5].*"}[2m])) / sum(rate(nginx_ingress_controller_requests[2m]))',
    ),
    (
        "irate_pg_commit",
        "irate-gauge-stored",
        "sum by (datname) (irate(pg_stat_database_xact_commit[5m]))",
    ),
    (
        "irate_pg_bgwriter",
        "irate",
        'irate(pg_stat_bgwriter_buffers_alloc_total{instance="127.0.0.1:9141"}[5m])',
    ),
    (
        "rate_pg_reset",
        "reset",
        'sum(rate(pg_stat_database_xact_commit{instance="127.0.0.1:9142",datname="app"}[5m]))',
    ),
    (
        "increase_http_count",
        "increase",
        "sum by (application) (increase(http_server_requests_seconds_count[10m]))",
    ),
    (
        "rate_http_sum_latency",
        "summary",
        "sum(rate(http_server_requests_seconds_sum[5m])) / sum(rate(http_server_requests_seconds_count[5m]))",
    ),
    (
        "avg_over_time_conn",
        "avg_over_time",
        "sum(avg_over_time(nginx_ingress_controller_nginx_process_connections[2m]))",
    ),
    ("avg_rss", "gauge", "avg(nginx_ingress_controller_nginx_process_resident_memory_bytes)"),
    ("sum_alloc_cpu", "gauge-sum", 'sum(kube_node_status_allocatable{resource="cpu"})'),
    ("sum_pods_running", "gauge-sum", 'sum(kube_pod_status_phase{phase="Running"})'),
    (
        "ratio_requests_alloc",
        "gauge-ratio",
        'sum(kube_pod_container_resource_requests{resource="cpu"}) / sum(kube_node_status_allocatable{resource="cpu"})',
    ),
    ("jvm_heap_by_app", "gauge-sum", 'sum by (application) (jvm_memory_used_bytes{area="heap"})'),
    (
        "mem_used_pct",
        "gauge-ratio",
        '100 * (1 - node_memory_MemAvailable_bytes{job="node-extras"} / node_memory_MemTotal_bytes{job="node-extras"})',
    ),
    (
        "hq_nginx_p95",
        "histogram",
        "histogram_quantile(0.95, sum by (le) (rate(nginx_ingress_controller_request_duration_seconds_bucket[2m])))",
    ),
    (
        "hq_nginx_p50_ingress",
        "histogram",
        "histogram_quantile(0.5, sum by (le, ingress) (rate(nginx_ingress_controller_request_duration_seconds_bucket[2m])))",
    ),
    (
        "hq_http_p99_app",
        "histogram",
        "histogram_quantile(0.99, sum by (le, application) (rate(http_server_requests_seconds_bucket[5m])))",
    ),
    (
        "topk_tx",
        "topk",
        'topk(3, sum by (name) (rate(container_network_transmit_bytes_total{name=~".+"}[5m])))',
    ),
    ("changes_restarts", "changes", "changes(kube_pod_container_status_restarts_total[30m]) > 1"),
    (
        "join_group_left",
        "join",
        'sum by (node) (kube_pod_container_resource_requests{resource="cpu"} * on (pod) group_left(node) kube_pod_info)',
    ),
    ("up_by_job", "up", "sum by (job) (up)"),
    ("up_flaky_avg", "up", 'avg_over_time(up{job="flaky"}[10m])'),
    (
        "alert_5xx_ratio",
        "alert",
        'sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"5.."}[2m])) / sum by (ingress) (rate(nginx_ingress_controller_requests[2m]))',
    ),
    (
        "alert_5xx_ratio_gt",
        "alert",
        'sum by (ingress) (rate(nginx_ingress_controller_requests{status=~"5.."}[2m])) / sum by (ingress) (rate(nginx_ingress_controller_requests[2m])) > 0.05',
    ),
    ("alert_target_down", "alert", 'up{job=~"flaky|missing"} == 0'),
    ("recording_name", "recording", "instance:node_cpu_utilisation:rate5m"),
    ("uptime_days", "gauge", '(time() - container_start_time_seconds{name=~".+"}) / 86400'),
    ("absent_failed_jobs", "absent", "absent(kube_job_status_failed)"),
    ("count_cmp_evicted", "comparison", 'count(kube_pod_status_reason{reason="Evicted"} == 1)'),
]


# Cases for the type experiment: run against both prometheus_server names ("ckg-local" untyped,
# "ckg-local-typed" with newrelic_metric_type forced by write_relabel_configs).
TYPED_CASES: list[tuple[str, str, str]] = [
    (
        "t_nginx_rate_ingress",
        "typed",
        "sum by (ingress) (rate(nginx_ingress_controller_requests[2m]))",
    ),
    ("t_nginx_irate_total", "typed", "sum(irate(nginx_ingress_controller_requests[2m]))"),
    ("t_pg_commit_irate", "typed", "sum by (datname) (irate(pg_stat_database_xact_commit[5m]))"),
    ("t_pg_activity_state", "typed", 'sum by (state) (pg_stat_activity_count{datname="app"})'),
    (
        "t_pg_locks_mode",
        "typed",
        'sum by (mode) (pg_locks_count{datname="app",mode=~"accesssharelock|rowexclusivelock"})',
    ),
    ("t_system_cpu_count", "typed", "sum(system_cpu_count)"),
]


def prom_get(path: str, **params: str) -> Any:
    with urllib.request.urlopen(f"{PROM}{path}?{urllib.parse.urlencode(params)}", timeout=60) as r:
        return json.load(r)["data"]


def metric_names() -> list[str]:
    return sorted(prom_get("/api/v1/label/__name__/values"), key=len, reverse=True)


def tag(expr: str, names: list[str], server: str | None = None) -> str:
    """Add ckg_test="true" (and prometheus_server) to every selector of the metrics in `names`."""
    extra = f',prometheus_server="{server}"' if server else ""
    pat = re.compile(r"(?<![\w:])(" + "|".join(re.escape(n) for n in names) + r")(?![\w:])(\{)?")

    def sub(m: re.Match[str]) -> str:
        if m[2]:
            return m[1] + '{ckg_test="true"' + extra + ","
        return m[1] + '{ckg_test="true"' + extra + "}"

    return pat.sub(sub, expr)


SKIP_KEYS = ("beginTimeSeconds", "endTimeSeconds", "facet", "x")


def value_of(row: dict[str, Any]) -> float | None:
    """The one numeric result column of a row; bucketPercentile returns {"95": value}."""
    for k, v in row.items():
        if k in SKIP_KEYS:
            continue
        if isinstance(v, dict) and v:
            v = next(iter(v.values()))
        if isinstance(v, int | float):
            return float(v)
    return None


def fkey(facet: Any, n: int) -> tuple[str, ...]:
    if facet is None:
        return ()
    return tuple(str(x) for x in facet) if isinstance(facet, list) else (str(facet),)


def local_hints(names: list[str]) -> NameMap:
    """Types and series keys from the local Prometheus, through the project's own read_hints."""

    def get(path: str, params: dict[str, str]) -> Any:
        real = "/api/v1/" + path.split("/api/v1/", 1)[1]
        with urllib.request.urlopen(
            f"{PROM}{real}?{urllib.parse.urlencode(params)}", timeout=60
        ) as r:
            return json.load(r)

    h = read_hints(get, "local", set(names))
    return NameMap.model_validate({"types": h.types, "series": h.series})


def run_case(
    cid: str,
    expr: str,
    t0: int,
    t1: int,
    names: list[str],
    chart: bool,
    nmap: NameMap,
    server: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"id": cid, "mode": "chart" if chart else "stat", "promql": expr}
    used = [n for n in names if re.search(r"(?<![\w:])" + re.escape(n) + r"(?![\w:])", expr)]
    opts = QueryOptions(
        variables={}, name_map=nmap, timeseries=chart, bucket_seconds=STEP if chart else None
    )
    try:
        conv = convert_query(tag(expr, used, server), "prometheus", opts)
    except Unconvertible as e:
        out["status"] = "UNCONVERTIBLE"
        out["detail"] = str(e)[:200]
        return out
    except Exception as e:  # report, do not hide
        out["status"] = "CONVERTER-ERROR"
        out["detail"] = f"{type(e).__name__}: {e}"[:200]
        return out
    out["nrql"] = conv.nrql
    out["notes"] = [n[:140] for n in conv.notes]
    out["facet"] = conv.facet
    q = f"{conv.nrql} SINCE {t0} UNTIL {t1}"
    nr = nrcommon.nrql(q)
    if nr and "error" in nr[0]:
        out["status"] = "NR-ERROR"
        out["detail"] = nr[0]["error"]
        return out
    if chart:
        pe = prom_get(
            "/api/v1/query_range", query=expr, start=str(t0), end=str(t1), step=str(STEP)
        )["result"]
        out.update(compare_chart(pe, nr, conv.facet, t0))
        # the same expression with every range set to the New Relic bucket: data path without window semantics
        e1 = re.sub(r"\[\d+[smhd]\]", f"[{STEP}s]", expr)
        if (
            e1 != expr and "increase(" not in expr
        ):  # increase() scales to its own range, so a 1m twin is not comparable
            p1 = prom_get(
                "/api/v1/query_range", query=e1, start=str(t0), end=str(t1), step=str(STEP)
            )["result"]
            c1 = compare_chart(p1, nr, conv.facet, t0)
            out["status_1m"] = c1.get("status")
            out["series_1m"] = c1.get("series")
    else:
        w = t1 - t0
        pexpr = re.sub(r"\[\d+[smhd]\]", f"[{w}s]", expr)
        pe = prom_get("/api/v1/query", query=pexpr, time=str(t1))["result"]
        out["promql_stat"] = pexpr
        out.update(compare_stat(pe, nr, conv.facet))
    return out


def nice(x: float | None) -> float | None:
    return None if x is None or math.isnan(x) else float(f"{x:.6g}")


def compare_chart(pe: list[dict], nr: list[dict], facet: list[str], t0: int) -> dict[str, Any]:
    nr_series: dict[tuple[str, ...], dict[int, float]] = {}
    for row in nr:
        v = value_of(row)
        if v is None:
            continue
        nr_series.setdefault(fkey(row.get("facet"), len(facet)), {})[int(row["endTimeSeconds"])] = v
    prom_series: dict[tuple[str, ...], dict[int, float]] = {}
    for s in pe:
        k = tuple(s["metric"].get(f, "") for f in facet) if facet else ()
        prom_series.setdefault(k, {}).update({int(t): float(v) for t, v in s["values"]})
        if not facet and len(pe) > 1:
            prom_series[k] = {}  # several Prometheus series, NR merged them: flagged below
    res: dict[str, Any] = {"prom_series": len(pe), "nr_series": len(nr_series)}
    if not nr_series:
        res["status"] = "EMPTY"
        return res
    rows = []
    for k, pv in prom_series.items():
        nv = nr_series.get(k)
        if nv is None:
            rows.append({"key": k, "missing_in": "nr"})
            continue
        ts = sorted(t for t in pv if t - t0 > SKIP * STEP)
        # a NR bucket ends at endTimeSeconds; Prometheus evaluates at the same instant: align on end time
        pairs = [(pv[t], nv[t]) for t in ts if t in nv]
        if len(pairs) < 3:
            rows.append({"key": k, "n": len(pairs), "note": "too few aligned points"})
            continue
        mp = statistics.fmean(p for p, _ in pairs)
        mn = statistics.fmean(n for _, n in pairs)
        errs = [abs(n - p) / abs(p) for p, n in pairs if abs(p) > 1e-12]
        rows.append(
            {
                "key": k,
                "n": len(pairs),
                "prom_mean": nice(mp),
                "nr_mean": nice(mn),
                "mean_ratio": nice(mn / mp) if mp else (1.0 if abs(mn) < 1e-12 else None),
                "median_bucket_err": nice(statistics.median(errs)) if errs else None,
            }
        )
    res["series"] = rows[:6]
    res["extra_nr_series"] = [list(k) for k in nr_series if k not in prom_series][:3]
    res["status"] = judge(rows, len(pe), len(facet), len(nr_series))
    return res


def judge(rows: list[dict], pn: int, nf: int, nn: int) -> str:
    if not nf and pn > 1:
        return "MERGED"
    ok = [r for r in rows if r.get("mean_ratio") is not None]
    if not ok:
        return "NO-OVERLAP"
    worst = max(abs(r["mean_ratio"] - 1) for r in ok)
    shape = max((r["median_bucket_err"] or 0) for r in ok)
    if len(ok) < len(rows) or nn != pn:
        return "SERIES-DIFF"
    if worst <= 0.05:
        return "MATCH" if shape <= 0.15 else "MATCH-MEAN"
    return "MISMATCH"


def compare_stat(pe: list[dict], nr: list[dict], facet: list[str]) -> dict[str, Any]:
    nrv: dict[tuple[str, ...], float] = {}
    for row in nr:
        v = value_of(row)
        if v is not None:
            nrv[fkey(row.get("facet"), len(facet))] = v
    pv: dict[tuple[str, ...], float] = {}
    for s in pe:
        pv[tuple(s["metric"].get(f, "") for f in facet) if facet else ()] = float(s["value"][1])
    res: dict[str, Any] = {"prom_series": len(pe), "nr_series": len(nrv)}
    if not nrv:
        res["status"] = "EMPTY"
        return res
    rows = []
    for k, p in pv.items():
        n = nrv.get(k)
        rows.append(
            {
                "key": k,
                "prom": nice(p),
                "nr": nice(n),
                "ratio": nice(n / p) if (n is not None and p) else None,
            }
        )
    res["series"] = rows[:6]
    if not facet and len(pe) > 1:
        res["status"] = "MERGED"
    else:
        r = [x["ratio"] for x in rows if x["ratio"] is not None]
        res["status"] = (
            (
                "MATCH"
                if r and all(abs(x - 1) <= 0.05 for x in r) and len(nrv) == len(pv)
                else "MISMATCH"
            )
            if r
            else "NO-OVERLAP"
        )
    return res


def main() -> None:
    nrcommon.load_env(sys.argv[1])
    if len(sys.argv) > 4:  # explicit window: T0 T1 as epoch seconds (multiples of 60)
        t0, t1 = int(sys.argv[3]), int(sys.argv[4])
    else:
        minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 30
        t1 = int(time.time() // 60 * 60) - 180  # ingest latency margin
        t0 = t1 - minutes * 60
    names = metric_names()
    results = []
    maps = {"plain": NameMap(), "hints": local_hints([n for n in names if not n.startswith("go_")])}
    for variant, nmap in maps.items():
        todo = [(c, g, e, None) for c, g, e in CASES]
        todo += [
            (c + "@" + sv, g, e, sv)
            for c, g, e in TYPED_CASES
            for sv in ("ckg-local", "ckg-local-typed")
        ]
        for cid, _grp, expr, server in todo:
            for chart in (True, False):
                try:
                    r = run_case(cid, expr, t0, t1, names, chart, nmap, server)
                except Exception as e:  # keep going; the failure is a result
                    r = {
                        "id": cid,
                        "mode": "chart" if chart else "stat",
                        "status": "HARNESS-ERROR",
                        "detail": f"{type(e).__name__}: {e}"[:200],
                    }
                r["group"] = _grp
                r["variant"] = variant
                results.append(r)
                print(f"{variant:5} {cid:26} {r['mode']:5} {r['status']}", flush=True)
    Path(sys.argv[2]).write_text(
        json.dumps({"since": t0, "until": t1, "step": STEP, "results": results}, indent=1)
    )


if __name__ == "__main__":
    main()
