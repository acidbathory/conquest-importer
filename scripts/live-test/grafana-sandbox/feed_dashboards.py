#!/usr/bin/env python3
"""Feed New Relic with realistic telemetry so the converted ckg-* dashboards show charts.

Reads the NerdGraph dashboard payloads (the output of the converter), derives for every metric the
type (counter -> delta `count` with interval.ms, everything else -> `gauge`), the attributes the
widget queries facet or filter on, and the variable queries, then:

  1. backfills the last --backfill-minutes (default 120) at 60 s steps,
  2. feeds live every 15 s for --live-minutes (default 45), then stops by itself.

Node Exporter Full uses REAL data from a local node_exporter (official release binary you
downloaded and verified, bound to 127.0.0.1) for the series macOS provides; everything else is
synthetic. Datapath rules: docs/research/grafana-notifications-datapath.md (label -> attribute,
metricName -> metric_name, counter -> delta count).

Safety: keys come from the environment/.env and are never printed; the account must be in
NEW_RELIC_ALLOWED_ACCOUNT_IDS; every datapoint carries ckg_test=true and ckg_source
(synthetic | synthetic-backfill | node_exporter); hard caps on points per request, per tick and in
total; TLS verification stays on (set SSL_CERT_FILE for a corporate CA). Only the Metric API is
written to; NRQL reads are in verify_dashboards.py.

Usage: feed_dashboards.py --payload-dir DIR --env FILE [--node-exporter BIN] [--dry-run] [--daemon]
"""

import argparse
import glob
import gzip
import itertools
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zlib
from typing import Any

import nrcommon

METRIC_API = nrcommon.METRIC_API
POST_CHUNK = 1500
MAX_TOTAL_POINTS = 1_500_000
MAX_TICK_POINTS = 20_000
BACKFILL_STEP = 60
LIVE_STEP = 15
REAL_SERIES_PER_METRIC = 12
NE_URL = "http://127.0.0.1:9100/metrics"

AGG = re.compile(r"\b(?:sum|average|latest|max|min|count|bucketPercentile)\(\s*([A-Za-z_]\w*)")
RATE = re.compile(r"rate\(\s*(?:filter\(\s*)?sum\(\s*([A-Za-z_]\w*)")
CLAUSE = re.compile(
    r"\b([A-Za-z_]\w*)\s+(NOT IN|IN|!=|=|NOT RLIKE|RLIKE|LIKE|IS NOT NULL|IS NULL)"
    r"(?:\s*(\([^)]*\)|'[^']*'))?"
)
LIT = re.compile(r"'([^']*)'")
COUNTER_NAME = re.compile(r"(_total|_sum|_count|_bucket)$")
COUNTER_EXTRA = {
    "pg_stat_database_blks_hit",
    "pg_stat_database_blks_read",
    "nginx_ingress_controller_success",
}
SMALL_EVENTS = re.compile(
    r"(err|drop|fail|retrans|colls|carrier|nohandler|overflow|squeez|oom_kill|timeout|rst|syncookie|conflict|"
    r"deadlock|hung|edac|fifo|frame|compressed|multicast|discard|noport|buf_|rcvq|ofoqueue|throttle|refused|"
    r"fsync|pswp|pgmajfault|Errors|Drops|Failed)"
)

# attribute domains (default values for an attribute a query facets or filters on)
DOMAINS: dict[str, list[str]] = {
    "device": ["sda", "nvme0n1"],
    "cpu": ["0", "1"],
    "state": ["active", "inactive", "runnable", "waiting"],
    "status": ["200", "404", "500", "503"],
    "level": ["error", "warn", "info", "debug"],
    "datname": ["app", "postgres", "analytics"],
    "mode": ["accesssharelock", "rowexclusivelock", "exclusivelock"],
    "collector": ["cpu", "meminfo", "netdev", "diskstats", "filesystem"],
    "name": ["unit-a", "unit-b"],
    "controller": ["0"],
    "package": ["0"],
    "power_supply": ["BAT0"],
    "ingress": ["web", "api", "admin"],
    "statefulset": ["kafka", "redis", "postgres"],
    "hpa": ["frontend", "worker"],
    "node": ["node-a", "node-b", "node-c"],
    "namespace": ["default", "prod", "monitoring"],
    "le": [],
}

