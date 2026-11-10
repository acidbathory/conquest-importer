"""Synthetic Prometheus exporter for the telemetry rig (stdlib only, binds 127.0.0.1 only).

One process serves several "targets", one TCP port each, so Prometheus sees several `instance`
values. Metric names, types and labels follow the community dashboards we convert: node_exporter
extras that macOS lacks, kube-state-metrics v2, cAdvisor, JVM Micrometer, postgres_exporter and the
NGINX ingress controller. Counters accumulate and reset on a schedule, histograms are cumulative
with _bucket/_sum/_count, info metrics carry value 1, some series appear or vanish on a schedule,
and one port is down part of the time. See docs/research/telemetry-rig-results.md for the cardinality.

Usage: exporter.py [--ttl SECONDS]   (exits by itself after the ttl, default 5400)
"""

import argparse
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = time.time()


def el() -> float:
    return time.time() - T0


def wave(period: float, phase: float = 0.0, amp: float = 0.3) -> float:
    return 1.0 + amp * math.sin(2 * math.pi * el() / period + phase)


def esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class Page:
    """Families with cumulative state. Counter values survive between scrapes."""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.fams: dict[str, str] = {}
        self.vals: dict[str, dict[tuple, float]] = {}
        self.last = time.time()
        self.dt = 0.0

    def tick(self) -> None:
        now = time.time()
        self.dt = now - self.last
        self.last = now

    def _put(self, name: str, kind: str, labels: dict, value: float, add: bool) -> None:
        self.fams[name] = kind
        d = self.vals.setdefault(name, {})
        k = tuple(sorted(labels.items()))
        d[k] = d.get(k, 0.0) + value if add else value

    def gauge(self, name: str, labels: dict, value: float) -> None:
        self._put(name, "gauge", labels, value, False)

    def counter(self, name: str, labels: dict, rate: float) -> None:
        """Add rate*dt (with 5 percent noise) to a cumulative counter."""
        self._put(name, "counter", labels, rate * self.dt * self.rng.uniform(0.95, 1.05), True)

    def hist(
        self, name: str, labels: dict, les: list, rate: float, median: float, sigma: float = 0.6
    ) -> None:
        n = round(rate * self.dt * self.rng.uniform(0.9, 1.1))
        self.fams[name] = "histogram"
        for le in les:
            frac = (
                1.0
                if le == "+Inf"
                else 0.5 * (1 + math.erf((math.log(le) - math.log(median)) / (sigma * 2**0.5)))
            )
            self._put(name + "_bucket", "counter", {**labels, "le": str(le)}, round(n * frac), True)
        self._put(name + "_sum", "counter", labels, n * median * math.exp(sigma**2 / 2), True)
        self._put(name + "_count", "counter", labels, n, True)

    def reset(self, prefix: str, **match: str) -> None:
        for name, d in self.vals.items():
            if name.startswith(prefix):
                for k in d:
                    if all(dict(k).get(a) == b for a, b in match.items()):
                        d[k] = 0.0

    def render(self) -> str:
        out = []
        seen = set()
        for name in sorted(self.vals):
            base = name
            for suf in ("_bucket", "_sum", "_count"):
                if name.endswith(suf) and self.fams.get(name[: -len(suf)]) in (
                    "histogram",
                    "summary",
                ):
                    base = name[: -len(suf)]
            if base != name and base in seen:
                pass
            elif base != name:
                out.append(f"# TYPE {base} {self.fams[base]}")
                seen.add(base)
            else:
                out.append(f"# TYPE {name} {self.fams[name]}")
            for k, v in sorted(self.vals[name].items()):
                lab = ",".join(f'{a}="{esc(str(b))}"' for a, b in k)
                out.append(f"{name}{{{lab}}} {v:.10g}" if lab else f"{name} {v:.10g}")
        return "\n".join(out) + "\n"


LAT = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, "+Inf"]


def lbl(**kw: str) -> dict:
    return kw


# ---------------------------------------------------------------- kube-state-metrics v2
CLUSTERS = {
    "prod-a": ["node-a1", "node-a2", "node-a3"],
    "prod-b": ["node-b1", "node-b2", "node-b3"],
}
NAMESPACES = ["default", "kube-system", "monitoring", "payments", "web"]


