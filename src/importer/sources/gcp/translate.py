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
from importer.core.emit.nrql import field_identifier, identifier
from importer.core.ids import source_id
from importer.core.ir import (
    Aggregation,
    Confidence,
    CountFilter,
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
    Signal,
    SignalKind,
    TagFilter,
    Threshold,
)
from importer.core.text import printable
from importer.sources.gcp import alert_fields
from importer.sources.gcp.filter import FilterError, Term, parse_filter
from importer.sources.gcp.models import GcpAlertPolicy, GcpCondition
from importer.sources.gcp.promql import PROMQL_KIND, translate_condition

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
    "ALIGN_STDDEV": (Aggregation.STDDEV, False),
    "ALIGN_NONE": (Aggregation.AVG, False),
    "": (Aggregation.AVG, False),
}
BOOLEAN = (
    "the aligner {name} works on a boolean metric; New Relic documents no encoding for boolean "
    "Google metrics, so check the values the metric holds there (for example 0 and 1) and build "
    "percentage(count(*), WHERE <metric> = 1) or sum() by hand"
)
RESAMPLES = (
    "the aligner {name} resamples the points onto the period boundaries, which New Relic "
    "does not do"
)
UNSUPPORTED_ALIGNERS = {
    "ALIGN_COUNT": (
        "the aligner ALIGN_COUNT counts the samples in each period; Google samples at the "
        "metric's own rate and New Relic stores one sample per 5 minute poll, so the counts "
        "are not comparable"
    ),
    "ALIGN_COUNT_TRUE": BOOLEAN.format(name="ALIGN_COUNT_TRUE"),
    "ALIGN_COUNT_FALSE": BOOLEAN.format(name="ALIGN_COUNT_FALSE"),
    "ALIGN_FRACTION_TRUE": BOOLEAN.format(name="ALIGN_FRACTION_TRUE"),
    "ALIGN_PERCENT_CHANGE": (
        "the aligner ALIGN_PERCENT_CHANGE is the percent change between consecutive points; "
        "NRQL derivative() gives an absolute rate of change, not a percent change"
    ),
    "ALIGN_INTERPOLATE": RESAMPLES.format(name="ALIGN_INTERPOLATE"),
    "ALIGN_NEXT_OLDER": RESAMPLES.format(name="ALIGN_NEXT_OLDER"),
    "ALIGN_PERCENTILE_05": (
        "the aligner ALIGN_PERCENTILE_05 is the 5th percentile; New Relic publishes only the "
        "50th, 95th and 99th percentile series of a distribution metric"
    ),
}
PERCENTILE_REDUCERS = {
    "REDUCE_PERCENTILE_50": 50,
    "REDUCE_PERCENTILE_95": 95,
    "REDUCE_PERCENTILE_99": 99,
}
UNSUPPORTED_REDUCERS = {
    "REDUCE_COUNT": "counts the time series, which a New Relic condition query cannot do",
    "REDUCE_COUNT_TRUE": "works on a boolean metric whose New Relic encoding is not documented",
    "REDUCE_COUNT_FALSE": "works on a boolean metric whose New Relic encoding is not documented",
    "REDUCE_FRACTION_TRUE": "works on a boolean metric whose New Relic encoding is not documented",
    "REDUCE_PERCENTILE_05": "is the 5th percentile, which New Relic does not publish for a "
    "distribution metric",
}
REDUCERS = {
    "REDUCE_MEAN": Aggregation.AVG,
    "REDUCE_MAX": Aggregation.MAX,
    "REDUCE_MIN": Aggregation.MIN,
    "REDUCE_SUM": Aggregation.SUM,
    "REDUCE_STDDEV": Aggregation.STDDEV,
}
# every perSeriesAligner and crossSeriesReducer in Google's enums (alert.proto, common.proto)
KNOWN_ALIGNERS = {*ALIGNERS, *UNSUPPORTED_ALIGNERS, "ALIGN_PERCENTILE_05", "ALIGN_INTERPOLATE"}
KNOWN_REDUCERS = {
    "REDUCE_NONE",
    "REDUCE_STDDEV",
    *REDUCERS,
    *PERCENTILE_REDUCERS,
    *UNSUPPORTED_REDUCERS,
}
STDDEV_MIN_PERIOD_SECONDS = 3 * 300  # three polls: fewer points give no meaningful deviation
MANUAL_KINDS = {
    "conditionMonitoringQueryLanguage": "MQL conditions are not converted",
    "conditionPrometheusQueryLanguage": "PromQL conditions are not converted",
    "conditionSql": "SQL conditions are not converted",
}
ABSENCE_KIND = "conditionAbsent"
ABSENCE_PREFIX = "a metric-absence condition could not be converted: "
SERVICE_SEGMENT = re.compile(r"^(?P<service>[a-z0-9_.]+?)(?:\.googleapis\.com)?/(?P<path>.+)$")
PLAIN_ATTRIBUTE = re.compile(r"[A-Za-z0-9_.]+")
_PLAIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
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
            return _valid_attribute(PROJECT_LABELS.get(label, f"gcp.{label}"), field)
    for prefix in ("metric.labels.", "metric.label."):
        if field.startswith(prefix):
            return _valid_attribute(f"metric.{field[len(prefix) :]}", field)
    return None


