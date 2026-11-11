"""Where a Google metric comes from, and what it takes for it to reach New Relic.

New Relic's Google integration polls the Cloud Monitoring service metrics (compute, storage, sql,
pubsub and so on). Everything else reaches Cloud Monitoring by another route and has to reach New
Relic by another route too. Under the owner's policy a condition or widget on such a metric is
converted when a correct query exists for the metric as it will arrive; the item then carries the
data path the customer must set up first.

Evidence (docs/research/gcp-estate-baseline.md, grafana-notifications-datapath.md section 6):
- Managed Prometheus metrics keep their Prometheus name once sent by remote write; the Google
  type segment (gauge, counter) is not part of it. DOCS.
- Ops Agent third-party metrics (`workload.googleapis.com/<name>`) are named after the
  OpenTelemetry receiver's metric (`jvm.memory.used`). Taken from the Ops Agent integration pages;
  not checked against live data: UNVERIFIED.
- Ops Agent host metrics (`agent.googleapis.com/...`) have no documented mapping to the
  OpenTelemetry host metrics receiver. The hint table below is our reading of both metric lists:
  UNVERIFIED. The Google name is kept in the query and a name map fixes it.
- Custom and external metrics have the name the sender gave them: UNVERIFIED.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from importer.core.ir import TagFilter

if TYPE_CHECKING:
    from importer.core.dashboard_ir import IRWidget
    from importer.core.ir import IRMonitor


class MetricSource(StrEnum):
    NATIVE = "native"
    OPS_AGENT = "ops-agent"
    MANAGED_PROMETHEUS = "managed-prometheus"
    CUSTOM = "custom"
    LOGGING_BASED = "logging-based"
    UNKNOWN = "unknown"


NATIVE = "native"
OPS_OR_OTEL = "needs-ops-agent-or-otel"
PROMETHEUS = "needs-prometheus-remote-write"
CUSTOM_INGEST = "needs-custom-ingest"
LOG_FORWARDING = "needs-log-forwarding"
UNKNOWN = "unknown"
DATA_PATHS = (NATIVE, OPS_OR_OTEL, PROMETHEUS, CUSTOM_INGEST, LOG_FORWARDING, UNKNOWN)
SOURCE_PATH = {
    MetricSource.NATIVE: NATIVE,
    MetricSource.OPS_AGENT: OPS_OR_OTEL,
    MetricSource.MANAGED_PROMETHEUS: PROMETHEUS,
    MetricSource.CUSTOM: CUSTOM_INGEST,
    MetricSource.LOGGING_BASED: UNKNOWN,
    MetricSource.UNKNOWN: UNKNOWN,
}
NON_GOOGLE_NATIVE_DOMAINS = ("kubernetes.io", "istio.io", "knative.dev")
METRIC_TYPE = re.compile(r"""metric\.type\s*=\s*\\?"([^"\\]+)""")
PROM_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")
# Managed Prometheus value types. A summary's quantiles are gauges (label `quantile`) and its
# _sum and _count are `summary:counter`; only a histogram arrives as several series.
PROM_TYPES = ("gauge", "counter", "summary", "summary:counter", "unknown", "unknown:counter")
SAFE_DOTTED = re.compile(r"[A-Za-z0-9_.]{1,200}")

# Our reading of the Ops Agent and OpenTelemetry host metrics receiver metric lists. UNVERIFIED.
OTEL_HINTS = {
    "agent.googleapis.com/cpu/utilization": "system.cpu.time (a rate by state)",
    "agent.googleapis.com/cpu/load_1m": "system.cpu.load_average.1m",
    "agent.googleapis.com/cpu/load_5m": "system.cpu.load_average.5m",
    "agent.googleapis.com/cpu/load_15m": "system.cpu.load_average.15m",
    "agent.googleapis.com/memory/percent_used": "system.memory.utilization",
    "agent.googleapis.com/memory/bytes_used": "system.memory.usage",
    "agent.googleapis.com/disk/percent_used": "system.filesystem.utilization",
    "agent.googleapis.com/disk/bytes_used": "system.filesystem.usage",
    "agent.googleapis.com/disk/read_bytes_count": "system.disk.io (direction=read)",
    "agent.googleapis.com/disk/write_bytes_count": "system.disk.io (direction=write)",
    "agent.googleapis.com/interface/traffic": "system.network.io",
    "agent.googleapis.com/interface/errors": "system.network.errors",
    "agent.googleapis.com/swap/percent_used": "system.paging.utilization",
    "agent.googleapis.com/processes/count_by_state": "system.processes.count",
}


class DataPathError(Exception):
    """The metric cannot be converted; the message is the reason shown to the customer."""


@dataclass(frozen=True)
class Arrival:
    """The metric as it will arrive in New Relic over its data path."""

    name: str
    data_path: str
    notes: tuple[str, ...]


def _domain(metric_type: str) -> str:
    return metric_type.split("/", 1)[0]


def classify(metric_type: str) -> MetricSource:
    """Which route a Google metric type takes to Cloud Monitoring."""
    domain = _domain(metric_type)
    if "/" not in metric_type or not domain:
        return MetricSource.UNKNOWN
    if domain in ("agent.googleapis.com", "workload.googleapis.com"):
        return MetricSource.OPS_AGENT
    if domain == "prometheus.googleapis.com" or metric_type.startswith(
        "external.googleapis.com/prometheus/"
    ):
        return MetricSource.MANAGED_PROMETHEUS
    if domain in ("custom.googleapis.com", "external.googleapis.com"):
        return MetricSource.CUSTOM
    if metric_type.startswith("logging.googleapis.com/user/"):
        return MetricSource.LOGGING_BASED
    if domain.endswith(".googleapis.com") or domain in NON_GOOGLE_NATIVE_DOMAINS:
        return MetricSource.NATIVE
    return MetricSource.UNKNOWN


def is_polled(metric_type: str) -> bool:
    return classify(metric_type) is MetricSource.NATIVE


def refusal(metric_type: str) -> str | None:
    """Why a metric cannot be converted at all, or None when it can (natively or via a path)."""
    source = classify(metric_type)
    if source is MetricSource.LOGGING_BASED:
        return (
            "the metric is not one of the Google Cloud service metrics New Relic polls: it is a "
            "log-based metric and its log filter is part of the metric definition, which the "
            "alert policy does not carry, so the importer cannot tell what to count; rewrite "
            "it as a log-match condition from that filter (the log-match path converts simple "
            "filters)"
        )
    if source is MetricSource.UNKNOWN:
        return (
            f"the metric type {metric_type!r} is not one of the Google Cloud service metrics, "
            "Ops Agent, Managed Prometheus, custom or log-based metrics, so the route it takes "
            "to New Relic is not known"
        )
    return None


def _prometheus(path: str) -> Arrival:
    name, _, kind = path.partition("/")
    if not kind:  # external.googleapis.com/prometheus/<name>
        name, kind = path, "gauge"
    if kind not in PROM_TYPES:
        raise DataPathError(
            f"a Prometheus {kind} metric arrives as several series (_bucket with an `le` label, "
            "_sum, _count), and a quantile from them is not expressible as one NRQL condition"
        )
    if not PROM_NAME.fullmatch(name):
        raise DataPathError(f"{name!r} is not a valid Prometheus metric name")
    return Arrival(
        name,
        PROMETHEUS,
        (
            "arrives under its Prometheus name (the Google type segment is dropped); New Relic "
            "stores names ending _total, _count or _bucket as counts (deltas), _sum as a summary "
            "and the rest as gauges, and labels as attributes of the same name",
        ),
    )


def _ops_agent(metric_type: str, path: str) -> Arrival:
    if metric_type.startswith("workload.googleapis.com/"):
        if not SAFE_DOTTED.fullmatch(path):
            raise DataPathError(f"{path!r} is not a usable metric name")
        return Arrival(
            path,
            OPS_OR_OTEL,
            (
                "third-party application metrics keep the OpenTelemetry receiver's metric name "
                f"when the same receiver runs in an OpenTelemetry Collector ({path}); the receiver "
                "name and version decide the exact name: UNVERIFIED",
            ),
        )
    dotted = path.replace("/", ".")
    if not SAFE_DOTTED.fullmatch(dotted):
        raise DataPathError(f"{path!r} is not a usable metric name")
    hint = OTEL_HINTS.get(metric_type)
    where = (
        f"the OpenTelemetry host metrics receiver's closest metric is {hint}"
        if hint
        else "no OpenTelemetry host metrics receiver metric has been matched to it"
    )
    return Arrival(
        f"agent.{dotted}",
        OPS_OR_OTEL,
        (
            "Ops Agent host metrics have no documented mapping to OpenTelemetry names, so the "
            f"Google name is kept; {where}; add a name map to rename it once data arrives "
            "(importer validate shows the names that did): UNVERIFIED",
        ),
    )


def arrival(metric_type: str) -> Arrival:
    """New Relic's name for a non-polled metric over its documented route."""
    source = classify(metric_type)
    path = metric_type.split("/", 1)[1] if "/" in metric_type else ""
    if source is MetricSource.MANAGED_PROMETHEUS:
        return _prometheus(path.removeprefix("prometheus/") if "external" in metric_type else path)
    if source is MetricSource.OPS_AGENT:
        return _ops_agent(metric_type, path)
    if source is MetricSource.CUSTOM:
        dotted = path.replace("/", ".")
        if not SAFE_DOTTED.fullmatch(dotted):
            raise DataPathError(f"{path!r} is not a usable metric name")
        return Arrival(
            dotted,
            CUSTOM_INGEST,
            (
                "a custom or external metric has the name its sender gave it; the Google path "
                "with dots is used, so send the metric under that name (OpenTelemetry or the "
                "Metric API) or add a name map: UNVERIFIED",
            ),
        )
    reason = refusal(metric_type)
    raise DataPathError(reason or f"{metric_type!r} is polled by the Google integration")


