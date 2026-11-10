"""The data path plan: what a migrator does so that converted rules and tiles have data.

`plan_files` is pure text from already-read inputs (saved IR, the data source list); it sends
nothing and reads no key. The license key appears only as a `SecretRef` name. Names from the
export reach the output only through `md_cell` or after matching the metric and attribute name
patterns in plan.py.
"""

import json
from collections import Counter

from importer.core.notification_ir import SecretRef
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.sources.grafana.datapath import needs
from importer.sources.grafana.datapath.needs import Item, Judged
from importer.sources.grafana.datapath.plan import (
    KEY_PLACEHOLDER,
    METRIC_WRITE,
    OTLP,
    REGIONS,
    SERVER_NAME,
    data_paths,
)
from importer.sources.grafana.notifications.models import DataSource

KEY_SECRET = SecretRef(name="NEW_RELIC_LICENSE_KEY")  # a name only; the value is supplied later
MAX_FAMILY_ROWS = 300
MAX_USES = 3

INTEGRATIONS = (
    ("node_", "host metrics from node_exporter", "Infrastructure agent (host monitoring)"),
    ("kube_", "kube-state-metrics", "Kubernetes integration (nri-bundle)"),
    ("container_", "cAdvisor container metrics", "Kubernetes integration (nri-bundle)"),
    ("go_", "Go runtime metrics from a client library", "OpenTelemetry SDK or agent"),
    ("process_", "process metrics from a client library", "OpenTelemetry SDK or agent"),
    ("http_", "application HTTP metrics from a client library", "OpenTelemetry SDK or agent"),
)
INTEGRATION_NOTE = (
    "A native integration writes its own names (for example host data lands in "
    "`SystemSample`, not in `node_*` metrics), so converted NRQL does not read it unless the "
    "queries are rewritten. The verdicts above assume the Prometheus names are kept, which "
    "means remote write of the same exporters. Whether a native integration can emit the "
    "Prometheus names is UNVERIFIED."
)

OTEL_COLLECTOR = """\
# OpenTelemetry Collector (contrib) scraping Prometheus targets and sending them to New Relic over
# OTLP. The license key is read from the environment variable NEW_RELIC_LICENSE_KEY (secret name
# {secret}, placeholder {placeholder}); it is never written here.
# UNVERIFIED against a live account: translation of Prometheus names to OTLP names (dots, the
# `_total` suffix) depends on the collector version; check with `check-grafana-data`.
receivers:
  prometheus:
    config:
      scrape_configs:
        - job_name: replace-me            # the job name your rules and tiles filter on
          static_configs:
            - targets: ["localhost:9100"] # replace with your exporters
processors:
  resource:
    attributes:
      - key: prometheus_server
        value: {server}
        action: upsert
  batch: {{}}
exporters:
  otlphttp/newrelic:
    endpoint: {otlp}
    headers:
      api-key: ${{env:NEW_RELIC_LICENSE_KEY}}
service:
  pipelines:
    metrics:
      receivers: [prometheus]
      processors: [resource, batch]
      exporters: [otlphttp/newrelic]
"""


def _steps(items: list[Item], region: str, server: str) -> list[str]:
    prom = any(i.converted and i.source == "prometheus" for i in items)
    loki = any(i.converted and i.source == "loki" for i in items)
    lines = ["## Steps", ""]
    lines += [
        f"**Step {needs.STEP_KEY}.** {needs.STEPS[needs.STEP_KEY].capitalize()}. Use the New "
        f"Relic ingest license key (not a user API key). The files carry the placeholder "
        f"`{KEY_PLACEHOLDER}` or read `NEW_RELIC_LICENSE_KEY` from the environment; the key "
        f"is never written to any file. (SecretRef name: `{KEY_SECRET.name}`.)",
        "",
    ]
    if prom:
        lines += [
            f"**Step {needs.STEP_METRICS}.** {needs.STEPS[needs.STEP_METRICS].capitalize()}. "
            "Pick one sender:",
            "",
            f"- Prometheus remote write: URL `{METRIC_WRITE[region]}?prometheus_server={server}`, "
            "bearer credentials from the key file (`prometheus-remote-write.yml`).",
            "- Grafana Alloy dual write to Grafana and New Relic (`alloy-dual-write.alloy`).",
            f"- OpenTelemetry Collector over OTLP to `{OTLP[region]}` with header `api-key` "
            "(`otel-collector.yml`).",
            "",
        ]
    if loki:
        lines += [
            f"**Step {needs.STEP_LOGS}.** {needs.STEPS[needs.STEP_LOGS].capitalize()}: "
            f"OTLP endpoint `{OTLP[region]}`, header `api-key` from the same secret; Alloy "
            "`otelcol.receiver.loki` feeds `alloy-otlp.alloy`.",
            "",
        ]
    if not prom and not loki:
        lines += [
            "No converted rule or tile reads Prometheus or Loki data, so no sender is needed.",
            "",
        ]
    return lines


def _datasources(items: list[Item], sources: list[DataSource]) -> list[str]:
    if not sources:
        return []
    counts = Counter(s.type for s in sources)
    used = Counter(i.source for i in items if i.converted)
    steps = {"prometheus": needs.STEP_METRICS, "loki": needs.STEP_LOGS}
    lines = [
        "## Data sources",
        "",
        "| Type | Count | New Relic data source needed | Step | Converted items |",
    ]
    lines.append("|---|---|---|---|---|")
    for row in data_paths(sources):
        step = f"{needs.STEP_KEY}, {steps[row.type]}" if row.type in steps else "none"
        path = row.path or "no New Relic data path (a rule over this source stays manual)"
        lines.append(
            f"| {md_cell(row.type, 40)} | {counts[row.type]} | {path} | {step} | "
            f"{used.get(row.type, 0)} |"
        )
    for t, n in sorted(counts.items()):
        if n > 1 and t in steps:
            lines.append(
                f"\nThere are {n} {md_cell(t, 40)} data sources. Each sender needs a distinct "
                "prometheus_server name; two senders under one name, or a highly available pair "
                "under two, are counted twice by `sum()`."
            )
    return [*lines, ""]


