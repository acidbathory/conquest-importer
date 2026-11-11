"""Azure PromQL rules, converted by the Grafana PromQL translator.

An Azure Prometheus rule group alert (and a PromQL metric alert criterion) is a Prometheus
alerting rule: one expression with the comparison inside it and a `for` duration. That is the
shape of a Grafana rule-file rule, so this adapter cleans the expression into plain PromQL, builds
a `GrafanaRule` and lets `importer.sources.grafana.translate` decide what converts and what is
refused. Nothing about PromQL is decided here, so Azure and Grafana refuse the same shapes for the
same reasons and share the remediation.

What the adapter changes in the expression:
- `#` comments are removed;
- a quoted selector `{"metric.name", label="x"}` becomes `metric_name{label="x"}` (dots and
  slashes in the name become underscores, the way Prometheus remote write spells them).
A `${NAME}` placeholder is refused: it expects a person to fill it in.
"""

import re
from typing import Any

from importer.core.ir import Confidence, IRMonitor, Signal, SignalKind, Threshold
from importer.sources.azure import reasons as R
from importer.sources.azure.common import Level, manual
from importer.sources.azure.models import AzureAlert, iso_seconds
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.translate import (
    N_INFERRED,
    N_ROUTING,
    N_RULE_FILE,
    Fail,
    translate_rule,
)

DROP_NOTES = frozenset({N_RULE_FILE, N_ROUTING, N_INFERRED})
QUOTED_SELECTOR = re.compile(r'\{\s*"((?:[^"\\]|\\.)*)"\s*(?:,\s*([^{}]*))?\}')
R_PLACEHOLDER = (
    "prometheus rule contains a template placeholder such as ${NAME}; fill it in before the "
    "rule can be converted"
)
R_NO_EXPRESSION = "prometheus rule has no expression"
R_DURATION = "prometheus rule has a duration that is not an ISO 8601 duration such as PT5M"
N_RENAMED = "the Azure metric name {original!r} is written {prom!r} in PromQL"


def _strip_comments(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        quote = ""
        for i, ch in enumerate(line):
            if quote:
                quote = "" if ch == quote else quote
            elif ch in "\"'`":
                quote = ch
            elif ch == "#":
                line = line[:i]
                break
        out.append(line)
    return " ".join(" ".join(out).split())


def _prom_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_:]", "_", name)


def clean(expression: str, notes: list[str]) -> str:
    text = _strip_comments(expression)
    if "${" in text:
        raise Fail(R_PLACEHOLDER)

    def rewrite(match: re.Match[str]) -> str:
        original = match[1]
        prom = _prom_name(original)
        if prom != original:
            notes.append(N_RENAMED.format(original=R.shown(original, 80), prom=prom))
        return prom + ("{" + match[2].strip() + "}" if match[2] and match[2].strip() else "")

    return QUOTED_SELECTOR.sub(rewrite, text)


def convert_expression(
    expression: object,
    *,
    key: str,
    name: str,
    for_text: object,
    interval_text: object,
    labels: object = None,
    group: str = "",
) -> tuple[IRMonitor, list[str]]:
    """The translated monitor (possibly manual) and Azure notes; a refusal is a manual monitor."""
    notes: list[str] = []
    try:
        if not isinstance(expression, str) or not expression.strip():
            raise Fail(R_NO_EXPRESSION)
        query = clean(expression, notes)
        duration = iso_seconds(for_text) if for_text not in (None, "") else 0
        interval = iso_seconds(interval_text) if interval_text not in (None, "") else None
        if duration is None or (interval_text not in (None, "") and interval is None):
            raise Fail(R_DURATION)
        rule = GrafanaRule(
            title=name,
            group=group,
            interval_seconds=interval,
            for_seconds=duration,
            labels=labels if isinstance(labels, dict) else {},
            datasource_managed=True,
            expr=query,
        )
        monitor = translate_rule(rule, key)
    except Fail as fail:
        monitor = IRMonitor(
            source_system="grafana",
            source_id=0,
            source_type="",
            name=name,
            signal=Signal(kind=SignalKind.UNKNOWN, raw_query=name),
            confidence=Confidence.MANUAL,
            reasons=[fail.reason],
        )
    return monitor, notes


def finish(
    base: dict[str, Any],
    found: IRMonitor,
    notes: list[str],
    level: Level,
    extra_notes: list[str],
    unmapped: dict[str, Any],
) -> IRMonitor:
    """Turn the Grafana-built monitor into an Azure one with Azure wording and severity."""
    if found.confidence is Confidence.MANUAL:
        return manual(base, list(found.reasons), unmapped=unmapped)
    thresholds = [
        Threshold(severity=level.severity, operator=t.operator, value=t.value)
        for t in found.thresholds
    ]
    kept = [r.replace("Grafana", "Azure Monitor") for r in found.reasons if r not in DROP_NOTES]
    return IRMonitor(
        **base,
        signal=found.signal,
        thresholds=thresholds,
        no_data=found.no_data,
        emit=found.emit,
        confidence=found.confidence,
        reasons=list(dict.fromkeys([R.N_PROM_NAMES, *notes, *extra_notes, *kept])),
        unmapped=unmapped,
        data_path="needs-prometheus-remote-write",
    )


def convert_metric_promql(
    alert: AzureAlert, base: dict[str, Any], level: Level, notes: list[str], extra: dict[str, Any]
) -> IRMonitor:
    """A metric alert whose criteria is PromQL (`odata.type` PromQLCriteria)."""
    props = alert.properties
    criteria = props.get("criteria")
    queries = criteria.get("allOf") if isinstance(criteria, dict) else None
    if not isinstance(queries, list) or not queries or not isinstance(queries[0], dict):
        return manual(base, [R.M_NO_CRITERIA], unmapped=extra)
    if len(queries) > 1:
        return manual(base, [R.M_SEVERAL], unmapped=extra)
    failing = criteria.get("failingPeriods") if isinstance(criteria, dict) else None
    for_text = failing.get("for") if isinstance(failing, dict) else None
    if queries[0].get("criterionType") == "DynamicThresholdCriterion":
        return manual(base, [R.M_DYNAMIC], unmapped=extra)
    found, found_notes = convert_expression(
        queries[0].get("query"),
        key=str(base["source_key"]),
        name=alert.name,
        for_text=for_text,
        interval_text=props.get("evaluationFrequency"),
    )
    return finish(base, found, found_notes, level, notes, extra)


__all__ = ["clean", "convert_expression", "convert_metric_promql", "finish"]