def arrival_label(key: str) -> str:
    """A label attribute as a sender writes it: plain, without the `gcp.` or `metric.` prefix."""
    for prefix in ("gcp.", "metric."):
        if key.startswith(prefix):
            return key.removeprefix(prefix).replace("projectId", "project_id")
    return key


def relabel(metric_type: str, scope: list[TagFilter]) -> list[TagFilter]:
    if is_polled(metric_type):
        return scope
    return [t.model_copy(update={"key": arrival_label(t.key)}) for t in scope]


LABEL_NOTE = (
    "label names are the sender's own (host.name, job, instance, namespace), not the gcp. and "
    "metric. attributes of the Google integration: UNVERIFIED, compare with the data that arrives"
)


def note_for(metric_type: str) -> str:
    """The reason line that names the data path an item needs (empty for polled metrics)."""
    source = classify(metric_type)
    path = SOURCE_PATH[source]
    if source is MetricSource.NATIVE:
        return ""
    return (
        f"data path: {path}; this metric is not polled by New Relic's Google integration, so "
        "the query returns data only after you send it (importer plan-gcp-datapath)"
    )


def path_of_metric(metric_type: str) -> str:
    return SOURCE_PATH[classify(metric_type)]


def metric_types_in(text: object) -> list[str]:
    return METRIC_TYPE.findall(str(text))


