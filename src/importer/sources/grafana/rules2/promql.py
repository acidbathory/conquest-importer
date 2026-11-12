"""PromQL forms beyond the plain selector: counters, ratios and absence.

Each form is registered with `register_prom_form` and returns None when the expression is not
its shape, so the older checks still give their reason. Nothing here reads text from the rule
except through the shared matcher parsing and `nrql_attribute`, which validate names.

What the evidence allows (docs/research/newrelic-notifications-and-nrql-reference.md, 2.3):
`sum()` and arithmetic over aggregates are usable in alert conditions; `rate()`, `derivative()`
and `bucketPercentile()` are not documented as usable, so counters become `sum(x)` over the
range, divided by the range for a rate, and `histogram_quantile` stays manual.
"""

import re
from dataclasses import dataclass

from importer.core.ir import (
    Aggregation,
    BinOp,
    Expr,
    HeartbeatSignal,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Num,
    Operator,
    Ref,
    Severity,
    SignalKind,
    Threshold,
)
from importer.sources.grafana.dashboard_convert.name_map import hinted_gauge
from importer.sources.grafana.translate import (
    BODY,
    COUNTER_SUFFIXES,
    EMPTY_SENSITIVE,
    EXACT_AGGREGATES,
    N_ABSENT,
    N_COUNTER,
    N_COUNTER_EMPTY,
    N_FACET_IDENTITY,
    N_METRIC,
    N_NEGATION,
    N_RATE_DIVISION,
    N_RATE_SAMPLES,
    N_RATIO_ZERO,
    PROM_COUNTER,
    PROM_MATCHER,
    PROM_NOT_SIMPLE,
    PROM_SELECTOR,
    QUOTED,
    R_ABSENT_THRESHOLD,
    R_AGG_MIX,
    R_COUNTER_AGG,
    R_COUNTER_NAME,
    R_COUNTER_RANGE,
    R_COUNTER_REDUCER,
    R_COUNTER_UNAGG,
    R_RATE_OF_GAUGE,
    R_RATIO_AGG,
    R_RATIO_SHAPE,
    RANGE_SECONDS,
    Built,
    Ctx,
    Fail,
    Match,
    looked_back,
    nrql_attribute,
    parse_matchers,
    register_prom_form,
    split_outer,
    tag_filter,
    time_window,
)

COUNT_SUFFIXES = ("_total", "_count", "_bucket")  # what New Relic stores as a delta count
SELECTOR = rf"([A-Za-z_:][A-Za-z0-9_:]*)\s*(?:\{{({BODY})\}})?"
RANGE = r"\[\s*(\d+)\s*([smh])\s*\]"
COUNTER_CALL = re.compile(rf"\s*(rate|increase)\s*\(\s*{SELECTOR}\s*{RANGE}\s*\)\s*")
ABSENT = re.compile(rf"\s*absent\(\s*{SELECTOR}\s*\)\s*")
ABSENT_OVER_TIME = re.compile(rf"\s*absent_over_time\(\s*{SELECTOR}\s*{RANGE}\s*\)\s*")
NUMBER = re.compile(r"\s*\d+(?:\.\d+)?\s*")
OTHER_OPERATORS = "+-%^<>=!&|"
MIN_RANGE = 60
LOOKBACK_SECONDS = 300  # Prometheus shows a series for five minutes after its last sample


@dataclass(frozen=True)
class _Operand:
    """One side of an expression: [agg [by (..)]] ( [rate|increase] ( selector [range] ) )."""

    aggregator: str | None
    group_by: tuple[str, ...]
    name: str
    matches: tuple[Match, ...]
    function: str | None = None
    seconds: int | None = None


def _operand(text: str) -> _Operand | None:
    """The operand, or None when the text is not this shape (so older checks answer)."""
    try:
        aggregator, group_by, inner = split_outer(text, "avg|sum|min|max")
    except Fail:
        return None
    call = COUNTER_CALL.fullmatch(inner)
    if call:
        function, name, body, number, unit = call.groups()
        seconds: int | None = int(number) * RANGE_SECONDS[unit]
    else:
        selector = PROM_SELECTOR.fullmatch(inner)
        if selector is None:
            return None
        function, seconds = None, None
        name, body = selector[1], selector[2]
    matches = parse_matchers(body or "", PROM_MATCHER, PROM_NOT_SIMPLE, absent_ok=True)
    return _Operand(aggregator, tuple(group_by), name, tuple(matches), function, seconds)