# paired attributes per metric (metric regex -> list of label sets); the keys are always added
PAIRS: list[tuple[str, list[dict[str, str]]]] = [
    (
        r"^jvm_memory_(used|committed|max)_bytes$",
        [
            {"area": "heap", "id": "G1 Eden Space"},
            {"area": "heap", "id": "G1 Old Gen"},
            {"area": "heap", "id": "G1 Survivor Space"},
            {"area": "nonheap", "id": "Metaspace"},
            {"area": "nonheap", "id": "CodeHeap 'non-nmethods'"},
            {"area": "nonheap", "id": "Compressed Class Space"},
        ],
    ),
    (r"^jvm_buffer_", [{"id": "direct"}, {"id": "mapped"}]),
    (
        r"^jvm_gc_pause_",
        [
            {"action": "end of minor GC", "cause": "G1 Evacuation Pause"},
            {"action": "end of major GC", "cause": "Allocation Failure"},
        ],
    ),
    (
        r"^node_filesystem_",
        [
            {"device": "/dev/sda1", "fstype": "ext4", "mountpoint": "/"},
            {"device": "/dev/sdb1", "fstype": "xfs", "mountpoint": "/data"},
        ],
    ),
    (r"^node_network_", [{"device": "eth0"}, {"device": "ens5"}, {"device": "lo"}]),
    (r"^node_disk_", [{"device": "sda"}, {"device": "nvme0n1"}]),
    (r"^node_(softnet|schedstat|cpu_)", [{"cpu": "0"}, {"cpu": "1"}]),
    (
        r"^node_scrape_collector",
        [{"collector": c} for c in ["cpu", "meminfo", "netdev", "diskstats", "filesystem"]],
    ),
    (r"^node_edac_", [{"controller": "0"}]),
    (r"^node_systemd_socket", [{"name": "sshd.socket"}, {"name": "docker.socket"}]),
    (r"^pg_static$", [{"short_version": "15.4", "version": "15.4.0", "server": "pg"}]),
    (
        r"^container_",
        [{"name": f"c{i}", "id": f"/docker/c{i}", "image": f"img{i}"} for i in range(1, 5)],
    ),
    (r"^kube_statefulset_", [{"statefulset": "kafka"}, {"statefulset": "redis"}]),
    (r"^kube_hpa_", [{"hpa": "frontend"}, {"hpa": "worker"}]),
    (r"^kube_node_info$", [{"node": "node-a"}, {"node": "node-b"}, {"node": "node-c"}]),
]
GIB = 1024.0**3
MEM_FRAC = {
    "MemTotal": 1.0,
    "MemFree": 0.15,
    "Cached": 0.25,
    "Buffers": 0.03,
    "SReclaimable": 0.04,
    "Slab": 0.06,
    "PageTables": 0.005,
    "SwapCached": 0.002,
    "Active": 0.35,
    "Inactive": 0.2,
    "Active_anon": 0.18,
    "Inactive_anon": 0.03,
    "Active_file": 0.17,
    "Inactive_file": 0.17,
    "AnonPages": 0.2,
    "Mapped": 0.05,
    "Shmem": 0.02,
    "Dirty": 0.001,
    "Writeback": 0.0002,
    "Mlocked": 0.0005,
    "Unevictable": 0.001,
    "KernelStack": 0.002,
    "SUnreclaim": 0.02,
    "AnonHugePages": 0.0,
    "Percpu": 0.001,
    "CommitLimit": 1.0,
    "Committed_AS": 0.5,
    "VmallocTotal": 4096.0,
    "VmallocUsed": 0.002,
    "VmallocChunk": 0.0,
    "DirectMap4k": 0.02,
    "DirectMap2M": 0.4,
    "DirectMap1G": 0.5,
    "HardwareCorrupted": 0.0,
    "NFS_Unstable": 0.0,
    "WritebackTmp": 0.0,
    "ShmemHugePages": 0.0,
    "ShmemPmdMapped": 0.0,
    "Zswap": 0.001,
    "Zswapped": 0.002,
    "SwapFree": 0.12,
    "SwapTotal": 0.125,
}
CONST_GAUGES = {
    "node_processes_max_processes": 4194304,
    "node_processes_max_threads": 4194304,
    "node_filefd_maximum": 1048576,
    "node_entropy_pool_size_bits": 4096,
    "node_nf_conntrack_entries_limit": 262144,
    "process_max_fds": 1048576,
    "process_files_max_files": 1048576,
    "process_virtual_memory_max_bytes": 1.8e19,
    "tomcat_threads_config_max_threads": 200,
    "jetty_threads_config_max": 200,
    "jvm_buffer_total_capacity_bytes": 67108864,
    "node_memory_Hugepagesize_bytes": 2097152,
    "node_memory_HugePages_Total": 0,
    "node_memory_HugePages_Free": 0,
    "node_memory_HugePages_Rsvd": 0,
    "node_memory_HugePages_Surp": 0,
    "kube_hpa_spec_max_replicas": 10,
    "kube_hpa_spec_min_replicas": 2,
    "kube_statefulset_status_replicas": 3,
    "kube_statefulset_status_replicas_ready": 3,
    "pg_settings_max_connections": 200,
    "pg_settings_shared_buffers_bytes": 2 * GIB,
    "pg_settings_effective_cache_size_bytes": 6 * GIB,
    "pg_settings_maintenance_work_mem_bytes": 512 * 1048576,
    "pg_settings_work_mem_bytes": 16 * 1048576,
    "pg_settings_max_wal_size_bytes": 4 * GIB,
    "pg_settings_random_page_cost": 1.1,
    "pg_settings_seq_page_cost": 1.0,
    "pg_settings_max_worker_processes": 8,
    "pg_settings_max_parallel_workers": 8,
    "node_cpu_scaling_frequency_max_hertz": 3.4e9,
    "node_cpu_scaling_frequency_min_hertz": 8e8,
    "node_timex_loop_time_constant": 2,
    "node_timex_maxerror_seconds": 0.5,
    "node_network_mtu_bytes": 1500,
    "node_network_speed_bytes": 1.25e9,
    "node_sockstat_TCP_mem_bytes": 3e6,
    "node_vmstat_pgfault_const": 0,
    "pg_exporter_last_scrape_duration_seconds": 0.04,
    "nginx_ingress_controller_config_hash": 1.2345e19,
    "nginx_ingress_controller_nginx_process_resident_memory_bytes": 1.2e8,
}
GAUGE_LEVELS: list[tuple[str, float, float]] = [
    # regex, level, amplitude (fraction of level)
    (r"^node_cpu_scaling_frequency_hertz", 2.4e9, 0.2),
    (r"^node_processes_pids", 320, 0.1),
    (r"^node_processes_threads", 900, 0.1),
    (r"^node_filefd_allocated", 2400, 0.1),
    (r"^node_entropy_available_bits", 3500, 0.1),
    (r"^node_nf_conntrack_entries$", 1800, 0.3),
    (r"^node_sockstat_.*_(mem|alloc)", 800, 0.3),
    (r"^node_sockstat_", 40, 0.3),
    (r"^node_tcp_connection_states", 30, 0.4),
    (r"^node_udp_queues", 2000, 0.5),
    (r"^node_systemd_units", 6, 0.1),
    (r"^node_procs_", 3, 0.5),
    (r"^node_arp_entries", 12, 0.2),
    (r"^node_disk_io_now", 1.5, 0.8),
    (r"^node_timex_offset", 0.0004, 0.8),
    (r"^node_timex_estimated_error", 0.0008, 0.5),
    (r"^node_timex_frequency", 1.0, 0.0001),
    (r"^process_open_fds", 14, 0.2),
    (r"^process_resident_memory_bytes", 2.4e7, 0.1),
    (r"^process_virtual_memory_bytes", 1.2e9, 0.02),
    (r"^node_scrape_collector_duration", 0.006, 0.6),
    (r"^process_uptime", 0, 0),
    (r"^process_memory_rss_bytes", 7.5e8, 0.1),
    (r"^process_memory_swap_bytes", 2e7, 0.1),
    (r"^process_threads", 85, 0.05),
    (r"^process_files_open_files", 420, 0.1),
    (r"^jvm_threads_live", 70, 0.1),
    (r"^jvm_threads_daemon", 55, 0.1),
    (r"^jvm_threads_peak", 92, 0.02),
    (r"^jvm_threads_states", 12, 0.3),
    (r"^jvm_classes_loaded", 17500, 0.01),
    (r"^jvm_buffer_memory_used", 4e7, 0.2),
    (r"^jvm_buffer_count", 30, 0.1),
    (r"^jvm_gc_pause_seconds_max", 0.03, 0.8),
    (r"^tomcat_threads_busy", 14, 0.5),
    (r"^tomcat_threads_current", 40, 0.1),
    (r"^jetty_threads_busy", 9, 0.5),
    (r"^jetty_threads_current", 32, 0.1),
    (r"^http_server_requests_seconds_max", 0.4, 0.8),
    (r"^pg_stat_database_numbackends", 25, 0.3),
    (r"^kube_hpa_status_current_replicas", 5, 0.4),
    (r"^container_memory", 1.5e8, 0.2),
    (r"^nginx_ingress_controller_nginx_process_connections", 90, 0.3),
    (r"_bytes$", 3e8, 0.2),
    (r"_seconds$", 0.02, 0.5),
]
COUNTER_LEVELS: list[tuple[str, float]] = [
    (r"^node_cpu_seconds", 0.2),
    (r"^process_cpu_seconds", 0.01),
    (r"^container_cpu_usage_seconds", 0.25),
    (r"^node_disk_(read|written)_bytes", 1.5e6),
    (r"^node_network_(receive|transmit)_bytes", 3e5),
    (r"^node_netstat_.*Octets", 2e5),
    (r"^node_disk_io_time_seconds", 0.12),
    (r"^node_disk_io_time_weighted", 0.2),
    (r"^node_disk_.*time_seconds", 0.02),
    (r"^node_pressure_", 0.004),
    (r"^node_disk_.*(completed|merged)", 60),
    (r"^node_network_.*(packets)", 400),
    (r"^node_netstat_.*(Segs|Datagrams|Msgs)", 300),
    (r"^node_(intr|context_switches)_total", 8000),
    (r"^node_forks_total", 4),
    (r"^node_vmstat_pgp?(in|out)", 900),
    (r"^node_vmstat_pgfault", 3000),
    (r"^node_softnet_processed", 250),
    (r"^node_schedstat_timeslices", 1500),
    (r"^node_netstat_Tcp_(Active|Passive)Opens", 3),
    (r"^container_network_.*_bytes", 1.2e5),
    (r"^jvm_gc_memory_allocated", 5e6),
    (r"^jvm_gc_memory_promoted", 4e5),
    (r"^logback_events_total", 1.2),
    (r"^http_server_requests_seconds_count", 25),
    (r"^jvm_gc_pause_seconds_count", 0.05),
    (r"^pg_stat_database_xact_commit", 220),
    (r"^pg_stat_database_xact_rollback", 1.2),
    (r"^pg_stat_database_(blks_hit)", 5000),
    (r"^pg_stat_database_blks_read", 150),
    (r"^pg_stat_database_temp_bytes", 3e4),
    (r"^pg_stat_bgwriter_checkpoint_(write|sync)_time", 20),
    (r"^pg_stat_bgwriter_", 40),
    (r"^nginx_ingress_controller_requests", 18),
    (r"^nginx_ingress_controller_request_duration_seconds_bucket", 18),
    (r"^nginx_ingress_controller_(request|response)_size_sum", 6e4),
    (r"^nginx_ingress_controller_nginx_process_cpu", 0.08),
    (r"^nginx_ingress_controller_success", 0.002),
]
STATUS_WEIGHT = {"200": 1.0, "301": 0.1, "404": 0.04, "500": 0.01, "503": 0.005}
LEVEL_WEIGHT = {"error": 0.05, "warn": 0.2, "info": 3.0, "debug": 6.0}
LE = ["0.005", "0.01", "0.025", "0.05", "0.1", "0.25", "0.5", "1", "2.5", "5", "10", "+Inf"]