def _valid_attribute(attribute: str, field: str) -> str:
    """The attribute when NRQL accepts it as a name; otherwise the condition stays manual."""
    try:
        identifier(attribute)
        if not PLAIN_ATTRIBUTE.fullmatch(attribute):
            raise ValueError(attribute)  # a metric label never needs a quoted name
    except ValueError:
        raise Fail(
            Confidence.MANUAL, f"label {field!r} does not make a valid New Relic attribute name"
        ) from None
    return attribute


def _scope(terms: list[Term]) -> tuple[str, list[TagFilter], list[str]]:
    metric_type = ""
    scope: list[TagFilter] = []
    notes: list[str] = []
    for term in terms:
        plain = term.op == "=" and not term.negate and not term.values
        if term.field == "metric.type" and plain:
            metric_type = term.value
        elif term.field == "resource.type" and plain:
            notes.append(f"resource type {term.value!r} is not part of the query")
        elif (label := _label_name(term.field)) and term.op in ("=", "!="):
            negate = term.negate != (term.op == "!=")
            scope.append(
                TagFilter(key=label, value=term.value, negate=negate, values=list(term.values))
            )
        else:
            raise Fail(Confidence.MANUAL, f"filter term on {term.field!r} is not supported")
    if not metric_type:
        raise Fail(Confidence.MANUAL, "the filter has no metric.type")
    return metric_type, scope, notes


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
    agg: dict[str, object], aggregation: Aggregation, aligner: str, period: int
) -> tuple[list[str], list[str], Aggregation, int | None]:
    """Group-by labels, notes, time aggregation and percentile (if any) for the cross-series
    reducer of a condition."""
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    group_by, notes = _group_labels(agg)
    if reducer in PERCENTILE_REDUCERS or reducer == "REDUCE_STDDEV":
        _check_one_point_per_series(reducer, aligner, period)
        notes.append(
            f"{reducer} runs over the points polled in each window, one for each series, so it "
            "is the same as taking it across series"
        )
        percentile = PERCENTILE_REDUCERS.get(reducer)
        return (
            group_by,
            notes,
            Aggregation.STDDEV if percentile is None else aggregation,
            percentile,
        )
    if reducer in REDUCERS:
        if REDUCERS[reducer] != aggregation:
            notes.append(
                f"{reducer} and {aligner} become one {aggregation.value} in New Relic's query"
            )
        return group_by, notes, aggregation, None
    if reducer in UNSUPPORTED_REDUCERS:
        raise Fail(Confidence.MANUAL, f"reducer {reducer} {UNSUPPORTED_REDUCERS[reducer]}")
    if reducer != "REDUCE_NONE":
        raise Fail(Confidence.MANUAL, f"reducer {reducer!r} is not supported")
    notes.append(
        "Google evaluates each time series on its own; the New Relic query combines them "
        "unless you facet by the resource id"
    )
    return group_by, notes, aggregation, None


def _check_one_point_per_series(reducer: str, aligner: str, period: int) -> None:
    """A percentile or deviation across series equals one across polled points only when every
    series has a single point in the window and the aligner leaves that point as it is."""
    counter = ALIGNERS.get(aligner, (Aggregation.AVG, False))[1]
    if period > POLL_SECONDS or counter or aligner.startswith("ALIGN_PERCENTILE_"):
        raise Fail(
            Confidence.MANUAL,
            f"{reducer} after {aligner or 'no aligner'} over {period}s: each series has several "
            "polls in the window and the per-series value is taken first, but a New Relic "
            "percentile or deviation mixes all the polls, so the result would differ",
        )


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
        metric_type, scope, notes = _scope(parse_filter(str(body.get("filter", ""))))
    except FilterError as err:
        raise Fail(Confidence.MANUAL, f"filter not supported: {err}") from err
    if metric_type.startswith("monitoring.googleapis.com/uptime_check/"):
        raise Fail(
            Confidence.MANUAL,
            "an uptime check alert depends on the check itself, which is not migrated",
        )
    if metric_type.startswith(NOT_GOOGLE_SERVICE):
        raise Fail(Confidence.MANUAL, UNCOLLECTED)
    return metric_type, scope, notes


