"""Metric types and series labels read from a Prometheus data source, for the dashboard converter.

The converter guesses counter or gauge from the metric name suffix and refuses sum() of a gauge
unless the name map says which labels name one series. Prometheus knows both: `/api/v1/metadata`
gives each metric's TYPE and `/api/v1/series` gives the label sets. This module reads them for
only the metrics the dashboards name, through Grafana's data source proxy, and writes them in the
name map format (`types` and `series`, see `dashboard_convert/name_map.py`).

Safeguards, on top of those of `core/safe_http.py` (no redirects, token scrubbing, response and
run byte caps, a deadline): GET only; the host is validated like the other Grafana extractors;
only three path shapes run, each with fixed parameter patterns; at most MAX_REQUESTS requests,
MAX_METRICS metrics, MAX_SERIES series per metric and MAX_RUN_BYTES bytes in all. Hints are
optional: any shortfall leaves a metric out and the converter falls back to the suffix guess for it.
"""

import json
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import httpx

from importer.core.safe_http import ReadBudget, get_json
from importer.sources.grafana.dashboards.datasources import resolve_target
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.extract import GrafanaError, validate_host

SERVICE = "Grafana"
UID = re.compile(r"[A-Za-z0-9_-]{1,40}")
DS_NAME = re.compile(r"[A-Za-z0-9 _.()-]{1,100}")
METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]{0,200}")
PROXY = re.compile(r"/api/datasources/proxy/uid/([A-Za-z0-9_-]{1,40})/api/v1/(metadata|series)")
DS_PATH = re.compile(r"/api/datasources/(?:uid/[A-Za-z0-9_-]{1,40}|name/[A-Za-z0-9 _.()-]{1,100})")
SERIES_MATCH = re.compile(r'\{__name__="[A-Za-z_:][A-Za-z0-9_:]{0,200}"\}')
DIGITS = re.compile(r"[0-9]{1,10}")
PARAMS = {"match[]": SERIES_MATCH, "limit": DIGITS, "start": DIGITS, "end": DIGITS}
MAX_METRICS = 500
MAX_REQUESTS = 600
MAX_SERIES = 1000  # per metric; more means the label set was not seen whole and gives no hint
MAX_RUN_BYTES = 64 * 1024 * 1024
MAX_METADATA = 100_000
MAX_KEY_LABELS = 4
MAX_KEY_FALLBACK = 8
WINDOW_SECONDS = 3600
MAX_WARNINGS = 20
# labels that usually name a scrape target, tried first when several sets identify the series
PRIORITY = (
    "job",
    "instance",
    "cluster",
    "namespace",
    "pod",
    "container",
    "node",
    "service",
    "device",
    "mountpoint",
    "name",
)
COUNTER_SUFFIXES = ("_total", "_count", "_sum")
KEYWORDS = "by without on ignoring group_left group_right bool and or unless offset inf nan"
EXPRESSION_SKIP = frozenset(KEYWORDS.split())
STRING = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
BRACES = re.compile(r"\{[^{}]*\}")
RANGES = re.compile(r"\[[^\]]*\]")
GROUPING = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)")
VARIABLES = re.compile(r"\$\{[^}]*\}|\$\w+|\[\[\w+(?::\w+)?\]\]")
WORD = re.compile(r"(?<![\w:.])([A-Za-z_:][A-Za-z0-9_:]*)(?![\w:])(\s*\()?")
NRQL_METRIC = re.compile(
    r"\b(?:sum|average|latest|min|max|count|uniqueCount|bucketPercentile)\(\s*"
    r"`?([A-Za-z_:][A-Za-z0-9_:.]*)`?"
)
Getter = Callable[[str, dict[str, str]], Any]


class HintsError(GrafanaError):
    """Reading the hints failed; the message is safe to show."""


# --- which metrics ---


def metrics_in_expression(expr: str) -> set[str]:
    """Metric names in a PromQL text: words that are not functions, keywords, labels or strings."""
    text = STRING.sub('""', expr)
    text = VARIABLES.sub(" ", text)
    names: set[str] = set()
    text = BRACES.sub(" ", text)
    text = RANGES.sub(" ", text)
    text = GROUPING.sub(" ", text)
    for found in WORD.finditer(text):
        if found[2] is None:  # a word followed by "(" is a function
            names.add(found[1])
    return {n for n in names if n not in EXPRESSION_SKIP and METRIC.fullmatch(n)}