def h01(*parts: object) -> float:
    return (zlib.crc32("|".join(map(str, parts)).encode()) & 0xFFFFFF) / float(0x1000000)


def gauss(*parts: object) -> float:
    u1, u2 = max(h01(*parts, "a"), 1e-9), h01(*parts, "b")
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


def wave(seed: str, t: float, amp: float, noise: float, spikes: float = 3.0) -> float:
    """Multiplier around 1: a daily wave, a ~40 min wave, noise and rare short spikes."""
    ph = h01(seed, "ph")
    v = (
        1
        + amp * math.sin(2 * math.pi * (t / 86400 + ph))
        + amp * 0.6 * math.sin(2 * math.pi * (t / 2400 + ph * 3))
    )
    v += noise * gauss(seed, int(t))
    block = int(t // 900)
    if h01(seed, block) < 0.06 and (t % 900) < 120:
        v *= 1 + spikes * h01(seed, block, "m")
    return max(v, 0.0)


# ---------------------------------------------------------------------------------------------
# spec derivation from the NerdGraph payloads
# ---------------------------------------------------------------------------------------------


def load_dashboards(payload_dir: str) -> list[dict[str, Any]]:
    out = []
    for path in sorted(glob.glob(os.path.join(payload_dir, "*.json"))):
        dash = json.load(open(path))["variables"]["dashboard"]
        queries = [
            (w["title"], q["query"])
            for p in dash["pages"]
            for w in p["widgets"]
            for q in w["rawConfiguration"].get("nrqlQueries", [])
        ]
        out.append(
            {"name": dash["name"], "queries": queries, "variables": dash.get("variables", [])}
        )
    return out


class MetricSpec:
    def __init__(self, name: str) -> None:
        self.name = name
        self.kind = "gauge"
        self.attrs: set[str] = set()
        self.consts: dict[str, set[str]] = {}

    def absorb(self, query: str) -> None:
        for m in re.finditer(r"FACET (.*?)(?: LIMIT| TIMESERIES| SINCE|$)", query):
            self.attrs.update(a.strip() for a in m[1].split(","))
        for m in CLAUSE.finditer(query):
            attr, op, rhs = m[1], m[2], m[3] or ""
            if attr in {"metricName", "FROM", "AND", "WHERE"} or attr.isupper():
                continue
            self.attrs.add(attr)
            if op in ("=", "IN") and "{{" not in rhs:
                self.consts.setdefault(attr, set()).update(LIT.findall(rhs))


def derive(dashboard: dict[str, Any]) -> dict[str, MetricSpec]:
    specs: dict[str, MetricSpec] = {}
    texts = [q for _, q in dashboard["queries"]]
    var_texts = [v["nrqlQuery"]["query"] for v in dashboard["variables"]]
    for q in texts:
        for m in AGG.finditer(q):
            specs.setdefault(m[1], MetricSpec(m[1])).absorb(q)
        for m in RATE.finditer(q):
            specs.setdefault(m[1], MetricSpec(m[1])).kind = "count"
    for q in var_texts:
        m = re.search(r"metricName = '(\w+)'", q)
        sel = re.search(r"uniques\((\w+)\)", q)
        if m and sel:
            spec = specs.setdefault(m[1], MetricSpec(m[1]))
            spec.attrs.add(sel[1])
            spec.absorb(q.split("WHERE", 1)[1] if "WHERE" in q else "")
    for s in specs.values():
        if COUNTER_NAME.search(s.name) or s.name in COUNTER_EXTRA:
            s.kind = "count"
    return specs


# ---------------------------------------------------------------------------------------------
# series model
# ---------------------------------------------------------------------------------------------


def entities(profile: str, host: str) -> list[dict[str, str]]:
    if profile == "node":
        rows = [
            ("node", f"{host}:9100", host),
            ("node", "node-b.ckg-synthetic:9100", "node-b"),
            ("node-edge", "edge-1.ckg-synthetic:9100", "edge-1"),
        ]
        return [{"job": j, "instance": i, "nodename": n} for j, i, n in rows]
    if profile == "jvm":
        return [
            {"application": a, "instance": i}
            for a, i in [
                ("orders-svc", "10.0.1.11:8080"),
                ("orders-svc", "10.0.1.12:8080"),
                ("payments-svc", "10.0.2.21:8080"),
            ]
        ]
    if profile == "pg":
        return [
            {"kubernetes_namespace": ns, "release": r, "instance": i}
            for ns, r, i in [
                ("db-prod", "pg-main", "pg-main-0:9187"),
                ("db-prod", "pg-reports", "pg-reports-0:9187"),
                ("db-stage", "pg-stage", "pg-stage-0:9187"),
            ]
        ]
    if profile == "nginx":
        return [
            {
                "controller_namespace": ns,
                "namespace": ns,
                "controller_class": c,
                "controller_pod": p,
                "controller": p,
            }
            for ns, c, p in [
                ("ingress-nginx", "k8s.io/ingress-nginx", "ingress-nginx-controller-abc12"),
                ("ingress-nginx", "k8s.io/ingress-nginx", "ingress-nginx-controller-def34"),
                ("ingress-edge", "nginx-internal", "ingress-edge-controller-ghi56"),
            ]
        ]
    if profile == "cadvisor":
        return [{"instance": f"10.0.3.{i}:8080"} for i in (31, 32, 33)]
    return [{"cluster": c} for c in ("prod-eu", "prod-us")]


def profile_of(name: str) -> str:
    n = name.lower()
    for key, prof in [
        ("node exporter", "node"),
        ("jvm", "jvm"),
        ("postgres", "pg"),
        ("nginx", "nginx"),
        ("cadvisor", "cadvisor"),
    ]:
        if key in n:
            return prof
    return "ksm"


def combos(spec: MetricSpec, ent_keys: set[str]) -> list[dict[str, str]]:
    base: list[dict[str, str]] = [{}]
    for pat, rows in PAIRS:
        if re.search(pat, spec.name):
            base = [dict(r) for r in rows]
            break
    fixed = set(base[0])
    rest = sorted(a for a in spec.attrs - ent_keys - fixed if a != "le")
    domains = []
    for a in rest:
        vals = sorted(spec.consts.get(a, set())) or DOMAINS.get(a, [f"{a}-1", f"{a}-2"])
        domains.append(vals or [f"{a}-1"])
    out = []
    for row in base:
        for prod in itertools.product(*domains):
            out.append({**row, **dict(zip(rest, prod, strict=True))})
    return out


class Series:
    def __init__(
        self, spec: MetricSpec, labels: dict[str, str], ent: dict[str, str], is_real_host: bool
    ) -> None:
        self.spec = spec
        self.labels = {("metric_name" if k == "metricName" else k): v for k, v in labels.items()}
        self.ent = ent
        self.real_host = is_real_host
        self.extra = False  # synthetic series added next to real ones (device names the dashboard regex accepts)
        self.seed = spec.name + json.dumps(self.labels, sort_keys=True)
        self.mult = 0.6 + 0.8 * h01(self.seed, "mult")
        if spec.name.startswith("kube_node_info") or spec.name == "node_uname_info":
            self.mult = 1.0

    # ---- rates / values -----------------------------------------------------------------
    def counter_rate(self, t: float, sib: "dict[str, Series]") -> float:
        n = self.spec.name
        if n.endswith("_bucket"):
            return 0.0  # handled in points()
        level = next((lv for pat, lv in COUNTER_LEVELS if re.search(pat, n)), 20.0)
        if SMALL_EVENTS.search(n) and not re.search(r"(bytes|packets|Octets)", n):
            level = 0.01
        if n.endswith("_sum") and "request_size" not in n and "response_size" not in n:
            cnt = sib.get(n[:-4] + "_count")
            if cnt is not None:
                return (
                    cnt.counter_rate(t, sib)
                    * (0.045 if "http" in n else 0.02)
                    * (1 + 0.3 * math.sin(t / 900))
                )
        st = self.labels.get("status")
        if st:
            level *= STATUS_WEIGHT.get(st, 0.01)
        lv = self.labels.get("level")
        if lv:
            level *= LEVEL_WEIGHT.get(lv, 0.1)
        if "pg_stat_database" in n and self.labels.get("datname") == "postgres":
            level *= 0.05
        if self.labels.get("device") == "lo":
            level *= 0.3
        rate = level * self.mult * wave(self.seed, t, 0.3, 0.12, 2.5)
        if n.startswith(
            ("node_cpu_seconds", "container_cpu", "node_disk_io_time", "node_pressure")
        ):
            rate = min(rate, 0.95)
        return rate

    def gauge_value(self, t: float, t0: float) -> float:
        n = self.spec.name
        if n in CONST_GAUGES:
            return float(CONST_GAUGES[n]) * (
                1 if n.startswith(("pg_", "kube_", "tomcat", "jetty")) else self.mult0()
            )
        if re.search(
            r"(_info|^up|^pg_up|^pg_static|_created|collector_success|network_carrier|power_supply|sync_status)",
            n,
        ):
            return 1.0
        if n == "process_uptime_seconds":
            return t - (t0 - 86400 * (1 + 9 * h01(self.seed, "up")))
        if n in ("node_boot_time_seconds",):
            return t0 - 86400 * (2 + 10 * h01(self.seed, "boot"))
        m = re.match(r"node_memory_(\w+?)_bytes$", n)
        if m and m[1] in MEM_FRAC:
            total = (8, 16, 32)[int(h01(self.ent.get("instance", ""), "mem") * 3)] * GIB
            frac = MEM_FRAC[m[1]]
            return (
                total
                * frac
                * (
                    1
                    if m[1] in ("MemTotal", "CommitLimit", "SwapTotal", "VmallocTotal")
                    else wave(self.seed, t, 0.05, 0.01, 0.3)
                )
            )
        if n == "node_filesystem_size_bytes":
            return (100 if self.labels.get("mountpoint") == "/" else 500) * GIB
        if n in ("node_filesystem_avail_bytes", "node_filesystem_free_bytes"):
            size = (100 if self.labels.get("mountpoint") == "/" else 500) * GIB
            return size * (0.35 + 0.1 * h01(self.seed, "fs")) * (1 - 0.01 * math.sin(t / 3000))
        if re.match(r"^jvm_memory_(used|committed)_bytes$", n):
            mx = (1.0 if self.labels.get("area") == "heap" else 0.25) * GIB
            part = 0.4 if self.labels.get("id") in ("G1 Old Gen", "Metaspace") else 0.15
            frac = part * (0.6 if "used" in n else 0.85) / 0.85
            return (
                mx * frac * self.mult * wave(self.seed, t, 0.1 if "used" in n else 0.02, 0.01, 0.2)
            )
        if n == "jvm_memory_max_bytes":
            return (1.0 if self.labels.get("area") == "heap" else 0.25) * GIB
        for pat, level, amp in GAUGE_LEVELS:
            if re.search(pat, n):
                return level * self.mult * wave(self.seed, t, amp, amp * 0.15, 0.5)
        return 40.0 * self.mult * wave(self.seed, t, 0.25, 0.05, 1.0)

    def mult0(self) -> float:
        return 0.9 + 0.2 * h01(self.ent.get("instance", ""), self.spec.name)

    def points(
        self, t: int, interval: int, t0: float, sib: "dict[str, Series]", source: str
    ) -> list[dict[str, Any]]:
        attrs = {**self.ent, **self.labels, "ckg_test": "true", "ckg_source": source}
        if self.spec.name != "node_uname_info":
            attrs.pop("nodename", None)
        if self.spec.kind != "count":
            return [
                {
                    "name": self.spec.name,
                    "type": "gauge",
                    "value": round(self.gauge_value(t, t0), 6),
                    "timestamp": t,
                    "attributes": attrs,
                }
            ]
        if self.spec.name.endswith("_bucket"):
            total = 18.0 * self.mult * wave(self.seed, t, 0.3, 0.12, 2.5) * interval
            out = []
            med = 0.08 * (1 + 0.5 * math.sin(t / 1500))
            for le in LE:
                x = float("inf") if le == "+Inf" else float(le)
                cdf = (
                    1.0
                    if x == float("inf")
                    else 0.5 * (1 + math.erf((math.log(x / med)) / (0.9 * math.sqrt(2))))
                )
                out.append(
                    {
                        "name": self.spec.name,
                        "type": "count",
                        "value": round(total * cdf, 4),
                        "timestamp": t,
                        "interval.ms": interval * 1000,
                        "attributes": {**attrs, "le": le},
                    }
                )
            return out
        rate = self.counter_rate(t, sib)
        return [
            {
                "name": self.spec.name,
                "type": "count",
                "value": round(rate * interval, 6),
                "timestamp": t,
                "interval.ms": interval * 1000,
                "attributes": attrs,
            }
        ]


class Model:
    def __init__(
        self,
        dashboards: list[dict[str, Any]],
        host: str,
        real_combos: dict[str, list[dict[str, str]]],
    ):
        self.series: list[Series] = []
        self.by_profile: dict[str, list[Series]] = {}
        for dash in dashboards:
            prof = profile_of(dash["name"])
            specs = derive(dash)
            ents = entities(prof, host)
            ent_keys = set(ents[0])
            for spec in specs.values():
                if prof == "node" and spec.name == "node_uname_info":
                    spec.attrs.add("nodename")
                for ei, ent in enumerate(ents):
                    real = prof == "node" and ei == 0
                    rows = (
                        real_combos.get(spec.name)
                        if real and spec.name in real_combos
                        else combos(spec, ent_keys)
                    )
                    for row in rows:
                        s = Series(spec, row, ent, real)
                        self.series.append(s)
                        self.by_profile.setdefault(prof, []).append(s)
                    if real and spec.name in real_combos and spec.name.startswith("node_disk_"):
                        for row in combos(
                            spec, ent_keys
                        ):  # macOS names (disk0) fail the dashboard's device regex
                            s = Series(spec, row, ent, real)
                            s.extra = True
                            self.series.append(s)
                            self.by_profile.setdefault(prof, []).append(s)

    def sibling_index(self) -> dict[tuple[str, str], dict[str, Series]]:
        idx: dict[tuple[str, str], dict[str, Series]] = {}
        for s in self.series:
            key = (json.dumps(s.ent, sort_keys=True), json.dumps(s.labels, sort_keys=True))
            idx.setdefault(key, {})[s.spec.name] = s
        return idx


# ---------------------------------------------------------------------------------------------
# posting
# ---------------------------------------------------------------------------------------------


class Poster:
    def __init__(self, dry: bool, log: Any) -> None:
        self.dry, self.log, self.total, self.requests = dry, log, 0, 0

    def send(self, points: list[dict[str, Any]]) -> None:
        if not points:
            return
        if self.total + len(points) > MAX_TOTAL_POINTS:
            raise SystemExit("total point cap reached; stopping")
        for p in points:
            if p["attributes"].get("ckg_test") != "true":
                raise SystemExit("refusing to send a point without ckg_test=true")
        for i in range(0, len(points), POST_CHUNK):
            chunk = points[i : i + POST_CHUNK]
            self.total += len(chunk)
            if self.dry:
                continue
            body = gzip.compress(json.dumps([{"metrics": chunk}]).encode())
            self._post(body)
            time.sleep(0.25)

    def _post(self, body: bytes) -> None:
        for attempt in range(4):
            req = urllib.request.Request(
                METRIC_API,
                data=body,
                method="POST",
                headers={
                    "Api-Key": os.environ["NEW_RELIC_LICENSE_KEY"],
                    "Content-Type": "application/json",
                    "Content-Encoding": "gzip",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    self.requests += 1
                    if resp.status not in (200, 202):
                        self.log(f"unexpected status {resp.status}")
                    return
            except urllib.error.HTTPError as e:
                self.log(f"metric api HTTP {e.code} (attempt {attempt + 1})")
                if e.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise SystemExit(f"metric api HTTP {e.code}; stopping") from None
                time.sleep(5 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError) as e:
                self.log(f"network error {type(e).__name__} (attempt {attempt + 1})")
                if attempt == 3:
                    raise SystemExit("network error; stopping") from None
                time.sleep(5 * (attempt + 1))


# ---------------------------------------------------------------------------------------------
# real node_exporter
# ---------------------------------------------------------------------------------------------

SAMPLE = re.compile(r"([A-Za-z_:][\w:]*)(?:\{(.*)\})? (\S+)")
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def scrape() -> dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, float]]:
    text = urllib.request.urlopen(NE_URL, timeout=10).read().decode()
    types: dict[str, str] = {}
    out: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[str, float]] = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            parts = line.split()
            types[parts[2]] = parts[3]
        elif line and not line.startswith("#"):
            m = SAMPLE.fullmatch(line.strip())
            if m:
                labels = tuple(sorted(LABEL.findall(m[2] or "")))
                try:
                    out[(m[1], labels)] = (types.get(m[1], "untyped"), float(m[3]))
                except ValueError:
                    continue
    return out


