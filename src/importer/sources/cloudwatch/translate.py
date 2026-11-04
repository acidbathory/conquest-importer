"""Translate CloudWatch alarms into IR, labelling how faithful each conversion is.

translate_alarm and translate_composite never raise: anything they cannot convert
becomes a manual or unsupported IRMonitor with the reason recorded.
"""

import hashlib
import json
import math
import re
from pathlib import Path

from importer.core.emit.nrql import identifier
from importer.core.ir import (
    Aggregation,
    BaselineSpec,
    BinOp,
    Confidence,
    CountFilter,
    CountSignal,
    Expr,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NoData,
    Notification,
    NrTarget,
    Num,
    Occurrences,
    Operator,
    Ref,
    Severity,
    Signal,
    SignalKind,
    TagFilter,
    Threshold,
)
from importer.core.text import printable
from importer.sources.cloudwatch.composite import analyse_rule
from importer.sources.cloudwatch.composite import remediation as composite_remediation
from importer.sources.cloudwatch.logfilter import (
    LOG_GROUP_ATTRIBUTE,
    UnsupportedPattern,
    parse_pattern,
)
from importer.sources.cloudwatch.models import (
    CloudWatchAlarm,
    CompositeAlarm,
    Dimension,
    MetricDataQuery,
)

NAMESPACE_MAP_PATH = Path(__file__).parent / "cloudwatch_map.json"
SUFFIX_MAP_PATH = Path(__file__).parent / "cloudwatch_suffixes.json"
DIMENSIONS_PATH = Path(__file__).parent / "cloudwatch_dimensions.json"
# Percentiles arrive as a separate gauge metric named <metric>.pNN (confirmed on API Gateway
# Latency.p99), and only when the metric stream is configured to send that percentile.
_PERCENTILE = re.compile(r"^p(\d{1,2})$")
PERCENTILE_NOTE = (
    "percentile metrics (<metric>.pNN) are only sent when the metric stream is configured for "
    "them: run ingest-setup with --percentile NAMESPACE:METRIC:pNN; the name was confirmed on "
    "API Gateway only"
)
# Custom metrics stream as aws.custom.<name>.<Metric>, with <name> lower-cased (confirmed for one
# single-word namespace; other shapes are left unmapped rather than guessed).
_CUSTOM_NAMESPACE = re.compile(r"^Custom/([A-Za-z0-9]+)$")
NAMING_NOTE = (
    "AWS metric and dimension names follow the New Relic Metric Streams convention "
    "(aws.<service>.<Metric>): confirmed against real data for DynamoDB, Lambda per-function "
    "metrics (suffix .byFunction) and Custom/<name> namespaces; assumed for other services, "
    "where a dimension-dependent suffix may apply"
)
AGGREGATION_DELAY_SECONDS = 300  # AWS metrics arrive later than agent metrics
OPERATORS = {
    "GreaterThanThreshold": Operator.ABOVE,
    "GreaterThanOrEqualToThreshold": Operator.ABOVE_OR_EQUALS,
    "LessThanThreshold": Operator.BELOW,
    "LessThanOrEqualToThreshold": Operator.BELOW_OR_EQUALS,
}
STATISTICS = {
    "Average": Aggregation.AVG,
    "Sum": Aggregation.SUM,
    "Minimum": Aggregation.MIN,
    "Maximum": Aggregation.MAX,
}
# Fields describing the record or its current state, not the alarm configuration.
IGNORED_FIELDS = {
    "AlarmArn",
    "AlarmConfigurationUpdatedTimestamp",
    "StateValue",
    "StateReason",
    "StateReasonData",
    "StateUpdatedTimestamp",
    "StateTransitionedTimestamp",
    "AlarmDescription",
    "AlarmName",
}
HANDLED_FIELDS = {
    "MetricName",
    "Namespace",
    "Statistic",
    "ExtendedStatistic",
    "Dimensions",
    "Period",
    "EvaluationPeriods",
    "DatapointsToAlarm",
    "Threshold",
    "ComparisonOperator",
    "TreatMissingData",
    "Metrics",
    "AlarmActions",
    "ActionsEnabled",
}
_MATH_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<op>[-+*/()]))"
)
_ALARM_REF = re.compile(r'\b(?:ALARM|OK|INSUFFICIENT_DATA)\(\s*"?([^")]+?)"?\s*\)')
MAX_DIMENSION_VALUE = 256