def ksm(p: Page) -> None:
    # a label named metricName collides with the New Relic attribute that holds the metric name
    p.gauge("ckg_metricname_probe", {"metricName": "orders_total", "env": "rig"}, 7)
    pods = []
    for c, nodes in CLUSTERS.items():
        for ni, node in enumerate(nodes):
            p.gauge(
                "kube_node_info",
                lbl(cluster=c, node=node, kernel_version="6.1.0", os_image="Ubuntu 22.04"),
                1,
            )
            p.gauge(
                "kube_node_spec_unschedulable",
                lbl(cluster=c, node=node),
                1 if node == "node-b3" and 600 < el() < 1500 else 0,
            )
            for r, cap, alloc in (("cpu", 8, 7.8), ("memory", 32e9, 30e9), ("pods", 110, 110)):
                p.gauge(
                    "kube_node_status_capacity",
                    lbl(cluster=c, node=node, resource=r, unit="core"),
                    cap,
                )
                p.gauge(
                    "kube_node_status_allocatable",
                    lbl(cluster=c, node=node, resource=r, unit="core"),
                    alloc,
                )
            for cond in ("Ready", "DiskPressure", "MemoryPressure", "PIDPressure"):
                bad = cond == "MemoryPressure" and node == "node-a2" and 1200 < el() < 1900
                for st in ("true", "false", "unknown"):
                    good = (st == "true") if cond == "Ready" else (st == "false")
                    on = (not good) if bad else good
                    if cond == "Ready" and bad:
                        on = False
                    p.gauge(
                        "kube_node_status_condition",
                        lbl(cluster=c, node=node, condition=cond, status=st),
                        1 if on else 0,
                    )
        for ns in NAMESPACES:
            p.gauge("kube_namespace_created", lbl(cluster=c, namespace=ns), 1.7e9 + hash(ns) % 1000)
            for i in range(3):
                pods.append(
                    (
                        c,
                        ns,
                        f"{ns}-app-{i}",
                        nodes[(i + len(ns)) % 3] if (nodes := CLUSTERS[c]) else "",
                    )
                )
    for k, (c, ns, pod, node) in enumerate(pods):
        p.gauge(
            "kube_pod_info", lbl(cluster=c, namespace=ns, pod=pod, node=node, host_ip="10.0.0.1"), 1
        )
        ph = "Running"
        if pod == "payments-app-2" and c == "prod-a":
            ph = "Pending" if 300 < el() < 900 else "Running"
        for phase in ("Pending", "Running", "Succeeded", "Failed", "Unknown"):
            p.gauge(
                "kube_pod_status_phase",
                lbl(cluster=c, namespace=ns, pod=pod, phase=phase),
                1 if phase == ph else 0,
            )
        ev = pod == "web-app-1" and c == "prod-b" and 1200 < el() < 2100
        for rs in ("Evicted", "NodeLost", "UnexpectedAdmissionError"):
            p.gauge(
                "kube_pod_status_reason",
                lbl(cluster=c, namespace=ns, pod=pod, reason=rs),
                1 if (ev and rs == "Evicted") else 0,
            )
        for res, req, lim in (("cpu", 0.25, 0.5), ("memory", 256e6, 512e6)):
            p.gauge(
                "kube_pod_container_resource_requests",
                lbl(
                    cluster=c,
                    namespace=ns,
                    pod=pod,
                    container="app",
                    node=node,
                    resource=res,
                    unit="core",
                ),
                req,
            )
            p.gauge(
                "kube_pod_container_resource_limits",
                lbl(
                    cluster=c,
                    namespace=ns,
                    pod=pod,
                    container="app",
                    node=node,
                    resource=res,
                    unit="core",
                ),
                lim,
            )
        p.gauge(
            "kube_pod_container_resource_requests_cpu_cores",
            lbl(cluster=c, namespace=ns, pod=pod, container="app", node=node),
            0.25,
        )
        p.gauge(
            "kube_pod_container_resource_requests_memory_bytes",
            lbl(cluster=c, namespace=ns, pod=pod, container="app", node=node),
            256e6,
        )
        crash = pod == "monitoring-app-0" and c == "prod-a"
        p.gauge(
            "kube_pod_container_status_running",
            lbl(cluster=c, namespace=ns, pod=pod, container="app"),
            1,
        )
        p.gauge(
            "kube_pod_container_status_waiting",
            lbl(cluster=c, namespace=ns, pod=pod, container="app"),
            1 if crash and int(el() / 120) % 2 else 0,
        )
        p.gauge(
            "kube_pod_container_status_terminated",
            lbl(cluster=c, namespace=ns, pod=pod, container="app"),
            0,
        )
        # restarts: a crash looping pod steps up every ~4 minutes; the counter lives in the page state
        lab = lbl(cluster=c, namespace=ns, pod=pod, container="app")
        p.counter("kube_pod_container_status_restarts_total", lab, 0)
        if crash and int(el()) % 240 < 15 and p.rng.random() < p.dt / 15:
            p._put("kube_pod_container_status_restarts_total", lab, 1, True)
    for c in CLUSTERS:
        for ns in NAMESPACES:
            for d in ("api", "worker"):
                lab = lbl(cluster=c, namespace=ns, deployment=d)
                rep = 3
                p.gauge("kube_deployment_status_replicas", lab, rep)
                p.gauge(
                    "kube_deployment_status_replicas_updated",
                    lab,
                    rep if not (d == "api" and ns == "web" and 1500 < el() < 1800) else 2,
                )
                p.gauge(
                    "kube_deployment_status_replicas_unavailable",
                    lab,
                    1 if (d == "api" and ns == "web" and 1500 < el() < 1800) else 0,
                )
        for ns in ("monitoring", "payments"):
            lab = lbl(cluster=c, namespace=ns, statefulset="db")
            p.gauge("kube_statefulset_status_replicas", lab, 3)
            p.gauge("kube_statefulset_status_replicas_ready", lab, 3 if el() < 2400 else 2)
        for ns in ("web", "payments"):
            lab = lbl(cluster=c, namespace=ns, hpa=f"{ns}-hpa")
            p.gauge("kube_hpa_spec_min_replicas", lab, 2)
            p.gauge("kube_hpa_spec_max_replicas", lab, 6)
            p.gauge(
                "kube_hpa_status_current_replicas", lab, round(2 + 4 * max(0, math.sin(el() / 900)))
            )
        for j in ("backup", "cleanup"):
            lab = lbl(cluster=c, namespace="default", job_name=j)
            p.gauge(
                "kube_job_status_active", lab, 1 if j == "backup" and int(el()) % 600 < 120 else 0
            )
            p.gauge("kube_job_status_succeeded", lab, 1)
            p.gauge(
                "kube_job_status_completion_time",
                lab,
                time.time() - 3600 * (30 if j == "cleanup" else 2),
            )
            if (
                el() > 2400 and j == "cleanup" and c == "prod-a"
            ):  # absent-able: appears only after 40 minutes
                p.gauge("kube_job_status_failed", lab, 2)
        for i in range(3):
            lab = lbl(cluster=c, namespace="monitoring", persistentvolumeclaim=f"data-{i}")
            for ph in ("Bound", "Pending", "Lost"):
                p.gauge(
                    "kube_persistentvolumeclaim_status_phase",
                    {**lab, "phase": ph},
                    1 if (ph == "Bound") != (i == 2 and c == "prod-b" and el() > 1800) else 0,
                )
            p.gauge("kube_persistentvolumeclaim_resource_requests_storage_bytes", lab, 20 * 2**30)


