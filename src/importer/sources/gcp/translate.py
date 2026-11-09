"""Alert policies to IR.

One IR monitor per condition. Threshold and log-match conditions convert; the rest (absence, MQL,
PromQL, SQL, ratios, forecasts) stay manual with the reason. `translate_policy` never raises.
New Relic's names for GCP metrics follow the documented rule but are not yet confirmed against
live data, so every conversion is approximate until they are.
"""

import json
import re
from pathlib import Path

from importer.core.ids import source_id
from importer.core.ir import (
    Aggregation,
    Confidence,
    CountFilter,
    CountSignal,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NoData,
    Notification,
    NrTarget,
    Occurrences,
    Operator,
    Severity,
    Signal,
    SignalKind,
    TagFilter,
    Threshold,
)
from importer.sources.gcp.filter import FilterError, Term, parse_filter
from importer.sources.gcp.models import GcpAlertPolicy, GcpCondition

OPERATORS = {
    "COMPARISON_GT": Operator.ABOVE,
    "COMPARISON_GE": Operator.ABOVE_OR_EQUALS,
    "COMPARISON_LT": Operator.BELOW,
    "COMPARISON_LE": Operator.BELOW_OR_EQUALS,
    "COMPARISON_EQ": Operator.EQUALS,
    "COMPARISON_NE": Operator.NOT_EQUALS,
}
# per-series aligner -> (time aggregation, metric is a counter)
ALIGNERS = {
    "ALIGN_MEAN": (Aggregation.AVG, False),
    "ALIGN_MAX": (Aggregation.MAX, False),
    "ALIGN_MIN": (Aggregation.MIN, False),
    "ALIGN_SUM": (Aggregation.SUM, False),
    "ALIGN_RATE": (Aggregation.SUM, True),
    "ALIGN_DELTA": (Aggregation.SUM, True),
    "ALIGN_PERCENTILE_50": (Aggregation.AVG, False),
    "ALIGN_PERCENTILE_95": (Aggregation.AVG, False),
    "ALIGN_PERCENTILE_99": (Aggregation.AVG, False),
    "ALIGN_NONE": (Aggregation.AVG, False),
    "": (Aggregation.AVG, False),
}
REDUCERS = {
    "REDUCE_MEAN": Aggregation.AVG,
    "REDUCE_MAX": Aggregation.MAX,
    "REDUCE_MIN": Aggregation.MIN,
    "REDUCE_SUM": Aggregation.SUM,
}
MANUAL_KINDS = {
    "conditionAbsent": "a metric-absence condition has no automatic conversion",
    "conditionMonitoringQueryLanguage": "MQL conditions are not converted",
    "conditionPrometheusQueryLanguage": "PromQL conditions are not converted",
    "conditionSql": "SQL conditions are not converted",
}
SERVICE_SEGMENT = re.compile(r"^(?P<service>[a-z0-9_.]+?)(?:\.googleapis\.com)?/(?P<path>.+)$")
SAFE_PATH = re.compile(r"^[a-z0-9_/]+$")
NAMING_NOTE = (
    "this metric did not appear in our live test, so its New Relic name follows the documented "
    "rule (a gcp. prefix, then the service and metric path) and is not confirmed; check it with "
    "`importer validate` once data flows"
)
# Cloud Logging severity levels, lowest to highest
SEVERITIES = [
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
]
SEVERITY_OPS = {">=", ">", "<=", "<"}
LOG_NOTE = (
    "converts to a count of Log events, so the project's logs must be forwarded to New Relic; "
    "attribute names for forwarded Google Cloud logs are not yet confirmed"
)
LABEL_NOTE = (
    "label attributes such as gcp.zone and gcp.instance_id are on some New Relic metrics and not "
    "others (event-style metrics carry only fullResourceName and the project); if the query "
    "returns nothing, filter on fullResourceName instead"
)
MISSING_NOTE = (
    "New Relic documents this metric and Google has it, but it never arrived in our live test "
    "(over an hour on a new VM), so the converted query may return no data; check before you "
    "enable it"
)
VERIFIED_PATH = Path(__file__).with_name("gcp_verified.json")
POLL_SECONDS = 300  # New Relic reads Google Cloud metrics every 5 minutes
AGGREGATION_DELAY_SECONDS = 120  # Cloud Monitoring can delay metrics by 2 to 3 minutes


def _load_verified() -> tuple[frozenset[str], frozenset[str]]:
    data = json.loads(VERIFIED_PATH.read_text())
    return frozenset(data["received"]), frozenset(data["documented_not_received"])


