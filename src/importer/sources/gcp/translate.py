"""Alert policies to IR.

One IR monitor per condition. Threshold, metric-absence and log-match conditions convert; the
rest (MQL, PromQL, SQL, ratios, forecasts) stay manual with the reason. `translate_policy` never
raises.
New Relic's names for GCP metrics follow the documented rule but are not yet confirmed against
live data, so every conversion is approximate until they are.
"""

import json
import re
from pathlib import Path

from importer.core.emit.common import MAX_AGGREGATION_WINDOW_SECONDS, MAX_DURATION_SECONDS
from importer.core.ids import source_id
from importer.core.ir import (
    Aggregation,
    BinOp,
    Confidence,
    CountSignal,
    HeartbeatSignal,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NoData,
    Notification,
    NrTarget,
    Occurrences,
    Operator,
    Ref,
    Severity,
    Signal,
    SignalKind,
    TagFilter,
    Threshold,
)
from importer.sources.gcp.log_scope import log_filters
from importer.sources.gcp.models import GcpAlertPolicy, GcpCondition
from importer.sources.gcp.scope import ScopeFail, label_name, metric_filter

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
    "conditionMonitoringQueryLanguage": "MQL conditions are not converted",
    "conditionPrometheusQueryLanguage": "PromQL conditions are not converted",
    "conditionSql": "SQL conditions are not converted",
}
ABSENCE_KIND = "conditionAbsent"
ABSENCE_PREFIX = "a metric-absence condition could not be converted: "
SERVICE_SEGMENT = re.compile(r"^(?P<service>[a-z0-9_.]+?)(?:\.googleapis\.com)?/(?P<path>.+)$")
SAFE_PATH = re.compile(r"^[a-z0-9_/]+$")
NAMING_NOTE = (
    "this metric did not appear in our live test, so its New Relic name follows the documented "
    "rule (a gcp. prefix, then the service and metric path) and is not confirmed; check it with "
    "`importer validate` once data flows"
)
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
ABSENCE_NOTE = (
    "this differs from Google: it fires when New Relic receives no data points for the metric "
    "for the duration; New Relic polls Google Cloud every 5 minutes, so the duration is at least "
    "300s and a whole number of aggregation windows"
)
ABSENCE_MISSING_NOTE = (
    "New Relic documents this metric, but it never arrived in our live test, so a condition "
    "that fires when no data arrives would fire as soon as it is enabled and stay open, which "
    "is not how the Google condition behaves"
)
POLL_SECONDS = 300  # New Relic reads Google Cloud metrics every 5 minutes
AGGREGATION_DELAY_SECONDS = 120  # Cloud Monitoring can delay metrics by 2 to 3 minutes


def _load_verified() -> tuple[frozenset[str], frozenset[str]]:
    data = json.loads(VERIFIED_PATH.read_text())
    return frozenset(data["received"]), frozenset(data["documented_not_received"])


RECEIVED, NOT_RECEIVED = _load_verified()


class Fail(Exception):
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
        raise Fail(Confidence.MANUAL, f"metric type {metric_type!r} is not a recognised name")
    return f"gcp.{match['service']}.{match['path'].replace('/', '.')}"


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


def seconds(value: object) -> int:
    text = str(value or "")
    digits = text[:-1]
    # isdecimal() alone accepts other scripts' digits that int() reads, and isdigit() accepts
    # superscripts that int() rejects; require ASCII digits
    return int(digits) if text.endswith("s") and digits.isascii() and digits.isdecimal() else 0


def _counter_window(aligner: str, period: int, *, chart: bool) -> tuple[int, float, str]:
    """Window, threshold scale and note for a counter: New Relic stores one change per poll."""
    window = max(period, POLL_SECONDS)
    # a rate is per second, so the total over the window is rate x window; a delta scales
    scale = float(window) if aligner == "ALIGN_RATE" else window / period
    first = "a counter metric: New Relic stores the change in each 5 minute polling interval, "
    if chart:
        note = (
            first + "so the chart adds up those changes over each bucket (over the whole time "
            "range for a single value); Google showed a rate or change per alignment period, so "
            f"divide by {scale:g} to compare with it"
        )
    else:
        note = (
            first + f"so the window is {window}s and the threshold is rescaled by {scale:g} "
            "(Google compares a rate or change per alignment period)"
        )
    return window, scale, note


