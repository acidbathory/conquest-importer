"""What a parsed metric selector means for a New Relic condition.

`to_query` returns the terms (one per metric in the selector), their aggregation, split and
filters, and the arithmetic between them, using Dynatrace dimension and metric names. Mapping
those to New Relic names is the translator's job, through the metric map. A transformation that
cannot be expressed in one alert condition raises `SelectorError` and the whole selector is
manual. The table of verdicts is docs/research/dynatrace-alerting-objects-deep.md section 5.1.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from importer.sources.dynatrace.classic.selector import (
    Arg,
    Binary,
    Call,
    Expr,
    Ident,
    Lit,
    Number,
    SelectorError,
    Series,
    Transform,
    parse_selector,
)

MAX_PERCENTILE = 100.0


@dataclass(frozen=True)
class Aggregate:
    func: str  # an NRQL function: average, min, max, sum, count, percentile, latest
    percentile: float | None = None


@dataclass(frozen=True)
class DimFilter:
    key: str  # a Dynatrace dimension key
    kind: str  # eq, prefix, suffix, contains, exists
    values: tuple[str, ...]
    negate: bool


@dataclass
class SeriesSpec:
    key: str
    aggregate: Aggregate | None = None
    group_by: list[str] = field(default_factory=list)
    filters: list[DimFilter] = field(default_factory=list)
    fill: float | None = None
    rate: bool = False
    aggregated: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ENum:
    value: float


@dataclass(frozen=True)
class ERef:
    index: int


@dataclass(frozen=True)
class EOp:
    op: str
    left: "QExpr"
    right: "QExpr"


QExpr = ENum | ERef | EOp


@dataclass
class Query:
    terms: list[SeriesSpec]
    expression: QExpr | None


AGGREGATES = {
    "avg": Aggregate("average"),
    "min": Aggregate("min"),
    "max": Aggregate("max"),
    "sum": Aggregate("sum"),
    "count": Aggregate("count"),
    "median": Aggregate("percentile", 50.0),
    "value": Aggregate("latest"),
    "last": Aggregate("latest"),
    "lastReal": Aggregate("latest"),
}
UNSUPPORTED = {
    "fold": "it collapses the series to one point, and an alert watches a stream",
    "delta": "a difference to the previous point is not available in an alert condition",
    "rollup": "a trailing aggregation window cannot be set per condition here",
    "timeshift": "a comparison with the past is not allowed in an alert condition",
    "partition": "splitting by value ranges is not available in an alert condition",
    "histogram": "histogram buckets are not allowed in an alert condition",
    "merge": "removing one dimension needs the full dimension list of the metric",
    "toUnit": "a unit conversion needs a unit table",
}
NOTED = {
    "sort": "the sort is not applied: New Relic evaluates every series, not the top few",
    "limit": "the series limit is not applied: New Relic evaluates every series",
    "names": "the added entity names are dropped; the New Relic name attributes are used",
    "parents": "the added parent entity is dropped",
    "setUnit": "setUnit only labels the unit; the unit is not changed",
    "smooth": "smooth(skipfirst) is dropped; its effect is one point after a gap",
}
RATE_NOTE = (
    "rate per minute is read as the sum over each one-minute window; New Relic stores "
    "counts per interval, so check the converted query against the Dynatrace chart"
)


def _text(arg: Arg, what: str) -> str:
    if not isinstance(arg, Lit):
        raise SelectorError(f"{what} needs quoted text arguments")
    return arg.value


def _aggregation(spec: SeriesSpec, aggregate: Aggregate | None) -> None:
    if spec.aggregated:
        raise SelectorError("two aggregations on one series are not supported")
    spec.aggregated = True
    spec.aggregate = aggregate


def _simple(name: str) -> Callable[[SeriesSpec, tuple[Arg, ...]], None]:
    def handler(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
        if args:
            raise SelectorError(f"{name} takes no arguments")
        _aggregation(spec, AGGREGATES[name])

    return handler


def _auto(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    if args:
        raise SelectorError("auto takes no arguments")
    _aggregation(spec, None)


def _percentile(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    if len(args) != 1 or not isinstance(args[0], Number) or args[0].unit:
        raise SelectorError("percentile takes one number")
    value = args[0].value
    if not math.isfinite(value) or not 0 <= value <= MAX_PERCENTILE:
        raise SelectorError("percentile is outside 0 to 100")
    _aggregation(spec, Aggregate("percentile", value))


def _split_by(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    spec.group_by = [_text(a, "splitBy") for a in args]


def _default(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    first = args[0] if args else None
    if not isinstance(first, Number) or first.unit or not math.isfinite(first.value):
        raise SelectorError("default takes a number")
    always = len(args) == 2 and args[1] == Ident("always")
    if len(args) > 2 or (len(args) == 2 and not always):
        raise SelectorError("default takes a number and optionally 'always'")
    spec.fill = first.value
    spec.notes.append(
        f"default({first.value:g}{', always' if always else ''}) is not applied: the converted "
        "condition has no gap fill, so a minute with no data is not read as that value"
    )


def _rate(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    if args and not (
        len(args) == 1 and isinstance(args[0], Number) and (args[0].value, args[0].unit) == (1, "m")
    ):
        raise SelectorError("rate over a period other than 1m is not supported")
    spec.rate = True
    spec.notes.append(RATE_NOTE)


def _filter_term(call: Call) -> list[DimFilter]:
    """A filter call as a list of tests that must all hold."""
    name, args = call.name, call.args
    if name == "and":
        return [t for a in args for t in _filter_term(_call(a))]
    if name == "or":
        return [_any_of(args)]
    if name == "not":
        return [_negated(args)]
    if name in ("eq", "ne") and len(args) == 2:
        key, value = _text(args[0], name), _text(args[1], name)
        return [DimFilter(key, "eq", (value,), name == "ne")]
    if name in ("prefix", "suffix", "contains") and len(args) == 2:
        return [DimFilter(_text(args[0], name), name, (_text(args[1], name),), False)]
    if name == "existsKey" and len(args) == 1:
        return [DimFilter(_text(args[0], name), "exists", (), False)]
    if name == "in" or name == "entitySelector":
        raise SelectorError("a filter on an entity selector needs entity resolution")
    if name == "series":
        raise SelectorError(
            "a series filter (a test on the aggregate of each series) is not supported"
        )
    if name == "remainder":
        raise SelectorError("the remainder filter has no equivalent")
    raise SelectorError(f"the filter {name!r} is not supported or has the wrong arguments")


def _call(arg: Arg) -> Call:
    if not isinstance(arg, Call):
        raise SelectorError("a filter argument must be a filter call")
    return arg


def _any_of(args: tuple[Arg, ...]) -> DimFilter:
    tests = [t for a in args for t in _filter_term(_call(a))]
    first = tests[0] if tests else None
    if (
        first is None
        or first.kind != "eq"
        or any(t.kind != "eq" or t.key != first.key or t.negate for t in tests)
        or first.negate
    ):
        raise SelectorError("an or filter across different dimensions or tests is not supported")
    return DimFilter(first.key, "eq", tuple(v for t in tests for v in t.values), False)


def _negated(args: tuple[Arg, ...]) -> DimFilter:
    tests = _filter_term(_call(args[0])) if len(args) == 1 else []
    if len(tests) != 1:
        raise SelectorError("a not filter over several tests is not supported")
    (test,) = tests
    return DimFilter(test.key, test.kind, test.values, not test.negate)


def _filter(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
    if len(args) != 1:
        raise SelectorError("filter takes one filter call")
    spec.filters += _filter_term(_call(args[0]))


def _noted(name: str) -> Callable[[SeriesSpec, tuple[Arg, ...]], None]:
    def handler(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
        del args
        spec.notes.append(NOTED[name])

    return handler


def _unsupported(name: str) -> Callable[[SeriesSpec, tuple[Arg, ...]], None]:
    def handler(spec: SeriesSpec, args: tuple[Arg, ...]) -> None:
        del spec, args
        raise SelectorError(f"the {name} transformation is not supported: {UNSUPPORTED[name]}")

    return handler


HANDLERS: dict[str, Callable[[SeriesSpec, tuple[Arg, ...]], None]] = {
    **{name: _simple(name) for name in AGGREGATES},
    "auto": _auto,
    "percentile": _percentile,
    "splitBy": _split_by,
    "filter": _filter,
    "default": _default,
    "rate": _rate,
    **{name: _noted(name) for name in NOTED},
    **{name: _unsupported(name) for name in UNSUPPORTED},
}


def _series_spec(series: Series) -> SeriesSpec:
    spec = SeriesSpec(series.key)
    for transform in series.transforms:
        _apply(spec, transform)
    if spec.rate:
        if spec.aggregate is None:
            spec.aggregate = Aggregate("sum")
        elif spec.aggregate.func != "sum":
            raise SelectorError("rate with an aggregation other than sum is not supported")
    return spec


def _apply(spec: SeriesSpec, transform: Transform) -> None:
    handler = HANDLERS.get(transform.name)
    if handler is None:
        raise SelectorError(f"unknown transformation {transform.name!r}")
    handler(spec, transform.args)


def _walk(node: Expr, terms: list[SeriesSpec]) -> QExpr:
    if isinstance(node, Number):
        if node.unit or not math.isfinite(node.value):
            raise SelectorError("a number with a unit in arithmetic is not supported")
        return ENum(node.value)
    if isinstance(node, Series):
        terms.append(_series_spec(node))
        return ERef(len(terms) - 1)
    return EOp(node.op, _walk(node.left, terms), _walk(node.right, terms))


def _same_shape(terms: list[SeriesSpec]) -> None:
    first = terms[0]
    for other in terms[1:]:
        if other.group_by != first.group_by or other.filters != first.filters:
            raise SelectorError("the terms of an expression must use the same split and filters")


def to_query(text: str) -> Query:
    """Parse and interpret a selector; raises SelectorError when it cannot be one condition."""
    ast = parse_selector(text)
    terms: list[SeriesSpec] = []
    expression = _walk(ast, terms)
    if not terms:
        raise SelectorError("the selector has no metric")
    _same_shape(terms)
    return Query(terms, None if isinstance(ast, Series) else expression)


__all__ = [
    "Aggregate",
    "Binary",
    "DimFilter",
    "ENum",
    "EOp",
    "ERef",
    "Query",
    "SelectorError",
    "SeriesSpec",
    "to_query",
]