# ---------------------------------------------------------------- cAdvisor
CONTAINERS = ["web", "api", "db", "cache", "worker", "proxy"]


def cadvisor(p: Page, inst: int) -> None:
    for i, name in enumerate(CONTAINERS):
        lab = lbl(name=name, id=f"/docker/{name}{inst}", image=f"img/{name}:1.{inst}")
        p.counter(
            "container_cpu_usage_seconds_total",
            {**lab, "cpu": "total"},
            0.05 * (i + 1) * wave(600, i),
        )
        p.gauge("container_memory_rss", lab, (100 + 40 * i) * 2**20 * wave(1200, i, 0.1))
        p.gauge("container_memory_cache", lab, (50 + 20 * i) * 2**20 * wave(900, i, 0.1))
        p.counter(
            "container_network_receive_bytes_total",
            {**lab, "interface": "eth0"},
            20000 * (i + 1) * wave(400, i),
        )
        p.counter(
            "container_network_transmit_bytes_total",
            {**lab, "interface": "eth0"},
            12000 * (i + 1) * wave(500, i),
        )
        start = T0 - 86400 * (i + 1)
        if (
            name == "worker" and inst == 1 and el() > 1500
        ):  # container restart: cpu counter resets, start time moves
            if not getattr(p, "worker_reset", False):
                p.worker_reset = True  # type: ignore[attr-defined]
                p.reset("container_", name="worker")
            start = T0 + 1500
        p.gauge("container_start_time_seconds", lab, start)
    # a short-lived unnamed cgroup that the dashboards exclude with name=~".+"
    p.counter(
        "container_cpu_usage_seconds_total",
        {"name": "", "id": "/", "image": "", "cpu": "total"},
        0.5,
    )
    p.gauge("container_memory_rss", {"name": "", "id": "/", "image": ""}, 2e9)
    p.gauge("container_memory_cache", {"name": "", "id": "/", "image": ""}, 1e9)


