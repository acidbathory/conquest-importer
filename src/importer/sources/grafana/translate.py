"""Grafana alert rules to IR.

One IR monitor per rule. A rule is a query, an optional resample and reduce, and one threshold
(or one classic condition), or a rule-file rule whose query holds the comparison. The query
converts when it is a plain Prometheus selector with one optional aggregation, a counter in
`rate`/`increase`, a ratio, `absent`, or a plain Loki `count_over_time`. The extra PromQL forms
live in `rules2/promql.py` and register in `PROM_FORMS`. Everything else is manual with a fixed
reason. Reasons never contain text from the rule: they name fixed vocabulary and parsed numbers
only, except one note that lists the folder, group and labels when each is short, plain and not
secret-looking (`rules2/fields.py`). Text that reaches the NRQL does so through the IR and the
shared escaping. `translate` never raises.

Neither the New Relic metric names nor the label attribute names are confirmed against live data,
so every conversion is approximate and says so.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from importer.core.emit.common import MAX_AGGREGATION_WINDOW_SECONDS, MAX_DURATION_SECONDS
from importer.core.emit.nrql import identifier
from importer.core.ids import source_id
from importer.core.ir import (
    Aggregation,
    Confidence,
    CountFilter,
    CountSignal,
    EmitOptions,
    HeartbeatSignal,
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
from importer.sources.grafana.models import GfNode, GrafanaRule
from importer.sources.grafana.notifications.promrules import split_comparison
from importer.sources.grafana.rules2 import fields

MIN_WINDOW_SECONDS = 60
MAX_EXPRESSION = 10_000
MAX_MATCHERS = 50
MAX_ALTERNATIVES = 100
EXPRESSION_UIDS = frozenset({"__expr__", "-100"})  # "-100" is the legacy spelling
OPERATORS = {
    "gt": Operator.ABOVE,
    "gte": Operator.ABOVE_OR_EQUALS,
    "lt": Operator.BELOW,
    "lte": Operator.BELOW_OR_EQUALS,
    "eq": Operator.EQUALS,
    "ne": Operator.NOT_EQUALS,
}
EMPTY_SENSITIVE = (Operator.BELOW, Operator.BELOW_OR_EQUALS, Operator.EQUALS)
RANGE_KINDS = frozenset(
    {"within_range", "outside_range", "within_range_included", "outside_range_included"}
)
# Grafana reducer -> time aggregation; count and median have no New Relic aggregation
REDUCERS = {
    "last": Aggregation.AVG,
    "mean": Aggregation.AVG,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
    "sum": Aggregation.SUM,
}
PROM_AGGREGATIONS = {
    "avg": Aggregation.AVG,
    "sum": Aggregation.SUM,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
}
COUNTER_SUFFIXES = ("_total", "_count", "_sum", "_bucket")
# data source types that are known and not converted -> the word shown in the reason
OTHER_DATASOURCES = {
    "cloudwatch": "cloudwatch",
    "elasticsearch": "elasticsearch",
    "grafana-azure-monitor-datasource": "azure monitor",
    "mysql": "sql",
    "postgres": "sql",
    "grafana-postgresql-datasource": "sql",
    "mssql": "sql",
    "influxdb": "influxdb",
    "graphite": "graphite",
    "tempo": "tempo",
    "jaeger": "jaeger",
    "zipkin": "zipkin",
    "stackdriver": "google cloud monitoring",
    "testdata": "testdata",
    "grafana-testdata-datasource": "testdata",
    "grafana-pyroscope-datasource": "pyroscope",
}
UID_HINTS = (("loki", "loki"), ("logs", "loki"), ("prom", "prometheus"), ("mimir", "prometheus"))

# manual reasons
R_UNREADABLE = "grafana rule could not be read (a field has an unexpected type or value)"
R_RANGE_MISSING = (
    "grafana query time range is missing or not a positive whole number of seconds, so the "
    "window to reduce over is unknown"
)
R_AGG_MIX = (
    "promql aggregation {agg}() across series with reduce function {reducer} cannot be one New "
    "Relic aggregation"
)
R_LOKI_REDUCER = (
    "logql range query reduced by something other than last cannot be one New Relic count"
)
R_REDUCE_MODE = (
    "grafana reduce mode replaces or rejects non-numeric values, which changes when the rule fires"
)
R_RECORD = "recording rule: it writes a metric and has no alert condition"
R_DS_MANAGED = "data source-managed rule (Mimir or Loki ruler), not a Grafana-managed rule"
R_NO_COMPARISON = (
    "data source-managed rule: the query has no comparison against a number, so there is no "
    "threshold to convert"
)
R_COMPARE_AND_NODE = (
    "promql expression compares inside the query and the rule also has a threshold node, so the "
    "two cannot be one New Relic threshold"
)
R_COUNTER_NAME = (
    "promql counter function on a metric whose name does not end in _total, _count or _bucket, "
    "so New Relic may not store it as a delta count"
)
R_COUNTER_AGG = (
    "promql counter rate aggregated with {agg}(); only sum() of a rate or increase converts"
)
R_COUNTER_REDUCER = (
    "promql counter rate reduced over time by {reducer}; only the last value converts"
)
R_COUNTER_RANGE = (
    "promql counter range of {seconds}s is not a whole number of minutes, so it cannot be one "
    "New Relic aggregation window"
)
R_RATIO_SHAPE = (
    "promql ratio: both sides must be the same aggregation of different metrics with the same "
    "labels, grouping and range; anything else needs filter() in one query"
)
R_RATIO_AGG = "promql ratio: {agg}() values; only avg() of gauges or sum() of counters converts"
R_ABSENT_THRESHOLD = (
    "promql absent() returns 1 when the series is missing, and the grafana threshold does not "
    "fire on 1"
)
R_MATH_QUERIES = (
    "grafana pipeline has a math expression that combines {count} queries, and a New Relic "
    "condition reads one query"
)
R_NO_NODE = "grafana pipeline: the condition does not point at a node of the rule"
R_SHAPE = "grafana pipeline is not one query, an optional reduce and one threshold"
R_MATH = "grafana pipeline has a math expression"
R_CLASSIC = "grafana pipeline has a classic condition"
R_SQL = "grafana pipeline has an SQL expression"
R_RESAMPLE = "grafana pipeline has a resample expression"
R_MIXED = "grafana pipeline queries more than one data source"
R_MULTI = "grafana pipeline has more than one query"
R_RANGE = "grafana threshold range (within or outside) has no single New Relic threshold"
R_OPERATOR = "grafana threshold operator is not supported"
R_VALUE = "grafana threshold value is not one finite number"
R_REDUCER = "grafana reduce function is not one of last, mean, min, max or sum"
R_DATASOURCE = "data source type {label} is not supported; only prometheus and loki convert"
R_AGG_WINDOW = (
    "grafana aggregation window {seconds}s is longer than the {limit}s New Relic evaluates"
)
R_PENDING = "grafana pending period {seconds}s is longer than the {limit}s New Relic allows"
PROM_NOT_SIMPLE = (
    "promql expression is not a simple metric selector with one optional avg, sum, min or max"
)
PROM_MATCHER = (
    "promql label matcher is not supported (only = and != with a string, and =~ or !~ with "
    "plain words joined by |, or a plain word with .* before or after)"
)
PROM_COUNTER = "promql metric name looks like a counter (_total, _count, _sum or _bucket)"
PROM_NAME = (
    "promql metric or label name cannot be an NRQL attribute name (a character NRQL rejects)"
)
LOKI_NOT_SIMPLE = "logql expression is not a count_over_time of a stream selector with line filters"
LOKI_MATCHER = (
    "logql label matcher is not supported (only = and != with a string, and =~ or !~ with "
    "plain words joined by |, or a plain word with .* before or after)"
)
LOKI_PARSER = "logql uses a parser stage or unwrap"
LOKI_FUNCTION = "logql uses a function other than count_over_time"
LOKI_REGEX = "logql uses a regular-expression line filter that is not a plain word"
LOKI_WILDCARD = "logql line filter text contains a wildcard character (* or %)"

# notes on a converted rule
N_INFERRED = (
    "the data source type is not in the rule and was inferred from its uid or the query; "
    "this is not confirmed"
)
N_METRIC = (
    "the metric must already reach New Relic (Prometheus remote write or OTLP); its New Relic "
    "name and label attributes are not confirmed, so check them with a query before enabling"
)
N_LOGS = (
    "the Loki logs must be forwarded to New Relic; the New Relic attribute names for Loki labels "
    "and for the log line (message) are not confirmed"
)
N_PER_SERIES = (
    "Grafana evaluates each series on its own; this query combines every series unless you "
    "add a FACET on a label"
)
N_NEGATION = (
    "a != or !~ matcher also matches series without that label in Prometheus; the New Relic "
    "query may differ for rows without the attribute"
)
N_LAST = (
    "Grafana takes the last sample of each series; New Relic averages the samples in each "
    "window, which differs from it"
)
N_PENDING = (
    "the pending period becomes the threshold duration; New Relic restarts the count when a "
    "window has no data, so it may differ"
)
N_ROUNDED_FOR = (
    "the pending period of {seconds}s is not a whole number of {window}s windows, so it is "
    "rounded up to {duration}s and may differ"
)
N_INTERVAL_UNKNOWN = (
    "the evaluation interval is not in this read of the rule, so the 60s window is a default "
    "that may differ"
)
N_ROUNDED_WINDOW = (
    "the window of {seconds}s is rounded up to a whole minute, {window}s, so it may differ"
)
N_RANGE_USED = (
    "the query time range is used as the aggregation window, because the reducer looks back over "
    "it; New Relic windows are separate, so it may differ"
)
N_RANGE_IGNORED = (
    "the query time range is ignored, because the rule takes only the last value; the window is "
    "the evaluation interval, so it may differ"
)
N_DROPNN = (
    "the reduce mode drops non-numeric values; New Relic ignores empty values too, so it may differ"
)
N_PAUSED_UNKNOWN = (
    "the paused flag has a value the importer does not understand, so the rule is treated as "
    "paused and the condition is created disabled; it must be confirmed before you enable it"
)
N_COPIES = "{count} identical copies of this rule were collapsed into the one kept"
N_DUP_UID = "another rule has the same uid but its content differs from this one, so both were kept"
N_LOKI_WINDOW = (
    "Grafana looks at the last {range}s at every evaluation; New Relic evaluates separate "
    "{window}s windows, so it may differ"
)
N_COUNT_EMPTY = (
    "a window with no logs is no row in New Relic, not a count of 0, so a below or equals "
    "threshold may differ"
)
N_PAUSED = (
    "the rule was paused in Grafana; the condition is created disabled and must be confirmed "
    "before you enable it"
)
N_KEEP_FIRING = "keep firing for was ignored: New Relic has no hold after a breach ends"
N_RECOVERY = (
    "the recovery threshold was ignored: New Relic closes a violation when the value stops "
    "breaching"
)
N_NODATA_ALERTING = (
    "no data state Alerting becomes a loss-of-signal notification after {minutes} minutes "
    "without data, which may differ"
)
N_NODATA_DEFAULT = (
    "on no data Grafana raises a separate no-data alert; New Relic does not, so it may differ"
)
N_NODATA_OK = (
    "on no data Grafana sets the state to normal; New Relic has no such state, so it may "
    "differ: an open violation can stay open until data returns"
)
N_NODATA_KEEP = "on no data Grafana keeps the last state, which has no equivalent in New Relic"
N_ERROR_STATE = (
    "when the query fails Grafana sets the error state; New Relic has no query-error state, "
    "so it may differ"
)
N_RULE_FILE = (
    "this rule comes from a Prometheus-style rule file (Mimir or Loki ruler); the threshold is "
    "the comparison in its query, and the rule has no Grafana pipeline"
)
N_COUNTER = (
    "New Relic stores a Prometheus counter as the change in each interval (the type is guessed "
    "from a name ending _total, _count or _bucket), so sum() over the window is the increase over "
    "that window; Prometheus extrapolates rate() and increase() to the ends of the range, so the "
    "values may differ a little"
)
N_RATE_DIVISION = (
    "the rate is sum() divided by the {seconds}s window, written as arithmetic because rate() is "
    "not documented as usable in an alert condition; check the value against Grafana, it may "
    "differ"
)
N_RATIO_ZERO = (
    "a window in which the denominator is 0 or empty may give no value in New Relic, where "
    "Prometheus gives NaN or no sample, so it may differ"
)
N_ABSENT = (
    "absent() becomes a loss-of-signal condition: it opens a violation when New Relic receives "
    "no data points for the metric for the duration, and it only works once the metric has "
    "arrived at least once; like absent() it fires only when no matching series reports at all, so "
    "one missing series among several does not alert, which may differ from the rule's intent"
)
N_CLASSIC = (
    "the classic condition was read as a reduce and a threshold on its one query; a classic "
    "condition drops labels, so it may differ when the query returns several series"
)
N_RESAMPLE = (
    "a resample step was ignored: New Relic sets the step with the aggregation window, so the "
    "values may differ"
)
N_ROUTING = "notifications (contact points and notification policies) must be configured manually"


class Fail(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- label matchers (shared by PromQL and LogQL) ---

LABEL = r"[A-Za-z_][A-Za-z0-9_]*"
LABEL_LIST = rf"\(\s*{LABEL}(?:\s*,\s*{LABEL})*\s*,?\s*\)"
QUOTED = r'"(?:[^"\\]|\\.)*"'
BODY = rf'(?:[^{{}}"]|{QUOTED})*'
MATCHER = re.compile(rf'\s*({LABEL})\s*(=~|!~|!=|=)\s*"((?:[^"\\]|\\.)*)"\s*(,|$)', re.DOTALL)
STRICT_VALUE = re.compile(r'(?:[^"\\\x00-\x1f\x7f]|\\["\\])*')
SAFE_WORD = r"[A-Za-z0-9_:/ -]+"
WORD = re.compile(SAFE_WORD)
PREFIX = re.compile(rf"({SAFE_WORD})\.\*")
SUFFIX = re.compile(rf"\.\*({SAFE_WORD})")
BOTH = re.compile(rf"\.\*({SAFE_WORD})\.\*")


@dataclass(frozen=True)
class Match:
    key: str
    kind: str  # eq, in, like or absent
    values: tuple[str, ...]
    negate: bool = False


def _unescape(value: str) -> str:
    return re.sub(r'\\(["\\])', r"\1", value)


def _regex(value: str, fail: str) -> tuple[str, tuple[str, ...]]:
    """Kind and values of a regex that is plain words, a prefix or a suffix; else raise."""
    if WORD.fullmatch(value):
        return "eq", (value,)
    parts = value.split("|")
    if 1 < len(parts) <= MAX_ALTERNATIVES and all(WORD.fullmatch(p) for p in parts):
        return "in", tuple(dict.fromkeys(parts))
    for pattern, wrap in ((BOTH, "*{}*"), (PREFIX, "{}*"), (SUFFIX, "*{}")):
        found = pattern.fullmatch(value)
        if found:
            return "like", (wrap.format(found[1]),)
    raise Fail(fail)


def _match(key: str, op: str, value: str, fail: str, *, absent_ok: bool) -> Match:
    if key == "__name__":
        raise Fail(fail)
    if op in ("=", "!="):
        if value == "":
            if op == "=" and absent_ok:
                return Match(key, "absent", ())
            raise Fail(fail)
        return Match(key, "eq", (value,), negate=op == "!=")
    kind, values = _regex(value, fail)
    return Match(key, kind, values, negate=op == "!~")


def parse_matchers(body: str, fail: str, not_simple: str, *, absent_ok: bool) -> list[Match]:
    found: list[Match] = []
    position = 0
    while body[position:].strip():
        item = MATCHER.match(body, position)
        if item is None or len(found) >= MAX_MATCHERS:
            raise Fail(not_simple)
        key, op, raw, _ = item.groups()
        if not STRICT_VALUE.fullmatch(raw):
            raise Fail(fail)
        found.append(_match(key, op, _unescape(raw), fail, absent_ok=absent_ok))
        position = item.end()
    return found


def tag_filter(m: Match) -> TagFilter:
    if m.kind == "absent":
        return TagFilter(key=m.key, absent=True)
    if m.kind == "in":
        return TagFilter(key=m.key, values=list(m.values), negate=m.negate)
    return TagFilter(key=m.key, value=m.values[0], negate=m.negate, wildcard=m.kind == "like")


def _count_filter(m: Match) -> CountFilter:
    if m.kind == "in":
        return CountFilter(key=m.key, value="", values=list(m.values), negate=m.negate)
    return CountFilter(key=m.key, value=m.values[0], negate=m.negate, wildcard=m.kind == "like")


# --- shared parsing helpers ---


def _skeleton(expr: str) -> str:
    """The expression without string contents and label bodies, for spotting features."""
    text = re.sub(QUOTED, '""', expr)
    text = re.sub(r"\{[^{}]*\}", "{}", text)
    return re.sub(r"\bby\s*\([^)]*\)", "", text)


def _first_feature(skeleton: str, table: list[tuple[re.Pattern[str], str]]) -> str | None:
    return next((reason for pattern, reason in table if pattern.search(skeleton)), None)


def _labels(text: str | None) -> list[str]:
    return list(dict.fromkeys(re.findall(LABEL, text or "")))


def nrql_attribute(name: str) -> str:
    try:
        return identifier(name).strip("`")
    except ValueError:
        raise Fail(PROM_NAME) from None


def split_outer(expr: str, aggregators: str) -> tuple[str | None, list[str], str]:
    """Split `agg [by (..)] (inner) [by (..)]` into aggregator, group labels and inner text."""
    wrapper = re.fullmatch(
        rf"\s*({aggregators})\s*(?:by\s*({LABEL_LIST}))?\s*\((.*?)\)\s*(?:by\s*({LABEL_LIST}))?\s*",
        expr,
        re.DOTALL,
    )
    if wrapper is None:
        return None, [], expr
    agg, first, inner, second = wrapper.groups()
    if first and second:
        raise Fail("")
    return agg, _labels(first or second), inner


# --- Prometheus ---

PROM_FEATURES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\boffset\b"), "promql uses offset"),
    (re.compile(r"\[[^\]]*:[^\]]*\]"), "promql uses a subquery"),
    (
        re.compile(r"\bgroup_(?:left|right)\b|\b(?:on|ignoring)\s*\("),
        "promql uses vector matching (on, ignoring, group_left or group_right)",
    ),
    (re.compile(r"\b(?:topk|bottomk|limitk|limit_ratio)\b"), "promql uses topk or bottomk"),
    (re.compile(r"\bhistogram_\w+\s*\("), "promql uses histogram_quantile"),
    (re.compile(r"\babsent(?:_over_time)?\s*\("), "promql uses absent()"),
    (re.compile(r"\w+_over_time\s*\("), "promql uses an _over_time function"),
    (
        re.compile(
            r"\b(?:rate|irate|increase|delta|idelta|deriv|predict_linear|changes|resets)\s*\("
        ),
        "promql uses rate, increase or another range function",
    ),
    (re.compile(r"\bwithout\b"), "promql uses without()"),
    (
        re.compile(r"==|!=|>=|<=|[<>+\-*/%^]|\b(?:and|or|unless|bool)\b"),
        "promql uses a binary operator or comparison",
    ),
]
PROM_SELECTOR = re.compile(rf"\s*([A-Za-z_:][A-Za-z0-9_:]*)\s*(?:\{{({BODY})\}})?\s*")


@dataclass(frozen=True)
class Ctx:
    """What the data source handlers need to know about the rule around the query."""

    reducer: str  # the Grafana reducer (last when the rule has none)
    instant: bool  # an instant query has one point, so the reducer cannot look back
    range_seconds: int | None  # relativeTimeRange.from, when it is a positive whole number
    threshold: Threshold | None = None  # the rule's own test, for forms that fix the value (absent)
    node: bool = True  # the rule has a threshold node (a Grafana rule); a rule-file rule has none


# the dashboard planner imports these under their old names
_Ctx = Ctx
_Fail = Fail


# an across-series aggregate and a reducer that one New Relic aggregation expresses exactly
EXACT_AGGREGATES = frozenset({("avg", "last"), ("avg", "mean"), ("max", "max"), ("min", "min")})


@dataclass
class Built:
    kind: SignalKind
    window: int | None  # a window the query itself fixes (a Loki range); None = the interval
    metric: MetricSignal | None = None
    count: CountSignal | None = None
    notes: list[str] = field(default_factory=list)
    threshold: Threshold | None = None  # a form that fixes its own threshold (absent)
    heartbeat: HeartbeatSignal | None = None
    minimum_duration: int = 0  # seconds the query itself asks the condition to last


def looked_back(ctx: Ctx) -> str:
    """The reducer that really applies: an instant query has one point, so it is `last`."""
    return "last" if ctx.instant else ctx.reducer


# Query forms beyond the plain selector (counters, ratios, absent). Each returns None when the
# expression is not its form, so the checks below still give their usual reason. Filled by
# `importer.sources.grafana.rules2`; registering twice adds nothing.
PromForm = Callable[[str, "Ctx"], "Built | None"]
PROM_FORMS: list[PromForm] = []


def register_prom_form(form: PromForm) -> None:
    if form not in PROM_FORMS:
        PROM_FORMS.append(form)


def time_window(ctx: Ctx, reducer: str) -> tuple[int | None, list[str]]:
    """The window a gauge query fixes (None: the interval) and the notes about the reducer."""
    if reducer == "last":
        return None, [N_RANGE_IGNORED]
    if ctx.range_seconds is None:
        raise Fail(R_RANGE_MISSING)
    return ctx.range_seconds, [N_RANGE_USED]


def _prometheus(expr: str, ctx: Ctx) -> Built:
    if len(expr) > MAX_EXPRESSION:
        raise Fail(PROM_NOT_SIMPLE)
    for form in PROM_FORMS:
        built = form(expr, ctx)
        if built is not None:
            return built
    if ctx.node and split_comparison(expr) is not None:
        raise Fail(R_COMPARE_AND_NODE)
    feature = _first_feature(_skeleton(expr), PROM_FEATURES)
    if feature:
        raise Fail(feature)
    try:
        aggregator, group_by, inner = split_outer(expr, "avg|sum|min|max")
    except Fail:
        raise Fail(PROM_NOT_SIMPLE) from None
    selector = PROM_SELECTOR.fullmatch(inner)
    if selector is None:
        raise Fail(PROM_NOT_SIMPLE)
    name, body = selector[1], selector[2] or ""
    matches = parse_matchers(body, PROM_MATCHER, PROM_NOT_SIMPLE, absent_ok=True)
    if name.endswith(COUNTER_SUFFIXES):
        raise Fail(PROM_COUNTER)
    attribute = nrql_attribute(name)
    notes = [N_METRIC]
    reducer = looked_back(ctx)
    if aggregator:
        # an aggregate across series, then a reduce over time, is two layers; one NRQL
        # aggregation covers both only when the two are the same operation
        if (aggregator, reducer) not in EXACT_AGGREGATES:
            raise Fail(R_AGG_MIX.format(agg=aggregator, reducer=reducer))
        aggregation = PROM_AGGREGATIONS[aggregator]
    else:
        aggregation = REDUCERS[reducer]
        notes.append(N_PER_SERIES)
        if reducer == "last":
            notes.append(N_LAST)
    fixed, window_notes = time_window(ctx, reducer)
    notes += window_notes
    if any(m.negate for m in matches):
        notes.append(N_NEGATION)
    term = MetricTerm(
        metric=attribute,
        space_aggregation=aggregation,
        nr_target=NrTarget(event="Metric", attribute=attribute),
    )
    metric = MetricSignal(
        terms={"a": term},
        aggregation=aggregation,
        scope=[tag_filter(m) for m in matches],
        group_by=[nrql_attribute(g) for g in group_by],
    )
    return Built(SignalKind.METRIC, fixed, metric=metric, notes=notes)


# --- Loki ---

LOKI_FEATURES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\|\s*[A-Za-z_]|\bunwrap\b"), LOKI_PARSER),
    (
        re.compile(
            r"\b(?:rate|bytes_rate|bytes_over_time|rate_counter|quantile_over_time"
            r"|sum_over_time|avg_over_time|min_over_time|max_over_time|stddev_over_time"
            r"|stdvar_over_time|first_over_time|last_over_time|absent_over_time|topk|bottomk"
            r"|vector|label_replace|avg|min|max|count|stddev|stdvar)\s*\("
        ),
        LOKI_FUNCTION,
    ),
]
LOKI_COUNT = re.compile(
    rf"\s*count_over_time\(\s*\{{({BODY})\}}\s*((?:(?:\|=|!=|\|~|!~)\s*{QUOTED}\s*)*)"
    r"\[\s*(\d+)\s*([smh])\s*\]\s*\)\s*"
)
LOKI_FILTER = re.compile(r'(\|=|!=|\|~|!~)\s*"((?:[^"\\]|\\.)*)"')
RANGE_SECONDS = {"s": 1, "m": 60, "h": 3600}


def _line_filter(op: str, raw: str) -> CountFilter:
    if not STRICT_VALUE.fullmatch(raw):
        raise Fail(LOKI_NOT_SIMPLE)
    text = _unescape(raw)
    if op in ("|~", "!~"):
        if not WORD.fullmatch(text):
            raise Fail(LOKI_REGEX)
    elif "%" in text or "*" in text:
        raise Fail(LOKI_WILDCARD)
    if not text:
        raise Fail(LOKI_NOT_SIMPLE)
    return CountFilter(key="message", value=f"*{text}*", negate=op in ("!=", "!~"), wildcard=True)


def _loki(expr: str, ctx: Ctx) -> Built:
    if ctx.reducer != "last" and not ctx.instant:
        # a range query returns a series of counts; min, max, mean or sum of them is not a count
        raise Fail(R_LOKI_REDUCER)
    if len(expr) > MAX_EXPRESSION:
        raise Fail(LOKI_NOT_SIMPLE)
    feature = _first_feature(_skeleton(expr), LOKI_FEATURES)
    if feature:
        raise Fail(feature)
    try:
        aggregator, group_by, inner = split_outer(expr, "sum")
    except Fail:
        raise Fail(LOKI_NOT_SIMPLE) from None
    count = LOKI_COUNT.fullmatch(inner)
    if count is None or int(count[3]) == 0:
        raise Fail(LOKI_NOT_SIMPLE)
    matches = parse_matchers(count[1], LOKI_MATCHER, LOKI_NOT_SIMPLE, absent_ok=False)
    if not matches:
        raise Fail(LOKI_NOT_SIMPLE)
    filters = [_count_filter(m) for m in matches]
    filters += [_line_filter(op, raw) for op, raw in LOKI_FILTER.findall(count[2])]
    notes = [N_LOGS]
    if not aggregator:
        notes.append(N_PER_SERIES)
    if any(m.negate for m in matches):
        notes.append(N_NEGATION)
    seconds = int(count[3]) * RANGE_SECONDS[count[4]]
    signal = CountSignal(filters=filters, group_by=[nrql_attribute(g) for g in group_by])
    return Built(SignalKind.LOG, seconds, count=signal, notes=notes)


# the data source type decides how the query is read
HANDLERS: dict[str, Callable[[str, Ctx], Built]] = {
    "prometheus": _prometheus,
    "loki": _loki,
}


# --- pipeline ---


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _expression_type(node: GfNode) -> str:
    """The expression type of a server-side node, or "" for a data source query."""
    if (
        node.datasourceUid in EXPRESSION_UIDS
        or _mapping(node.model.get("datasource")).get("type") == "__expr__"
    ):
        return str(node.model.get("type") or "unknown")
    return ""


def _lone_classic(rule: GrafanaRule, types: set[str]) -> bool:
    """One classic condition on one query and nothing else: the one classic shape that converts."""
    classic = [n for n in rule.data if _expression_type(n) == "classic_conditions"]
    return (
        types - {""} == {"classic_conditions"}
        and len(classic) == 1
        and classic[0].refId == rule.condition
    )


def _math_reason(rule: GrafanaRule) -> str:
    """R_MATH, with the number of queries it combines when that is more than one."""
    refs = {
        ref
        for n in rule.data
        if _expression_type(n) == "math"
        for ref in re.findall(r"\$\{?(\w+)\}?", str(n.model.get("expression", "")))
    }
    return R_MATH_QUERIES.format(count=len(refs)) if len(refs) > 1 else R_MATH


def _check_expressions(rule: GrafanaRule) -> list[GfNode]:
    """The data source queries of the rule, after refusing expression types that do not convert."""
    types = {_expression_type(n) for n in rule.data}
    queries = [n for n in rule.data if not _expression_type(n)]
    if "sql" in types:
        raise Fail(R_SQL)
    if "classic_conditions" in types and not _lone_classic(rule, types):
        raise Fail(R_CLASSIC)
    if len({q.datasourceUid for q in queries}) > 1:
        raise Fail(R_MIXED)
    if "math" in types:
        raise Fail(_math_reason(rule))
    if len(queries) > 1:
        raise Fail(R_MULTI)
    return queries


def _reduce_step(
    nodes: dict[str, GfNode], step: GfNode | None, used: list[GfNode]
) -> tuple[GfNode | None, str, list[str]]:
    """Take an optional reduce node off the chain: the next node, the reducer and the notes."""
    if step is None or _expression_type(step) != "reduce":
        return step, "last", []
    used.append(step)
    notes: list[str] = []
    mode = str(_mapping(step.model.get("settings")).get("mode") or "").lower()
    if mode == "dropnn":
        notes.append(N_DROPNN)
    elif mode not in ("", "strict"):
        raise Fail(R_REDUCE_MODE)
    return (
        nodes.get(str(step.model.get("expression"))),
        str(step.model.get("reducer", "")).lower(),
        notes,
    )


def _classic(
    nodes: dict[str, GfNode], condition: GfNode
) -> tuple[GfNode, str, Threshold, list[str]]:
    """Query, reducer, threshold and notes of a classic condition with one test."""
    tests = condition.model.get("conditions")
    if not isinstance(tests, list) or len(tests) != 1:
        raise Fail(R_CLASSIC)
    test = _mapping(tests[0])
    params = _mapping(test.get("query")).get("params")
    query = nodes.get(str(params[0])) if isinstance(params, list) and len(params) == 1 else None
    if query is None or _expression_type(query):
        raise Fail(R_SHAPE)
    reducer = str(_mapping(test.get("reducer")).get("type", "")).lower()
    reducer = "mean" if reducer == "avg" else reducer
    if reducer not in REDUCERS:
        raise Fail(R_REDUCER)
    evaluator = _mapping(test.get("evaluator"))
    threshold = _make_threshold(evaluator.get("type"), evaluator.get("params"))
    return query, reducer, threshold, [N_CLASSIC]


def _pipeline(rule: GrafanaRule) -> tuple[GfNode, str, Threshold, bool, list[str]]:
    """Query, reducer, threshold, recovery flag and notes of the rule's pipeline."""
    _check_expressions(rule)
    nodes = {n.refId: n for n in rule.data}
    condition = nodes.get(rule.condition)
    if condition is None:
        raise Fail(R_NO_NODE)
    if _expression_type(condition) == "classic_conditions":
        query, reducer, threshold, notes = _classic(nodes, condition)
        return query, reducer, threshold, False, notes
    if _expression_type(condition) != "threshold" or len(nodes) != len(rule.data):
        raise Fail(R_SHAPE)
    used = [condition]
    step, reducer, notes = _reduce_step(
        nodes, nodes.get(str(condition.model.get("expression"))), used
    )
    if step is not None and _expression_type(step) == "resample":
        used.append(step)
        notes.append(N_RESAMPLE)
        step = nodes.get(str(step.model.get("expression")))
    if step is None or _expression_type(step):
        raise Fail(R_SHAPE)
    used.append(step)
    if len(used) != len(rule.data):
        stray = any(_expression_type(n) == "resample" and n not in used for n in rule.data)
        raise Fail(R_RESAMPLE if stray else R_SHAPE)
    if reducer not in REDUCERS:
        raise Fail(R_REDUCER)
    threshold, recovery = _threshold(condition)
    return step, reducer, threshold, recovery, notes


