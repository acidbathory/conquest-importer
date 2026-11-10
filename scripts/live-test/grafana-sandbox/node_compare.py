"""Run the converter's NRQL for Node Exporter style PromQL against New Relic and compare with raw scrapes.

Usage (from the repository root, with the project environment):
  uv run python scripts/live-test/grafana-sandbox/node_compare.py RAWFILE ENVFILE OUTJSON

Read-only: NerdGraph NRQL only. For each expression it records two expectations from the raw
scrapes kept locally: `nr` (what the Metric API data should give if New Relic does its arithmetic
on the points we sent) and `prom` (what Prometheus itself would show). The converter's NRQL is
run twice: as a chart (TIMESERIES 60 SECONDS, buckets anchored at SINCE) and as a stat (no TIMESERIES).
"""

import json
import math
import sys
from collections.abc import Callable
from typing import Any

import node_exporter_roundtrip as ner
import nrcommon

from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query

B = 60
Labels = dict[str, str]
Group = tuple[str, ...]


def load(raw: str) -> tuple[list[float], dict[str, list[tuple[Labels, list[tuple[float, float]]]]]]:
    times: list[float] = []
    series: dict[str, dict[tuple[tuple[str, str], ...], list[tuple[float, float]]]] = {}
    for line in open(raw):
        s = json.loads(line)
        times.append(s["ts"])
        for (name, labels), (_kind, val) in ner.parse(s["text"]).items():
            series.setdefault(name, {}).setdefault(labels, []).append((s["ts"], val))
    out: dict[str, list[tuple[Labels, list[tuple[float, float]]]]] = {}
    for name, d in series.items():
        out[name] = [({**dict(k), "job": "ckg_node", "instance": "127.0.0.1:9100"}, v) for k, v in d.items()]
    return times, out


def gkey(labels: Labels, by: list[str]) -> Group:
    return tuple(labels.get(b, "") for b in by)