def _first_aggregation(body: dict[str, object]) -> tuple[dict[str, object], str]:
    """The single aggregation stage of a condition and its aligner."""
    aggregations = body.get("aggregations")
    if isinstance(aggregations, list) and len(aggregations) > 1:
        raise Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
    first = aggregations[0] if isinstance(aggregations, list) and aggregations else {}
    return check_aligner(first if isinstance(first, dict) else {})


def check_aligner(agg: dict[str, object]) -> tuple[dict[str, object], str]:
    """The aggregation stage and its aligner; manual when the aligner is not mapped."""
    aligner = str(agg.get("perSeriesAligner", ""))
    if aligner not in ALIGNERS:
        raise Fail(
            Confidence.MANUAL,
            UNSUPPORTED_ALIGNERS.get(aligner, f"aligner {aligner!r} is not supported"),
        )
    return agg, aligner


def _group_labels(agg: dict[str, object]) -> tuple[list[str], list[str]]:
    """Mapped group-by attributes without repeats, and a note for each field that was dropped."""
    raw = agg.get("groupByFields")
    fields = raw if isinstance(raw, list) else []
    labels: list[str] = []
    notes: list[str] = []
    for field in map(str, fields):
        label = _label_name(field)
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
    if period < 60 or period % 60:
        raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
    if aligner == "ALIGN_STDDEV" and period < STDDEV_MIN_PERIOD_SECONDS:
        raise Fail(
            Confidence.MANUAL,
            f"ALIGN_STDDEV over {period}s has one point or two per series (New Relic polls every "
            f"{POLL_SECONDS}s), which gives no meaningful deviation; use a period of "
            f"{STDDEV_MIN_PERIOD_SECONDS}s or more",
        )
    group_by, notes, aggregation, percentile = _reducer(agg, aggregation, aligner, period)
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
    target = NrTarget(event="Metric", attribute=name)
    if percentile is not None:
        # the percentile is one of three fixed integers and the name was validated above
        target.select = f"percentile({identifier(name)}, {percentile})"
    term = MetricTerm(metric=metric_type, space_aggregation=aggregation, nr_target=target)
    signal = MetricSignal(
        terms={"a": term}, aggregation=aggregation, scope=scope, group_by=group_by
    )
    return signal, period, scale, notes


def _trigger_size(body: dict[str, object]) -> tuple[int, float]:
    """(count, percent) of the trigger; (1, 0) when it asks for any one series."""
    trigger = body.get("trigger")
    if not isinstance(trigger, dict):
        return 1, 0.0
    try:
        return int(trigger.get("count") or 1), float(trigger.get("percent") or 0)
    except (TypeError, ValueError):
        raise Fail(
            Confidence.MANUAL, "the trigger is not a number of series or a percent"
        ) from None


def _one_series(agg: dict[str, object]) -> bool:
    """True when the aggregation reduces everything to a single series."""
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    return reducer not in ("REDUCE_NONE", "") and not agg.get("groupByFields")


def _trigger_text(count: int, percent: float) -> str:
    return f"{percent:g}% of the time series" if percent else f"{count} time series"


def _threshold_trigger(body: dict[str, object], agg: dict[str, object]) -> list[str]:
    """Notes for a trigger on a threshold condition; manual when several series must breach."""
    count, percent = _trigger_size(body)
    if count <= 1 and not percent:
        return []
    if _one_series(agg):
        return [
            f"the trigger of {_trigger_text(count, percent)} does not matter here: the "
            "aggregation reduces everything to a single series"
        ]
    raise Fail(
        Confidence.MANUAL,
        "a trigger on several time series is not supported: it needs "
        f"{_trigger_text(count, percent)} to breach together, and New Relic evaluates each "
        "series on its own",
    )


def _absence_trigger(body: dict[str, object], agg: dict[str, object]) -> list[str]:
    count, percent = _trigger_size(body)
    if (count <= 1 and not percent) or percent >= 100 or _one_series(agg):
        return []
    return [
        f"Google fires when {_trigger_text(count, percent)} stop reporting; this condition "
        "fires only when no series reports at all, so it fires later and less often"
    ]


def _metric_condition(
    body: dict[str, object],
) -> tuple[MetricSignal, int, int, float, list[str]]:
    if body.get("denominatorFilter"):
        raise Fail(Confidence.MANUAL, "ratio conditions (denominator filter) are not supported")
    metric_type, scope, scope_notes = metric_scope(body)
    agg, aligner = _first_aggregation(body)
    trigger_notes = _threshold_trigger(body, agg)
    period = seconds(agg.get("alignmentPeriod"))
    duration = seconds(body.get("duration"))
    signal, period, scale, notes = metric_series(metric_type, scope, agg, aligner, period)
    notes = [*trigger_notes, *notes]
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