RECEIVED, NOT_RECEIVED = _load_verified()


class _Fail(Exception):
    def __init__(self, confidence: Confidence, reason: str) -> None:
        super().__init__(reason)
        self.confidence = confidence
        self.reason = reason


def nr_metric_name(metric_type: str) -> str:
    """New Relic's name for a Cloud Monitoring metric type.

    Confirmed on live data: `gcp.` plus the service and the metric path with dots, and the name
    is otherwise unchanged. No suffix is added: Google's own counter names already end in
    `_count` where they should (`disk/read_bytes_count`).
    """
    match = SERVICE_SEGMENT.match(metric_type)
    if match is None or not SAFE_PATH.match(match["path"]):
        raise _Fail(Confidence.MANUAL, f"metric type {metric_type!r} is not a recognised name")
    return f"gcp.{match['service']}.{match['path'].replace('/', '.')}"


PROJECT_LABELS = {"project_id": "gcp.projectId"}


NOT_GOOGLE_SERVICE = (
    "custom.googleapis.com/",
    "external.googleapis.com/",
    "workload.googleapis.com/",
    "prometheus.googleapis.com/",
    "agent.googleapis.com/",
    "logging.googleapis.com/user/",
)
UNCOLLECTED = (
    "the metric is not one of the Google Cloud service metrics that New Relic documents "
    "collecting (custom, Ops Agent, Prometheus and log-based metrics are not listed), so a "
    "converted query would return no data"
)


def _label_name(field: str) -> str | None:
    """New Relic's attribute for a Cloud Monitoring label (confirmed on live data).

    Resource labels become `gcp.<label>` (`gcp.zone`, `gcp.instance_id`, `gcp.topic_id`); the
    project is `gcp.projectId`; metric labels become `metric.<label>`.
    """
    for prefix in ("resource.labels.", "resource.label."):
        if field.startswith(prefix):
            label = field[len(prefix) :]
            return PROJECT_LABELS.get(label, f"gcp.{label}")
    for prefix in ("metric.labels.", "metric.label."):
        if field.startswith(prefix):
            return f"metric.{field[len(prefix) :]}"
    return None


def _scope(terms: list[Term]) -> tuple[str, list[TagFilter], list[str]]:
    metric_type = ""
    scope: list[TagFilter] = []
    notes: list[str] = []
    for term in terms:
        if term.field == "metric.type" and term.op == "=" and not term.negate:
            metric_type = term.value
        elif term.field == "resource.type" and term.op == "=" and not term.negate:
            notes.append(f"resource type {term.value!r} is not part of the query")
        elif (label := _label_name(term.field)) and term.op == "=" and not term.negate:
            scope.append(TagFilter(key=label, value=term.value))
        else:
            raise _Fail(Confidence.MANUAL, f"filter term on {term.field!r} is not supported")
    if not metric_type:
        raise _Fail(Confidence.MANUAL, "the filter has no metric.type")
    return metric_type, scope, notes


def _seconds(value: object) -> int:
    text = str(value or "")
    return int(text[:-1]) if text.endswith("s") and text[:-1].isdigit() else 0


def _counter_window(aligner: str, period: int) -> tuple[int, float, str]:
    """Window, threshold scale and note for a counter: New Relic stores one change per poll."""
    window = max(period, POLL_SECONDS)
    # a rate is per second, so the total over the window is rate x window; a delta scales
    scale = float(window) if aligner == "ALIGN_RATE" else window / period
    note = (
        "a counter metric: New Relic stores the change in each 5 minute polling interval, "
        f"so the window is {window}s and the threshold is rescaled by {scale:g} "
        "(Google compares a rate or change per alignment period)"
    )
    return window, scale, note


def _reducer(
    agg: dict[str, object], aggregation: Aggregation, aligner: str
) -> tuple[list[str], list[str]]:
    """Group-by labels and notes for the cross-series reducer of a condition."""
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    raw = agg.get("groupByFields")
    fields = raw if isinstance(raw, list) else []
    group_by = [label for f in fields if (label := _label_name(str(f)))]
    if reducer in REDUCERS:
        if REDUCERS[reducer] == aggregation:
            return group_by, []
        return group_by, [
            f"{reducer} and {aligner} become one {aggregation.value} in New Relic's query"
        ]
    if reducer != "REDUCE_NONE":
        raise _Fail(Confidence.MANUAL, f"reducer {reducer!r} is not supported")
    return group_by, [
        "Google evaluates each time series on its own; the New Relic query combines them "
        "unless you facet by the resource id"
    ]