# ---------------------------------------------------------------- JVM Micrometer
HEAP = ["G1 Eden Space", "G1 Old Gen", "G1 Survivor Space"]
NONHEAP = [
    "Metaspace",
    "CodeHeap 'non-nmethods'",
    "CodeHeap 'profiled nmethods'",
    "CodeHeap 'non-profiled nmethods'",
    "Compressed Class Space",
]
HB = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, "+Inf"]


def jvm(p: Page, app: str, k: int) -> None:
    a = lbl(application=app)
    for area, ids in (("heap", HEAP), ("nonheap", NONHEAP)):
        for j, i in enumerate(ids):
            lab = {**a, "area": area, "id": i}
            mx = -1 if area == "nonheap" and j else (1024 if area == "heap" else 256) * 2**20
            used = (80 + 60 * j) * 2**20 * wave(700 + 100 * k, j + k, 0.25)
            p.gauge("jvm_memory_used_bytes", lab, used)
            p.gauge("jvm_memory_committed_bytes", lab, used * 1.3)
            p.gauge("jvm_memory_max_bytes", lab, mx)
    for i in ("direct", "mapped"):
        lab = {**a, "id": i}
        p.gauge(
            "jvm_buffer_memory_used_bytes", lab, 2**20 * (40 if i == "direct" else 4) * wave(500, k)
        )
        p.gauge("jvm_buffer_total_capacity_bytes", lab, 2**20 * 64)
        p.gauge("jvm_buffer_count_buffers", lab, 12)
    for act, cause, r in (
        ("end of minor GC", "G1 Evacuation Pause", 0.2),
        ("end of major GC", "G1 Humongous Allocation", 0.01),
    ):
        lab = {**a, "action": act, "cause": cause}
        p.fams["jvm_gc_pause_seconds"] = "summary"
        p.counter("jvm_gc_pause_seconds_count", lab, r * wave(600, k))
        p.counter("jvm_gc_pause_seconds_sum", lab, r * 0.02 * wave(600, k))
        p.gauge("jvm_gc_pause_seconds_max", lab, 0.03 * wave(300, k))
    for s, v in (
        ("runnable", 12),
        ("blocked", 1),
        ("waiting", 20),
        ("timed-waiting", 14),
        ("new", 0),
        ("terminated", 0),
    ):
        p.gauge(
            "jvm_threads_states_threads", {**a, "state": s}, v + round(2 * math.sin(el() / 200 + k))
        )
    p.gauge("jvm_threads_live_threads", a, 48 + round(3 * math.sin(el() / 300)))
    p.gauge("jvm_threads_daemon_threads", a, 40)
    p.gauge("jvm_threads_peak_threads", a, 61)
    p.gauge("jvm_classes_loaded_classes", a, 14200 + el() / 10)
    p.gauge("process_cpu_usage", a, 0.2 * wave(500, k))
    p.gauge("system_cpu_usage", a, 0.35 * wave(500, k))
    p.gauge("system_cpu_count", a, 4)
    p.gauge("system_load_average_1m", a, 1.2 * wave(900, k))
    p.gauge("process_uptime_seconds", a, 86400 + el())
    for uri, rate, med in (
        ("/api/orders", 20, 0.04),
        ("/api/health", 5, 0.005),
        ("/api/pay", 8, 0.12),
    ):
        for status, share in (("200", 0.97), ("500", 0.03)):
            if status == "500" and uri == "/api/health":
                continue
            lab = {
                **a,
                "method": "GET",
                "uri": uri,
                "status": status,
                "outcome": "SUCCESS" if status == "200" else "SERVER_ERROR",
                "exception": "None",
            }
            p.hist(
                "http_server_requests_seconds",
                lab,
                HB,
                rate * share * wave(450, k),
                med * (3 if status == "500" else 1),
            )
            p.gauge("http_server_requests_seconds_max", lab, med * 4 * wave(200, k))