def _reducer(
    agg: dict[str, object], aggregation: Aggregation, aligner: str
) -> tuple[list[str], list[str]]:
    """Group-by labels and notes for the cross-series reducer of a condition."""
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    group_by, notes = _group_labels(agg)
    if reducer in REDUCERS:
        if REDUCERS[reducer] != aggregation:
            notes.append(
                f"{reducer} and {aligner} become one {aggregation.value} in New Relic's query"
            )
        return group_by, notes
    if reducer != "REDUCE_NONE":
        raise Fail(Confidence.MANUAL, f"reducer {reducer!r} is not supported")
    notes.append(
        "Google evaluates each time series on its own; the New Relic query combines them "
        "unless you facet by the resource id"
    )
    return group_by, notes


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


def _check_limits(period: int, window: int) -> None:
    """Manual when New Relic would refuse the window at emit time."""
    if period > MAX_AGGREGATION_WINDOW_SECONDS:
        raise Fail(
            Confidence.MANUAL,
            f"aggregation window {period}s is over New Relic's limit of "
            f"{MAX_AGGREGATION_WINDOW_SECONDS}s",
        )
    if window > MAX_DURATION_SECONDS:
        raise Fail(
            Confidence.MANUAL,
            f"duration {window}s is over New Relic's limit of {MAX_DURATION_SECONDS}s",
        )


def metric_scope(body: dict[str, object]) -> tuple[str, list[TagFilter], list[str]]:
    """Metric type, label scope and notes from a condition's filter; manual when not collected."""
    try:
        found = metric_filter(str(body.get("filter", "")))
    except ScopeFail as err:
        raise Fail(Confidence.MANUAL, str(err)) from err
    metric_type, scope, notes = found.metric_type, found.filters, found.notes
    if metric_type.startswith("monitoring.googleapis.com/uptime_check/"):
        raise Fail(
            Confidence.MANUAL,
            "an uptime check alert depends on the check itself, which is not migrated",
        )
    if metric_type.startswith(NOT_GOOGLE_SERVICE):
        raise Fail(Confidence.MANUAL, UNCOLLECTED)
    return metric_type, scope, notes


def _first_aggregation(
    body: dict[str, object], key: str = "aggregations"
) -> tuple[dict[str, object], str]:
    """The single aggregation stage of a condition (or of its denominator) and its aligner."""
    aggregations = body.get(key)
    if isinstance(aggregations, list) and len(aggregations) > 1:
        raise Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
    first = aggregations[0] if isinstance(aggregations, list) and aggregations else {}
    return check_aligner(first if isinstance(first, dict) else {})


def check_aligner(agg: dict[str, object]) -> tuple[dict[str, object], str]:
    """The aggregation stage and its aligner; manual when the aligner is not mapped."""
    aligner = str(agg.get("perSeriesAligner", ""))
    if aligner not in ALIGNERS:
        raise Fail(Confidence.MANUAL, f"aligner {aligner!r} is not supported")
    return agg, aligner


def _group_label(field: str) -> str | None:
    try:
        return label_name(field)
    except ScopeFail as err:
        raise Fail(Confidence.MANUAL, str(err)) from err


def _group_labels(agg: dict[str, object]) -> tuple[list[str], list[str]]:
    """Mapped group-by attributes without repeats, and a note for each field that was dropped."""
    raw = agg.get("groupByFields")
    fields = raw if isinstance(raw, list) else []
    labels: list[str] = []
    notes: list[str] = []
    for field in map(str, fields):
        label = _group_label(field)
        if label is None:
            notes.append(f"group-by field {field!r} is not mapped and was dropped")
        elif label not in labels:
            labels.append(label)
    return labels, notes