def _make_threshold(kind: object, params: object) -> Threshold:
    """The critical threshold for an evaluator type and its parameters."""
    if kind in RANGE_KINDS:
        raise Fail(R_RANGE)
    if not isinstance(kind, str) or kind not in OPERATORS:
        raise Fail(R_OPERATOR)
    if not (
        isinstance(params, list)
        and len(params) == 1
        and isinstance(params[0], int | float)
        and not isinstance(params[0], bool)
        and math.isfinite(params[0])
    ):
        raise Fail(R_VALUE)
    return Threshold(severity=Severity.CRITICAL, operator=OPERATORS[kind], value=float(params[0]))


def _threshold(node: GfNode) -> tuple[Threshold, bool]:
    """The threshold, and whether the rule also had a recovery threshold."""
    conditions = node.model.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 1:
        raise Fail(R_SHAPE)
    condition = _mapping(conditions[0])
    evaluator = _mapping(condition.get("evaluator"))
    threshold = _make_threshold(evaluator.get("type"), evaluator.get("params"))
    return threshold, bool(condition.get("unloadEvaluator"))


def _kind_of(declared: object, uid: str, expr: str) -> tuple[str, bool]:
    """The data source type and whether it had to be inferred (it is not in every read)."""
    if isinstance(declared, str) and declared:
        return declared.lower(), False
    for hint, kind in UID_HINTS:
        if hint in uid.lower():
            return kind, True
    flat = expr.replace(" ", "")
    return ("loki" if re.search(r"(?<![\w:])\{", flat) else "prometheus"), True


