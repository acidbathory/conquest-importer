"""Guidance files for getting Grafana-side data to New Relic. Text only: nothing here sends data.

Converted NRQL works only when the data exists under the names the NRQL uses, and nothing in
New Relic reads from Grafana Cloud Metrics, so the data has to be sent (dual write) before the
converted conditions mean anything. The files written:

- prometheus-remote-write.yml: a Prometheus `remote_write` block for New Relic.
- alloy-dual-write.alloy: Grafana Alloy writing to both Grafana and New Relic.
- alloy-otlp.alloy: Alloy sending metrics, logs and traces over OTLP.
- naming-consequences.md: what the metric type and label names become, with evidence levels.
- check-queries.md: the NRQL a `check-grafana-data` run sends (read-only), as text.

The license key is never read from the environment or written to a file: the files carry a
placeholder or name the environment variable the key goes in. Endpoints and behaviour follow
docs/research/grafana-notifications-datapath.md section 6; items marked UNVERIFIED there stay
marked here.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from importer.core.emit.nrql import identifier, quote_string
from importer.core.report_brand import header_lines
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import DataSource

REGIONS = ("us", "eu", "jp")
METRIC_WRITE = {
    "us": "https://metric-api.newrelic.com/prometheus/v1/write",
    "eu": "https://metric-api.eu.newrelic.com/prometheus/v1/write",
    "jp": "https://metric-api.jp.nr-data.net/prometheus/v1/write",
}
OTLP = {
    "us": "https://otlp.nr-data.net",
    "eu": "https://otlp.eu01.nr-data.net",
    "jp": "https://otlp.jp.nr-data.net",
}
SERVER_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
METRIC_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,254}")
ATTRIBUTE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")
KEY_PLACEHOLDER = "REPLACE_WITH_YOUR_NEW_RELIC_LICENSE_KEY"  # a placeholder, never a key
MAX_HOURS = 168
MAX_TARGETS = 500
NOT_VALID = "not valid"
NO_TARGETS = (
    "No converted conditions were given, so the queries below are templates. Run `plan-grafana-"
    "datapath` again with `--alerts <folder with ir/>` to fill them in."
)

PROMETHEUS_YML = """\
# Prometheus (2.26 or later) remote write to New Relic. Put the New Relic ingest license key
# (not a user API key) in the file named below, readable only by Prometheus. Do not paste it here.
# {placeholder}: create /etc/prometheus/secrets/newrelic-license-key with the key as its only text.
remote_write:
  - url: {url}?prometheus_server={server}
    authorization:
      credentials_file: /etc/prometheus/secrets/newrelic-license-key
    # New Relic infers the metric type from the name (see naming-consequences.md). To force a
    # type, relabel the metric; the label is removed on receipt. Replace my_counter and counter:
    # write_relabel_configs:
    #   - source_labels: [__name__]
    #     regex: my_counter
    #     target_label: newrelic_metric_type
    #     replacement: counter
"""

ALLOY_DUAL = """\
// Grafana Alloy writing every sample to Grafana and to New Relic. Set the environment variables
// before starting Alloy; no key is stored in this file.
//   GRAFANA_PROM_URL, GRAFANA_PROM_USER, GRAFANA_PROM_TOKEN: your existing Grafana endpoint
//   NEW_RELIC_LICENSE_KEY: the New Relic ingest license key ({placeholder})
// The write_relabel_config attribute names were taken from the Prometheus convention and were
// not checked against the Alloy reference: UNVERIFIED.
prometheus.remote_write "dual" {{
  endpoint {{
    name = "grafana"
    url  = sys.env("GRAFANA_PROM_URL")
    basic_auth {{
      username = sys.env("GRAFANA_PROM_USER")
      password = sys.env("GRAFANA_PROM_TOKEN")
    }}
  }}
  endpoint {{
    name         = "newrelic"
    url          = "{url}?prometheus_server={server}"
    bearer_token = sys.env("NEW_RELIC_LICENSE_KEY")
    // To force a metric type, uncomment and edit:
    // write_relabel_config {{
    //   source_labels = ["__name__"]
    //   regex         = "my_counter"
    //   target_label  = "newrelic_metric_type"
    //   replacement   = "counter"
    //   action        = "replace"
    // }}
  }}
}}
"""

ALLOY_OTLP = """\
// Grafana Alloy sending OTLP to New Relic. The key goes in the environment variable
// NEW_RELIC_LICENSE_KEY ({placeholder}); it is not stored here.
// The `auth` link inside `client` follows the usual Alloy pattern but the argument table was not
// read: UNVERIFIED. Logs from Loki sources reach this exporter through otelcol.receiver.loki;
// scraped Prometheus metrics need otelcol.receiver.prometheus in front of it.
otelcol.auth.headers "newrelic" {{
  header {{
    key   = "api-key"
    value = sys.env("NEW_RELIC_LICENSE_KEY")
  }}
}}