class _Fail(Exception):
    def __init__(self, confidence: Confidence, reason: str) -> None:
        super().__init__(reason)
        self.confidence = confidence
        self.reason = reason


def load_namespace_map(path: Path = NAMESPACE_MAP_PATH) -> dict[str, str]:
    raw = json.loads(path.read_text())
    return {k: str(v) for k, v in raw.items() if not k.startswith("_")}


def load_suffix_map(path: Path = SUFFIX_MAP_PATH) -> dict[str, dict[str, str]]:
    raw = json.loads(path.read_text())
    return {k: dict(v) for k, v in raw.items() if not k.startswith("_")}


SUFFIXES = load_suffix_map()


def load_dimension_registry(path: Path = DIMENSIONS_PATH) -> dict[str, list[str]]:
    raw = json.loads(path.read_text())
    return {k: list(v) for k, v in raw.items() if not k.startswith("_")}


KNOWN_DIMENSIONS = load_dimension_registry()


def metric_prefix(namespace: str, prefixes: dict[str, str]) -> str | None:
    """The New Relic metric prefix for a CloudWatch namespace, or None if unknown."""
    if namespace in prefixes:
        return prefixes[namespace]
    custom = _CUSTOM_NAMESPACE.match(namespace)
    return f"aws.custom.{custom.group(1).lower()}" if custom else None


def metric_suffix(namespace: str, dims: list[Dimension]) -> str:
    return SUFFIXES.get(namespace, {}).get(",".join(sorted(d.Name for d in dims)), "")


def alarm_namespaces(alarms: list[CloudWatchAlarm]) -> set[str]:
    """Every CloudWatch namespace the given alarms read from."""
    found: set[str] = set()
    for a in alarms:
        if a.Namespace:
            found.add(a.Namespace)
        for q in a.Metrics or []:
            if q.MetricStat:
                found.add(q.MetricStat.Metric.Namespace)
    return found


def alarm_source_id(key: str) -> int:
    """A stable integer id derived from the alarm ARN or name (used for file names and labels)."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


# --- metric math -------------------------------------------------------------------


def _parse_math(expression: str, known: set[str]) -> tuple[Expr, set[str]]:  # noqa: C901 - single-pass expression parser
    """Parse arithmetic over metric ids. Functions and anything else are rejected."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = expression.strip()
    while pos < len(text):
        m = _MATH_TOKEN.match(text, pos)
        if not m:
            raise _Fail(Confidence.MANUAL, "metric math uses syntax that is not supported yet")
        kind = m.lastgroup or ""
        tokens.append((kind, m.group(kind)))
        pos = m.end()
        if len(tokens) > 100:
            raise _Fail(Confidence.MANUAL, "metric math expression is too long")
    for (kind, _), nxt in zip(tokens, [*tokens[1:], ("", "")], strict=True):
        if kind == "id" and nxt == ("op", "("):
            raise _Fail(Confidence.MANUAL, "metric math functions are not supported yet")

    used: set[str] = set()
    index = 0

    def peek() -> tuple[str, str] | None:
        return tokens[index] if index < len(tokens) else None

    def take_op(allowed: str) -> str | None:
        nonlocal index
        tok = peek()
        if tok and tok[0] == "op" and tok[1] in allowed:
            index += 1
            return tok[1]
        return None

    def atom() -> Expr:
        nonlocal index
        tok = peek()
        if tok is None:
            raise _Fail(Confidence.MANUAL, "metric math expression ended unexpectedly")
        index += 1
        if tok[0] == "num":
            return Num(value=float(tok[1]))
        if tok[0] == "id":
            if tok[1] not in known:
                raise _Fail(Confidence.MANUAL, f"metric math refers to unknown id {tok[1]!r}")
            used.add(tok[1])
            return Ref(name=tok[1])
        if tok[1] == "(":
            inner = expr()
            if not take_op(")"):
                raise _Fail(Confidence.MANUAL, "unbalanced parentheses in metric math")
            return inner
        raise _Fail(Confidence.MANUAL, "metric math expression could not be parsed")

    def product() -> Expr:
        left = atom()
        while op := take_op("*/"):
            left = BinOp(op=op, left=left, right=atom())
        return left

    def expr() -> Expr:
        left = product()
        while op := take_op("+-"):
            left = BinOp(op=op, left=left, right=product())
        return left

    result = expr()
    if index != len(tokens):
        raise _Fail(Confidence.MANUAL, "metric math expression could not be parsed")
    return result, used