def _pieces(expr: str) -> tuple[list[str], list[str]] | None:
    """The text between the top-level `/` and `*`, and those operators; None if anything else
    (another operator, unbalanced brackets) is at the top level."""
    masked = re.sub(QUOTED, lambda m: "x" * len(m[0]), expr)
    depth, start = 0, 0
    pieces: list[str] = []
    operators: list[str] = []
    for index, char in enumerate(masked):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0 and char in "/*":
            pieces.append(expr[start:index])
            operators.append(char)
            start = index + 1
        elif depth == 0 and char in OTHER_OPERATORS and not _in_name(masked, index):
            return None
        if depth < 0:
            return None
    pieces.append(expr[start:])
    return (pieces, operators) if depth == 0 else None


def _in_name(masked: str, index: int) -> bool:
    """A `-` or `+` inside a number such as 1e-3 is not an operator; names have none."""
    return masked[index] in "+-" and index > 0 and masked[index - 1] in "eE"


# --- counters ---


def _check_counter(operand: _Operand, ctx: Ctx, facet: bool = False) -> int:
    """The range in seconds of a counter operand that converts; else raise the reason.

    `facet`: a rate with no sum() may convert when the rule names the labels to group by."""
    if operand.aggregator is None and not (facet and ctx.labels):
        raise Fail(R_COUNTER_UNAGG)
    if operand.aggregator not in (None, "sum"):
        raise Fail(R_COUNTER_AGG.format(agg=operand.aggregator))
    if hinted_gauge(operand.name):
        raise Fail(R_RATE_OF_GAUGE)
    if not operand.name.endswith(COUNT_SUFFIXES):
        raise Fail(R_COUNTER_NAME)
    reducer = looked_back(ctx)
    if reducer != "last":
        raise Fail(R_COUNTER_REDUCER.format(reducer=reducer))
    seconds = operand.seconds or 0
    if seconds < MIN_RANGE or seconds % 60:
        raise Fail(R_COUNTER_RANGE.format(seconds=seconds))
    return seconds


def _counter_notes(ctx: Ctx) -> list[str]:
    """Notes every counter form carries: the two-sample rule and the idle counter."""
    notes = [N_RATE_SAMPLES]
    if ctx.threshold is not None and ctx.threshold.operator in EMPTY_SENSITIVE:
        notes.append(N_COUNTER_EMPTY)
    return notes


def _term(name: str) -> MetricTerm:
    attribute = nrql_attribute(name)
    return MetricTerm(
        metric=attribute,
        space_aggregation=Aggregation.SUM,
        nr_target=NrTarget(event="Metric", attribute=attribute),
    )


def _counter_form(expr: str, ctx: Ctx) -> Built | None:
    if "rate" not in expr and "increase" not in expr:
        return None
    parts = _pieces(expr)
    operand = _operand(expr)
    if parts is None or parts[1] or operand is None or operand.function is None:
        return None
    seconds = _check_counter(operand, ctx, facet=True)
    term = _term(operand.name)
    division: Expr | None = None
    notes = [N_METRIC, N_COUNTER]
    if operand.function == "rate":
        division = BinOp(op="/", left=Ref(name="a"), right=Num(value=float(seconds)))
        notes.append(N_RATE_DIVISION.format(seconds=seconds))
    notes += _counter_notes(ctx)
    if any(m.negate for m in operand.matches):
        notes.append(N_NEGATION)
    group_by = list(operand.group_by)
    if operand.aggregator is None:
        group_by = list(ctx.labels)
        notes.append(N_FACET_IDENTITY.format(labels=", ".join(group_by)))
    metric = MetricSignal(
        terms={"a": term},
        expression=division,
        aggregation=Aggregation.SUM,
        scope=[tag_filter(m) for m in operand.matches],
        group_by=[nrql_attribute(g) for g in group_by],
    )
    return Built(SignalKind.METRIC, seconds, metric=metric, notes=notes)


# --- ratios ---


def _ratio_parts(expr: str) -> tuple[str, str, float] | None:
    """(numerator, denominator, factor) for `a / b`, `a / b * 100` or `100 * a / b`."""
    parts = _pieces(expr)
    if parts is None:
        return None
    pieces, operators = parts
    if operators == ["/"]:
        return pieces[0], pieces[1], 1.0
    numbers = [NUMBER.fullmatch(p) for p in pieces]
    if operators == ["/", "*"] and numbers[2] and not numbers[0] and not numbers[1]:
        return pieces[0], pieces[1], float(pieces[2])
    if operators == ["*", "/"] and numbers[0] and not numbers[1] and not numbers[2]:
        return pieces[1], pieces[2], float(pieces[0])
    return None


