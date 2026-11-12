"""PromQL and LogQL to NRQL for dashboard widgets (research note, section 5).

The coverage planner (`dashboards/query_tier.py`) decides which queries are convertible by
rewriting them and asking the alert translator's own handlers. This module reads the same shapes
and writes NRQL: selectors with an optional aggregation (tier 1), rate, irate, increase,
histogram_quantile, topk and LogQL rate (tier 2), arithmetic between those and numbers, and
count (tier 3). Labels and metric names go through `identifier`, literals through
`quote_string`; a variable becomes `{{name}}`. A shape the planner reads and this module cannot
write raises `Unconvertible`; a test keeps the two in step.
"""

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal

from importer.core.dashboard_nrql import check_raw_nrql
from importer.core.emit.nrql import identifier, quote_string
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.legend import Legend
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.nrql import (
    LOG_EVENT,
    MAX_FACETS,
    VAR_TOKEN,
    Bin,
    Leaf,
    Node,
    Num,
    cond_sql,
    facet_attributes,
    hoisted,
    interval_text,
    leaves,
    render,
    strip_outer,
)
from importer.sources.grafana.dashboard_convert.reuse import (
    BODY,
    COUNTER_SUFFIXES,
    MAX_EXPRESSION,
    NAME,
    QUOTED,
    Cond,
    Unconvertible,
    loki_query,
    outer,
    prom_conditions,
    selector,
)
from importer.sources.grafana.dashboards.query_tier import (
    COUNT_AGG,
    HISTOGRAM,
    NOT_PEELED,
    SUM_BY,
    TOPK,
    peel_math,
)
from importer.sources.grafana.dashboards.regex_matchers import PRESENT, RegexUse, mask
from importer.sources.grafana.promql_normalize import normalize_promql