# --- single alarm --------------------------------------------------------------------


class _Resolved:
    def __init__(self) -> None:
        self.terms: dict[str, MetricTerm] = {}
        self.expression: Expr | None = None
        self.aggregation = Aggregation.AVG
        self.scope: list[TagFilter] = []
        self.period = 0
        self.notes: list[str] = []


def _resolve_term(
    namespace: str,
    metric_name: str,
    dims: list[Dimension],
    stat: str,
    prefixes: dict[str, str],
    percentile_aggregation: Aggregation = Aggregation.MAX,
) -> tuple[MetricTerm, list[TagFilter]]:
    prefix = metric_prefix(namespace, prefixes)
    if prefix is None:
        hint = (
            "only Custom/ followed by letters and digits is supported (no '-', '_' or '.')"
            if namespace.startswith("Custom/")
            else "it is not one of the supported AWS services"
        )
        raise _Fail(Confidence.MANUAL, f"no New Relic mapping for namespace {namespace!r}: {hint}")
    percentile = _PERCENTILE.match(stat)
    # A percentile is one value per minute, so the time aggregation picks the extreme that
    # matches the alarm direction (highest for "above", lowest for "below").
    aggregation = percentile_aggregation if percentile else STATISTICS.get(stat)
    if aggregation is None:
        raise _Fail(Confidence.MANUAL, f"statistic {stat!r} is not supported yet")
    try:
        name = f"{prefix}.{metric_name}{metric_suffix(namespace, dims)}"
        if percentile:
            name += f".{stat}"
        attribute = identifier(name)
        scope = []
        for d in dims:
            if len(d.Value) > MAX_DIMENSION_VALUE or not d.Value.isprintable():
                raise ValueError("dimension value is too long or has control characters")
            scope.append(TagFilter(key=identifier(f"{prefix}.{d.Name}"), value=d.Value))
        # Pin the dimension set: rows of other sets share the metric name.
        used = {d.Name for d in dims}
        scope.extend(
            TagFilter(key=identifier(f"{prefix}.{other}"), absent=True)
            for other in sorted(set(KNOWN_DIMENSIONS.get(namespace, [])) - used)
        )
    except ValueError:
        raise _Fail(
            Confidence.MANUAL, "metric or dimension name has characters that are not supported"
        ) from None
    term = MetricTerm(
        metric=f"{namespace}:{metric_name}",
        space_aggregation=aggregation,
        nr_target=NrTarget(event="Metric", attribute=attribute),
    )
    return term, scope


def _resolve(alarm: CloudWatchAlarm, prefixes: dict[str, str]) -> _Resolved:
    out = _Resolved()
    if alarm.ThresholdMetricId:
        raise _Fail(Confidence.MANUAL, "anomaly detection alarms are not supported yet")
    if alarm.Metrics is None:
        if not (alarm.Namespace and alarm.MetricName):
            raise _Fail(Confidence.MANUAL, "alarm has no metric")
        statistic = alarm.ExtendedStatistic or alarm.Statistic or ""
        direction = OPERATORS.get(alarm.ComparisonOperator)
        below = direction in (Operator.BELOW, Operator.BELOW_OR_EQUALS)
        agg = Aggregation.MIN if below else Aggregation.MAX
        term, scope = _resolve_term(
            alarm.Namespace, alarm.MetricName, alarm.Dimensions, statistic, prefixes, agg
        )
        if _PERCENTILE.match(statistic):
            out.notes.append(PERCENTILE_NOTE)
            if (alarm.Period or 0) > 60:
                out.notes.append(
                    f"a {statistic} over {alarm.Period}s is approximated by the "
                    f"{'lowest' if below else 'highest'} per-minute {statistic}"
                )
        out.terms = {"m1": term}
        out.scope = scope
        out.aggregation = term.space_aggregation
        out.period = alarm.Period or 0
        return out

    by_id: dict[str, MetricDataQuery] = {q.Id: q for q in alarm.Metrics}
    returned = [q for q in alarm.Metrics if q.ReturnData]
    if len(returned) != 1:
        raise _Fail(Confidence.MANUAL, "alarm evaluates more than one metric query")
    main = returned[0]
    if main.MetricStat is not None:
        wanted = {main.Id}
        out.expression = None
    elif main.Expression:
        known = {i for i, q in by_id.items() if q.MetricStat is not None}
        out.expression, wanted = _parse_math(main.Expression, known)
    else:
        raise _Fail(Confidence.MANUAL, "metric query has neither a metric nor an expression")

    scopes: list[list[TagFilter]] = []
    periods: set[int] = set()
    stats: set[Aggregation] = set()
    for qid in sorted(wanted):
        stat = by_id[qid].MetricStat
        if stat is None:
            raise _Fail(Confidence.MANUAL, "nested metric math is not supported yet")
        term, scope = _resolve_term(
            stat.Metric.Namespace,
            stat.Metric.MetricName,
            stat.Metric.Dimensions,
            stat.Stat,
            prefixes,
        )
        out.terms[qid] = term
        scopes.append(sorted(scope, key=lambda f: (f.absent, f.key)))
        periods.add(stat.Period)
        stats.add(term.space_aggregation)
    if any(s != scopes[0] for s in scopes):
        raise _Fail(Confidence.MANUAL, "metrics in the expression have different dimensions")
    if len(periods) != 1:
        raise _Fail(Confidence.MANUAL, "metrics in the expression have different periods")
    if len(stats) != 1:
        raise _Fail(Confidence.MANUAL, "metrics in the expression use different statistics")
    out.scope = scopes[0]
    out.period = periods.pop()
    out.aggregation = stats.pop()
    return out