# ---------------------------------------------------------------- node_exporter extras (Linux names)
def node_extra(p: Page, k: int) -> None:
    total = 16 * 2**30
    used = total * 0.55 * wave(1500, k, 0.15)
    avail = total - used
    mem = {
        "MemTotal": total,
        "MemFree": avail * 0.4,
        "MemAvailable": avail,
        "Buffers": 3e8,
        "Cached": avail * 0.5,
        "Active": used * 0.6,
        "Inactive": used * 0.3,
        "Active_anon": used * 0.4,
        "Inactive_anon": 1e8,
        "Active_file": 2e9,
        "Inactive_file": 2e9,
        "Slab": 5e8,
        "SReclaimable": 3e8,
        "SUnreclaim": 2e8,
        "SwapTotal": 4 * 2**30,
        "SwapFree": 4 * 2**30 - 1e8 * wave(1000),
        "SwapCached": 1e6,
        "Dirty": 1e6 * wave(60),
        "Writeback": 0,
        "Mapped": 6e8,
        "AnonPages": used * 0.4,
        "Shmem": 2e7,
        "PageTables": 3e7,
        "KernelStack": 1e7,
        "Committed_AS": total * 1.1,
        "CommitLimit": total * 1.5,
        "VmallocTotal": 3.5e13,
        "VmallocUsed": 0,
        "Percpu": 1e7,
        "Mlocked": 0,
        "Unevictable": 0,
        "AnonHugePages": 0,
        "HugePages_Total": 0,
        "HugePages_Free": 0,
        "Hugepagesize": 2 * 2**20,
    }
    for n, v in mem.items():
        nm = f"node_memory_{n}" + ("" if n.startswith("HugePages") else "_bytes")
        p.gauge(nm, {}, v)
    for cpu in range(4):
        for mode, share in (
            ("idle", 0.6),
            ("user", 0.2),
            ("system", 0.1),
            ("iowait", 0.03),
            ("nice", 0.01),
            ("irq", 0.005),
            ("softirq", 0.015),
            ("steal", 0.01),
        ):
            f = wave(500, cpu + k) if mode in ("user", "system") else 1.0
            idle_adj = (2 - wave(500, cpu + k)) if mode == "idle" else 1.0
            p.counter("node_cpu_seconds_total", lbl(cpu=str(cpu), mode=mode), share * f * idle_adj)
    for w, per in (("1", 600), ("5", 900), ("15", 1200)):
        p.gauge(f"node_load{w}", {}, 1.5 * wave(per, k, 0.5))
    for mp, dev, size in (
        ("/", "/dev/sda1", 100e9),
        ("/data", "/dev/sdb1", 500e9),
        ("/boot", "/dev/sda2", 1e9),
    ):
        lab = lbl(device=dev, fstype="ext4", mountpoint=mp)
        free = size * (0.4 - (0.0001 * el() if mp == "/data" else 0))
        p.gauge("node_filesystem_size_bytes", lab, size)
        p.gauge("node_filesystem_avail_bytes", lab, free * 0.95)
        p.gauge("node_filesystem_free_bytes", lab, free)
        p.gauge("node_filesystem_files", lab, 6e6)
        p.gauge("node_filesystem_files_free", lab, 5e6)
        p.gauge("node_filesystem_readonly", lab, 0)
        p.gauge("node_filesystem_device_error", lab, 0)
    for dev in ("sda", "sdb"):
        lab = lbl(device=dev)
        for n, r in (
            ("node_disk_read_bytes_total", 4e5),
            ("node_disk_written_bytes_total", 9e5),
            ("node_disk_reads_completed_total", 40),
            ("node_disk_writes_completed_total", 90),
            ("node_disk_io_time_seconds_total", 0.1),
            ("node_disk_io_time_weighted_seconds_total", 0.15),
            ("node_disk_read_time_seconds_total", 0.05),
            ("node_disk_write_time_seconds_total", 0.08),
            ("node_disk_reads_merged_total", 3),
            ("node_disk_writes_merged_total", 8),
            ("node_disk_discards_completed_total", 0),
            ("node_disk_flush_requests_total", 2),
        ):
            p.counter(n, lab, r * wave(300, k))
        p.gauge("node_disk_io_now", lab, 1 + round(math.sin(el() / 50) + 1))
    for dev in ("eth0", "lo", "docker0"):
        lab = lbl(device=dev)
        for n, r in (
            ("receive_bytes_total", 3e5),
            ("transmit_bytes_total", 2e5),
            ("receive_packets_total", 400),
            ("transmit_packets_total", 350),
            ("receive_errs_total", 0.001),
            ("transmit_errs_total", 0.0005),
            ("receive_drop_total", 0.01),
            ("transmit_drop_total", 0),
        ):
            p.counter("node_network_" + n, lab, r * wave(240, k + len(dev)))
        p.gauge("node_network_mtu_bytes", lab, 65536 if dev == "lo" else 1500)
        p.gauge("node_network_speed_bytes", lab, 1.25e8)
        p.gauge("node_network_carrier", lab, 1)
    for n, r in (
        ("node_netstat_Tcp_ActiveOpens", 3),
        ("node_netstat_Tcp_PassiveOpens", 4),
        ("node_netstat_Tcp_InSegs", 900),
        ("node_netstat_Tcp_OutSegs", 850),
        ("node_netstat_Tcp_RetransSegs", 0.5),
        ("node_netstat_Tcp_InErrs", 0.01),
        ("node_netstat_Tcp_OutRsts", 0.2),
        ("node_netstat_Udp_InDatagrams", 30),
        ("node_netstat_Udp_OutDatagrams", 30),
        ("node_netstat_Udp_InErrors", 0),
        ("node_netstat_IpExt_InOctets", 4e5),
        ("node_netstat_IpExt_OutOctets", 3e5),
        ("node_netstat_TcpExt_ListenDrops", 0.001),
        ("node_vmstat_pgfault", 800),
        ("node_vmstat_pgmajfault", 0.5),
        ("node_vmstat_pgpgin", 200),
        ("node_vmstat_pgpgout", 400),
        ("node_vmstat_pswpin", 0),
        ("node_vmstat_pswpout", 0),
        ("node_context_switches_total", 5000),
        ("node_forks_total", 2),
        ("node_intr_total", 3000),
    ):
        p.counter(n, {}, r * wave(360, k))
    p.gauge("node_netstat_Tcp_CurrEstab", {}, 120 + 20 * math.sin(el() / 100))
    p.gauge("node_vmstat_oom_kill", {}, 0)
    for n, v in (
        ("node_sockstat_sockets_used", 300),
        ("node_sockstat_TCP_inuse", 40),
        ("node_sockstat_TCP_alloc", 55),
        ("node_sockstat_TCP_tw", 12),
        ("node_sockstat_TCP_orphan", 0),
        ("node_sockstat_UDP_inuse", 5),
        ("node_sockstat_TCP_mem_bytes", 2e5),
        ("node_filefd_allocated", 2400),
        ("node_filefd_maximum", 1e6),
        ("node_procs_running", 2),
        ("node_procs_blocked", 0),
        ("node_processes_pids", 220),
        ("node_processes_threads", 600),
        ("node_entropy_available_bits", 256),
        ("node_nf_conntrack_entries", 900),
        ("node_nf_conntrack_entries_limit", 65536),
        ("node_boot_time_seconds", T0 - 86400 * 9),
        ("node_timex_offset_seconds", 0.0002),
        ("node_timex_sync_status", 1),
        ("node_arp_entries", 14),
        ("node_textfile_scrape_error", 0),
    ):
        p.gauge(n, {}, v)
    p.gauge("node_time_seconds", {}, time.time())
    p.counter("node_pressure_cpu_waiting_seconds_total", {}, 0.02)
    p.counter("node_pressure_memory_waiting_seconds_total", {}, 0.001)
    p.counter("node_pressure_io_waiting_seconds_total", {}, 0.01)
    for st, v in (("ESTABLISHED", 120), ("TIME_WAIT", 12), ("LISTEN", 18), ("CLOSE_WAIT", 2)):
        p.gauge("node_tcp_connection_states", lbl(state=st), v)
    for st in ("active", "inactive", "failed"):
        p.gauge(
            "node_systemd_units",
            lbl(state=st),
            {"active": 180, "inactive": 40, "failed": 1 if k == 1 and el() > 2000 else 0}[st],
        )
    for chip in ("platform_coretemp_0", "pci0000:00_0000:00:01_1"):
        p.gauge(
            "node_hwmon_chip_names",
            lbl(chip=chip, chip_name="coretemp" if "core" in chip else "nvme"),
            1,
        )
        for sensor in ("temp1", "temp2"):
            p.gauge(
                "node_hwmon_temp_celsius",
                lbl(chip=chip, sensor=sensor),
                45 + 8 * math.sin(el() / 400 + k),
            )
            p.gauge("node_hwmon_temp_crit_celsius", lbl(chip=chip, sensor=sensor), 100)
    p.gauge(
        "node_uname_info",
        lbl(
            nodename=f"vm-extra-{k}",
            sysname="Linux",
            release="6.1.0",
            machine="x86_64",
            version="#1 SMP",
        ),
        1,
    )
    for col in ("cpu", "meminfo", "diskstats", "filesystem", "netstat"):
        p.gauge("node_scrape_collector_success", lbl(collector=col), 1)
        p.gauge("node_scrape_collector_duration_seconds", lbl(collector=col), 0.002 + 0.001 * k)