def metric_series(
    metric_type: str,
    scope: list[TagFilter],
    agg: dict[str, object],
    aligner: str,
    period: int,
    *,
    chart: bool = False,
) -> tuple[MetricSignal, int, float, list[str]]:
    """The signal for one metric with its aggregation: signal, window, scale and notes.

    Shared by alert conditions and dashboard charts (`chart` words the counter note for a chart).
    """
    aggregation, counter = ALIGNERS[aligner]
    group_by, notes = _reducer(agg, aggregation, aligner)
    if period < 60 or period % 60:
        raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
    name = nr_metric_name(metric_type)
    if aligner.startswith("ALIGN_PERCENTILE_"):
        name += "." + "p" + aligner.rsplit("_", 1)[1]
        notes.append(
            "a distribution metric: New Relic publishes the percentile as its own series, "
            "already computed for each polling interval"
        )
    scale = 1.0
    if counter:
        period, scale, note = _counter_window(aligner, period, chart=chart)
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
    return signal, period, scale, notes


def ratio_series(
    body: dict[str, object], numerator: MetricSignal, aligner: str, period: int, notes: list[str]
) -> tuple[MetricSignal, float, list[str]]:
    """A ratio condition: the numerator signal divided by the series of the denominator filter.

    Both sides must aggregate the same way (same aligner, window and group-by), so one NRQL query
    expresses it; each side keeps its own label filters (`filter()`), the ones both share stay in
    the WHERE. A counter ratio needs no rescaling: the window cancels.
    """
    try:
        denominator = metric_scope({**body, "filter": body.get("denominatorFilter")})
        key = "denominatorAggregations" if body.get("denominatorAggregations") else "aggregations"
        agg, den_aligner = _first_aggregation(body, key)
        den_period = seconds(agg.get("alignmentPeriod"))
        den, *_ = metric_series(denominator[0], denominator[1], agg, den_aligner, den_period)
    except Fail as fail:
        raise Fail(Confidence.MANUAL, f"ratio denominator: {fail.reason}") from fail
    (num_term,) = numerator.terms.values()
    (den_term,) = den.terms.values()
    window = numerator_window(period, aligner)
    if (
        ALIGNERS[aligner][0] != ALIGNERS[den_aligner][0]
        or ALIGNERS[aligner][1] != ALIGNERS[den_aligner][1]
        or window != numerator_window(den_period, den_aligner)
        or numerator.group_by != den.group_by
    ):
        raise Fail(
            Confidence.MANUAL,
            "ratio conditions are not supported when the numerator and denominator differ in "
            "aligner, alignment period or group-by (one New Relic query aggregates both alike)",
        )
    shared = [f for f in numerator.scope if f in den.scope]
    num_term = num_term.model_copy(
        update={"scope": [f for f in numerator.scope if f not in shared]}
    )
    den_term = den_term.model_copy(update={"scope": [f for f in den.scope if f not in shared]})
    signal = MetricSignal(
        terms={"a": num_term, "b": den_term},
        expression=BinOp(op="/", left=Ref(name="a"), right=Ref(name="b")),
        aggregation=numerator.aggregation,
        scope=shared,
        group_by=numerator.group_by,
    )
    kept = [n for n in notes if not n.startswith("a counter metric:")]
    if ALIGNERS[aligner][1]:
        kept.append(
            f"a ratio of counters: New Relic adds up the changes in each window of {window}s for "
            "both sides and divides, which is the ratio Google computes per alignment period, so "
            "the threshold is not rescaled"
        )
    kept.append(
        "the ratio is one query, sum(numerator) / sum(denominator); a window in which the "
        "denominator is zero gives no value instead of failing"
    )
    return signal, 1.0, kept


def numerator_window(period: int, aligner: str) -> int:
    """The window a series is aggregated over in New Relic (counters use at least a poll)."""
    return max(period, POLL_SECONDS) if ALIGNERS[aligner][1] else period


def _metric_condition(
    body: dict[str, object],
) -> tuple[MetricSignal, int, int, float, list[str]]:
    if body.get("forecastOptions"):
        raise Fail(Confidence.MANUAL, "forecast conditions are not supported")
    trigger = body.get("trigger")
    if isinstance(trigger, dict) and (trigger.get("percent") or int(trigger.get("count", 1)) > 1):
        raise Fail(Confidence.MANUAL, "a trigger on several time series is not supported")
    metric_type, scope, scope_notes = metric_scope(body)
    agg, aligner = _first_aggregation(body)
    period = seconds(agg.get("alignmentPeriod"))
    duration = seconds(body.get("duration"))
    signal, period, scale, notes = metric_series(metric_type, scope, agg, aligner, period)
    if body.get("denominatorFilter"):
        signal, scale, notes = ratio_series(body, signal, aligner, period, notes)
    window, note = _whole_windows(duration, period)
    _check_limits(period, window)
    if note:
        notes.append(note)
    return signal, period, window, scale, [*scope_notes, *notes]