_BAND = re.compile(
    r"^\s*ANOMALY_DETECTION_BAND\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:,\s*([0-9.]+)\s*)?\)\s*$"
)
ANOMALY_DIRECTIONS = {
    "GreaterThanUpperThreshold": "upper_only",
    "LessThanLowerThreshold": "lower_only",
    "LessThanLowerOrGreaterThanUpperThreshold": "upper_and_lower",
}
DEFAULT_BAND_DEVIATIONS = 2.0


def _anomaly(alarm: CloudWatchAlarm) -> tuple[CloudWatchAlarm, BaselineSpec]:
    """Split an anomaly-detection alarm into its metric and the width of its band.

    Returns a plain single-metric alarm (the metric the model watches) plus the baseline settings.
    """
    direction = ANOMALY_DIRECTIONS.get(alarm.ComparisonOperator)
    if direction is None:
        raise _Fail(Confidence.MANUAL, f"comparison {alarm.ComparisonOperator!r} is not supported")
    queries = alarm.Metrics or []
    band = next((q for q in queries if q.Id == alarm.ThresholdMetricId and q.Expression), None)
    match = _BAND.match(band.Expression or "") if band else None
    if match is None:
        raise _Fail(
            Confidence.MANUAL,
            "anomaly detection alarm whose band is not a plain ANOMALY_DETECTION_BAND(metric, n)",
        )
    source = next((q for q in queries if q.Id == match.group(1) and q.MetricStat), None)
    if source is None or source.MetricStat is None:
        raise _Fail(
            Confidence.MANUAL,
            "anomaly detection alarm built on metric math, not on one metric",
        )
    spec = source.MetricStat
    stat = spec.Stat
    plain = CloudWatchAlarm(
        AlarmName=alarm.AlarmName,
        Namespace=spec.Metric.Namespace,
        MetricName=spec.Metric.MetricName,
        Dimensions=spec.Metric.Dimensions,
        Statistic=None if _PERCENTILE.match(stat) else stat,
        ExtendedStatistic=stat if _PERCENTILE.match(stat) else None,
        Period=spec.Period,
        EvaluationPeriods=alarm.EvaluationPeriods,
        DatapointsToAlarm=alarm.DatapointsToAlarm,
        ComparisonOperator="GreaterThanThreshold",
        Threshold=0.0,
    )
    width = float(match.group(2)) if match.group(2) else DEFAULT_BAND_DEVIATIONS
    return plain, BaselineSpec(direction=direction, deviations=width)