def real_points(
    prev: Any, cur: Any, ts: int, elapsed: float, wanted: set[str], host: str
) -> list[dict[str, Any]]:
    pts: list[dict[str, Any]] = []
    per_metric: dict[str, int] = {}
    for key, (kind, val) in cur.items():
        name, labels = key
        if name not in wanted:
            continue
        per_metric[name] = per_metric.get(name, 0) + 1
        if per_metric[name] > REAL_SERIES_PER_METRIC:
            continue
        attrs = {k: v for k, v in labels}
        if "metricName" in attrs:
            attrs["metric_name"] = attrs.pop("metricName")
        attrs.update(
            job="node", instance=f"{host}:9100", ckg_test="true", ckg_source="node_exporter"
        )
        if kind == "counter":
            if prev is None or key not in prev:
                continue
            before = prev[key][1]
            delta = val if val < before else val - before
            pts.append(
                {
                    "name": name,
                    "type": "count",
                    "value": delta,
                    "timestamp": ts,
                    "interval.ms": int(elapsed * 1000),
                    "attributes": attrs,
                }
            )
        else:
            pts.append(
                {"name": name, "type": "gauge", "value": val, "timestamp": ts, "attributes": attrs}
            )
    return pts


def start_node_exporter(binary: str, log: Any) -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(  # noqa: S603 - the verified node_exporter binary, fixed arguments
        [binary, "--web.listen-address=127.0.0.1:9100"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        try:
            scrape()
            log("node_exporter up on 127.0.0.1:9100")
            return proc
        except OSError:
            time.sleep(1)
    proc.terminate()
    raise SystemExit("node_exporter did not start")


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------


def run(args: argparse.Namespace, log: Any) -> None:
    nrcommon.load_env(args.env)
    nrcommon.account()
    host = socket.gethostname().split(".")[0].lower()
    dashboards = load_dashboards(args.payload_dir)
    node_specs = next(derive(d) for d in dashboards if profile_of(d["name"]) == "node")
    wanted = set(node_specs)
    proc = start_node_exporter(args.node_exporter, log) if args.node_exporter else None
    try:
        first = scrape() if proc else {}
        real_combos: dict[str, list[dict[str, str]]] = {}
        counts: dict[str, int] = {}
        for name, labels in first:
            if name in wanted and counts.get(name, 0) < REAL_SERIES_PER_METRIC:
                counts[name] = counts.get(name, 0) + 1
                real_combos.setdefault(name, []).append(
                    {k: v for k, v in labels if k not in ("job", "instance")}
                )
        model = Model(dashboards, host, real_combos)
        sib = model.sibling_index()
        per_series_sib = {
            id(s): sib[(json.dumps(s.ent, sort_keys=True), json.dumps(s.labels, sort_keys=True))]
            for s in model.series
        }
        log(
            f"series: {len(model.series)}; per profile: "
            + ", ".join(f"{k}={len(v)}" for k, v in model.by_profile.items())
        )
        poster = Poster(args.dry_run, log)
        t0 = time.time()
        # backfill
        end = int(t0) // BACKFILL_STEP * BACKFILL_STEP
        steps = args.backfill_minutes * 60 // BACKFILL_STEP
        for i in range(steps):
            ts = end - (steps - 1 - i) * BACKFILL_STEP
            pts = []
            for s in model.series:
                if args.backfill_extra_only and not s.extra:
                    continue
                pts += s.points(
                    ts,
                    BACKFILL_STEP,
                    t0,
                    per_series_sib[id(s)],
                    "synthetic-backfill" if s.real_host else "synthetic",
                )
            poster.send(pts)
            if i % 20 == 0:
                log(f"backfill step {i + 1}/{steps}, points sent {poster.total}")
        log(f"backfill done: {poster.total} points, {poster.requests} requests")
        # live
        live_end = time.time() + args.live_minutes * 60
        log("live feed until " + time.strftime("%H:%M", time.localtime(live_end)) + " local")
        prev, prev_t = first, time.time()
        tick = int(time.time()) // LIVE_STEP * LIVE_STEP
        while time.time() < live_end and not STOP:
            tick += LIVE_STEP
            time.sleep(max(0.0, tick - time.time()))
            pts = []
            cur = scrape() if proc else None
            now = time.time()
            real_names: set[str] = set()
            if cur is not None:
                rp = real_points(prev, cur, tick, now - prev_t, wanted, host)
                real_names = {p["name"] for p in rp}
                pts += rp
                prev, prev_t = cur, now
            for s in model.series:
                if s.real_host and not s.extra and s.spec.name in real_names:
                    continue
                pts += s.points(tick, LIVE_STEP, t0, per_series_sib[id(s)], "synthetic")
            if len(pts) > MAX_TICK_POINTS:
                raise SystemExit("tick point cap exceeded")
            poster.send(pts)
            if tick % 300 == 0:
                log(f"live tick {tick}: {len(pts)} points, total {poster.total}")
        log(f"done: {poster.total} points, {poster.requests} requests")
    finally:
        if proc:
            proc.terminate()
            log("node_exporter stopped")


STOP = False


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-dir", required=True)
    ap.add_argument("--env", required=True)
    ap.add_argument("--node-exporter", help="path to the verified node_exporter binary")
    ap.add_argument("--backfill-minutes", type=int, default=120)
    ap.add_argument("--live-minutes", type=int, default=45)
    ap.add_argument(
        "--backfill-extra-only",
        action="store_true",
        help="backfill only the extra synthetic series",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--out-dir", default=os.path.join(root, "out", "live-run"))
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    logpath = os.path.join(args.out_dir, "telemetry-feed.log")
    pidpath = os.path.join(args.out_dir, "telemetry-feed.pid")
    if args.daemon:
        if os.fork():
            return 0
        os.setsid()
        if os.fork():
            os._exit(0)
        with open(os.devnull) as dn:
            os.dup2(dn.fileno(), 0)
        with open(logpath, "a") as lf:
            os.dup2(lf.fileno(), 1)
            os.dup2(lf.fileno(), 2)
        with open(pidpath, "w") as pf:
            pf.write(str(os.getpid()))

    def log(msg: str) -> None:
        print(time.strftime("%H:%M:%S"), msg, flush=True)

    def on_term(_s: int, _f: Any) -> None:
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, on_term)
    try:
        run(args, log)
    finally:
        if args.daemon and os.path.exists(pidpath):
            os.remove(pidpath)
    return 0


if __name__ == "__main__":
    sys.exit(main())
