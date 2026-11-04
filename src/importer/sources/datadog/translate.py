"""Translate Datadog monitors into IR, labelling how faithful each conversion is.

translate_monitor never raises: anything it cannot convert becomes a manual or
unsupported IRMonitor with the reason recorded.
"""

import json
import re
from pathlib import Path

from pydantic import BaseModel, model_validator

from importer.core.emit.nrql import TAG_ATTRIBUTES
from importer.core.ir import (
    Confidence,
    CountSignal,
    HeartbeatSignal,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NoData,
    Notification,
    NrTarget,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.queries import (
    UnsupportedQuery,
    parse_count_query,
    parse_metric_query,
    parse_service_check,
)

METRIC_MAP_PATH = Path(__file__).parent / "metric_map.json"
HANDLED_OPTIONS = {"thresholds", "notify_no_data", "no_data_timeframe"}
RECOVERY_KEYS = ("critical_recovery", "warning_recovery", "ok")
# Fields Datadog always returns that describe the record, not the alert configuration.
IGNORED_METADATA = {
    "org_id",
    "created_at",
    "deleted",
    "creator",
    "multi",
    "matching_downtimes",
    "overall_state_modified",
    "restricted_roles",
}
# Option values that equal Datadog's defaults carry no configuration worth reporting.
DEFAULT_OPTIONS = {
    "notify_audit": False,
    "locked": False,
    "timeout_h": 0,
    "include_tags": True,
    "require_full_window": True,
    "new_host_delay": 300,
    "renotify_interval": 0,
    "escalation_message": "",
    "silenced": {},
}
# Service checks that have a New Relic equivalent: check name -> event that proves the host reports.
HEARTBEAT_CHECKS = {"datadog.agent.up": "SystemSample"}
INDEXES_WITHOUT_CAVEAT = {"*", "trace-search"}
_HANDLE = re.compile(r"(?<![\w@])@[\w.\-]+")
_TEMPLATE_VAR = re.compile(r"\{\{[^}]+\}\}")
_COMPOSITE = re.compile(r"^\s*!?\d+(\s*(&&|\|\|)\s*!?\d+)*\s*$")


class MetricMapping(BaseModel):
    event: str
    attribute: str | None = None
    select: str | None = None
    scale: float = 1.0
    note: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> "MetricMapping":
        if (self.attribute is None) == (self.select is None):
            raise ValueError("set exactly one of attribute or select")
        return self


def load_metric_map(path: Path = METRIC_MAP_PATH) -> dict[str, MetricMapping]:
    raw = json.loads(path.read_text())
    return {k: MetricMapping.model_validate(v) for k, v in raw.items() if not k.startswith("_")}


class _Fail(Exception):
    def __init__(self, kind: SignalKind, confidence: Confidence, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind
        self.confidence = confidence
        self.reason = reason


class _Converted(BaseModel):
    signal: Signal
    operator: Operator = Operator.ABOVE
    value: float = 0.0
    scale: float = 1.0
    thresholds: list[Threshold] | None = None  # set when the form fixes its own thresholds
    no_data: NoData | None = None
    reasons: list[str] = []


# --- per-form conversion -------------------------------------------------------


def _convert_metric(monitor: DatadogMonitor, metric_map: dict[str, MetricMapping]) -> _Converted:
    kind = SignalKind.METRIC
    try:
        parsed = parse_metric_query(monitor.query)
    except UnsupportedQuery as exc:
        raise _Fail(kind, Confidence.MANUAL, str(exc)) from None

    mappings: dict[str, MetricMapping] = {}
    for name, term in parsed.terms.items():
        mapping = metric_map.get(term.metric)
        if mapping is None:
            raise _Fail(
                kind,
                Confidence.MANUAL,
                f"no New Relic mapping for metric {term.metric!r}; add it to metric_map.json",
            )
        mappings[name] = mapping

    first = next(iter(parsed.terms.values()))
    if any((t.scope, t.group_by) != (first.scope, first.group_by) for t in parsed.terms.values()):
        raise _Fail(kind, Confidence.MANUAL, "formula terms have different scopes or groupings")
    events = sorted({m.event for m in mappings.values()})
    if len(events) > 1:
        raise _Fail(
            kind,
            Confidence.MANUAL,
            f"formula mixes metrics from different New Relic events: {', '.join(events)}",
        )
    multi = len(parsed.terms) > 1
    if multi and any(m.scale != 1.0 for m in mappings.values()):
        raise _Fail(kind, Confidence.MANUAL, "unit conversion inside a formula is not supported")

    reasons: list[str] = []
    reasons.extend(dict.fromkeys(m.note for m in mappings.values() if m.note))
    if first.scope:
        reasons.append("scope tags were mapped to attribute names that are unverified")
    unverified = [g for g in first.group_by if g not in TAG_ATTRIBUTES[kind]]
    if unverified:
        reasons.append(f"group-by tags mapped to unverified attributes: {unverified}")
    if any(t.rate_hint for t in parsed.terms.values()):
        reasons.append("as_count()/as_rate() was ignored; check the counting semantics")
    collapsed = any(
        t.space_aggregation != parsed.time_aggregation and mappings[name].select is None
        for name, t in parsed.terms.items()
    )
    if collapsed and not first.group_by:
        reasons.append("different time and space aggregations were collapsed into one")

    scale = 1.0 if multi else next(iter(mappings.values())).scale
    signal = Signal(
        kind=kind,
        raw_query=monitor.query,
        window_seconds=parsed.window_seconds,
        metric=MetricSignal(
            terms={
                name: MetricTerm(
                    metric=term.metric,
                    space_aggregation=term.space_aggregation,
                    nr_target=NrTarget(
                        event=mappings[name].event,
                        attribute=mappings[name].attribute,
                        select=mappings[name].select,
                    ),
                )
                for name, term in parsed.terms.items()
            },
            expression=parsed.expression,
            aggregation=parsed.time_aggregation,
            scope=first.scope,
            group_by=first.group_by,
        ),
    )
    return _Converted(
        signal=signal,
        operator=parsed.operator,
        value=parsed.threshold,
        scale=scale,
        reasons=reasons,
    )


def _convert_count(monitor: DatadogMonitor) -> _Converted:
    try:
        parsed = parse_count_query(monitor.query)
    except UnsupportedQuery as exc:
        kind = SignalKind.LOG if monitor.type == "log alert" else SignalKind.APM
        raise _Fail(kind, Confidence.MANUAL, str(exc)) from None
    kind = SignalKind.LOG if parsed.kind == "logs" else SignalKind.APM
    label = "log" if kind == SignalKind.LOG else "APM"
    reasons = [f"{label} attribute names are unverified (for example status/service mappings)"]
    if any(f.wildcard for f in parsed.filters):
        reasons.append("wildcards were converted to LIKE matches")
    if parsed.index not in INDEXES_WITHOUT_CAVEAT:
        reasons.append(f"index filter {parsed.index!r} was ignored")
    return _Converted(
        signal=Signal(
            kind=kind,
            raw_query=monitor.query,
            window_seconds=parsed.window_seconds,
            count=CountSignal(filters=parsed.filters, group_by=parsed.group_by),
        ),
        operator=parsed.operator,
        value=parsed.threshold,
        reasons=reasons,
    )


def _convert_service_check(monitor: DatadogMonitor) -> _Converted:
    kind = SignalKind.HEARTBEAT
    try:
        parsed = parse_service_check(monitor.query)
    except UnsupportedQuery as exc:
        raise _Fail(kind, Confidence.MANUAL, str(exc)) from None
    event = HEARTBEAT_CHECKS.get(parsed.check)
    if event is None:
        raise _Fail(
            kind,
            Confidence.MANUAL,
            f"no New Relic equivalent for service check {parsed.check!r}",
        )
    return _Converted(
        signal=Signal(
            kind=kind,
            raw_query=monitor.query,
            window_seconds=parsed.last_n * 60,
            heartbeat=HeartbeatSignal(
                check=parsed.check,
                group_by=parsed.group_by,
                nr_target=NrTarget(event=event, select="count(*)"),
            ),
        ),
        thresholds=[Threshold(severity=Severity.CRITICAL, operator=Operator.BELOW, value=1)],
        no_data=NoData(notify=True, timeout_minutes=parsed.last_n),
        reasons=[
            "heartbeat is approximated by counting infrastructure agent samples",
            "check interval is assumed to be 1 minute",
        ],
    )


# --- shared logic ----------------------------------------------------------------


def _thresholds(monitor: DatadogMonitor, conv: _Converted, reasons: list[str]) -> list[Threshold]:
    if conv.thresholds is not None:
        return conv.thresholds
    options = monitor.options.get("thresholds", {})
    critical = options.get("critical", conv.value)
    if float(critical) != conv.value:
        reasons.append("critical threshold in options differs from the query; using the query")
    result = [
        Threshold(severity=Severity.CRITICAL, operator=conv.operator, value=conv.value * conv.scale)
    ]
    if "warning" in options:
        result.append(
            Threshold(
                severity=Severity.WARNING,
                operator=conv.operator,
                value=float(options["warning"]) * conv.scale,
            )
        )
    if any(key in options for key in RECOVERY_KEYS):
        reasons.append("recovery thresholds are not supported and were dropped")
    return result


def translate_monitor(monitor: DatadogMonitor, metric_map: dict[str, MetricMapping]) -> IRMonitor:
    unmapped = {
        k: v
        for k, v in monitor.options.items()
        if k not in HANDLED_OPTIONS and not (k in DEFAULT_OPTIONS and v == DEFAULT_OPTIONS[k])
    }
    unmapped.update(
        {k: v for k, v in (monitor.model_extra or {}).items() if k not in IGNORED_METADATA}
    )
    if monitor.priority is not None:
        unmapped["priority"] = monitor.priority
    reasons: list[str] = []

    handles = sorted(set(_HANDLE.findall(monitor.message)))
    if handles:
        reasons.append(f"notification handles must be configured manually: {', '.join(handles)}")
    if _TEMPLATE_VAR.search(monitor.message):
        reasons.append("message uses template variables that may not exist in New Relic")

    base = {
        "source_id": monitor.id,
        "source_type": monitor.type,
        "name": monitor.name,
        "tags": monitor.tags,
        "notification": Notification(message_template=monitor.message, handles=handles),
        "unmapped": unmapped,
    }

    def failed(kind: SignalKind, confidence: Confidence, reason: str) -> IRMonitor:
        return IRMonitor(
            **base,
            signal=Signal(kind=kind, raw_query=monitor.query),
            confidence=confidence,
            reasons=[reason, *reasons],
        )

    try:
        if monitor.type in ("metric alert", "query alert"):
            conv = _convert_metric(monitor, metric_map)
        elif monitor.type in ("log alert", "trace-analytics alert"):
            conv = _convert_count(monitor)
        elif monitor.type == "service check":
            conv = _convert_service_check(monitor)
        elif monitor.type == "composite":
            ids = re.findall(r"\d+", monitor.query)
            if _COMPOSITE.match(monitor.query):
                detail = f"combines monitors {', '.join(ids)}"
            else:
                detail = "has an unrecognised expression"
            raise _Fail(
                SignalKind.COMPOSITE,
                Confidence.UNSUPPORTED,
                f"composite monitor {detail}; New Relic has no composite condition. "
                "Recreate as one NRQL condition or filter in a workflow",
            )
        else:
            raise _Fail(
                SignalKind.UNKNOWN, Confidence.UNSUPPORTED, f"monitor type {monitor.type!r}"
            )
    except _Fail as fail:
        return failed(fail.kind, fail.confidence, fail.reason)

    reasons = [*conv.reasons, *reasons]
    if unmapped:
        reasons.append(f"settings not migrated: {', '.join(sorted(unmapped))}")
    thresholds = _thresholds(monitor, conv, reasons)
    no_data = conv.no_data or NoData(
        notify=bool(monitor.options.get("notify_no_data", False)),
        timeout_minutes=monitor.options.get("no_data_timeframe"),
    )
    return IRMonitor(
        **base,
        signal=conv.signal,
        thresholds=thresholds,
        no_data=no_data,
        confidence=Confidence.APPROXIMATE if reasons else Confidence.EXACT,
        reasons=reasons,
    )
