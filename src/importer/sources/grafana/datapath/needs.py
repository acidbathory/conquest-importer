"""What converted Grafana rules and dashboard tiles need from a data path, and a verdict for each.

Converted NRQL reads `Metric` or `Log` under the names Prometheus or Loki used, so each rule or
tile has data only after a sender is set up (plan.py writes the snippets). This module reads the
saved IR of `convert` and `convert-grafana-dashboards`, never the network, and answers:

- which metric families are used, with the type they get in New Relic and the labels that must
  arrive as attributes (`families`);
- for each rule and tile, whether its data path is known and after which step it has data
  (`judge`), so a report can say "converted AND data path known" (`coverage`).

Naming rules are the documented ones in plan.NAMING (research note
grafana-notifications-datapath.md, section 6). Where a rule is UNVERIFIED there it is a "check
first" verdict here, never a "known" one.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.core.ir import Confidence, IRMonitor, SignalKind
from importer.sources.grafana.datapath.plan import ATTRIBUTE_NAME, METRIC_NAME
from importer.sources.grafana.notifications.models import DataSource

STEP_KEY = 1  # put the ingest license key where the sender reads it
STEP_METRICS = 2  # send Prometheus metrics (remote write, Alloy or an OpenTelemetry collector)
STEP_LOGS = 3  # send Loki logs (Alloy over OTLP)
STEPS = {
    STEP_KEY: "store the New Relic ingest license key under the secret name NEW_RELIC_LICENSE_KEY",
    STEP_METRICS: "send the Prometheus metrics (prometheus-remote-write.yml, alloy-dual-write."
    "alloy or otel-collector.yml)",
    STEP_LOGS: "send the Loki logs over OTLP (alloy-otlp.alloy)",
}
COUNT_SUFFIXES = ("_total", "_count", "_bucket")
SUMMARY_SUFFIX = "_sum"
SECTION_HEADING = "## Data path coverage"
RENAMED_LABEL = "metric_name"  # what the relabel snippet renames a `metricName` label to
MAX_LISTED = 12

SELECT_FN = re.compile(
    r"\b(?:sum|average|latest|min|max|count|uniqueCount|median|stddev|bucketPercentile|"
    r"percentile|filter|rate|derivative)\s*\(\s*(`[^`]+`|[A-Za-z_:][A-Za-z0-9_:]*)\s*[,)]"
)
QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'")
VARIABLE = re.compile(r"\{\{[^}]*\}\}")
LABEL = re.compile(
    r"(`[^`]+`|[A-Za-z_][A-Za-z0-9_.]*)\s*(?:NOT\s+IN|NOT\s+LIKE|IS\s+NOT|IS|IN|LIKE|!=|=)\s"
)
KEYWORDS = {"AND", "OR", "NOT", "WHERE"}
FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)
CLAUSE_END = re.compile(r"\b(?:FACET|LIMIT|TIMESERIES|SINCE|UNTIL|COMPARE|ORDER)\b")


class Verdict(StrEnum):
    DATA_PATH_KNOWN = "data path known"
    CHECK_FIRST = "check first"
    NO_PATH = "no data path"
    NOT_CONVERTED = "not converted"


def metric_type(name: str) -> str:
    """The type New Relic infers from a name sent by Prometheus remote write."""
    if name.endswith(COUNT_SUFFIXES):
        return "count"
    return "summary" if name.endswith(SUMMARY_SUFFIX) else "gauge"


def label_attribute(label: str) -> tuple[str, str]:
    """(attribute name in New Relic, note) for a Prometheus label."""
    if label == "metricName":
        return (
            RENAMED_LABEL,
            "a label named metricName collides with the attribute that holds the metric name; "
            f"rename it to {RENAMED_LABEL} in the sender and in the converted NRQL",
        )
    if label == "le":
        return "le", "histogram bucket bound; arrives as an attribute named le"
    return label, ""


@dataclass(frozen=True)
class Item:
    kind: str  # rule | tile
    name: str
    converted: bool
    source: str  # prometheus | loki | "" (nothing to read)
    metrics: tuple[str, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Judged:
    item: Item
    verdict: Verdict
    steps: tuple[int, ...] = ()
    notes: tuple[str, ...] = ()

    def text(self) -> str:
        if self.verdict is Verdict.DATA_PATH_KNOWN:
            ids = " and ".join(str(s) for s in self.steps)
            return f"will have data after step {ids}" + "".join(f"; {n}" for n in self.notes)
        return "; ".join((self.verdict.value, *self.notes))


@dataclass
class Family:
    name: str
    nr_type: str
    uses: list[str] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Coverage:
    total: int
    converted: int
    known: int
    check_first: int


def _mask(nrql: str) -> str:
    return VARIABLE.sub("0", QUOTED.sub("''", nrql))


def _unquote(name: str) -> str:
    return name.strip("`")


def parse_nrql(nrql: str) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """(metric names, filter and facet attribute names, source) of one converted query."""
    text = _mask(nrql)
    found = FROM.search(text)
    event = found.group(1) if found else ""
    source = {"Metric": "prometheus", "Log": "loki"}.get(event, "")
    select = text[: found.start()] if found else text
    metrics = (
        tuple(dict.fromkeys(_unquote(m) for m in SELECT_FN.findall(select)))
        if source == "prometheus"
        else ()
    )
    rest = text[found.end() :] if found else ""
    labels: list[str] = []
    where = re.search(r"\bWHERE\b", rest)
    if where:
        clause = rest[where.end() :]
        end = CLAUSE_END.search(clause)
        found_labels = LABEL.findall(clause[: end.start()] if end else clause)
        labels += [
            _unquote(m) for m in found_labels if m.upper() not in KEYWORDS and m != "message"
        ]
    facet = re.search(r"\bFACET\b(.*?)(?:\bLIMIT\b|\bTIMESERIES\b|\bSINCE\b|$)", rest)
    if facet:
        labels += [_unquote(p.strip()) for p in facet.group(1).split(",") if p.strip()]
    return metrics, tuple(dict.fromkeys(labels)), source


def rule_items(monitors: list[IRMonitor]) -> list[Item]:
    items = []
    for m in monitors:
        if m.source_system != "grafana":
            continue
        converted = m.confidence in (Confidence.EXACT, Confidence.APPROXIMATE)
        metric = m.signal.metric
        count = m.signal.count
        if m.signal.kind is SignalKind.LOG and count is not None:
            labels = tuple(dict.fromkeys(f.key for f in count.filters))
            items.append(Item("rule", m.name, converted, "loki", (), labels))
        elif metric is not None:
            names = tuple(dict.fromkeys(t.metric for t in metric.terms.values()))
            keys = [f.key for f in metric.scope if not f.absent] + list(metric.group_by)
            items.append(
                Item("rule", m.name, converted, "prometheus", names, tuple(dict.fromkeys(keys)))
            )
        else:
            items.append(Item("rule", m.name, converted, "", (), ()))
    return items


def tile_items(dashboards: list[IRDashboard]) -> list[Item]:
    items = []
    for d in dashboards:
        for w in d.widgets:
            if w.kind is WidgetKind.MARKDOWN or w.source_type == "row":
                continue
            name = f"{d.name} / {w.title}"
            queries = [parse_nrql(s.nrql) for s in w.raw_series]
            if w.kind is not WidgetKind.CHART or w.confidence is Confidence.MANUAL or not queries:
                items.append(Item("tile", name, False, "", (), ()))
                continue
            metrics = tuple(dict.fromkeys(n for q in queries for n in q[0]))
            labels = tuple(dict.fromkeys(a for q in queries for a in q[1]))
            sources = {q[2] for q in queries} - {""}
            source = "loki" if sources == {"loki"} else ("prometheus" if sources else "")
            items.append(Item("tile", name, True, source, metrics, labels))
    return items


def judge(items: list[Item], sources: list[DataSource]) -> list[Judged]:
    """One verdict per item. `sources` is the data source list of the export, possibly empty."""
    types = {s.type for s in sources}
    out = []
    for item in items:
        if not item.converted:
            out.append(Judged(item, Verdict.NOT_CONVERTED))
            continue
        if item.source == "loki":
            steps = (STEP_KEY, STEP_LOGS)
        elif item.source == "prometheus" and item.metrics:
            steps = (STEP_KEY, STEP_METRICS)
        else:
            out.append(Judged(item, Verdict.NO_PATH, (), ("the query names no metric or log",)))
            continue
        problems = []
        if any(not METRIC_NAME.fullmatch(m) for m in item.metrics):
            out.append(Judged(item, Verdict.NO_PATH, (), ("a metric name is not valid",)))
            continue
        if any(":" in m for m in item.metrics):
            problems.append(
                "a name with a colon (recording rule): whether it is stored unchanged is "
                "UNVERIFIED, run check-grafana-data"
            )
        problems += [label_attribute(x)[1] for x in item.labels if x == "metricName"]
        wanted = "loki" if item.source == "loki" else "prometheus"
        if types and wanted not in types:
            problems.append(f"the export lists no {wanted} data source")
        if problems:
            out.append(Judged(item, Verdict.CHECK_FIRST, steps, tuple(problems)))
        else:
            out.append(Judged(item, Verdict.DATA_PATH_KNOWN, steps))
    return out


def families(items: list[Item]) -> list[Family]:
    """Metric families used by converted items, merged across rules and tiles."""
    found: dict[str, Family] = {}
    for item in items:
        if not item.converted or item.source != "prometheus":
            continue
        for name in item.metrics:
            fam = found.setdefault(name, Family(name, metric_type(name)))
            fam.uses.append(f"{item.kind}: {item.name}")
            fam.labels.update(x for x in item.labels if ATTRIBUTE_NAME.fullmatch(x))
    return [found[k] for k in sorted(found)]


def coverage(judged: list[Judged]) -> Coverage:
    counts = Counter(j.verdict for j in judged)
    converted = len(judged) - counts[Verdict.NOT_CONVERTED]
    return Coverage(
        len(judged), converted, counts[Verdict.DATA_PATH_KNOWN], counts[Verdict.CHECK_FIRST]
    )


def coverage_section(parts: dict[str, Coverage]) -> str:
    """Closing report section; `parts` maps a plural noun (rules, tiles) to its coverage."""
    lines = [SECTION_HEADING, ""]
    for noun, cov in parts.items():
        lines.append(
            f"- {noun.capitalize()}: {cov.total} read, {cov.converted} converted, "
            f"**{cov.known} of {cov.converted} converted with a known data path** "
            f"({cov.check_first} more need a check first)."
        )
    lines += [
        "",
        "A known data path means a sender recipe exists and the names are covered by the "
        "documented rules. It does not mean the data arrives: run `check-grafana-data` after "
        "the senders are running. See datapath-plan.md in the plan folder.",
    ]
    return "\n".join(lines) + "\n"


def replace_section(report: str, section: str) -> str:
    """Put `section` at the end of `report`, replacing an earlier copy of the same section."""
    start = report.find(SECTION_HEADING)
    if start != -1:
        nxt = report.find("\n## ", start + len(SECTION_HEADING))
        report = report[:start] + (report[nxt + 1 :] if nxt != -1 else "")
    return report.rstrip("\n") + "\n\n" + section