def _log_signal(alarm: CloudWatchAlarm) -> tuple[CountSignal, list[str]]:
    """An alarm on a metric made by a log metric filter becomes a count of matching log events."""
    refs = alarm.log_metric_filters
    if len(refs) != 1:
        raise _Fail(
            Confidence.MANUAL,
            f"{len(refs)} log metric filters publish this metric; it cannot be traced to one "
            "log query",
        )
    ref = refs[0]
    where = (
        f"log metric filter {printable(ref.filter_name, 60)!r} on log group "
        f"{printable(ref.log_group, 80)!r}"
    )
    if alarm.Metrics is not None:
        raise _Fail(
            Confidence.MANUAL, f"{where}: metric math over a log-derived metric is not converted"
        )
    if alarm.Statistic != "Sum" or alarm.ExtendedStatistic:
        raise _Fail(
            Confidence.MANUAL,
            f"{where}: statistic {alarm.ExtendedStatistic or alarm.Statistic} is not converted; "
            "only Sum, the number of matching events, is",
        )
    if ref.value != "1":
        raise _Fail(
            Confidence.MANUAL,
            f"{where}: it publishes a value taken from the log line ({printable(ref.value, 40)}), "
            "not a count of events",
        )
    if ref.has_dimensions or alarm.Dimensions:
        raise _Fail(Confidence.MANUAL, f"{where}: dimensions on a log metric are not converted")
    if not ref.log_group:
        raise _Fail(Confidence.MANUAL, f"{where}: the log group is unknown")
    try:
        parsed = parse_pattern(ref.pattern)
    except UnsupportedPattern as exc:
        raise _Fail(Confidence.MANUAL, f"{where}: pattern not converted: {exc}") from None
    count = CountSignal(
        filters=[CountFilter(key=LOG_GROUP_ATTRIBUTE, value=ref.log_group), *parsed.filters]
    )
    notes = [
        f"converted from the {where} (pattern {printable(ref.pattern, 100)!r}) to a count of "
        "matching log events. It only returns data once that log group is forwarded to New "
        f"Relic; the group is matched on the {LOG_GROUP_ATTRIBUTE} attribute, which New Relic adds "
        "to logs sent through its Firehose and Lambda forwarders. Compare the counts with "
        "CloudWatch before enabling, because text matching may differ in case sensitivity",
    ]
    return count, notes


