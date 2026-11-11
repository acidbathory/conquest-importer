"""Google PromQL alert conditions, converted by the Grafana PromQL translator.

A Google `conditionPrometheusQueryLanguage` is a Prometheus alerting rule: one query with the
comparison inside it, a `duration` (Prometheus `for`) and an `evaluationInterval`. That is the
shape of a Grafana rule-file rule, so this adapter cleans the query into plain PromQL, builds a
`GrafanaRule` and lets `importer.sources.grafana.translate` decide what converts and what is
refused. Nothing about PromQL is decided here, so the two sources refuse the same shapes for the
same reasons and share the cookbook remediation.

What the adapter changes in the query, and says so in the notes:
- `#` comments are removed;
- Google's quoted selector `{"metric.name", label="x"}` and `{"__name__"="m", ...}` become
  `metric_name{label="x"}`; names with dots or slashes get the Prometheus spelling Google documents
  (`workload.googleapis.com/jvm.memory.heap.used` is `workload_googleapis_com_jvm_memory_heap_used`;
  Google writes a colon after the domain, NRQL attribute names cannot hold one);
- the `monitored_resource` matcher selects a Google resource type and is not a label on data that
  arrives through remote write, so it is dropped.
A `${NAME}` placeholder is refused: the sample policies expect a person to fill it in.
"""

import re
from typing import Any