otelcol.exporter.otlphttp "newrelic" {{
  client {{
    endpoint = "{otlp}"
    auth     = otelcol.auth.headers.newrelic.handler
  }}
}}
"""


def _keep_block(metrics: list[str], alloy: bool) -> str:
    """Commented relabel rules: keep only the used metrics, and rename a `metricName` label."""
    names = "|".join(m for m in metrics if METRIC_NAME.fullmatch(m))
    lead = "    // " if alloy else "    # "
    head = (
        "Optional (UNVERIFIED against a live account): keep only the metrics the converted\n"
        "rules and tiles read, and rename a label called metricName, which collides with the\n"
        "New Relic attribute that holds the metric name (the converted NRQL must then use\n"
        "metric_name if it filtered on that label). "
    )
    head += "Add inside the endpoint block:" if alloy else "Add under this remote_write entry:"
    out = [lead + line for line in head.split("\n")]
    if alloy:
        blocks = [
            ("__name__", f'regex = "{names}"', 'action = "keep"') if names else None,
            ("metricName", 'target_label = "metric_name"', 'action = "replace"'),
            ("metricName", 'regex = "metricName"', 'action = "labeldrop"'),
        ]
        for b in (x for x in blocks if x):
            out += [f"{lead}write_relabel_config {{", f'{lead}  source_labels = ["{b[0]}"]']
            out += [f"{lead}  {b[1]}", f"{lead}  {b[2]}", f"{lead}}}"]
    else:
        out.append(lead + "write_relabel_configs:")
        if names:
            out += [f"{lead}  - source_labels: [__name__]", f'{lead}    regex: "{names}"']
            out.append(f"{lead}    action: keep")
        out += [f"{lead}  - source_labels: [metricName]", f"{lead}    target_label: metric_name"]
        out += [f"{lead}  - regex: metricName", f"{lead}    action: labeldrop"]
    return "\n".join(out) + "\n"


DATA_PATHS = {
    "prometheus": "Prometheus remote write or Alloy dual write (prometheus-remote-write.yml, "
    "alloy-dual-write.alloy), or OTLP metrics",
    "loki": "OTLP logs through Alloy (otelcol.receiver.loki, alloy-otlp.alloy) or the Log API",
    "tempo": "OTLP traces to the OTLP endpoint (alloy-otlp.alloy); span names not verified",
}


@dataclass(frozen=True)
class DataPath:
    type: str
    path: str  # "" when there is none
    reason: str  # R_DATASOURCE_PATH when there is none


def data_paths(sources: list[DataSource]) -> list[DataPath]:
    """One row per data source type, with how its data can reach New Relic."""
    found = {s.type for s in sources}
    return [
        DataPath(t, DATA_PATHS[t], "") if t in DATA_PATHS else DataPath(t, "", R.R_DATASOURCE_PATH)
        for t in sorted(found)
    ]


def _hours(hours: int) -> int:
    if not 1 <= hours <= MAX_HOURS:
        raise ValueError(f"hours must be 1 to {MAX_HOURS}")
    return hours


def metric_queries(metric: str, hours: int) -> list[str]:
    """The read-only NRQL a metric check sends, built from a validated metric name."""
    if not METRIC_NAME.fullmatch(metric):
        raise ValueError("not a valid metric name")
    since = f"SINCE {_hours(hours)} hours ago"
    where = f"FROM Metric WHERE metricName = {quote_string(metric)} {since}"
    return [
        f"SELECT count(*) {where}",
        f"SELECT keyset() {where}",
        f"SELECT uniques(prometheus_server) {where}",
    ]


def log_queries(attributes: list[str], hours: int) -> list[str]:
    """Log checks: which attribute names exist, and how many events carry each."""
    since = f"SINCE {_hours(hours)} hours ago"
    queries = [f"SELECT keyset() FROM Log {since}"]  # noqa: S608 - the window is an int
    # identifier() accepts only a validated attribute name
    queries += [
        f"SELECT count(*) FROM Log WHERE {identifier(a)} IS NOT NULL {since}"  # noqa: S608
        for a in attributes
    ]
    return queries


NAMING = """\
# What the names become in New Relic

Evidence: DOCS is a documentation page read, UNVERIFIED is inference or memory. Sources are listed
in docs/research/grafana-notifications-datapath.md (section 6).

## Metrics sent by Prometheus remote write

- The protocol carries no type, so New Relic infers it from the name. `*_bucket`, `*_count` and
  `*_total` are stored as counts, `*_sum` as a summary, everything else as a gauge. Add the label
  `newrelic_metric_type` (counter, gauge or summary, lower case) in `write_relabel_configs` to
  override it. DOCS.
- A Prometheus counter is cumulative; a New Relic count is a delta (2 then 5 becomes 2 then 3).
  Read a count with `sum()` or `rate(sum(m), 1 second)` over the window, a gauge with `latest`,
  `average` or `max`. Where in the pipeline the conversion happens is UNVERIFIED. DOCS.
- Every label becomes an attribute with the same name, and `__name__` is the metric name. `job`
  and `instance` stay as attributes named `job` and `instance`. A classic histogram becomes
  `_bucket` counts with an `le` attribute, a `_sum` and a `_count`. DOCS.
