"""Davis anomaly detectors (`builtin:davis.anomaly-detectors`) to New Relic conditions.

Only one shape converts: a static-threshold detector over a simple DQL `timeseries` query of one
metric with `avg`, `min`, `max` or `sum` and an optional `by:{...}`. Baseline detectors, records
mode and any other query stay manual with a reason of their own. The analyzer inputs are strings
in a list of key/value pairs; numbers are read from text or from JSON numbers.
"""

import re

from importer.core.ir import (
    Confidence,
    IRMonitor,
    Occurrences,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.dynatrace.classic.common import Fail, finite, monitor, shown
from importer.sources.dynatrace.classic.metric_events import DISABLED
from importer.sources.dynatrace.classic.models import DetectorValue, SettingsObject
from importer.sources.dynatrace.classic.selector_query import Aggregate, SeriesSpec
from importer.sources.dynatrace.classic.signals import build_signal
from importer.sources.dynatrace.classic.windows import sample_window

SOURCE_TYPE = "davis anomaly detector"
MAX_QUERY = 2000
MAX_NUMBER = 10**15  # a threshold; larger is not a real measurement
MAX_COUNT = 10**9  # samples and windows
MAX_NUMBER_TEXT = 40
STATIC = "StaticThresholdAnomalyDetectionAnalyzer"
BASELINE_ANALYZERS = (
    "AutoAdaptiveAnomalyDetectionAnalyzer",
    "SeasonalBaselineAnomalyDetectionAnalyzer",
)
RECORDS = "RecordAnomalyDetectionAnalyzer"
QUERY_KEYS = ("query", "query.expression")
# no nested quantifier and no alternation of overlapping classes, so matching is linear
TIMESERIES = re.compile(
    r"timeseries\s+(?:[A-Za-z_][A-Za-z0-9_]*\s*=\s*)?(avg|min|max|sum)\(\s*([A-Za-z0-9_.]+)\s*\)"
    r"(?:\s*,\s*by\s*:\s*\{([A-Za-z0-9_.,\s]*)\})?"
)
DIMENSION = re.compile(r"[A-Za-z0-9_.]+")
AGGREGATES = {
    "avg": Aggregate("average"),
    "min": Aggregate("min"),
    "max": Aggregate("max"),
    "sum": Aggregate("sum"),
}
OPERATORS = {"ABOVE": Operator.ABOVE, "BELOW": Operator.BELOW}
NOT_SIMPLE = (
    "the detector query is not a simple timeseries: only `timeseries [name =] avg|min|max|sum"
    "(metric) [, by:{dimension, ...}]` converts"
)


def _inputs(value: DetectorValue) -> dict[str, object]:
    found: dict[str, object] = {}
    for pair in value.analyzer.input:
        if pair.key in found and found[pair.key] != pair.value:
            raise Fail(f"detector input {shown(pair.key)} is set twice with different values")
        found[pair.key] = pair.value
    return found


def _number(inputs: dict[str, object], key: str, *, required: bool = True) -> float | None:
    raw = inputs.get(key)
    if raw is None or raw == "":
        if required:
            raise Fail(f"detector input {key} is missing")
        return None
    if isinstance(raw, bool):
        raise Fail(f"detector input {key} is not a number")
    # checked before float(): a 400-digit integer overflows it, and no real input is this large
    if isinstance(raw, int) and abs(raw) > MAX_NUMBER:
        raise Fail(f"detector input {key} is out of range")
    if isinstance(raw, str) and len(raw) > MAX_NUMBER_TEXT:
        raise Fail(f"detector input {key} is not a number")
    try:
        number = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        raise Fail(f"detector input {key} is not a number") from None
    return finite(number, f"detector input {key}")


def _count(inputs: dict[str, object], key: str, *, required: bool = True) -> int | None:
    number = _number(inputs, key, required=required)
    if number is None:
        return None
    if number != int(number):
        raise Fail(f"detector input {key} is not a whole number")
    if abs(number) > MAX_COUNT:
        raise Fail(f"detector input {key} is out of range")
    return int(number)


def _series(inputs: dict[str, object]) -> SeriesSpec:
    query = next((str(inputs[k]) for k in QUERY_KEYS if inputs.get(k)), "")
    if not query:
        raise Fail("the detector has no query input")
    if len(query) > MAX_QUERY:
        raise Fail(f"the detector query is longer than {MAX_QUERY} characters. {NOT_SIMPLE}")
    match = TIMESERIES.fullmatch(query.strip())
    if match is None:
        raise Fail(NOT_SIMPLE)
    func, metric, dims = match.groups()
    names = list(dict.fromkeys(d.strip() for d in (dims or "").split(",") if d.strip()))
    if any(DIMENSION.fullmatch(d) is None for d in names):
        raise Fail(NOT_SIMPLE)
    return SeriesSpec(metric, aggregate=AGGREGATES[func], group_by=names, aggregated=True)


def _check_analyzer(name: str) -> None:
    tail = name.rsplit(".", 1)[-1]
    if tail == STATIC:
        return
    if tail in BASELINE_ANALYZERS:
        raise Fail(
            "baseline detectors (auto-adaptive and seasonal) are not converted yet: only a "
            "static-threshold detector over a simple timeseries is"
        )
    if tail == RECORDS:
        raise Fail("records mode detectors are not converted: arbitrary DQL has no NRQL equivalent")
    raise Fail(f"the detector analyzer is not recognised ({shown(tail)})")


def _convert(value: DetectorValue, key: str, name: str) -> IRMonitor:
    _check_analyzer(value.analyzer.name)
    inputs = _inputs(value)
    operator = OPERATORS.get(str(inputs.get("alertCondition", "")))
    if operator is None:
        raise Fail(
            f"alert condition {shown(str(inputs.get('alertCondition', '')))} needs a baseline "
            "detector; a static threshold is ABOVE or BELOW"
        )
    spec = _series(inputs)
    threshold = _number(inputs, "threshold")
    window = sample_window(
        _count(inputs, "violatingSamples") or 0,
        _count(inputs, "slidingWindow") or 0,
        _count(inputs, "dealertingSamples", required=False),
        value.executionSettings.queryOffset,
        str(inputs.get("alertOnMissingData", "")).lower() == "true",
    )
    built = build_signal([spec], None, [])
    scaled = finite((threshold or 0.0) * built.factor, "the converted threshold")
    reasons = (
        [] if value.enabled else [DISABLED.replace("metric event", "detector")]
    ) + window.notes
    result = monitor(
        key=key,
        name=name,
        source_type=SOURCE_TYPE,
        confidence=Confidence.APPROXIMATE,
        reasons=reasons + built.notes,
        signal=Signal(
            kind=SignalKind.METRIC,
            raw_query=name,
            window_seconds=window.window_seconds,
            aggregation_window_seconds=60,
            aggregation_delay_seconds=window.delay_seconds,
            occurrences=Occurrences.ALL,
            metric=built.signal,
        ),
    )
    result.thresholds = [Threshold(severity=Severity.CRITICAL, operator=operator, value=scaled)]
    result.no_data = window.no_data
    if not value.enabled:
        result.unmapped["enabled"] = False
    return result


def translate_detector(obj: SettingsObject, key: str) -> IRMonitor:
    """One Davis detector as IR; never raises."""
    value = DetectorValue.model_validate(obj.value)
    name = value.title.strip() or "Davis anomaly detector"
    try:
        return _convert(value, key, name)
    except Fail as fail:
        return monitor(
            key=key,
            name=name,
            source_type=SOURCE_TYPE,
            confidence=Confidence.MANUAL,
            reasons=[fail.reason],
        )