def _absence_aggregation(body: dict[str, object]) -> tuple[dict[str, object], str]:
    """The aggregation stage of an absence check. Any aligner or reducer in Google's enums is
    accepted: the check only asks whether a data point arrived."""
    aggregations = body.get("aggregations")
    if isinstance(aggregations, list) and len(aggregations) > 1:
        raise Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
    first = aggregations[0] if isinstance(aggregations, list) and aggregations else {}
    agg = first if isinstance(first, dict) else {}
    aligner = str(agg.get("perSeriesAligner", ""))
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    if aligner not in KNOWN_ALIGNERS:
        raise Fail(Confidence.MANUAL, f"aligner {aligner!r} is not supported")
    if reducer not in KNOWN_REDUCERS:
        raise Fail(Confidence.MANUAL, f"reducer {reducer!r} is not supported")
    return agg, aligner


def _absence_condition(body: dict[str, object]) -> tuple[HeartbeatSignal, int, int, list[str]]:
    """A loss-of-signal check: heartbeat signal, aggregation window, duration and notes."""
    metric_type, scope, scope_notes = metric_scope(body)
    agg, aligner = _absence_aggregation(body)
    period = seconds(agg.get("alignmentPeriod"))
    if period < 60 or period % 60:
        raise Fail(Confidence.MANUAL, f"alignment period {period}s is not supported")
    duration = seconds(body.get("duration"))
    if duration < 60:
        raise Fail(Confidence.MANUAL, f"duration {duration}s is not supported")
    name = nr_metric_name(metric_type)
    group_by, notes = _group_labels(agg)
    notes.append(ABSENCE_NOTE)
    notes += _absence_trigger(body, agg)
    reducer = str(agg.get("crossSeriesReducer", "REDUCE_NONE"))
    if aligner not in ALIGNERS or reducer not in (*REDUCERS, "REDUCE_NONE"):
        notes.append(
            f"the aligner {aligner or 'none'} and reducer {reducer} do not change what an "
            "absence check sees: it only asks whether any data point arrived"
        )
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


def _log_count(body: dict[str, object]) -> CountSignal:
    try:
        terms = parse_filter(str(body.get("filter", "")))
    except FilterError as err:
        raise Fail(Confidence.MANUAL, f"log filter not supported: {err}") from err
    filters: list[CountFilter] = []
    for term in terms:
        if term.field == "log_id" and term.op == "()":
            # the log name ends in the URL-encoded id; the encoded slash cannot be written in a
            # LIKE pattern, so the slashes in the id match anything
            pattern = "*/logs/" + term.value.replace("/", "*")
            filters.append(CountFilter(key="logName", value=pattern, wildcard=True))
        elif term.values and term.op in ("=", "!="):
            negate = term.negate != (term.op == "!=")
            filters.append(
                CountFilter(key=term.field, value="", values=list(term.values), negate=negate)
            )
        elif (
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
            raise Fail(
                Confidence.MANUAL, f"log filter comparison on {term.field!r} is not supported"
            )
    for f in filters:
        try:
            f.key = field_identifier(f.key if _PLAIN_NAME.fullmatch(f.key) else f"`{f.key}`")
        except ValueError:
            raise Fail(
                Confidence.MANUAL,
                f"log field {printable(f.key, 60)!r} is not a valid New Relic attribute name",
            ) from None
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

    if cond.kind == PROMQL_KIND:
        severity = Severity.WARNING if policy.severity == "WARNING" else Severity.CRITICAL
        return translate_condition(base, cond.body, severity=severity, prior=reasons)
    if cond.kind in MANUAL_KINDS:
        return failed(Confidence.MANUAL, MANUAL_KINDS[cond.kind])
    if not cond.kind:
        return failed(Confidence.MANUAL, "condition type is not recognised")
    severity, severity_note = alert_fields.severity_for(policy.severity)
    body = cond.body
    no_data = NoData()
    extra: dict[str, object] = {}
    log = cond.kind == "conditionMatchedLog"
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
            forecast_emit, forecast_note, forecast_fail = alert_fields.forecast(body)
            if forecast_fail:
                raise Fail(Confidence.MANUAL, forecast_fail)
            metric, period, duration, scale, notes = _metric_condition(body)
            reasons += notes
            if forecast_note:
                reasons.append(forecast_note)
            no_data, missing_emit, missing_note = alert_fields.missing_data(
                str(body.get("evaluationMissingData", "")), duration
            )
            extra |= forecast_emit | missing_emit
            if missing_note:
                reasons.append(missing_note)
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
    settings = alert_fields.carry_fields(policy, body, log=log)
    reasons += [*([severity_note] if severity_note else []), *settings.notes]
    return IRMonitor(
        **base,
        tags=settings.tags,
        emit=alert_fields.emit_options(settings, extra),
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