def _datasource_type(node: GfNode) -> tuple[str, bool]:
    declared = _mapping(node.model.get("datasource")).get("type")
    return _kind_of(declared, node.datasourceUid, str(node.model.get("expr", "")))


def _instant(query: GfNode) -> bool:
    return query.queryType == "instant" or query.model.get("instant") is True


def _range_seconds(query: GfNode) -> int | None:
    seconds = query.relativeTimeRange.get("from")
    ok = isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0
    return seconds if ok else None


@dataclass
class _Plan:
    """What a rule asks, whatever shape it was read in: one query, how to read it, one test."""

    kind: str  # the data source type
    inferred: bool
    expr: str
    ctx: Ctx
    threshold: Threshold | None  # None: the query itself must fix it (absent)
    recovery: bool = False
    notes: list[str] = field(default_factory=list)


def _grafana_plan(rule: GrafanaRule) -> _Plan:
    query, reducer, threshold, recovery, notes = _pipeline(rule)
    kind, inferred = _datasource_type(query)
    ctx = Ctx(reducer, _instant(query), _range_seconds(query), threshold)
    return _Plan(kind, inferred, str(query.model.get("expr", "")), ctx, threshold, recovery, notes)


def _rule_file_plan(rule: GrafanaRule) -> _Plan:
    """A Prometheus-style rule: the comparison with a number in the query is the threshold."""
    comparison = split_comparison(rule.expr)
    expr = comparison.query if comparison else rule.expr
    threshold = _make_threshold(comparison.operator, [comparison.value]) if comparison else None
    kind, inferred = _kind_of(None, "", expr)
    return _Plan(kind, inferred, expr, Ctx("last", True, None, threshold, node=False), threshold)