def _whole_windows(duration: int, period: int) -> tuple[int, str]:
    """The breach duration as a whole number of windows, and a note when it had to change."""
    window = max(duration, period)
    if not window % period:
        return window, ""
    window = -(-window // period) * period
    return window, (
        f"the duration of {duration}s is not a whole number of {period}s windows, so it is "
        f"rounded up to {window}s and the condition fires slightly later than in Google"
    )


def _metric_condition(
    body: dict[str, object],
) -> tuple[MetricSignal, int, int, float, list[str]]:
    notes: list[str] = []
    if body.get("denominatorFilter"):
        raise _Fail(Confidence.MANUAL, "ratio conditions (denominator filter) are not supported")
    if body.get("forecastOptions"):
        raise _Fail(Confidence.MANUAL, "forecast conditions are not supported")
    trigger = body.get("trigger")
    if isinstance(trigger, dict) and (trigger.get("percent") or int(trigger.get("count", 1)) > 1):
        raise _Fail(Confidence.MANUAL, "a trigger on several time series is not supported")
    try:
        metric_type, scope, scope_notes = _scope(parse_filter(str(body.get("filter", ""))))
    except FilterError as err:
        raise _Fail(Confidence.MANUAL, f"filter not supported: {err}") from err
    if metric_type.startswith("monitoring.googleapis.com/uptime_check/"):
        raise _Fail(
            Confidence.MANUAL,
            "an uptime check alert depends on the check itself, which is not migrated",
        )
    if metric_type.startswith(NOT_GOOGLE_SERVICE):
        raise _Fail(Confidence.MANUAL, UNCOLLECTED)
    aggregations = body.get("aggregations")
    if isinstance(aggregations, list) and len(aggregations) > 1:
        raise _Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
    first = aggregations[0] if isinstance(aggregations, list) and aggregations else {}
    agg = first if isinstance(first, dict) else {}
    aligner = str(agg.get("perSeriesAligner", ""))
    if aligner not in ALIGNERS:
        raise _Fail(Confidence.MANUAL, f"aligner {aligner!r} is not supported")
    aggregation, counter = ALIGNERS[aligner]
    group_by, reducer_notes = _reducer(agg, aggregation, aligner)
    notes += reducer_notes
    period = _seconds(agg.get("alignmentPeriod"))
    duration = _seconds(body.get("duration"))
    if period < 60 or period % 60:
        raise _Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
    name = nr_metric_name(metric_type)
    if aligner.startswith("ALIGN_PERCENTILE_"):
        name += "." + "p" + aligner.rsplit("_", 1)[1]
        notes.append(
            "a distribution metric: New Relic publishes the percentile as its own series, "
            "already computed for each polling interval"
        )
    scale = 1.0
    if counter:
        period, scale, note = _counter_window(aligner, period)
        notes.append(note)
    notes += _name_notes(name, aligner, uses_labels=bool(scope or group_by))
    term = MetricTerm(
        metric=metric_type,
        space_aggregation=aggregation,
        nr_target=NrTarget(event="Metric", attribute=name),
    )
    signal = MetricSignal(
        terms={"a": term}, aggregation=aggregation, scope=scope, group_by=group_by
    )
    window, note = _whole_windows(duration, period)
    if note:
        notes.append(note)
    return signal, period, window, scale, [*scope_notes, *notes]


def _name_notes(name: str, aligner: str, *, uses_labels: bool) -> list[str]:
    """Say what is and is not confirmed about the New Relic name and attributes."""
    notes: list[str] = []
    base = name.rsplit(".", 1)[0] if aligner.startswith("ALIGN_PERCENTILE_") else name
    if base in NOT_RECEIVED:
        notes.append(MISSING_NOTE)
    elif base not in RECEIVED:
        notes.append(NAMING_NOTE)
    if uses_labels:
        notes.append(LABEL_NOTE)
    return notes


def _log_count(body: dict[str, object]) -> CountSignal:
    try:
        terms = parse_filter(str(body.get("filter", "")))
    except FilterError as err:
        raise _Fail(Confidence.MANUAL, f"log filter not supported: {err}") from err
    filters: list[CountFilter] = []
    for term in terms:
        if (
            term.field == "severity"
            and term.op in SEVERITY_OPS
            and term.value.upper() in SEVERITIES
        ):
            first = SEVERITIES.index(term.value.upper())
            levels = SEVERITIES[first:] if term.op in (">=", ">") else SEVERITIES[: first + 1]
            if term.op == ">":
                levels = SEVERITIES[first + 1 :]
            elif term.op == "<":
                levels = SEVERITIES[:first]
            filters.append(CountFilter(key="severity", value="", values=levels, negate=term.negate))
        elif term.op == ":":
            filters.append(
                CountFilter(
                    key=term.field, value=f"*{term.value}*", negate=term.negate, wildcard=True
                )
            )
        elif term.op in ("=", "!="):
            filters.append(
                CountFilter(
                    key=term.field, value=term.value, negate=term.negate != (term.op == "!=")
                )
            )
        else:
            raise _Fail(
                Confidence.MANUAL, f"log filter comparison on {term.field!r} is not supported"
            )
    return CountSignal(filters=filters)


def _convert(policy: GcpAlertPolicy, cond: GcpCondition, name: str) -> IRMonitor:
    key = cond.name or f"{policy.name}/{cond.displayName}"
    reasons: list[str] = []
    if policy.notificationChannels:
        reasons.append("notification channels must be configured manually")
    base = {
        "source_system": "gcp",
        "source_id": source_id(key),
        "source_key": key,
        "source_type": cond.kind or "unknown condition",
        "name": name,
        "notification": Notification(
            message_template=str(policy.documentation.get("content", "")),
            handles=sorted(policy.notificationChannels),
        ),
    }

    def failed(confidence: Confidence, reason: str) -> IRMonitor:
        return IRMonitor(
            **base,
            signal=Signal(kind=SignalKind.METRIC, raw_query=name),
            confidence=confidence,
            reasons=[reason, *reasons],
        )

    if cond.kind in MANUAL_KINDS:
        return failed(Confidence.MANUAL, MANUAL_KINDS[cond.kind])
    if not cond.kind:
        return failed(Confidence.MANUAL, "condition type is not recognised")
    severity = Severity.WARNING if policy.severity == "WARNING" else Severity.CRITICAL
    body = cond.body
    no_data = NoData()
    try:
        if cond.kind == "conditionMatchedLog":
            count = _log_count(body)
            reasons.append(LOG_NOTE)
            signal = Signal(
                kind=SignalKind.LOG,
                raw_query=name,
                window_seconds=60,
                aggregation_window_seconds=60,
                occurrences=Occurrences.AT_LEAST_ONCE,
                count=count,
            )
            thresholds = [Threshold(severity=severity, operator=Operator.ABOVE, value=0)]
        else:
            operator = OPERATORS.get(str(body.get("comparison", "")))
            if operator is None:
                return failed(
                    Confidence.MANUAL, f"comparison {body.get('comparison')!r} is not supported"
                )
            metric, period, duration, scale, notes = _metric_condition(body)
            reasons += notes
            missing = str(body.get("evaluationMissingData", ""))
            if missing.endswith("_ACTIVE"):
                no_data = NoData(notify=True, timeout_minutes=max(1, duration // 60))
            elif missing.endswith(("_INACTIVE", "_NO_OP")):
                reasons.append(
                    f"{missing} has no equivalent; New Relic leaves the condition unchanged "
                    "when data is missing"
                )
            signal = Signal(
                kind=SignalKind.METRIC,
                raw_query=name,
                window_seconds=duration,
                aggregation_window_seconds=period,
                aggregation_delay_seconds=AGGREGATION_DELAY_SECONDS,
                occurrences=Occurrences.ALL,
                metric=metric,
            )
            value = float(str(body.get("thresholdValue", 0))) * scale
            thresholds = [Threshold(severity=severity, operator=operator, value=value)]
    except _Fail as fail:
        return failed(fail.confidence, fail.reason)
    return IRMonitor(
        **base,
        signal=signal,
        thresholds=thresholds,
        no_data=no_data,
        confidence=Confidence.APPROXIMATE,
        reasons=reasons,
    )


def translate_policy(policy: GcpAlertPolicy) -> list[IRMonitor]:
    many = len(policy.conditions) > 1
    monitors = []
    for cond in policy.conditions:
        name = f"{policy.displayName} / {cond.displayName}" if many else policy.displayName
        monitor = _convert(policy, cond, name)
        if many and policy.combiner.startswith("AND") and monitor.confidence != Confidence.MANUAL:
            monitor.reasons.append(
                f"the Google policy fires only when all {len(policy.conditions)} conditions "
                "hold; New Relic evaluates each condition on its own"
            )
        if not policy.enabled:
            monitor.unmapped["enabled"] = False
        monitors.append(monitor)
    return monitors


def translate(policies: list[GcpAlertPolicy]) -> list[IRMonitor]:
    return [m for p in policies for m in translate_policy(p)]