def _name_notes(
    name: str, aligner: str, *, uses_labels: bool, missing_note: str = MISSING_NOTE
) -> list[str]:
    """Say what is and is not confirmed about the New Relic name and attributes."""
    notes: list[str] = []
    base = name.rsplit(".", 1)[0] if aligner.startswith("ALIGN_PERCENTILE_") else name
    if base in NOT_RECEIVED:
        notes.append(missing_note)
    elif base not in RECEIVED:
        notes.append(NAMING_NOTE)
    if uses_labels:
        notes.append(LABEL_NOTE)
    return notes


def _absence_condition(body: dict[str, object]) -> tuple[HeartbeatSignal, int, int, list[str]]:
    """A loss-of-signal check: heartbeat signal, aggregation window, duration and notes."""
    metric_type, scope, scope_notes = metric_scope(body)
    agg, aligner = _first_aggregation(body)
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    if reducer not in (*REDUCERS, "REDUCE_NONE"):
        raise Fail(Confidence.MANUAL, f"reducer {reducer!r} is not supported")
    period = seconds(agg.get("alignmentPeriod"))
    if period < 60 or period % 60:
        raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
    duration = seconds(body.get("duration"))
    if duration < 60:
        raise Fail(Confidence.MANUAL, f"duration {duration}s is not supported")
    name = nr_metric_name(metric_type)
    group_by, notes = _group_labels(agg)
    notes.append(ABSENCE_NOTE)
    if period < POLL_SECONDS:
        notes.append(
            f"the alignment period of {period}s is raised to {POLL_SECONDS}s, the polling interval"
        )
    window = max(period, POLL_SECONDS)
    if duration < window:
        notes.append(f"the duration of {duration}s is raised to {window}s, one polling interval")
    duration, note = _whole_windows(duration, window)
    _check_limits(window, duration)
    if note:
        notes.append(note)
    if not group_by:
        notes.append(
            "Google tracks each time series on its own; this condition fires only when no "
            "series reports, so group by the resource id to alert for each one"
        )
    notes += _name_notes(
        name, aligner, uses_labels=bool(scope or group_by), missing_note=ABSENCE_MISSING_NOTE
    )
    signal = HeartbeatSignal(
        check=name,
        group_by=group_by,
        scope=scope,
        nr_target=NrTarget(event="Metric", attribute=name),
    )
    return signal, window, duration, [*scope_notes, *notes]


def _log_count(body: dict[str, object]) -> tuple[CountSignal, list[str]]:
    try:
        filters, notes = log_filters(str(body.get("filter", "")))
    except ScopeFail as err:
        raise Fail(Confidence.MANUAL, str(err)) from err
    return CountSignal(filters=filters), notes


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
        if cond.kind == ABSENCE_KIND:
            heartbeat, period, duration, notes = _absence_condition(cond.body)
            reasons += notes
            signal = Signal(
                kind=SignalKind.HEARTBEAT,
                raw_query=name,
                window_seconds=duration,
                aggregation_window_seconds=period,
                aggregation_delay_seconds=AGGREGATION_DELAY_SECONDS,
                occurrences=Occurrences.ALL,
                heartbeat=heartbeat,
            )
            thresholds = [Threshold(severity=severity, operator=Operator.BELOW, value=1)]
            no_data = NoData(notify=True, timeout_minutes=max(1, duration // 60))
        elif cond.kind == "conditionMatchedLog":
            count, log_notes = _log_count(body)
            reasons += [LOG_NOTE, *dict.fromkeys(log_notes)]
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
    except Fail as fail:
        prefix = ABSENCE_PREFIX if cond.kind == ABSENCE_KIND else ""
        return failed(fail.confidence, prefix + fail.reason)
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