def translate_alarm(alarm: CloudWatchAlarm, prefixes: dict[str, str]) -> IRMonitor:  # noqa: C901 - one sequential mapping pipeline
    key = alarm.AlarmArn or alarm.AlarmName
    reasons: list[str] = []
    handles = sorted(set(alarm.AlarmActions))
    if handles:
        reasons.append("alarm actions (such as SNS topics) must be configured manually")
    unmapped = {
        k: v
        for k, v in (alarm.model_extra or {}).items()
        if k not in IGNORED_FIELDS and k not in HANDLED_FIELDS
    }
    if alarm.OKActions:
        unmapped["OKActions"] = alarm.OKActions
    if alarm.InsufficientDataActions:
        unmapped["InsufficientDataActions"] = alarm.InsufficientDataActions
    if not alarm.ActionsEnabled:
        unmapped["ActionsEnabled"] = False
    if alarm.Unit:
        unmapped["Unit"] = alarm.Unit

    base = {
        "source_system": "cloudwatch",
        "source_id": alarm_source_id(key),
        "source_key": key,
        "source_type": "log-metric alarm" if alarm.log_metric_filters else "metric alarm",
        "name": alarm.AlarmName,
        "notification": Notification(
            message_template=alarm.AlarmDescription or "", handles=handles
        ),
    }

    def failed(confidence: Confidence, reason: str) -> IRMonitor:
        return IRMonitor(
            **base,
            signal=Signal(kind=SignalKind.METRIC, raw_query=alarm.AlarmName),
            confidence=confidence,
            reasons=[reason, *reasons],
            unmapped=unmapped,
        )

    baseline: BaselineSpec | None = None
    measured = alarm  # the alarm whose metric is read (for an anomaly alarm, its source metric)
    operator = OPERATORS.get(alarm.ComparisonOperator)
    threshold = alarm.Threshold
    if operator is None:
        if (
            "Anomaly" in alarm.ComparisonOperator
            or "Band" in alarm.ComparisonOperator
            or "Lower" in alarm.ComparisonOperator
            or "Upper" in alarm.ComparisonOperator
        ):
            try:
                measured, baseline = _anomaly(alarm)
            except _Fail as fail:
                return failed(fail.confidence, fail.reason)
            operator, threshold = Operator.ABOVE, baseline.deviations
            reasons.append(
                "anomaly detection becomes a New Relic baseline condition: both learn the normal "
                f"range and alert outside {baseline.deviations:g} standard deviations, but the "
                "models differ and New Relic needs a few days of data to settle. Review the "
                "alerts it raises before you enable it"
            )
        else:
            return failed(
                Confidence.MANUAL, f"comparison {alarm.ComparisonOperator!r} is not supported"
            )
    if threshold is None:
        return failed(Confidence.MANUAL, "alarm has no static threshold")
    log_count: CountSignal | None = None
    resolved: _Resolved | None = None
    log_notes: list[str] = []
    try:
        if alarm.log_metric_filters:
            log_count, log_notes = _log_signal(alarm)
            period = alarm.Period or 0
        else:
            resolved = _resolve(measured, prefixes)
            period = resolved.period
    except _Fail as fail:
        return failed(fail.confidence, fail.reason)
    if period < 60 or period % 60:
        return failed(Confidence.MANUAL, f"period {period}s (high-resolution) is not supported")

    evals = alarm.EvaluationPeriods
    datapoints = alarm.DatapointsToAlarm or evals
    if datapoints < 1 or evals < 1 or datapoints > evals:
        return failed(Confidence.MANUAL, "evaluation periods are inconsistent")
    if datapoints == evals:
        occurrences, duration = Occurrences.ALL, period * evals
    elif datapoints == 1:
        occurrences, duration = Occurrences.AT_LEAST_ONCE, period * evals
    else:
        occurrences, duration = Occurrences.ALL, period * datapoints
        reasons.append(
            f"{datapoints} of {evals} datapoints is approximated as "
            f"{datapoints} consecutive breaches"
        )

    if resolved is not None:
        reasons.append(NAMING_NOTE)
        reasons.extend(n for n in resolved.notes if n not in reasons)
    reasons.extend(log_notes)
    missing = alarm.TreatMissingData or "missing"
    no_data = NoData()
    if missing == "breaching":
        no_data = NoData(notify=True, timeout_minutes=max(1, math.ceil(period * evals / 60)))
    elif missing in ("notBreaching", "ignore"):
        reasons.append(
            f"TreatMissingData={missing} has no equivalent; New Relic leaves the condition "
            "unchanged when data is missing"
        )

    signal = Signal(
        kind=SignalKind.LOG if log_count is not None else SignalKind.METRIC,
        raw_query=alarm.AlarmName,
        window_seconds=duration,
        aggregation_window_seconds=period,
        aggregation_delay_seconds=AGGREGATION_DELAY_SECONDS,
        occurrences=occurrences,
        metric=MetricSignal(
            terms=resolved.terms,
            expression=resolved.expression,
            aggregation=resolved.aggregation,
            scope=resolved.scope,
        )
        if resolved is not None
        else None,
        count=log_count,
        baseline=baseline,
    )
    return IRMonitor(
        **base,
        signal=signal,
        thresholds=[Threshold(severity=Severity.CRITICAL, operator=operator, value=threshold)],
        no_data=no_data,
        confidence=Confidence.APPROXIMATE,
        reasons=reasons,
        unmapped=unmapped,
    )


def translate_composite(
    alarm: CompositeAlarm, child_status: dict[str, Confidence] | None = None
) -> IRMonitor:
    key = alarm.AlarmArn or alarm.AlarmName
    names = _ALARM_REF.findall(alarm.AlarmRule)
    shown = ", ".join(n[:60] for n in names[:5]) + (" ..." if len(names) > 5 else "")
    reason = (
        f"composite alarm combines {len(names)} alarms ({shown}); New Relic has no composite "
        "condition. Recreate as one NRQL condition or filter in a workflow"
    )
    return IRMonitor(
        source_system="cloudwatch",
        source_id=alarm_source_id(key),
        source_key=key,
        source_type="composite alarm",
        name=alarm.AlarmName,
        signal=Signal(kind=SignalKind.COMPOSITE, raw_query=alarm.AlarmRule),
        notification=Notification(handles=sorted(set(alarm.AlarmActions))),
        confidence=Confidence.UNSUPPORTED,
        reasons=[reason],
        remediation=composite_remediation(
            analyse_rule(alarm.AlarmRule), child_status or {}, sorted(set(alarm.AlarmActions))
        ),
    )