# ---------------------------------------------------------------- postgres_exporter
def postgres(p: Page, release: str, k: int) -> None:
    p.gauge(
        "pg_up",
        lbl(release=release),
        1 if not (release == "pg-replica" and 1800 < el() < 2100) else 0,
    )
    p.gauge(
        "pg_static",
        lbl(release=release, version="15.4", short_version="15.4.0", server="pg.local:5432"),
        1,
    )
    p.gauge("pg_postmaster_start_time_seconds", lbl(release=release), T0 - 86400 * 3)
    p.gauge(
        "pg_exporter_last_scrape_duration_seconds",
        lbl(kubernetes_namespace="db", release=release),
        0.03 * wave(100, k),
    )
    for n, v in (
        ("max_connections", 200),
        ("shared_buffers_bytes", 2**30),
        ("effective_cache_size_bytes", 3 * 2**30),
        ("maintenance_work_mem_bytes", 6.4e7),
        ("work_mem_bytes", 4e6),
        ("max_wal_size_bytes", 2**30),
        ("random_page_cost", 4),
        ("seq_page_cost", 1),
        ("max_worker_processes", 8),
        ("max_parallel_workers", 8),
    ):
        p.gauge("pg_settings_" + n, lbl(release=release) if n == "max_connections" else {}, v)
    for n, r in (
        ("buffers_alloc_total", 300),
        ("buffers_backend_fsync_total", 0.01),
        ("buffers_backend_total", 40),
        ("buffers_checkpoint_total", 120),
        ("buffers_clean_total", 60),
        ("checkpoint_sync_time_total", 5),
        ("checkpoint_write_time_total", 40),
    ):
        p.counter("pg_stat_bgwriter_" + n, {}, r * wave(600, k))
    for n in ("process_cpu_seconds_total",):
        p.counter(n, lbl(release=release), 0.04 * wave(300, k))
    p.gauge("process_resident_memory_bytes", lbl(release=release), 90e6 * wave(900, k, 0.1))
    p.gauge("process_virtual_memory_bytes", lbl(release=release), 1.2e9)
    p.gauge("process_open_fds", lbl(release=release), 24)
    for dn, scale in (("postgres", 0.2), ("app", 1.0), ("template1", 0.0)):
        lab = lbl(datname=dn)
        for n, r in (
            ("tup_fetched", 900),
            ("tup_returned", 2500),
            ("tup_inserted", 80),
            ("tup_updated", 50),
            ("tup_deleted", 8),
            ("xact_commit", 60),
            ("xact_rollback", 0.7),
            ("blks_hit", 5000),
            ("blks_read", 90),
            ("conflicts", 0.0),
            ("deadlocks", 0.005),
            ("temp_bytes", 3000),
        ):
            lab2 = {**lab, "release": release} if n == "tup_inserted" else lab
            p.counter("pg_stat_database_" + n, lab2, r * scale * wave(420, k + len(dn)))
        for st, v in (
            ("active", 3 * scale),
            ("idle", 20 * scale),
            ("idle in transaction", 2 * scale),
            ("idle in transaction (aborted)", 0),
            ("fastpath function call", 0),
            ("disabled", 0),
        ):
            p.gauge(
                "pg_stat_activity_count",
                {**lab, "state": st},
                round(v + (scale and math.sin(el() / 90))),
            )
        for mode in (
            "accessexclusivelock",
            "accesssharelock",
            "exclusivelock",
            "rowexclusivelock",
            "rowsharelock",
            "sharelock",
            "sharerowexclusivelock",
            "shareupdateexclusivelock",
        ):
            p.gauge(
                "pg_locks_count",
                {**lab, "mode": mode},
                round(4 * scale) if mode in ("accesssharelock", "rowexclusivelock") else 0,
            )
    if release == "pg-replica" and el() > 3000 and not getattr(p, "rs", False):
        p.rs = True  # type: ignore[attr-defined]
        p.reset("pg_stat_database_xact_commit")  # counter reset (stats reset / failover)