class Ctx:
    def __init__(self, raw: str) -> None:
        self.times, self.data = load(raw)
        self.since = int(self.times[0])
        span = self.times[-1] + 1 - self.since
        self.until = self.since + B * math.ceil(span / B)
        self.nb = (self.until - self.since) // B
        self.stat_until = math.ceil(self.times[-1])  # the stat window ends at the last scrape

    def k(self, ts: float) -> int:
        return int((ts - self.since) // B)

    def pick(self, metric: str, filt: Callable[[Labels], bool]) -> list[tuple[Labels, list[tuple[float, float]]]]:
        return [(lab, v) for lab, v in self.data[metric] if filt(lab)]

    # --- expectations: dict group -> dict bucket -> value (None bucket = whole window)
    def rate(self, metric: str, filt: Callable[[Labels], bool], by: list[str]) -> tuple[dict, dict]:
        nr: dict[Group, dict[int | None, float]] = {}
        prom: dict[Group, dict[int | None, float]] = {}
        for lab, vs in self.pick(metric, filt):
            g = gkey(lab, by)
            total = 0.0
            for (_, a), (t, b) in zip(vs, vs[1:], strict=False):
                d = b if b < a else b - a
                total += d
                cell = nr.setdefault(g, {})
                cell[self.k(t)] = cell.get(self.k(t), 0.0) + d / B
                cell[None] = cell.get(None, 0.0) + d / (self.stat_until - self.since)
            p = prom.setdefault(g, {})
            p[None] = p.get(None, 0.0) + total / (vs[-1][0] - vs[0][0])
        for g, cell in prom.items():
            for kk in nr[g]:
                if kk is not None:
                    cell[kk] = cell[None]
        return nr, prom

    def gauge(self, metric: str, filt: Callable[[Labels], bool], by: list[str], agg: str) -> tuple[dict, dict]:
        samples: dict[Group, dict[int, list[float]]] = {}
        lastper: dict[Group, dict[int, dict[int, float]]] = {}
        allv: dict[Group, list[float]] = {}
        latest: dict[Group, dict[int, float]] = {}
        for si, (lab, vs) in enumerate(self.pick(metric, filt)):
            g = gkey(lab, by)
            for t, v in vs:
                samples.setdefault(g, {}).setdefault(self.k(t), []).append(v)
                allv.setdefault(g, []).append(v)
                lastper.setdefault(g, {}).setdefault(self.k(t), {})[si] = v
            latest.setdefault(g, {})[si] = vs[-1][1]

        def red(xs: list[float]) -> float:
            return sum(xs) if agg == "sum" else sum(xs) / len(xs)

        nr: dict[Group, dict[int | None, float]] = {}
        prom: dict[Group, dict[int | None, float]] = {}
        for g, d in samples.items():
            nr[g] = {kk: red(xs) for kk, xs in d.items()}
            nr[g][None] = red(allv[g])
            nr[g][-1] = list(latest[g].values())[-1]
            prom[g] = {kk: red(list(per.values())) for kk, per in lastper[g].items()}
            prom[g][None] = red(list(latest[g].values()))
        return nr, prom


def combine(a: dict, b: dict, f: Callable[[float, float], float]) -> dict:
    return {g: {kk: f(a[g][kk], b[g][kk]) for kk in a[g] if kk in b.get(g, {})} for g in a}


def run(raw: str, env: str, out: str) -> None:
    nrcommon.load_env(env)
    c = Ctx(raw)
    job = lambda lab: True  # noqa: E731
    nolo = lambda lab: lab.get("device") != "lo"  # noqa: E731
    notmp = lambda lab: lab.get("fstype") != "tmpfs"  # noqa: E731
    notidle = lambda lab: lab.get("mode") != "idle"  # noqa: E731
    cpu = "node_cpu_seconds_total"
    tests: list[tuple[str, str, Callable[[], tuple[dict, dict]]]] = [
        ("cpu by mode", f'sum by (mode) (rate({cpu}{{job="ckg_node"}}[5m]))', lambda: c.rate(cpu, job, ["mode"])),
        ("cpu not idle by instance",
         f'sum by (instance) (rate({cpu}{{job="ckg_node", mode!="idle"}}[$__rate_interval]))',
         lambda: c.rate(cpu, notidle, ["instance"])),
        ("mem used", 'node_memory_total_bytes{job="ckg_node"} - node_memory_free_bytes{job="ckg_node"}',
         lambda: tuple(combine(x, y, lambda p, q: p - q) for x, y in zip(  # type: ignore[return-value]
             c.gauge("node_memory_total_bytes", job, [], "avg"), c.gauge("node_memory_free_bytes", job, [], "avg"), strict=True))),
        ("mem free", 'node_memory_free_bytes{job="ckg_node"}', lambda: c.gauge("node_memory_free_bytes", job, [], "avg")),
        ("mem used percent",
         '100 * (1 - sum(node_memory_free_bytes{job="ckg_node"}) / sum(node_memory_total_bytes{job="ckg_node"}))',
         lambda: tuple(combine(x, y, lambda p, q: 100 * (1 - p / q)) for x, y in zip(  # type: ignore[return-value]
             c.gauge("node_memory_free_bytes", job, [], "sum"), c.gauge("node_memory_total_bytes", job, [], "sum"), strict=True))),
        ("load1 avg", 'avg(node_load1{job="ckg_node"})', lambda: c.gauge("node_load1", job, [], "avg")),
        ("load1", 'node_load1{job="ckg_node"}', lambda: c.gauge("node_load1", job, [], "avg")),
        ("net rx by device", 'sum by (device) (rate(node_network_receive_bytes_total{job="ckg_node", device!="lo"}[5m]))',
         lambda: c.rate("node_network_receive_bytes_total", nolo, ["device"])),
        ("fs avail (no facet)", 'node_filesystem_avail_bytes{job="ckg_node", fstype!="tmpfs"}',
         lambda: c.gauge("node_filesystem_avail_bytes", notmp, [], "avg")),
        ("fs avail sum by mountpoint", 'sum by (mountpoint) (node_filesystem_avail_bytes{job="ckg_node"})',
         lambda: c.gauge("node_filesystem_avail_bytes", job, ["mountpoint"], "sum")),
    ]
    results: list[dict[str, Any]] = []
    for name, expr, expect in tests:
        for chart in (True, False):
            opts = QueryOptions(variables={}, name_map=NameMap(), timeseries=chart, bucket_seconds=B if chart else None)
            conv = convert_query(expr, "prometheus", opts)
            window = f" SINCE {c.since} UNTIL {c.until if chart else c.stat_until}"
            q = conv.nrql
            q = q.replace(" TIMESERIES", window + " TIMESERIES") if chart else q + window
            if chart and "TIMESERIES" not in q:
                q += window
            rows = nrcommon.nrql(q)
            nr, prom = expect()
            if not chart and conv.nrql.startswith("SELECT latest"):
                nr, prom = _latest(c, name, nr, prom)
            res = {"name": name, "promql": expr, "nrql": conv.nrql, "chart": chart, "notes": conv.notes,
                   "facet": conv.facet, "rows": len(rows)}
            res.update(_compare(c, conv.facet, rows, nr, prom, chart))
            results.append(res)
            print(name, "chart" if chart else "stat", {k: res[k] for k in res if k.startswith(("max", "stat", "error"))})
    json.dump(results, open(out, "w"), indent=1)


def _latest(c: Ctx, name: str, nr: dict, prom: dict) -> tuple[dict, dict]:
    """A stat of a bare gauge is latest(): one sample, not a mean."""
    return {g: {None: cells.get(-1, cells[None])} for g, cells in nr.items()}, prom


def _compare(c: Ctx, facet: list[str], rows: list[dict], nr: dict, prom: dict, chart: bool) -> dict[str, Any]:
    if rows and "error" in rows[0]:
        return {"error": rows[0]["error"][:200]}
    value_key = next((k for k in rows[0] if k not in ("beginTimeSeconds", "endTimeSeconds", "facet", *facet)), None) if rows else None
    got: dict[Group, dict[int | None, float]] = {}
    for r in rows:
        g = tuple(str(r.get(f, "")) for f in facet)
        kk = int((r["beginTimeSeconds"] - c.since) // B) if chart else None
        v = r.get(value_key)
        if isinstance(v, (int, float)):
            got.setdefault(g, {})[kk] = float(v)
    if not got:
        return {"error": "no numeric rows", "rows_raw": rows[:2]}
    interior = range(1, c.nb - 1)
    out: dict[str, Any] = {"groups": len(got), "groups_expected": len(nr)}
    worst_nr = worst_prom = 0.0
    sample: dict[str, Any] = {}
    for g, cells in got.items():
        e_nr, e_pr = nr.get(g), prom.get(g)
        if e_nr is None:
            continue
        keys = [kk for kk in cells if (kk in interior if chart else True)]
        for kk in keys:
            if kk in e_nr:
                worst_nr = max(worst_nr, abs(cells[kk] - e_nr[kk]) / (abs(e_nr[kk]) or 1.0))
            if e_pr and kk in e_pr:
                worst_prom = max(worst_prom, abs(cells[kk] - e_pr[kk]) / (abs(e_pr[kk]) or 1.0))
        if not sample:
            kk = keys[len(keys) // 2] if keys else None
            sample = {"group": list(g), "observed": cells.get(kk), "expected_nr": e_nr.get(kk), "expected_prom": (e_pr or {}).get(kk)}
        if chart and 0 in cells and 0 in e_nr:
            out.setdefault("first_bucket", []).append([cells[0], e_nr[0], (e_pr or {}).get(0)])
    out["max_rel_err_vs_nr"] = round(worst_nr, 6)
    out["max_rel_err_vs_prom"] = round(worst_prom, 6)
    out["sample"] = sample
    return out


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3])
