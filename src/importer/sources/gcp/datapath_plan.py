"""The Google Cloud data-path plan: what to set up so converted items on non-polled metrics work.

Text only: nothing here sends data or reads a key. The license key is a placeholder or the name of
an environment variable in every file. Endpoints follow the Grafana plan
(`sources/grafana/datapath/plan.py`, docs/research/grafana-notifications-datapath.md section 6).
Items marked UNVERIFIED are not checked against a live account.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import IRMonitor
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.sources.gcp import datapath as dp
from importer.sources.grafana.datapath.plan import KEY_PLACEHOLDER, METRIC_WRITE, OTLP, REGIONS

SAFE_NAME = re.compile(r"[A-Za-z0-9_.:]{1,255}")
MAX_NAMES = 500

OPS_AGENT_OTEL = """\
# OpenTelemetry Collector (contrib) standing in for the Ops Agent: host metrics and, commented out,
# the third-party application receivers, sent to New Relic over OTLP. Run it next to the Ops Agent
# or instead of it. The license key is read from the environment variable NEW_RELIC_LICENSE_KEY
# ({placeholder}); it is never written here.
# UNVERIFIED: the Ops Agent publishes `agent.googleapis.com/...` names and no documented mapping
# to the names this receiver produces exists. The plan lists our best match per metric; add a name
# map in the importer once you have seen the names that arrive.
receivers:
  hostmetrics:
    collection_interval: 60s
    scrapers:
      cpu: {{}}
      load: {{}}
      memory: {{}}
      disk: {{}}
      filesystem: {{}}
      network: {{}}
      paging: {{}}
      processes: {{}}
  # Third-party applications (the Ops Agent's workload.googleapis.com/<name> metrics). Keep the
  # receiver you use; its metric names are the names the converted queries use (UNVERIFIED).
  # nginx:
  #   endpoint: http://localhost:80/status
  # mysql:
  #   endpoint: localhost:3306
  #   username: replace-me
  #   password: ${{env:MYSQL_MONITOR_PASSWORD}}   # set in the environment, never here
  # postgresql:
  #   endpoint: localhost:5432
  # redis:
  #   endpoint: localhost:6379
processors:
  resourcedetection:
    detectors: [gcp]
  batch: {{}}
exporters:
  otlphttp/newrelic:
    endpoint: {otlp}
    headers:
      api-key: ${{env:NEW_RELIC_LICENSE_KEY}}
service:
  pipelines:
    metrics:
      receivers: [hostmetrics]   # add the application receivers you enabled above
      processors: [resourcedetection, batch]
      exporters: [otlphttp/newrelic]
"""

PROMETHEUS_WRITE = """\
# Prometheus (2.26 or later) remote write to New Relic, for metrics that Google Managed Service for
# Prometheus collects today. Managed collection writes only to Google, and no way to also write to
# New Relic from it was found in the documentation: run a Prometheus server or an OpenTelemetry
# Collector with the same scrape targets (your PodMonitoring resources) and add this block.
# Put the New Relic ingest license key (not a user API key) in the file named below, readable only
# by Prometheus. Do not paste it here. {placeholder}: create
# /etc/prometheus/secrets/newrelic-license-key with the key as its only text.
remote_write:
  - url: {url}?prometheus_server=gcp-managed
    authorization:
      credentials_file: /etc/prometheus/secrets/newrelic-license-key
    # New Relic infers the type from the name (_total, _count, _bucket are counts; _sum a summary;
    # the rest gauges). To force one, relabel and replace my_counter:
    # write_relabel_configs:
    #   - source_labels: [__name__]
    #     regex: my_counter
    #     target_label: newrelic_metric_type
    #     replacement: counter
"""

CUSTOM_TEXT = """\
Custom and external metrics are written by your own code through the Cloud Monitoring API or
OpenCensus. In New Relic the name is whatever the sender uses. The converted queries use the Google
path with dots (`custom.googleapis.com/orders/failed` becomes `orders.failed`): UNVERIFIED. Send
the metric under that name, or tell the importer the real name with a name map. With the
OpenTelemetry SDK set, in the environment of the process (no key in code or files):

    OTEL_EXPORTER_OTLP_ENDPOINT={otlp}
    OTEL_EXPORTER_OTLP_HEADERS=api-key=${{NEW_RELIC_LICENSE_KEY}}      # {placeholder}
    OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta

New Relic prefers delta temporality for counters and histograms (docs/research/
grafana-notifications-datapath.md section 6.2).
"""

LOG_TEXT = """\
Log-match conditions count Log events in New Relic. Forward Cloud Logging to New Relic (for
example a log sink to Pub/Sub and a New Relic forwarder). The attribute names of forwarded Google
logs are not confirmed: UNVERIFIED. This is not a metric route, so it has its own class.
"""

STEPS = {
    dp.NATIVE: "Nothing to send. Link the project (`gcp-link-terraform`); New Relic polls it.",
    dp.OPS_OR_OTEL: "Run an OpenTelemetry Collector with the host metrics and application "
    "receivers (otel-collector-ops-agent.yml).",
    dp.PROMETHEUS: "Send the Prometheus metrics by remote write (prometheus-remote-write.yml).",
    dp.CUSTOM_INGEST: "Send the custom metrics over OTLP under the names listed (section below).",
    dp.LOG_FORWARDING: "Forward Cloud Logging to New Relic (section below).",
    dp.UNKNOWN: "The route is not known; see the item's reasons.",
}


@dataclass
class ClassPlan:
    items: int = 0
    metrics: set[str] = field(default_factory=set)
    skipped: int = 0


def _names(monitor: IRMonitor) -> list[str]:
    sig = monitor.signal
    if sig.metric:
        return [
            t.nr_target.attribute
            for t in sig.metric.terms.values()
            if t.nr_target and t.nr_target.attribute
        ]
    if sig.heartbeat:
        return [sig.heartbeat.check]
    return []


def collect(monitors: list[IRMonitor], dashboards: list[IRDashboard]) -> dict[str, ClassPlan]:
    """Converted items grouped by data path, with the New Relic metric names each class needs."""
    plans: dict[str, ClassPlan] = {}
    for monitor in monitors:
        if monitor.confidence.value in ("manual", "unsupported"):
            continue
        plan = plans.setdefault(monitor.data_path or dp.NATIVE, ClassPlan())
        plan.items += 1
        for name in _names(monitor):
            (plan.metrics.add(name) if SAFE_NAME.fullmatch(name) else _skip(plan))
    for dash in dashboards:
        for widget in dash.widgets:
            if widget.kind.value != "chart":
                continue
            plan = plans.setdefault(widget.data_path or dp.NATIVE, ClassPlan())
            plan.items += 1
            for series in widget.series:
                for term in series.signal.terms.values():
                    name = (term.nr_target.attribute if term.nr_target else "") or ""
                    (plan.metrics.add(name) if SAFE_NAME.fullmatch(name) else _skip(plan))
    return plans


def _skip(plan: ClassPlan) -> None:
    plan.skipped += 1


def _hint_rows(metrics: set[str]) -> list[str]:
    rows = []
    for google, hint in sorted(dp.OTEL_HINTS.items()):
        short = "agent." + google.split("/", 1)[1].replace("/", ".")
        if short in metrics or hint in metrics:
            status = "VERIFIED" if google in dp.OTEL_VERIFIED else "UNVERIFIED"
            rows.append(f"| {md_cell(short)} | {md_cell(hint, 100)} | {status} |")
    return rows


def plan_files(region: str, plans: dict[str, ClassPlan]) -> dict[str, str]:
    if region not in REGIONS:
        raise ValueError("region must be us, eu or jp")
    lines = [
        *header_lines("Data path plan", "gcp"),
        "",
        "Converted conditions and widgets on metrics that New Relic's Google integration does not "
        "poll return data only after you send those metrics. Nothing here sends data. Confirm "
        "with `check-gcp-data` once the senders run. Items marked UNVERIFIED are not checked "
        "against a live account.",
        "",
        "## What each class needs",
        "",
        "| Data path | Converted items | Metrics | What to do |",
        "|---|---:|---:|---|",
    ]
    for path in dp.DATA_PATHS:
        if path in plans:
            plan = plans[path]
            lines.append(
                f"| {path} | {plan.items} | {len(plan.metrics)} | {md_cell(STEPS[path], 160)} |"
            )
    for path in (dp.OPS_OR_OTEL, dp.PROMETHEUS, dp.CUSTOM_INGEST):
        found = plans.get(path)
        if found is None:
            continue
        plan = found
        lines += ["", f"## {path}", "", f"{STEPS[path]}", "", "Metric names the queries use:", ""]
        names = sorted(plan.metrics)[:MAX_NAMES]
        lines += [f"- `{n}`" for n in names]
        if len(plan.metrics) > MAX_NAMES:
            lines.append(f"- and {len(plan.metrics) - MAX_NAMES} more (datapath-plan.json)")
        if path == dp.OPS_OR_OTEL:
            rows = _hint_rows(plan.metrics)
            if rows:
                lines += [
                    "",
                    "Closest OpenTelemetry names for the host metrics (our reading, UNVERIFIED):",
                    "",
                    "| Query uses | Closest OpenTelemetry metric | Evidence |",
                    "|---|---|---|",
                    *rows,
                ]
        if path == dp.CUSTOM_INGEST:
            lines += [
                "",
                *CUSTOM_TEXT.format(otlp=OTLP[region], placeholder=KEY_PLACEHOLDER).splitlines(),
            ]
    if dp.LOG_FORWARDING in plans:
        lines += ["", f"## {dp.LOG_FORWARDING}", "", *LOG_TEXT.splitlines()]
    data = {
        "secret": "NEW_RELIC_LICENSE_KEY",
        "classes": {
            path: {
                "items": plan.items,
                "metrics": sorted(plan.metrics)[:MAX_NAMES],
                "skipped_unsafe_names": plan.skipped,
                "step": STEPS[path],
            }
            for path, plan in plans.items()
        },
    }
    return {
        "datapath-plan.md": "\n".join(lines) + "\n",
        "datapath-plan.json": json.dumps(data, indent=2) + "\n",
        "otel-collector-ops-agent.yml": OPS_AGENT_OTEL.format(
            placeholder=KEY_PLACEHOLDER, otlp=OTLP[region]
        ),
        "prometheus-remote-write.yml": PROMETHEUS_WRITE.format(
            placeholder=KEY_PLACEHOLDER, url=METRIC_WRITE[region]
        ),
    }


def write_files(files: dict[str, str], out: Path) -> None:
    """Write into a new or empty folder."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