def metrics_from_dashboards(path: Path) -> set[str]:
    """Metric names in the visible Prometheus queries of an extract folder or file."""
    found: set[str] = set()
    for dash in load_estate(path).dashboards:
        for panel in dash.panels:
            for target in panel.targets:
                if target.hidden or resolve_target(target, panel, dash) != "prometheus":
                    continue
                found |= metrics_in_expression(target.expr)
    return found


def _walk(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "nrql" and isinstance(value, str):
                yield value
            else:
                yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def metrics_from_ir(files: list[Path]) -> set[str]:
    """Metric names in the NRQL of converted dashboards (only widgets that converted have any)."""
    found: set[str] = set()
    for file in files:
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError, RecursionError):
            continue
        for nrql in _walk(data):
            found |= {m for m in NRQL_METRIC.findall(nrql) if METRIC.fullmatch(m)}
    return found


def metric_names(path: Path) -> set[str]:
    """Names from an extract folder, or from converted IR (a `dashboard-ir` folder or a file)."""
    folder = path / "dashboard-ir" if (path / "dashboard-ir").is_dir() else path
    files = sorted(folder.glob("*.json")) if folder.is_dir() else [folder]
    for file in files[:3]:
        try:
            head = json.loads(file.read_text())
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(head, dict) and "widgets" in head and "source_system" in head:
            return metrics_from_ir(files)
    return metrics_from_dashboards(path)


# --- the read-only client ---


def _allowed(path: str, params: dict[str, str]) -> bool:
    if DS_PATH.fullmatch(path):
        return not params
    found = PROXY.fullmatch(path)
    if found is None:
        return False
    if found[2] == "metadata":
        return set(params) <= {"limit"} and all(DIGITS.fullmatch(v) for v in params.values())
    return "match[]" in params and all(
        k in PARAMS and PARAMS[k].fullmatch(v) for k, v in params.items()
    )


@contextmanager
def hint_getter(
    token: str,
    base: str,
    transport: httpx.BaseTransport | None = None,
    allow_host: str | None = None,
    max_requests: int = MAX_REQUESTS,
) -> Iterator[Getter]:
    """A GET-only Getter for the three allow-listed path shapes; closes its connection pool."""
    try:
        checked = validate_host(base, allow_host)
    except ValueError as exc:
        raise HintsError(str(exc)) from None
    if checked != base:
        raise HintsError("the base URL must be https://<host> exactly as validated")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    budget = ReadBudget()
    budget.bytes_left = MAX_RUN_BYTES
    requests = 0
    with httpx.Client(
        timeout=60, headers=headers, transport=transport, follow_redirects=False
    ) as client:

        def get(path: str, params: dict[str, str]) -> Any:
            nonlocal requests
            if not _allowed(path, params):
                raise HintsError("this path is not on the allow-list of read-only calls")
            requests += 1
            if requests > max_requests:
                raise HintsError(f"more than {max_requests} requests; stopped")
            return get_json(client, base + path, params, token, HintsError, SERVICE, budget)

        yield get


def find_datasource(get: Getter, wanted: str) -> str:
    """The uid of the Prometheus data source named or identified by `wanted`."""
    candidates: list[str] = []
    if UID.fullmatch(wanted):
        candidates.append(f"/api/datasources/uid/{wanted}")
    if DS_NAME.fullmatch(wanted):
        candidates.append(f"/api/datasources/name/{wanted}")
    if not candidates:
        raise HintsError("--datasource must be a data source uid or name")
    last = ""
    for path in candidates:
        try:
            body = get(path, {})
        except HintsError as exc:
            last = str(exc)
            if "returned 404" in last:
                continue
            raise
        kind, uid = body.get("type"), body.get("uid")
        if not (isinstance(kind, str) and "prometheus" in kind and isinstance(uid, str)):
            raise HintsError("that data source is not a Prometheus data source")
        if not UID.fullmatch(uid):
            raise HintsError("the data source uid has an unexpected form")
        return uid
    raise HintsError(f"no data source {wanted!r} was found ({last})")


# --- reading and deciding ---


@dataclass
class Hints:
    types: dict[str, str] = field(default_factory=dict)
    series: dict[str, list[str]] = field(default_factory=dict)
    requested: int = 0
    unknown: list[str] = field(default_factory=list)  # named in dashboards, not in the metadata
    truncated: list[str] = field(default_factory=list)  # too many series to name a key from
    skipped: int = 0  # metrics left out by the MAX_METRICS cap
    warnings: list[str] = field(default_factory=list)