def _same_shape(left: _Operand, right: _Operand) -> bool:
    return left.aggregator is not None and (
        left.aggregator,
        left.group_by,
        left.matches,
        left.function,
        left.seconds,
    ) == (right.aggregator, right.group_by, right.matches, right.function, right.seconds)


def _ratio_window(
    left: _Operand, right: _Operand, ctx: Ctx
) -> tuple[Aggregation, int | None, list[str]]:
    """Aggregation, fixed window and notes of a ratio; both sides are checked the same way."""
    if left.function:
        seconds = _check_counter(left, ctx)
        _check_counter(right, ctx)
        return Aggregation.SUM, seconds, [N_COUNTER, *_counter_notes(ctx)]
    if any(side.name.endswith(COUNTER_SUFFIXES) for side in (left, right)):
        raise Fail(PROM_COUNTER)
    if left.aggregator in ("min", "max"):
        raise Fail(R_RATIO_AGG.format(agg=left.aggregator))
    reducer = looked_back(ctx)
    if (left.aggregator, reducer) not in EXACT_AGGREGATES:
        raise Fail(R_AGG_MIX.format(agg=left.aggregator, reducer=reducer))
    fixed, notes = time_window(ctx, reducer)
    return Aggregation.AVG, fixed, notes


def _ratio_form(expr: str, ctx: Ctx) -> Built | None:
    parts = _ratio_parts(expr)
    if parts is None:
        return None
    left, right = _operand(parts[0]), _operand(parts[1])
    if left is None or right is None:
        return None
    if not _same_shape(left, right):
        raise Fail(R_RATIO_SHAPE)
    aggregation, fixed, notes = _ratio_window(left, right, ctx)
    ratio: Expr = BinOp(op="/", left=Ref(name="a"), right=Ref(name="b"))
    if parts[2] != 1.0:
        ratio = BinOp(op="*", left=ratio, right=Num(value=parts[2]))
    terms = {"a": _term(left.name), "b": _term(right.name)}
    for term in terms.values():
        term.space_aggregation = aggregation
    metric = MetricSignal(
        terms=terms,
        expression=ratio,
        aggregation=aggregation,
        scope=[tag_filter(m) for m in left.matches],
        group_by=[nrql_attribute(g) for g in left.group_by],
    )
    notes = [N_METRIC, *notes, N_RATIO_ZERO]
    if any(m.negate for m in left.matches):
        notes.append(N_NEGATION)
    return Built(SignalKind.METRIC, fixed, metric=metric, notes=notes)


# --- absence ---


def _fires_on_one(threshold: Threshold) -> bool:
    """Whether the Grafana threshold is breached by the value 1 that absent() returns."""
    tests = {
        Operator.ABOVE: threshold.value < 1,
        Operator.ABOVE_OR_EQUALS: threshold.value <= 1,
        Operator.BELOW: threshold.value > 1,
        Operator.BELOW_OR_EQUALS: threshold.value >= 1,
        Operator.EQUALS: threshold.value == 1,
        Operator.NOT_EQUALS: threshold.value != 1,
    }
    return tests[threshold.operator]


def _absent_form(expr: str, ctx: Ctx) -> Built | None:
    over_time = ABSENT_OVER_TIME.fullmatch(expr)
    found = over_time or ABSENT.fullmatch(expr)
    if found is None:
        return None
    name, body = found[1], found[2]
    seconds = int(found[3]) * RANGE_SECONDS[found[4]] if over_time else 0
    if ctx.threshold is not None and not _fires_on_one(ctx.threshold):
        raise Fail(R_ABSENT_THRESHOLD)
    matches = parse_matchers(body or "", PROM_MATCHER, PROM_NOT_SIMPLE, absent_ok=True)
    attribute = nrql_attribute(name)
    heartbeat = HeartbeatSignal(
        check=attribute,
        scope=[tag_filter(m) for m in matches],
        nr_target=NrTarget(event="Metric", attribute=attribute),
    )
    notes = [N_METRIC, N_ABSENT]
    if any(m.negate for m in matches):
        notes.append(N_NEGATION)
    return Built(
        SignalKind.HEARTBEAT,
        None,
        notes=notes,
        threshold=Threshold(severity=Severity.CRITICAL, operator=Operator.BELOW, value=1),
        heartbeat=heartbeat,
        minimum_duration=seconds,
        silence_seconds=seconds or LOOKBACK_SECONDS,
    )


for _form in (_absent_form, _counter_form, _ratio_form):
    register_prom_form(_form)