def _worst(paths: list[str]) -> str:
    order = (UNKNOWN, OPS_OR_OTEL, PROMETHEUS, CUSTOM_INGEST, LOG_FORWARDING, NATIVE)
    return next((p for p in order if p in paths), NATIVE)


def tag_monitor(monitor: "IRMonitor", body: dict[str, object], kind: str) -> None:
    """Set `data_path` (and the reason that names it) on a converted condition."""
    from importer.core.ir import Confidence

    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return
    if kind == "conditionMatchedLog":
        monitor.data_path = LOG_FORWARDING
        return
    types = metric_types_in(body.get("filter", ""))
    path = path_of_metric(types[0]) if types else UNKNOWN
    monitor.data_path = path
    if path != NATIVE and types:
        monitor.reasons.insert(0, note_for(types[0]))


def tag_widget(widget: "IRWidget") -> "IRWidget":
    """Set `data_path` on a converted chart from the metrics in its series."""
    from importer.core.dashboard_ir import WidgetKind

    if widget.kind is not WidgetKind.CHART:
        return widget
    types: list[str] = []
    for series in widget.series:
        types += [t.metric for t in series.signal.terms.values()]
    if not types:
        return widget
    widget.data_path = _worst([path_of_metric(t) for t in types])
    if widget.data_path != NATIVE:
        first = next(t for t in types if path_of_metric(t) == widget.data_path)
        widget.reasons = [note_for(first), *widget.reasons]
    return widget