from importer.core.ir import (
    Confidence,
    IRMonitor,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.core.remediation import Rule, register_rules
from importer.sources.grafana.models import GrafanaRule, parse_duration
from importer.sources.grafana.notifications.promrules import split_comparison
from importer.sources.grafana.translate import (
    N_INFERRED,
    N_ROUTING,
    N_RULE_FILE,
    Fail,
    translate_rule,
)

PROMQL_KIND = "conditionPrometheusQueryLanguage"
R_PLACEHOLDER = (
    "promql query contains a template placeholder such as ${NAME}; fill it in with the real "
    "value before the condition can be converted"
)
R_DURATION = "promql condition has a duration or evaluation interval that is not like 5m or 300s"
R_QUERY = "promql condition has no query"
N_NAMES = (
    "metric names are read as Prometheus names: this condition only returns data if the metric "
    "reaches New Relic through Prometheus remote write (Google Managed Service for Prometheus "
    "data keeps its original name there) or an OpenTelemetry collector; a Google Cloud service "
    "metric collected by the New Relic Google Cloud integration is named gcp.<service>.<path> "
    "instead, so map the name by hand in that case"
)
N_RENAMED = "the Google metric name {original!r} is written {prom!r} in PromQL"
N_RESOURCE = (
    "the monitored_resource matcher ({value}) was dropped: it picks a Google resource type and "
    "is not a label on data that arrives through remote write"
)
N_VALIDATION = (
    "disableMetricValidation is true: Google does not check that the metric exists when the "
    "policy is saved; New Relic does not either, so check the name with a query"
)
N_LATENCY = (
    "Google evaluates the query every {interval}s; the New Relic condition uses whole-minute "
    "windows and adds an aggregation delay and ingest latency, so it opens later than Google "
    "and may differ"
)
N_COLON_NAME = (
    "{name} is a Cloud Monitoring metric spelled the PromQL way; the colon is written as _ because "
    "NRQL names cannot hold one, and New Relic only has the metric if it is sent there (the "
    "Google Cloud integration names it gcp.<service>.<path>)"
)
N_GOOGLE_FIELDS = (
    "Google's PromQL conditions do not follow the policy auto-close setting; New Relic closes "
    "a violation when the value stops breaching"
)
DROP_NOTES = frozenset({N_RULE_FILE, N_ROUTING, N_INFERRED})
SAFE_TAG = re.compile(r"[A-Za-z0-9_.-]{1,64}")
PROM_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
NAME_ONLY = re.compile(r'\s*"((?:[^"\\]|\\.)*)"\s*')
DUNDER_NAME = re.compile(r'\s*"__name__"\s*=\s*"((?:[^"\\]|\\.)*)"\s*')
COLON_FORM = re.compile(r"\b([a-z0-9_]+_googleapis_com):(?=[a-z0-9_])")

RULES: list[Rule] = [
    (
        re.compile(r"^promql query contains a template placeholder"),
        "PromQL query with a template placeholder",
        [
            "The sample policies from Google use ${CLUSTER_NAME}-style placeholders for a person "
            "to replace. Put the real project, cluster or resource value in the query in "
            "Google, export the policy again and convert it, or write the NRQL by hand with "
            "a WHERE clause on the matching attribute.",
        ],
    ),
]
register_rules(RULES)


def _scan(text: str) -> list[tuple[str, bool]]:
    """Split text into (piece, quoted) runs; quoted pieces include their quotes."""
    pieces: list[tuple[str, bool]] = []
    i, start = 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            if i > start:
                pieces.append((text[start:i], False))
            j = i + 1
            while j < len(text) and text[j] != ch:
                j += 2 if text[j] == "\\" and ch != "`" else 1
            pieces.append((text[i : j + 1], True))
            i = start = j + 1
        else:
            i += 1
    if start < len(text):
        pieces.append((text[start:], False))
    return pieces


def _strip_comments(text: str) -> str:
    out: list[str] = []
    for piece, quoted in _scan(text):
        out.append(piece if quoted else re.sub(r"#[^\n]*", "", piece))
    return "".join(out)


def _colon_names(text: str, notes: list[str]) -> str:
    """`domain_googleapis_com:path` names lose the colon, which NRQL attribute names cannot hold."""
    out: list[str] = []
    for piece, quoted in _scan(text):
        if not quoted:
            notes += [
                N_COLON_NAME.format(name=f"{found}:...")
                for found in dict.fromkeys(COLON_FORM.findall(piece))
            ]
            piece = COLON_FORM.sub(r"\1_", piece)
        out.append(piece)
    return "".join(out)


def _prom_name(original: str) -> str:
    """The Prometheus spelling of a Cloud Monitoring or OpenTelemetry metric name."""
    if PROM_NAME.fullmatch(original):
        return original
    domain, slash, path = original.partition("/")
    if slash:
        return re.sub(r"[^A-Za-z0-9_]", "_", f"{domain}_{path}")
    return re.sub(r"[^A-Za-z0-9_]", "_", original)


def _split_items(body: str) -> list[str]:
    items: list[str] = []
    current = ""
    for piece, quoted in _scan(body):
        if quoted:
            current += piece
            continue
        parts = piece.split(",")
        current += parts[0]
        for part in parts[1:]:
            items.append(current)
            current = part
    items.append(current)
    return [i.strip() for i in items if i.strip()]


def _rewrite_selectors(text: str, notes: list[str]) -> str:
    """Quoted names become plain selectors and `monitored_resource` matchers are dropped."""
    result = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":  # a string outside a selector is copied as it is
            stop = _string_end(text, i)
            result += text[i : stop + 1]
            i = stop + 1
        elif ch == "{":
            end = _closing_brace(text, i)
            if end is None:
                return text  # unbalanced; the translator refuses it with its own reason
            result += _selector(text[i + 1 : end], notes)
            i = end + 1
        else:
            result += ch
            i += 1
    return result


def _string_end(text: str, start: int) -> int:
    quote = text[start]
    pos = start + 1
    while pos < len(text) and text[pos] != quote:
        pos += 2 if text[pos] == "\\" and quote != "`" else 1
    return min(pos, len(text) - 1)


def _closing_brace(text: str, start: int) -> int | None:
    pos = start + 1
    while pos < len(text):
        if text[pos] in "\"'`":
            pos = _string_end(text, pos)
        elif text[pos] == "}":
            return pos
        pos += 1
    return None


def _selector(body: str, notes: list[str]) -> str:
    items = _split_items(body)
    name = ""
    rest: list[str] = []
    for index, item in enumerate(items):
        bare = NAME_ONLY.fullmatch(item)
        dunder = DUNDER_NAME.fullmatch(item)
        matcher = re.fullmatch(r'monitored_resource\s*(=~|!~|!=|=)\s*("(?:[^"\\]|\\.)*")', item)
        if bare and index == 0:
            name = bare[1]
        elif dunder:
            name = dunder[1]
        elif matcher:
            notes.append(N_RESOURCE.format(value=f"{matcher[1]} {matcher[2]}"))
        else:
            rest.append(item)
    if not name:
        return "{" + body + "}"
    prom = _prom_name(name)
    if prom != name:
        notes.append(N_RENAMED.format(original=name, prom=prom))
    return prom + ("{" + ", ".join(rest) + "}" if rest else "")


def _seconds(value: object) -> int | None:
    try:
        return parse_duration(value)
    except ValueError:
        return None


def _tags(body: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for key, tag in (("ruleGroup", "gcp:rule-group:"), ("alertRule", "gcp:alert-rule:")):
        value = body.get(key)
        if isinstance(value, str) and SAFE_TAG.fullmatch(value):
            tags.append(tag + value)
    return tags


def _labels(body: dict[str, Any]) -> dict[str, Any]:
    value = body.get("labels")
    return dict(value) if isinstance(value, dict) else {}


def _outer_parens(query: str) -> str:
    """`(a / b) > 1` is `a / b > 1`: one pair of parentheses around the whole left side goes."""
    comparison = split_comparison(query)
    if comparison is None or not query.startswith(comparison.query):
        return query
    left, tail = comparison.query.strip(), query[len(comparison.query) :]
    while left.startswith("(") and _closing_paren(left) == len(left) - 1:
        left = left[1:-1].strip()
    return left + tail


def _closing_paren(text: str) -> int | None:
    depth = 0
    for index, (piece, quoted) in _pieces_with_offsets(text):
        if quoted:
            continue
        for offset, ch in enumerate(piece):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                return index + offset
    return None


def _pieces_with_offsets(text: str) -> list[tuple[int, tuple[str, bool]]]:
    out: list[tuple[int, tuple[str, bool]]] = []
    position = 0
    for piece, quoted in _scan(text):
        out.append((position, (piece, quoted)))
        position += len(piece)
    return out


def _clean_query(raw: str, notes: list[str]) -> str:
    query = _strip_comments(raw)
    if "${" in query:
        raise Fail(R_PLACEHOLDER)
    query = _colon_names(_rewrite_selectors(query, notes), notes)
    return _outer_parens(" ".join(query.split()))


def convert(body: dict[str, Any], *, key: str, name: str, severity: Severity) -> IRMonitor:
    """The translated monitor with Google's notes; a refusal comes back as a manual monitor."""
    notes: list[str] = []
    raw = body.get("query")
    if not isinstance(raw, str) or not raw.strip():
        raise Fail(R_QUERY)
    query = _clean_query(raw, notes)
    duration = _seconds(body.get("duration"))
    interval = _seconds(body.get("evaluationInterval"))
    if duration is None or interval is None:
        raise Fail(R_DURATION)
    group = body.get("ruleGroup")
    rule = GrafanaRule(
        title=name,
        group=group if isinstance(group, str) else "",
        interval_seconds=interval or None,
        for_seconds=duration,
        labels=_labels(body),
        datasource_managed=True,
        expr=query,
    )
    monitor = translate_rule(rule, key)
    if monitor.confidence is Confidence.MANUAL:
        return monitor
    monitor.thresholds = [
        Threshold(severity=severity, operator=t.operator, value=t.value) for t in monitor.thresholds
    ]
    kept = [
        r.replace("Grafana", "Google Cloud Monitoring")
        for r in monitor.reasons
        if r not in DROP_NOTES
    ]
    extra = [N_NAMES, *notes]
    if body.get("disableMetricValidation") is True:
        extra.append(N_VALIDATION)
    extra += [N_LATENCY.format(interval=interval or 30), N_GOOGLE_FIELDS]
    monitor.reasons = list(dict.fromkeys([*extra, *kept]))
    monitor.tags = _tags(body)
    return monitor


def translate_condition(
    base: dict[str, Any], body: dict[str, Any], *, severity: Severity, prior: list[str]
) -> IRMonitor:
    """The IR monitor for one PromQL condition; `base` holds the source fields the caller built."""
    name = str(base["name"])
    try:
        found = convert(body, key=str(base["source_key"]), name=name, severity=severity)
    except Fail as fail:
        reasons = [fail.reason]
    else:
        if found.confidence is not Confidence.MANUAL:
            return IRMonitor(
                **base,
                signal=found.signal,
                thresholds=found.thresholds,
                no_data=found.no_data,
                tags=found.tags,
                confidence=found.confidence,
                reasons=[*prior, *found.reasons],
            )
        reasons = found.reasons
    return IRMonitor(
        **base,
        signal=Signal(kind=SignalKind.METRIC, raw_query=name),
        confidence=Confidence.MANUAL,
        reasons=[*reasons, *prior],
    )