# ---------------------------------------------------------------- NGINX ingress controller
def nginx(p: Page) -> None:
    for pi, pod in enumerate(
        ("ingress-nginx-controller-7d9c-abcde", "ingress-nginx-controller-7d9c-fghij")
    ):
        c = lbl(
            controller_class="k8s.io/ingress-nginx",
            controller_namespace="ingress-nginx",
            controller_pod=pod,
        )
        p.gauge(
            "nginx_ingress_controller_config_hash",
            {**c, "namespace": "ingress-nginx", "controller": pod},
            1.2e18 + pi,
        )
        p.gauge(
            "nginx_ingress_controller_config_last_reload_successful",
            c,
            0 if (pi == 1 and 1000 < el() < 1200) else 1,
        )
        p.gauge("nginx_ingress_controller_success", {**c, "count": "1"}, 1)
        p.counter(
            "nginx_ingress_controller_nginx_process_cpu_seconds_total", c, 0.03 * wave(400, pi)
        )
        p.gauge(
            "nginx_ingress_controller_nginx_process_resident_memory_bytes",
            c,
            120e6 * wave(1000, pi, 0.1),
        )
        for st, v in (("active", 40), ("reading", 1), ("writing", 4), ("waiting", 35)):
            p.gauge(
                "nginx_ingress_controller_nginx_process_connections",
                {**c, "state": st},
                v * wave(180, pi),
            )
        if pi == 1 and el() > 2100 and not getattr(p, "reset1", False):
            p.reset1 = True  # type: ignore[attr-defined]
            p.reset(
                "nginx_ingress_controller_", controller_pod=pod
            )  # pod restart: all its counters restart
        for ing, ns, svc, rate in (
            ("shop", "web", "shop-svc", 25),
            ("api", "payments", "api-svc", 15),
            ("admin", "default", "admin-svc", 2),
        ):
            il = {**c, "ingress": ing, "namespace": ns, "controller": pod}
            for st, share in (
                ("200", 0.9),
                ("301", 0.04),
                ("404", 0.04),
                ("500", 0.02 if not (ing == "api" and 1500 < el() < 1800) else 0.25),
            ):
                p.counter(
                    "nginx_ingress_controller_requests",
                    {
                        **il,
                        "status": st,
                        "service": svc,
                        "host": f"{ing}.example.test",
                        "method": "GET",
                        "path": "/",
                    },
                    rate * share * wave(360, pi),
                )
            p.hist(
                "nginx_ingress_controller_request_duration_seconds",
                il,
                LAT,
                rate * wave(360, pi),
                0.03 * (1 + (ing == "api")),
            )
            p.counter("nginx_ingress_controller_request_size_sum", il, rate * 600)
            p.counter("nginx_ingress_controller_response_size_sum", il, rate * 9000)