def _families(items: list[Item]) -> list[str]:
    fams = needs.families(items)
    if not fams:
        return []
    lines = [
        "## Metric families",
        "",
        "Type is what New Relic infers from the name on remote write: `count` is stored as a "
        "delta (Prometheus sends 2 then 5, New Relic stores 2 then 3), so converted queries "
        "read it with `sum()` or `rate(sum(m), 1 second)`; `summary` is a `_sum`; `gauge` is "
        "everything else. Suffixes are kept. Over OTLP the name depends on the translation "
        "strategy.",
        "",
        "| Metric | Type in New Relic | Labels read (become attributes) | Used by |",
        "|---|---|---|---|",
    ]
    for f in fams[:MAX_FAMILY_ROWS]:
        labels = ", ".join(sorted(needs.label_attribute(x)[0] for x in f.labels)) or "none"
        by = "; ".join(md_cell(u, 60) for u in f.uses[:MAX_USES])
        more = f" (+{len(f.uses) - MAX_USES} more)" if len(f.uses) > MAX_USES else ""
        lines.append(
            f"| {md_cell(f.name, 100)} | {f.nr_type} | {md_cell(labels, 100)} | {by}{more} |"
        )
    if len(fams) > MAX_FAMILY_ROWS:
        lines.append(
            f"\n{len(fams) - MAX_FAMILY_ROWS} more metric families are in datapath-plan.json."
        )
    lines += ["", "### Native integration alternatives", ""]
    seen = [p for p in INTEGRATIONS if any(f.name.startswith(p[0]) for f in fams)]
    lines += [f"- `{p[0]}*` ({p[1]}): {p[2]}." for p in seen] or ["- none of the known prefixes."]
    lines += ["", INTEGRATION_NOTE, ""]
    return lines


LABELS = """\
## Label to attribute mapping

- Every label becomes an attribute with the same name; `__name__` becomes `metricName`.
- `job` and `instance` stay `job` and `instance`. A histogram keeps `le` on `_bucket`.
- `prometheus_server` (from the remote write URL or the collector processor) is added to every
  point; `newrelic_metric_type` is read and removed.
- **Collision:** a Prometheus label called `metricName` has the same name as the attribute that
  holds the metric name. Rename it in the sender (the commented `write_relabel_configs` in the
  snippets rename it to `metric_name`); converted NRQL that filters on it must use the new name.
  Whether New Relic keeps the label or overwrites it is UNVERIFIED.
- A label with a dot or a name starting with `__` does not occur in Prometheus data; internal
  `__` labels are dropped by Prometheus before sending.
"""


def _verdicts(title: str, judged: list[Judged]) -> list[str]:
    if not judged:
        return []
    lines = [f"## {title}", "", "| Name | Verdict |", "|---|---|"]
    lines += [f"| {md_cell(j.item.name, 100)} | {md_cell(j.text(), 200)} |" for j in judged]
    return [*lines, ""]


def plan_files(
    region: str,
    prometheus_server: str,
    items: list[Item],
    judged: list[Judged],
    sources: list[DataSource],
) -> dict[str, str]:
    """datapath-plan.md, datapath-plan.json and otel-collector.yml."""
    if region not in REGIONS:
        raise ValueError("region must be us, eu or jp")
    if not SERVER_NAME.fullmatch(prometheus_server):
        raise ValueError("prometheus server name must be 1 to 64 letters, digits, - or _")
    rules = [j for j in judged if j.item.kind == "rule"]
    tiles = [j for j in judged if j.item.kind == "tile"]
    cov = {"rules": needs.coverage(rules), "tiles": needs.coverage(tiles)}
    lines = [
        *header_lines("Data path plan", "grafana"),
        "",
        "Converted rules and tiles query New Relic; nothing here sends data. Each verdict says "
        "after which step the item has data, assuming the sender runs and the names arrive as "
        "described. Confirm with `check-grafana-data` once the senders run.",
        "",
        needs.coverage_section({k: v for k, v in cov.items() if v.total}).rstrip("\n"),
        "",
        *_steps(items, region, prometheus_server),
        *_datasources(items, sources),
        *_families(items),
        *LABELS.splitlines(),
        "",
        *_verdicts("Rule verdicts", rules),
        *_verdicts("Tile verdicts", tiles),
    ]
    data = {
        "secret": {"name": KEY_SECRET.name},
        "steps": {str(k): v for k, v in needs.STEPS.items()},
        "coverage": {k: vars(v) for k, v in cov.items()},
        "families": [
            {"name": f.name, "type": f.nr_type, "labels": sorted(f.labels), "uses": len(f.uses)}
            for f in needs.families(items)
        ],
        "verdicts": [
            {
                "kind": j.item.kind,
                "name": j.item.name,
                "verdict": j.verdict.value,
                "steps": list(j.steps),
                "notes": list(j.notes),
            }
            for j in judged
        ],
    }
    otel = OTEL_COLLECTOR.format(
        secret=KEY_SECRET.name,
        placeholder=KEY_PLACEHOLDER,
        server=prometheus_server,
        otlp=OTLP[region],
    )
    return {
        "datapath-plan.md": "\n".join(lines) + "\n",
        "datapath-plan.json": json.dumps(data, indent=2) + "\n",
        "otel-collector.yml": otel,
    }