- `prometheus_server=<name>` in the URL becomes an attribute on every point. Two replicas of a
  highly available pair sending under different names are counted twice by `sum()`. DOCS.
- A metric name with a colon (a recording rule such as `job:request_latency:mean5m`) or a dot
  needs backquotes in NRQL. Whether the colon is stored unchanged is UNVERIFIED, so a converted
  condition on such a name is not generated and the check reports it as not checked.
- Data is visible after about five minutes. Data older than 48 hours or newer than 24 hours is
  dropped, so history is not backfilled: a converted condition starts with an empty window. DOCS.
- Limits that bite alerts (150 attributes per metric, 4,096 characters per attribute value,
  cardinality caps per account per day) were taken from a second session's notes and not
  re-read: UNVERIFIED.

## Metrics sent over OTLP

- OTLP names are the OpenTelemetry names with dots kept. Data that came from Prometheus through a
  collector may have had dots turned into underscores and suffixes such as `_total` added,
  depending on the translation strategy; the name the NRQL must use depends on the pipeline that
  wrote the data. DOCS.

## Logs and traces

- Loki labels become log attributes; the exact names (for example whether `service_name` stays
  that way) and the use of `message` for the log line are UNVERIFIED. The check lists the
  attribute names that arrive.
- Tempo traces go to the same OTLP endpoint; the span attribute names were not read: UNVERIFIED.

## What this does not do

Nothing in New Relic reads from Grafana Cloud Metrics, and a rule over several data sources, or
over Grafana Cloud's own usage metrics, has no New Relic source at all.
"""


def _sources_section(sources: list[DataSource]) -> str:
    if not sources:
        return ""
    counts: dict[str, int] = {}
    for s in sources:
        counts[s.type] = counts.get(s.type, 0) + 1
    lines = ["", "## Data sources in the export", ""]
    for row in data_paths(sources):
        path = row.path or "no New Relic data path (a rule over this source stays manual)"
        lines.append(f"- {row.type}: {counts[row.type]}. {path}.")
    return "\n".join(lines) + "\n"


def _queries_text(metrics: list[str], attributes: list[str], hours: int) -> str:
    lines = [
        *header_lines("Queries a data check sends", "grafana"),
        "",
        "All are read-only NRQL.",
        "",
    ]
    if not metrics and not attributes:
        lines += [NO_TARGETS, "", "For a metric `<metric name>`:", ""]
        lines += [f"    {q}" for q in metric_queries("example_metric", hours)]
        lines += ["", "For logs:", ""]
        lines += [f"    {q}" for q in log_queries([], hours)]
    for metric in metrics:
        lines.append("")
        if METRIC_NAME.fullmatch(metric):
            lines += [f"    {q}" for q in metric_queries(metric, hours)]
        else:
            lines.append(f"A metric name that is {NOT_VALID} was skipped and no query is sent.")
    valid = [a for a in attributes if ATTRIBUTE_NAME.fullmatch(a)]
    if valid or any(a not in valid for a in attributes):
        lines.append("")
        lines += [f"    {q}" for q in log_queries(valid, hours)]
    if len(valid) != len(attributes):
        lines += ["", f"A log attribute name that is {NOT_VALID} was skipped and no query is sent."]
    lines += [
        "",
        "Not sent: freshness (`SELECT max(timestamp) FROM Metric WHERE metricName = ...`), because "
        "the `timestamp` attribute on Metric is UNVERIFIED, and the stored metric type, because "
        "reading it from NRDB is not documented.",
    ]
    return "\n".join(lines) + "\n"


def build_files(
    region: str,
    prometheus_server: str = "prometheus-1",
    metrics: list[str] | None = None,
    log_attributes: list[str] | None = None,
    datasources: list[DataSource] | None = None,
    hours: int = 24,
) -> dict[str, str]:
    """File name to text. Raises ValueError for an unknown region or a bad server name."""
    if region not in REGIONS:
        raise ValueError("region must be us, eu or jp")
    if not SERVER_NAME.fullmatch(prometheus_server):
        raise ValueError("prometheus server name must be 1 to 64 letters, digits, - or _")
    fill = {
        "url": METRIC_WRITE[region],
        "otlp": OTLP[region],
        "server": prometheus_server,
        "placeholder": KEY_PLACEHOLDER,
    }
    return {
        "prometheus-remote-write.yml": PROMETHEUS_YML.format(**fill)
        + _keep_block(metrics or [], alloy=False),
        "alloy-dual-write.alloy": ALLOY_DUAL.format(**fill).replace(
            "  }\n}\n", _keep_block(metrics or [], alloy=True) + "  }\n}\n"
        ),
        "alloy-otlp.alloy": ALLOY_OTLP.format(**fill),
        "naming-consequences.md": NAMING + _sources_section(datasources or []),
        "check-queries.md": _queries_text(
            (metrics or [])[:MAX_TARGETS], (log_attributes or [])[:MAX_TARGETS], hours
        ),
    }


def write_files(files: dict[str, str], out: Path) -> None:
    """Write into a new or empty folder."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