MAX_NESTING = 20
MAX_OPERANDS = 50
MAX_ALIAS = 100
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31_536_000}
BUILTINS = {
    "__rate_interval": "VARBRATE",
    "__interval": "VARBINTERVAL",
    "__interval_ms": "VARBINTERVALMS",
    "__range": "VARBRANGE",
    "__range_s": "VARBRANGES",
    "__range_ms": "VARBRANGEMS",
}
RANGE_TOKENS = frozenset({"VARBRANGE", "VARBRANGES", "VARBRANGEMS"})
VARIABLE = re.compile(r"\$\{(\w+)(:[^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
WINDOW = r"(?:\d+[smhdwy])+|VARB[A-Z]+|VARV\d+"
RATE_CALL = re.compile(
    rf"(rate|irate|increase) ?\( ?({NAME}) ?(\{{{BODY}\}})? ?\[ ?({WINDOW}) ?\] ?\)"
)
NUMBER = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|VARV\d+")
COMPARISON = re.compile(r"==|!=|>=|<=|[<>%^]|\b(?:and|or|unless|bool)\b")
RANGE_ANY = re.compile(rf"\[ ?(?:{WINDOW}) ?\]")
LOKI_RATE = re.compile(r"\brate ?\(")
NR_AGGREGATE = {"sum": "sum", "avg": "average", "min": "min", "max": "max", "count": "count"}
QUANTILE = re.compile(r"0?\.\d+|[01](?:\.\d+)?")
MAX_CONSTANT = 200


@dataclass(frozen=True)
class QueryOptions:
    """What the converter needs to know about the dashboard and the widget around a query."""

    variables: dict[str, str | None]  # name -> constant value, or None for a real variable
    name_map: NameMap
    timeseries: bool  # the widget draws over time (line, area)
    bucket_seconds: int | None = None  # a fixed bucket the query or panel asked for
    intervals: frozenset[str] = frozenset()  # interval variables: a window the bucket replaces
    unavailable: frozenset[str] = frozenset()  # dashboard variables that did not convert
    approx_series: bool = False  # write sum/count of a gauge as average * cardinality (set by
    # convert_query for a dashboard widget after the exact paths have refused)


@dataclass(frozen=True)
class Statement:
    """The parts of one converted query, kept so a rewrite can rebuild it (promql_engine)."""

    select: str
    alias: str  # the ` AS '...'` text, or ""
    event: str
    where: tuple[str, ...]
    facet: tuple[str, ...]
    limit: int | None  # LIMIT n for a facet; None is LIMIT MAX
    timeseries: str  # ` TIMESERIES ...` text, or ""

    def render(self) -> str:
        query = f"SELECT {self.select}{self.alias} FROM {identifier_or_fail(self.event)}"  # noqa: S608
        if self.where:
            query += " WHERE " + " AND ".join(self.where)
        if self.facet:
            query += " FACET " + ", ".join(self.facet)
            query += f" LIMIT {self.limit}" if self.limit is not None else " LIMIT MAX"
        return query + self.timeseries


@dataclass
class Converted:
    nrql: str
    notes: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    numeric_variables: list[str] = field(default_factory=list)
    facet: list[str] = field(default_factory=list)
    statement: Statement | None = None


# --- text preparation ---


def tokenize(expr: str, opts: QueryOptions) -> tuple[str, list[str]]:
    """Variables become `VARV<n>`, built-ins `VARB...`; a constant is written in as its value."""
    names: list[str] = []

    def one(match: re.Match[str]) -> str:
        name = match[1] or match[3] or match[4]
        if match[2]:
            raise Unconvertible(r.C_SHAPE)  # a variable format such as ${x:csv}
        if name in BUILTINS:
            return BUILTINS[name]
        if name.startswith("__"):
            return "VARBOTHER"
        if name in opts.intervals:
            return BUILTINS["__interval"]
        if name in opts.unavailable:
            raise Unconvertible(r.C_VARIABLE_UNDEFINED)
        if name not in opts.variables:
            return match[0]
        value = opts.variables[name]
        if value is not None:
            if len(value) > MAX_CONSTANT or any(c in value for c in "\r\n\x00"):
                raise Unconvertible(r.C_SHAPE)
            return value
        if name not in names:
            names.append(name)
        return f"VARV{names.index(name)}"

    return VARIABLE.sub(one, expr), names


def _outside_strings(text: str, change: "re.Pattern[str]", replacement: str) -> str:
    """`change.sub(replacement, text)` on the parts of `text` that are not inside a string."""
    parts = re.split(f"({QUOTED})", text)
    return "".join(p if i % 2 else change.sub(replacement, p) for i, p in enumerate(parts))


def _mask(text: str) -> str:
    """The text with everything inside quotes or brackets blanked, so only depth 0 is visible."""
    out: list[str] = []
    depth, quote, escaped = 0, False, False
    for ch in text:
        if quote:
            quote = not (ch == '"' and not escaped)
            escaped = ch == "\\" and not escaped
            out.append(" ")
        elif ch == '"':
            quote = True
            out.append(" ")
        elif ch in "([{":
            out.append(ch if depth == 0 else " ")
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
            out.append(ch if depth == 0 else " ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


def _exponent(masked: str, sign: int) -> bool:
    start = sign - 1
    while start > 0 and (masked[start - 1].isalnum() or masked[start - 1] in "_."):
        start -= 1
    return NUMBER.fullmatch(masked[start:sign] + "0") is not None


def _split(text: str, masked: str) -> tuple[list[str], list[str]]:
    """Operands and the binary + - * / between them at depth 0."""
    if COMPARISON.search(masked):
        raise Unconvertible(r.C_SHAPE)
    parts: list[str] = []
    ops: list[str] = []
    start, previous = 0, ""
    for i, ch in enumerate(masked):
        if ch in "+-*/" and previous and (previous.isalnum() or previous in "_)]}"):
            if ch in "+-" and previous in "eE" and _exponent(masked, i):
                continue
            parts.append(text[start:i])
            ops.append(ch)
            start = i + 1
        if not ch.isspace():
            previous = ch
    parts.append(text[start:])
    return parts, ops


def _fold(operands: list[Node], ops: list[str]) -> Node:
    """Left to right, * and / before + and -."""
    nodes = [operands[0]]
    later: list[str] = []
    for op, node in zip(ops, operands[1:], strict=True):
        if op in "*/":
            nodes[-1] = Bin(op, nodes[-1], node)
        else:
            later.append(op)
            nodes.append(node)
    result = nodes[0]
    for op, node in zip(later, nodes[1:], strict=True):
        result = Bin(op, result, node)
    return result


def _window(token: str) -> tuple[str, int]:
    """(kind, seconds): kind is `seconds`, `range` ($__range) or `dynamic` (anything computed)."""
    if token.startswith(("VARB", "VARV")):
        return ("range" if token in RANGE_TOKENS else "dynamic"), 0
    total = sum(int(n) * UNIT_SECONDS[u] for n, u in re.findall(r"(\d+)([smhdwy])", token))
    return "seconds", total


# --- the parser ---


class _Parse:
    def __init__(self, opts: QueryOptions, names: list[str], regexes: dict[str, RegexUse]) -> None:
        self.opts = opts
        self.names = names
        self.regexes = regexes
        self.notes: list[str] = []
        self.metrics: list[str] = []
        self.numeric: list[str] = []
        self.limit: int | None = None
        self.var_labels: dict[str, list[str]] = {}  # variable -> labels it filters
        self.mapped = False

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    # -- expression level --

    def expr(self, text: str, language: str, depth: int = 0) -> Node:
        text = text.strip()
        if depth > MAX_NESTING or not text:
            raise Unconvertible(r.C_SHAPE)
        masked = _mask(text)
        stripped = masked.strip()
        if stripped.startswith("(") and stripped.endswith(")") and stripped.count("(") == 1:
            return self.expr(text[1:-1], language, depth + 1)
        parts, ops = _split(text, masked)
        if len(parts) > MAX_OPERANDS:
            raise Unconvertible(r.C_SHAPE)
        if len(parts) > 1:
            return _fold([self.expr(p, language, depth + 1) for p in parts], ops)
        if text[0] in "+-":
            inner = self.expr(text[1:], language, depth + 1)
            return inner if text[0] == "+" else Bin("-", Num("0"), inner)
        return self.atom(text, language, depth)

    def atom(self, text: str, language: str, depth: int) -> Node:
        if NUMBER.fullmatch(text):
            return self.number(text)
        if "VARB" in text and not RANGE_ANY.search(text):
            raise Unconvertible(r.C_BUILTIN)
        if language == "loki":
            return self.loki(text)
        return (
            self.topk(text, depth) or self.histogram(text) or self.count(text) or self.query(text)
        )

    def number(self, text: str) -> Num:
        found = VAR_TOKEN.fullmatch(text)
        if found is None:
            return Num(text)
        name = self.names[int(found[1])]
        self.numeric.append(name)
        return Num("{{" + name + "}}")

    # -- tier 2 and 3 wrappers --

    def topk(self, text: str, depth: int) -> Node | None:
        found = TOPK.fullmatch(text)
        if found is None:
            return None
        if not found[1].isdigit():
            raise Unconvertible(r.C_SHAPE)
        node = self.expr(found[2], "prometheus", depth + 1)
        if self.limit is None:
            self.limit = int(found[1])
        self.note(r.N_TOPK)
        return node

    def histogram(self, text: str) -> Node | None:
        found = HISTOGRAM.fullmatch(text)
        if found is None:
            return None
        quantile = re.match(r"histogram_quantile ?\( ?([^,]*),", text)
        if quantile is None or not QUANTILE.fullmatch(quantile[1].strip()):
            raise Unconvertible(r.C_SHAPE)
        percent = (Decimal(quantile[1].strip()) * 100).normalize()
        inner = found[1].strip()
        grouped = SUM_BY.fullmatch(inner)
        labels: list[str] = []
        if grouped is not None and not RATE_CALL.fullmatch(inner):
            labels = [x.strip() for x in (grouped[1] or grouped[3] or "").split(",") if x.strip()]
            if "le" not in labels:
                raise Unconvertible(r.C_SHAPE)
            inner = grouped[2].strip()
            labels = [label for label in labels if label != "le"]
        return self.bucket_percentile(inner, labels, f"{percent:f}")

    def bucket_percentile(self, inner: str, labels: list[str], percent: str) -> Leaf:
        call = RATE_CALL.fullmatch(inner)  # a bucket counter is only read through a rate call
        if call is None:
            raise Unconvertible(r.C_SHAPE)
        name, body = call[2], (call[3] or "")[1:-1]
        if not self.is_bucket(name):
            raise Unconvertible(r.C_SHAPE)
        event, metric = self.metric(name)
        where = self.where(body)
        core = f"bucketPercentile({metric}, {percent})"
        return Leaf(core, where=where, facet=self.facet(labels), event=event, filterable=False)

    def count(self, text: str) -> Node | None:
        if not COUNT_AGG.match(text):
            return None
        self.note(r.N_COUNT)
        return self.query(text)

    # -- leaves --

    def query(self, text: str) -> Node:
        agg, labels, inner = outer(text, "avg|sum|min|max|count", r.C_SHAPE)
        inner = inner.strip()
        call = RATE_CALL.fullmatch(inner)
        if call is not None:
            return self.rate_leaf(agg, labels, call)
        name, body = selector(inner, r.C_SHAPE)
        if self.is_counter(name):
            raise Unconvertible(r.C_SHAPE)
        event, metric = self.metric(name)
        func = NR_AGGREGATE[agg or "avg"]
        if agg == "count":  # counts samples, not series
            if not self.opts.approx_series:
                raise Unconvertible(r.C_GAUGE_SERIES)
            return self.series_leaf(agg, labels, body, event, metric)
        if agg == "sum":  # a New Relic sum() would add every sample, not the value Grafana shows
            if not self.one_series(name, labels, body):
                if not self.opts.approx_series:
                    raise Unconvertible(r.C_GAUGE_SERIES)
                return self.series_leaf(agg, labels, body, event, metric)
            self.note(r.N_SUM_GAUGE)
            func = "average" if self.opts.timeseries else "latest"
        if agg is None:
            func = "latest" if not self.opts.timeseries else "average"
        return Leaf(
            f"{func}({metric})",
            where=self.where(body),
            facet=self.facet(labels),
            event=event,
            kept=agg is None,
        )

    def series_leaf(self, agg: str, labels: list[str], body: str, event: str, metric: str) -> Leaf:
        """Dashboards only: sum is average * cardinality, count is cardinality (approximate).

        Charts only. A single value would have to be read over `SINCE 5 minutes ago`, which the
        dashboard query check rejects (the time picker owns the window), so it stays refused.
        """
        if not self.opts.timeseries:
            raise Unconvertible(r.C_GAUGE_SERIES)
        if agg == "sum":
            core, filterable = f"(average({metric}) * cardinality({metric}))", False
            self.note(r.N_SUM_CARDINALITY)
        else:
            core, filterable = f"cardinality({metric})", True
            self.note(r.N_COUNT_CARDINALITY)
        return Leaf(
            core,
            where=self.where(body),
            facet=self.facet(labels),
            event=event,
            filterable=filterable,
            approx=True,
        )

    def check_stored_type(self, name: str) -> None:
        """Note a hinted type that New Relic's name rule on remote write would get the other way."""
        kind = self.opts.name_map.types.get(name)
        if kind is None:
            return
        stored_as_count = name.endswith(COUNTER_SUFFIXES)
        if kind == "gauge" and stored_as_count:
            self.note(r.n_type_override(name, "gauge"))
        elif kind == "counter" and not stored_as_count:
            self.note(r.n_type_override(name, "counter"))

    def is_counter(self, name: str) -> bool:
        """The hinted type when there is one, else the name suffix."""
        kind = self.opts.name_map.types.get(name)
        if kind is None:
            return name.endswith(COUNTER_SUFFIXES)
        return kind != "gauge"

    def is_bucket(self, name: str) -> bool:
        kind = self.opts.name_map.types.get(name)
        return kind == "histogram_bucket" or (kind is None and name.endswith("_bucket"))

    def one_series(self, name: str, labels: list[str], body: str) -> bool:
        """True when the name map says these labels name one series and the query fixes them."""
        needed = self.opts.name_map.series.get(name)
        if needed is None:
            return False
        pinned = {
            c.key
            for c in prom_conditions(body, r.C_SHAPE)
            if c.kind == "eq"
            and not c.negate
            and len(c.values) == 1
            and not VAR_TOKEN.search(c.values[0])
        }
        return set(needed) <= pinned | set(labels)

    def rate_leaf(self, agg: str | None, labels: list[str], call: re.Match[str]) -> Leaf:
        function, name, body, window = call[1], call[2], (call[3] or "")[1:-1], call[4]
        if agg == "count":
            raise Unconvertible(r.C_SHAPE)
        if agg in ("avg", "min", "max"):  # one sample's delta over a whole bucket: too low
            raise Unconvertible(r.C_AGG_RATE)
        if self.opts.name_map.is_gauge(name):
            raise Unconvertible(r.C_RATE_OF_GAUGE)
        if not self.is_counter(name):  # same refusal as the alert path: a gauge's total is no rate
            raise Unconvertible(r.C_COUNTER_NAME)
        event, metric = self.metric(name)
        core = f"{NR_AGGREGATE[agg or 'sum']}({metric})"
        kind, seconds = _window(window)
        prefix, suffix = "rate(", ", 1 second)"
        if function == "increase":
            if kind == "seconds":
                suffix = f", {interval_text(seconds)})"
            else:
                prefix, suffix = "", ""
                if kind == "dynamic":
                    self.note(r.N_INCREASE)
        elif kind == "dynamic":
            self.note(r.N_RATE_WINDOW)
        if function == "irate":
            self.note(r.N_IRATE)
        if self.opts.timeseries and not self.opts.bucket_seconds:
            self.note(r.N_RATE_BUCKET)
        return Leaf(
            core,
            prefix,
            suffix,
            where=self.where(body),
            facet=self.facet(labels),
            event=event,
            kept=agg is None,
        )

    def loki(self, text: str) -> Node:
        plain = _outside_strings(text, RANGE_ANY, "[5m]")
        rated = any(LOKI_RATE.search(p) for p in _plain(plain))
        plain = _outside_strings(plain, LOKI_RATE, "count_over_time(")
        query = loki_query(plain, r.C_SHAPE)
        where = tuple(cond_sql(c, self.names) for c in query.conditions)
        if not rated:
            self.note(r.N_RATE_WINDOW)
        return Leaf(
            "count(*)",
            "rate(" if rated else "",
            ", 1 second)" if rated else "",
            where=where,
            facet=self.facet(query.group_by),
            event=LOG_EVENT,
            kept=not plain.lstrip().startswith("sum"),
        )

    # -- pieces --

    def metric(self, name: str) -> tuple[str, str]:
        """(event, attribute) for a Prometheus metric name, through the name map."""
        if VAR_TOKEN.search(name) or "VARB" in name:
            raise Unconvertible(r.C_VAR_NAME)
        self.metrics.append(name)
        self.check_stored_type(name)
        entry = self.opts.name_map.metrics.get(name)
        if entry is not None:
            self.mapped = True
        event, attribute = (entry.event, entry.attribute) if entry else ("Metric", name)
        try:
            return event, metric_identifier(attribute)
        except ValueError:
            raise Unconvertible(r.C_NAME) from None

    def where(self, body: str) -> tuple[str, ...]:
        if "VARB" in body:
            raise Unconvertible(r.C_BUILTIN)
        conds = [self.regex(c) for c in prom_conditions(body, r.C_SHAPE)]
        for cond in conds:
            for value in cond.values:
                for token in VAR_TOKEN.findall(value):
                    labels = self.var_labels.setdefault(self.names[int(token)], [])
                    labels.append(cond.key)
        return tuple(cond_sql(c, self.names) for c in conds)

    def regex(self, cond: Cond) -> Cond:
        """A condition on a masked word becomes RLIKE, or a presence test for `.+`."""
        use = self.regexes.get(cond.values[0]) if cond.kind == "eq" else None
        if use is None:
            return cond
        if use.pattern == PRESENT:
            return Cond(cond.key, "absent", negate=not cond.negate)
        return Cond(cond.key, "rlike", (use.pattern,), cond.negate)

    def facet(self, labels: list[str]) -> tuple[str, ...]:
        if any(VAR_TOKEN.search(label) for label in labels):
            raise Unconvertible(r.C_VAR_NAME)
        return tuple(facet_attributes(labels))


def _plain(text: str) -> list[str]:
    """The parts of `text` outside strings."""
    return re.split(f"({QUOTED})", text)[::2]


# --- assembly ---


def _alias(legend: Legend) -> str:
    if not legend.alias:
        return ""
    return f" AS {quote_string(printable(legend.alias, MAX_ALIAS))}"


def _facet_list(
    found: list[Leaf], legend: Legend, repeat: list[str], notes: list[str]
) -> list[str]:
    first = found[0]
    labels = list(first.facet)
    unmatched = False
    for label in legend.labels:
        if first.kept:
            labels.append(identifier_or_fail(label))
        elif identifier_or_fail(label) not in labels:
            unmatched = True
    labels += [identifier_or_fail(x) for x in repeat]
    if unmatched:
        notes.append(r.N_LEGEND_LABEL)
    labels = list(dict.fromkeys(labels))
    if len(labels) > MAX_FACETS:
        labels = labels[:MAX_FACETS]
        notes.append(r.N_FACET_LIMIT)
    return labels


RECORDING_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")


def metric_identifier(name: str) -> str:
    """A metric name as an NRQL attribute. Recording rules (`job:x:rate5m`) keep their colons in
    New Relic and NRQL reads them in backticks (measured with the telemetry rig)."""
    if ":" in name and RECORDING_NAME.fullmatch(name):
        return f"`{name}`"
    return identifier(name)


def identifier_or_fail(name: str) -> str:
    try:
        return identifier(name)
    except ValueError:
        raise Unconvertible(r.C_NAME) from None


def _check_shape(found: list[Leaf]) -> None:
    if not found:
        raise Unconvertible(r.C_SHAPE)
    if len({leaf.event for leaf in found}) > 1:
        raise Unconvertible(r.C_EVENTS)
    if len({frozenset(leaf.facet) for leaf in found}) > 1:
        raise Unconvertible(r.C_SHAPE)


def convert_query(
    expr: str,
    family: str,
    opts: QueryOptions,
    legend: Legend | None = None,
    repeat_var: str | None = None,
) -> Converted:
    """NRQL for one Grafana query, or `Unconvertible`. `family` is prometheus or loki.

    The shapes the regular-expression reader writes come first, unchanged; a query it refuses
    goes to the engine (promql_engine), which writes only rewrites it can prove and otherwise
    leaves the original refusal in place.
    """
    if family == "prometheus":
        expr = normalize_promql(expr)
    try:
        return convert_classic(expr, family, opts, legend, repeat_var)
    except Unconvertible as refused:
        if family != "prometheus":
            raise
        from importer.sources.grafana.dashboard_convert.promql_engine import convert_with_engine

        done = convert_with_engine(expr, opts, legend, repeat_var)
        if done is not None:
            return done
        if refused.reason == r.C_GAUGE_SERIES and not opts.approx_series:
            try:
                return convert_classic(
                    expr, family, replace(opts, approx_series=True), legend, repeat_var
                )
            except Unconvertible:
                raise refused from None
        raise


def convert_classic(
    expr: str,
    family: str,
    opts: QueryOptions,
    legend: Legend | None = None,
    repeat_var: str | None = None,
) -> Converted:
    """The original reader: `Unconvertible` for anything outside its shapes."""
    legend = legend or Legend(None, (), False)
    if len(expr) > MAX_EXPRESSION:
        raise Unconvertible(r.C_SHAPE)
    peeled = peel_math(expr) if family == "prometheus" else NOT_PEELED
    expr = peeled.inner or expr
    text, names = tokenize(" ".join(expr.split()), opts)
    text, regexes = mask(text) if family == "prometheus" else (text, {})
    parse = _Parse(opts, names, regexes)
    node = parse.expr(text, family)
    found = leaves(node)
    _check_shape(found)
    notes = parse.notes
    common = hoisted(found)
    repeat = list(dict.fromkeys(parse.var_labels.get(repeat_var or "", [])))
    if repeat_var:
        notes.append(r.N_REPEAT if repeat else r.N_REPEAT_FLAT)
    labels = _facet_list(found, legend, repeat, notes)
    first = found[0]
    if first.kept and not labels:
        notes.append(r.N_MERGED)
    if legend.lost_text:
        notes.append(r.N_LEGEND_TEXT)
    if parse.limit is not None and not labels:
        notes.append(r.N_TOPK_FLAT)
    if parse.mapped:
        notes.append(r.N_NAME_MAP)
    if not opts.timeseries and any(not leaf.core.startswith("latest(") for leaf in found):
        notes.append(r.N_INSTANT)
    if any(leaf.approx for leaf in found) and r.N_COUNT_CARDINALITY in notes:
        notes = [n for n in notes if n != r.N_COUNT]
    select = strip_outer(render(node, common))
    select = f"{peeled.prefix}{select}{peeled.suffix}"
    statement = _statement(select, first, common, labels, parse, legend)
    nrql = statement.render()
    try:
        check_raw_nrql(nrql)
    except ValueError:
        raise Unconvertible(r.C_NRQL) from None
    used = [n for n in names if "{{" + n + "}}" in nrql]
    return Converted(
        nrql,
        list(dict.fromkeys(notes)),
        list(dict.fromkeys(parse.metrics)),
        used,
        list(dict.fromkeys(parse.numeric)),
        labels,
        statement,
    )


def _statement(
    select: str, first: Leaf, common: list[str], labels: list[str], parse: _Parse, legend: Legend
) -> Statement:
    seconds = parse.opts.bucket_seconds
    series = ""
    if parse.opts.timeseries:
        series = f" TIMESERIES {seconds} SECONDS" if seconds else " TIMESERIES AUTO"
    return Statement(
        select, _alias(legend), first.event, tuple(common), tuple(labels), parse.limit, series
    )


LOG_LINES = 200


def convert_log_lines(expr: str, opts: QueryOptions) -> Converted:
    """A raw LogQL stream (`{app="x"} |= "error"`) as the lines of a log table."""
    text, names = tokenize(" ".join(expr.split()), opts)
    if not text.startswith("{") or len(text) > MAX_EXPRESSION:
        raise Unconvertible(r.C_SHAPE)
    query = loki_query(f"count_over_time({text} [5m])", r.C_SHAPE)
    where = [cond_sql(c, names) for c in query.conditions]
    nrql = f"SELECT timestamp, message FROM {LOG_EVENT}"  # noqa: S608
    nrql += (" WHERE " + " AND ".join(where) if where else "") + f" LIMIT {LOG_LINES}"
    try:
        check_raw_nrql(nrql)
    except ValueError:
        raise Unconvertible(r.C_NRQL) from None
    used = [n for n in names if "{{" + n + "}}" in nrql]
    return Converted(nrql, [r.N_LOGS], [], used)