class Handler(BaseHTTPRequestHandler):
    page: Page
    build = None  # type: ignore[assignment]
    flaky = False
    lock = threading.Lock()

    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_error(404)
            return
        if self.flaky and int(el() / 300) % 3 == 1:  # five minutes down in every fifteen
            self.send_error(503)
            return
        with self.lock:
            self.page.tick()
            self.build(self.page)
            body = self.page.render().encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a: object) -> None:
        pass


TARGETS = {
    9101: ksm,
    9111: lambda p: jvm(p, "orders", 1),
    9112: lambda p: jvm(p, "payments", 2),
    9121: lambda p: node_extra(p, 1),
    9122: lambda p: node_extra(p, 2),
    9131: lambda p: cadvisor(p, 1),
    9132: lambda p: cadvisor(p, 2),
    9141: lambda p: postgres(p, "pg-main", 1),
    9142: lambda p: postgres(p, "pg-replica", 2),
    9151: nginx,
    9161: lambda p: p.gauge("ckg_flaky_heartbeat", {}, 1),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ttl", type=int, default=5400)
    ap.add_argument(
        "--dry", action="store_true", help="render every target once, print series counts, exit"
    )
    args = ap.parse_args()
    if args.dry:
        total = 0
        for port, fn in TARGETS.items():
            pg = Page(port)
            pg.tick()
            time.sleep(0.01)
            pg.tick()
            fn(pg)
            n = sum(len(d) for d in pg.vals.values())
            total += n
            print(port, n, "series")
        print("total", total)
        return
    for port, fn in TARGETS.items():
        h = type(
            f"H{port}",
            (Handler,),
            {
                "page": Page(port),
                "build": staticmethod(fn),
                "flaky": port == 9161,
                "lock": threading.Lock(),
            },
        )
        srv = ThreadingHTTPServer(("127.0.0.1", port), h)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(args.ttl)


if __name__ == "__main__":
    main()