def _metadata_types(body: Any) -> dict[str, str]:
    data = body.get("data") if isinstance(body, dict) else None
    out: dict[str, str] = {}
    if isinstance(data, dict):
        for name, rows in list(data.items())[:MAX_METADATA]:
            kind = rows[0].get("type") if isinstance(rows, list) and rows else None
            if isinstance(name, str) and isinstance(kind, str):
                out[name] = kind
    return out


def type_of(name: str, meta: dict[str, str]) -> str | None:
    """counter, gauge or histogram_bucket for a series name, or None when Prometheus is unsure."""
    kind = meta.get(name)
    if kind == "counter":
        return "counter"
    if kind in ("gauge", "info", "stateset", "summary"):
        return "gauge"  # a summary's own name is its quantile series
    for suffix in ("_bucket", "_count", "_sum", "_total"):
        if not name.endswith(suffix):
            continue
        base = meta.get(name[: -len(suffix)])
        if suffix == "_bucket" and base == "histogram":
            return "histogram_bucket"
        if suffix in ("_count", "_sum") and base in ("histogram", "summary"):
            return "counter"
        if suffix == "_total" and base == "counter":
            return "counter"
    return None


def key_labels(rows: list[dict[str, str]]) -> list[str] | None:
    """The labels that together name exactly one of the observed series; None if none is safe.

    Labels equal in every series are not needed. An empty list means one series. The smallest set
    (preferring job, instance, pod and the like) is chosen; with no set of MAX_KEY_LABELS or fewer
    the varying labels all go in, up to MAX_KEY_FALLBACK.
    """
    if len(rows) <= 1:
        return []
    names = {k for row in rows for k in row if k != "__name__"}
    varying = [k for k in names if len({row.get(k, "") for row in rows}) > 1]
    ordered = [k for k in PRIORITY if k in varying] + sorted(
        k for k in varying if k not in PRIORITY
    )
    for size in range(1, min(MAX_KEY_LABELS, len(ordered)) + 1):
        for subset in combinations(ordered, size):
            if len({tuple(row.get(k, "") for k in subset) for row in rows}) == len(rows):
                return sorted(subset)
    if len(ordered) <= MAX_KEY_FALLBACK:
        keyed = len({tuple(row.get(k, "") for k in ordered) for row in rows}) == len(rows)
        return sorted(ordered) if keyed else None
    return None


def _series_rows(body: Any) -> list[dict[str, str]]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return []
    return [
        {k: v for k, v in row.items() if isinstance(k, str) and isinstance(v, str)}
        for row in data
        if isinstance(row, dict)
    ]


def _warn(hints: Hints, text: str) -> None:
    if len(hints.warnings) < MAX_WARNINGS:
        hints.warnings.append(text)


def read_hints(get: Getter, uid: str, wanted: set[str], now: float | None = None) -> Hints:
    """Types and series keys for the metrics in `wanted`, read through the data source proxy."""
    hints = Hints()
    names = sorted(wanted)
    if len(names) > MAX_METRICS:
        hints.skipped = len(names) - MAX_METRICS
        names = names[:MAX_METRICS]
    base = f"/api/datasources/proxy/uid/{uid}/api/v1"
    meta = _metadata_types(get(f"{base}/metadata", {"limit": str(MAX_METADATA)}))
    hints.requested += 1
    end = int(now if now is not None else time.time())
    window = {"start": str(end - WINDOW_SECONDS), "end": str(end), "limit": str(MAX_SERIES + 1)}
    for name in names:
        kind = type_of(name, meta)
        if kind is None:
            hints.unknown.append(name)
            continue
        hints.types[name] = kind
        if kind != "gauge":
            continue
        try:
            body = get(f"{base}/series", {"match[]": '{__name__="' + name + '"}', **window})
        except HintsError as exc:
            _warn(hints, f"stopped reading series after {name}: {exc}")
            break
        hints.requested += 1
        rows = _series_rows(body)
        if len(rows) > MAX_SERIES:
            hints.truncated.append(name)
            continue
        keys = key_labels(rows) if rows else None
        if keys is None:
            if rows:
                hints.truncated.append(name)
            continue
        hints.series[name] = keys
    return hints


def hints_document(hints: Hints) -> dict[str, Any]:
    """The hints in the name map format, ready to write as JSON."""
    return {
        "_comment": "Written by grafana-prom-hints from Prometheus metadata and observed series. "
        "Use with convert-grafana-dashboards --hints.",
        "types": dict(sorted(hints.types.items())),
        "series": dict(sorted(hints.series.items())),
    }