def _built(plan: _Plan) -> Built:
    handler = HANDLERS.get(plan.kind)
    if handler is None:
        raise Fail(R_DATASOURCE.format(label=OTHER_DATASOURCES.get(plan.kind, "unknown")))
    return handler(plan.expr, plan.ctx)


# --- windows and states ---


def _whole_minutes(seconds: int) -> int:
    return max(MIN_WINDOW_SECONDS, -(-seconds // 60) * 60)


def _windows(rule: GrafanaRule, fixed: int | None, minimum: int = 0) -> tuple[int, int, list[str]]:
    """Aggregation window, threshold duration and notes. Both are whole numbers of windows."""
    notes: list[str] = []
    if fixed is not None:
        base = fixed
    elif rule.interval_seconds:
        base = rule.interval_seconds
    else:
        base = MIN_WINDOW_SECONDS
        notes.append(N_INTERVAL_UNKNOWN)
    window = _whole_minutes(base)
    if window > MAX_AGGREGATION_WINDOW_SECONDS:
        raise Fail(R_AGG_WINDOW.format(seconds=window, limit=MAX_AGGREGATION_WINDOW_SECONDS))
    if window != base:
        notes.append(N_ROUNDED_WINDOW.format(seconds=base, window=window))
    if fixed is not None and rule.interval_seconds != fixed:
        notes.append(N_LOKI_WINDOW.format(range=fixed, window=window))
    duration = max(rule.for_seconds, window, minimum)
    duration = -(-duration // window) * window
    if duration > MAX_DURATION_SECONDS:
        raise Fail(R_PENDING.format(seconds=rule.for_seconds, limit=MAX_DURATION_SECONDS))
    if rule.for_seconds:
        notes.append(N_PENDING)
        if duration != rule.for_seconds:
            notes.append(
                N_ROUNDED_FOR.format(seconds=rule.for_seconds, window=window, duration=duration)
            )
    return window, duration, notes


def _error_notes(rule: GrafanaRule) -> list[str]:
    return [N_ERROR_STATE] if rule.exec_err_state in ("Error", "Alerting", "KeepLast") else []


def _states(rule: GrafanaRule, duration: int, heartbeat: bool) -> tuple[NoData, list[str]]:
    notes: list[str] = []
    no_data = NoData()
    if heartbeat:  # absence is the condition: loss of signal always opens the violation
        return NoData(notify=True, timeout_minutes=max(1, duration // 60)), _error_notes(rule)
    if rule.no_data_state == "Alerting":
        no_data = NoData(notify=True, timeout_minutes=max(1, duration // 60))
        notes.append(N_NODATA_ALERTING.format(minutes=no_data.timeout_minutes))
    elif rule.no_data_state == "NoData":
        notes.append(N_NODATA_DEFAULT)
    elif rule.no_data_state == "KeepLast":
        notes.append(N_NODATA_KEEP)
    elif rule.no_data_state == "OK":
        notes.append(N_NODATA_OK)
    return no_data, notes + _error_notes(rule)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


# --- one rule ---


def _plan(rule: GrafanaRule) -> _Plan:
    if rule.datasource_managed:
        if not rule.expr:
            raise Fail(R_DS_MANAGED)
        return _rule_file_plan(rule)
    return _grafana_plan(rule)


def _rule_notes(rule: GrafanaRule, plan: _Plan) -> list[str]:
    """Notes about the rule's own settings, in the order they are shown."""
    notes = [N_INFERRED] if plan.inferred else []
    if rule.expr:
        notes.append(N_RULE_FILE)
    return notes


def _tail_notes(rule: GrafanaRule, runbook_refused: bool) -> list[str]:
    """Paused, duplicate, origin and routing notes that close the list."""
    notes: list[str] = []
    if rule.duplicate_uid:
        notes.append(N_DUP_UID)
    if rule.copies_merged:
        notes.append(N_COPIES.format(count=rule.copies_merged))
    if runbook_refused:
        notes.append(fields.N_RUNBOOK_IGNORED)
    origin = fields.origin_note(rule.folder, rule.group, rule.labels)
    if origin:
        notes.append(origin)
    return [*notes, N_ROUTING]


def _convert(rule: GrafanaRule, key: str) -> IRMonitor:
    if rule.unreadable:
        raise Fail(R_UNREADABLE)
    if rule.record is not None:
        raise Fail(R_RECORD)
    plan = _plan(rule)
    built = _built(plan)
    threshold = built.threshold or plan.threshold
    if threshold is None:
        raise Fail(R_NO_COMPARISON)
    window, duration, window_notes = _windows(rule, built.window, built.minimum_duration)
    no_data, state_notes = _states(rule, duration, built.kind is SignalKind.HEARTBEAT)
    notes = _rule_notes(rule, plan) + built.notes + plan.notes
    if built.kind is SignalKind.LOG and threshold.operator in EMPTY_SENSITIVE:
        notes.append(N_COUNT_EMPTY)
    notes += window_notes
    if plan.recovery:
        notes.append(N_RECOVERY)
    if rule.keep_firing_for_seconds:
        notes.append(N_KEEP_FIRING)
    notes += state_notes
    if rule.is_paused:
        notes.insert(0, N_PAUSED_UNKNOWN if rule.paused_unknown else N_PAUSED)
    runbook, refused = fields.runbook(rule.annotations)
    notes += _tail_notes(rule, refused)
    annotations = rule.annotations
    message = _text(annotations.get("description")) or _text(annotations.get("summary"))
    monitor = _monitor(rule, key, Confidence.APPROXIMATE, notes)
    monitor.signal = Signal(
        kind=built.kind,
        raw_query=monitor.name,
        window_seconds=duration,
        aggregation_window_seconds=window,
        occurrences=Occurrences.ALL,
        metric=built.metric,
        count=built.count,
        heartbeat=built.heartbeat,
    )
    monitor.thresholds = [threshold]
    monitor.no_data = no_data
    monitor.notification = Notification(message_template=message)
    if runbook:
        monitor.emit = EmitOptions(runbook_url=runbook)
    return monitor


def _monitor(rule: GrafanaRule, key: str, confidence: Confidence, reasons: list[str]) -> IRMonitor:
    name = rule.title or "(untitled rule)"
    monitor = IRMonitor(
        source_system="grafana",
        source_id=source_id(key),
        source_key=key,
        source_type="alert rule",
        name=name,
        signal=Signal(kind=SignalKind.UNKNOWN, raw_query=name),
        confidence=confidence,
        reasons=reasons,
    )
    if rule.is_paused:
        monitor.unmapped["enabled"] = False
    return monitor


def translate_rule(rule: GrafanaRule, key: str | None = None) -> IRMonitor:
    key = key or rule.uid or f"{rule.folder}/{rule.group}/{rule.title}"
    try:
        return _convert(rule, key)
    except Fail as fail:
        return _monitor(rule, key, Confidence.MANUAL, [fail.reason])


def translate(rules: list[GrafanaRule]) -> list[IRMonitor]:
    """One monitor per rule; a repeated key gets an index so ids stay distinct."""
    seen: dict[str, int] = {}
    monitors: list[IRMonitor] = []
    for rule in rules:
        base = rule.uid or f"{rule.folder}/{rule.group}/{rule.title}"
        seen[base] = seen.get(base, 0) + 1
        monitors.append(translate_rule(rule, base if seen[base] == 1 else f"{base}#{seen[base]}"))
    return monitors
