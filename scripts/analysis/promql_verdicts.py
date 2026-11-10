"""NRQL verdicts per PromQL construct (data for the census and the translator design).

Codes:
  1  expressible exactly: a fixed NRQL pattern with no condition the data can break.
  2  expressible with named caveats (scrape interval, bucket, counter or gauge, label sets).
  3  expressible only through a subquery (`FROM (SELECT ...)`, `WHERE x IN (FROM ...)`) or
     FACET math (`FACET concat(...)`, `capture(...)`, `cases(...)`).
  4  not expressible (or only with data the importer does not have at conversion time).

Status says how much evidence stands behind the verdict:
  MEASURED     a live round trip in this repository (docs/research/live-grafana-counter-roundtrip.md)
  DOCS         the NRQL feature is documented (index N1..N10 in grafana-complex-dashboards-repos.md
               section 0, and R-A = newrelic-grafana-data-and-query-analogues.md rows F01..F64),
               and the equivalence is argued from the documentation, not run
  UNVERIFIED   a candidate pattern; the function or its behaviour on Metric data was not read or run
  n/a          nothing to verify (code 4, or syntax only)

The census projection treats a construct as supported when its code is at most the chosen cap.
Nothing here was run against New Relic in the census task; the differential harness of the
design section is what turns UNVERIFIED into MEASURED.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    code: int
    nrql: str
    note: str
    status: str
    source: str = ""


def _v(code: int, nrql: str, note: str, status: str, source: str = "") -> Verdict:
    return Verdict(code, nrql, note, status, source)


def _none(note: str, source: str = "") -> Verdict:
    return Verdict(4, "", note, "n/a", source)


VERDICTS: dict[str, Verdict] = {
    # --- selectors and matchers ---
    "selector.instant": _v(
        2,
        "SELECT latest(g) FROM Metric (stat) / average(g) (chart)",
        "series are merged unless FACET names the identifying labels; 'latest' of several series is undefined (measured: one arbitrary mount); gauge vs counter is read from the metric name",
        "MEASURED",
        "LIVE-C part 2",
    ),
    "selector.range": _v(
        2, "argument of a range function (see fn.*)", "a range vector alone is not a result", "n/a"
    ),
    "selector.no_name": _none(
        "`{__name__=~...}` selects metrics by name pattern; NRQL names the metric in the function argument, so a pattern over names has no SELECT form (candidate: FACET metricName with count(*), which counts samples not values)",
        "R-C 4.1",
    ),
    "matcher.name_label": _none("same as selector.no_name", "R-C 4.1"),
    "matcher.eq": _v(
        1,
        "WHERE attr = 'v'",
        "label name must be a valid attribute (backtick quoted by identifier())",
        "DOCS",
        "N1",
    ),
    "matcher.ne": _v(
        2,
        "WHERE attr != 'v'",
        "PromQL also matches series that lack the label; NRQL `!=` on a missing attribute is not documented to match (UNVERIFIED), so exact only when every series has the label",
        "UNVERIFIED",
        "N1",
    ),
    "matcher.re": _v(
        2,
        "WHERE attr RLIKE 'p' (plain alternatives: attr IN ('a','b'))",
        "RE2 and full-string match on both sides (N1); the converter only carries a checked subset; patterns that match the empty string differ (missing label)",
        "DOCS",
        "N1; regex_matchers.py",
    ),
    "matcher.nre": _v(
        2,
        "WHERE attr NOT RLIKE 'p'",
        "as matcher.re; behaviour of NOT RLIKE on a missing attribute is UNVERIFIED",
        "UNVERIFIED",
        "N1",
    ),
    "matcher.re.complex_syntax": _v(
        2,
        "WHERE attr RLIKE 'p'",
        "RE2 on both sides, but the converter only writes a checked subset (no backslash, ^, $, (?) so that no escaping question reaches the NRQL string; backslashes need quote_string escaping and a test per pattern",
        "DOCS",
        "N1; regex_matchers.py",
    ),
    "matcher.re.matches_empty": _v(
        2,
        "drop the matcher for `.*`; otherwise (attr RLIKE 'p' OR attr IS NULL)",
        "PromQL treats a missing label as empty; the NRQL pair is exact only if NRQL stores no empty-string attributes (UNVERIFIED)",
        "UNVERIFIED",
        "regex_matchers.py",
    ),
    "matcher.ne_empty": _v(
        2,
        "WHERE attr IS NOT NULL",
        '`label!=""` means present; exact only if empty strings are not stored as values (UNVERIFIED)',
        "UNVERIFIED",
        "regex_matchers.py",
    ),
    "matcher.eq_empty": _v(
        2, "WHERE attr IS NULL", '`label=""` means absent; same empty-string caveat', "UNVERIFIED"
    ),
    "matcher.value_variable": _v(
        2,
        "attr IN ({{v}}) (multi-value variable)",
        "a New Relic variable is substituted whole: fixed text mixed with a variable cannot be written (C_VAR_MATCHER); 'All' needs the include-all setting",
        "DOCS",
        "N9",
    ),
    "name.recording_rule": _v(
        2,
        "SELECT latest(`level:metric:ops`) FROM Metric",
        "exact only if the recording rule output is itself remote-written to New Relic; otherwise the rule expression must be inlined (needs the rule file)",
        "DOCS",
        "R-C 4.1",
    ),
    "name.variable": _none(
        "a variable inside a metric name: N9 lists an 'identifier' output format, but use in a function argument was not read or run; candidate only",
        "N9",
    ),
    # --- range functions ---
    "fn.rate": _v(
        2,
        "SELECT rate(sum(c), 1 second) FROM Metric [FACET ...] TIMESERIES <bucket>",
        "exact only when the bucket is a multiple of the scrape interval (measured: 5 s buckets on 10 s data give 0 and 2.4); counter must be a delta `count`; the [w] window is not honoured; stat divides by the query window",
        "MEASURED",
        "LIVE-C part 1",
    ),
    "fn.irate": _v(
        2,
        "same as rate, or latestrate(c, 1 second)",
        "measured equal on steady data; differs on spikes (bucket average, not last two samples)",
        "MEASURED",
        "LIVE-C part 1",
    ),
    "fn.increase": _v(
        2,
        "SELECT sum(c) ... TIMESERIES <w> (or rate(sum(c), <w>))",
        "sum of delta counts is the exact increase; a stat over data shorter than w extrapolates (measured 4385.8 vs 720)",
        "MEASURED",
        "LIVE-C part 1",
    ),
    "fn.delta": _v(
        2,
        "SELECT latest(g) - earliest(g) ... TIMESERIES <w>",
        "`earliest` on Metric gauges is not in the function list read (R-A F11); candidate: derivative(g, <w>) * <w> is a regression slope, not the end-point difference",
        "UNVERIFIED",
        "R-A F11",
    ),
    "fn.idelta": _v(
        2,
        "latestrate(g, 1 minute) * interval",
        "only with regular scrapes",
        "UNVERIFIED",
        "R-A F12",
    ),
    "fn.deriv": _v(
        2,
        "SELECT derivative(g, 1 second) ... TIMESERIES <w>",
        "both are least squares (N1); derivative needs two points in the bucket",
        "UNVERIFIED",
        "R-A F13",
    ),
    "fn.predict_linear": _v(
        2,
        "latest(g) + derivative(g, 1 second) * <t seconds>",
        "window is the query window, not [w]; NRQL has predictLinear(g, t) and a PREDICT clause (N1) which the documentation advises against with TIMESERIES",
        "UNVERIFIED",
        "R-C 4.11",
    ),
    "fn.avg_over_time": _v(
        2,
        "SELECT average(g) ... TIMESERIES <w> SLIDE BY <step>",
        "average over samples; SLIDE BY gives the overlapping window; a stat uses the dashboard window instead of [w]",
        "DOCS",
        "N1 slide-by; R-A F18",
    ),
    "fn.min_over_time": _v(
        2, "SELECT min(g) ... TIMESERIES <w> SLIDE BY <step>", "as avg_over_time", "DOCS", "R-A F19"
    ),
    "fn.max_over_time": _v(
        2, "SELECT max(g) ... TIMESERIES <w> SLIDE BY <step>", "as avg_over_time", "DOCS", "R-A F19"
    ),
    "fn.sum_over_time": _v(
        2,
        "SELECT sum(g) ... TIMESERIES <w> SLIDE BY <step>",
        "adds samples, so the value depends on the scrape count in w",
        "DOCS",
        "R-A F20",
    ),
    "fn.count_over_time": _v(
        2, "SELECT count(g) ... TIMESERIES <w>", "counts samples", "DOCS", "R-A F21"
    ),
    "fn.last_over_time": _v(2, "SELECT latest(g) ... TIMESERIES <w>", "", "DOCS", "R-A F22"),
    "fn.quantile_over_time": _v(
        2,
        "SELECT percentile(g, q*100) ... TIMESERIES <w>",
        "NRQL percentile is sketch based, not exact",
        "DOCS",
        "R-A F24",
    ),
    "fn.stddev_over_time": _v(
        2,
        "SELECT stddev(g) ... TIMESERIES <w>",
        "population vs sample deviation not stated; not in the Metric gauge list read",
        "UNVERIFIED",
        "R-A F25",
    ),
    "fn.changes": _none(
        "counters are stored as deltas and resets are absorbed at ingest; the number of value changes has no NRQL function",
        "R-A F16",
    ),
    "fn.resets": _none("resets are not stored", "R-A F15"),
    "fn.absent": _none(
        "an empty result is NULL in NRQL, not 1; an alert condition (loss of signal) is the equivalent, not a query",
        "R-A F45",
    ),
    # --- instant functions ---
    "fn.histogram_quantile": _v(
        2,
        "SELECT bucketPercentile(x_bucket, q*100) FROM Metric FACET ...",
        "only for `sum by (le, ...) (rate(x_bucket[w]))`; interpolation inside a bucket equal to Prometheus is UNVERIFIED; not usable in alert conditions",
        "DOCS",
        "N1 func-bucket-percentile; R-C 4.5",
    ),
    "fn.label_replace": _v(
        3,
        "FACET capture(attr, r'(?P<c>...)') / concat(substring(...)) / cases(WHERE ... AS '...')",
        "FACET math; usable only when the new label is used for grouping; behaviour of capture() on Metric attributes is UNVERIFIED",
        "UNVERIFIED",
        "R-C 4.8",
    ),
    "fn.label_join": _v(3, "FACET concat(a, '-', b)", "FACET math", "UNVERIFIED", "R-C 4.8"),
    "fn.clamp_min": _v(1, "clamp_min(x, n)  (or if(x < n, n, x))", "", "DOCS", "R-A F46; R-C 4.11"),
    "fn.clamp_max": _v(1, "clamp_max(x, n)  (or if(x > n, n, x))", "", "DOCS", "R-A F46"),
    "fn.clamp": _v(1, "clamp_max(clamp_min(x, lo), hi)", "", "DOCS", "R-A F46"),
    "fn.abs": _v(1, "abs(x)", "", "DOCS", "R-A F47"),
    "fn.ceil": _v(1, "ceil(x)", "", "DOCS", "R-A F47"),
    "fn.floor": _v(1, "floor(x)", "", "DOCS", "R-A F47"),
    "fn.sqrt": _v(1, "sqrt(x)", "", "DOCS", "R-A F47"),
    "fn.exp": _v(1, "exp(x)", "", "DOCS", "R-A F47"),
    "fn.ln": _v(1, "ln(x)", "", "DOCS", "R-A F47"),
    "fn.log2": _v(1, "log(x, 2)", "", "DOCS", "R-A F48"),
    "fn.log10": _v(1, "log10(x)", "", "DOCS", "R-A F48"),
    "fn.round": _v(
        2,
        "round(x, k) when to_nearest is 10^-k; otherwise round(x / n) * n",
        "Prometheus rounds to the nearest multiple of to_nearest; NRQL's second argument is read as decimal places; tie rounding not stated",
        "DOCS",
        "R-A F48",
    ),
    "fn.time": _v(
        2,
        "aggregationendtime()",
        "end of the bucket, not the evaluation instant",
        "DOCS",
        "R-A F50",
    ),
    "fn.hour": _v(
        2,
        "hourOf(aggregationendtime())",
        "time zone: PromQL is UTC; NRQL needs WITH TIMEZONE 'UTC'; function names beyond hourOf/minuteOf/weekOf unconfirmed",
        "UNVERIFIED",
        "R-A F52",
    ),
    "fn.day_of_month": _v(
        2, "dayOfMonthOf(...) (name unconfirmed)", "see fn.hour", "UNVERIFIED", "R-A F52"
    ),
    "fn.day_of_week": _v(
        2, "dayOfWeekOf(...) (name unconfirmed)", "see fn.hour", "UNVERIFIED", "R-A F52"
    ),
    "fn.month": _v(2, "monthOf(...) (name unconfirmed)", "see fn.hour", "UNVERIFIED", "R-A F52"),
    "fn.vector": _none(
        "a constant has no series; the idiom `or vector(0)` is idiom.or_vector", "R-A F53"
    ),
    "fn.scalar": _v(
        3,
        "SELECT x / (SELECT latest(y) FROM Metric)  (subquery as a number)",
        "documented as a delta query on events (N2); on Metric data UNVERIFIED; scalar() of a multi-series vector is NaN in PromQL",
        "UNVERIFIED",
        "N2; R-C 4.11",
    ),
    "fn.sort": _v(
        2, "ORDER BY on a table widget", "ordering only; no effect on a chart", "DOCS", "R-A F55"
    ),
    "fn.sort_desc": _v(
        2,
        "ORDER BY <col> DESC on a table widget",
        "ordering only; no effect on a chart",
        "DOCS",
        "R-A F55",
    ),
    "fn.timestamp": _none("no documented NRQL function", "R-A F51"),
    # --- aggregation ---
    "agg.sum": _v(
        2,
        "SELECT sum(x) ... FACET ...",
        "counter (delta count): exact. Gauge: adds every sample in the bucket, so it is high by the samples per series (measured 4x per 60 s bucket, 41x for 600 s); exact form is a nested latest()-per-series",
        "MEASURED",
        "LIVE-C part 2",
    ),
    "agg.avg": _v(
        2,
        "SELECT average(x) ... FACET ...",
        "equal weight per sample, not per series; equal to PromQL only if series have the same sample count in the bucket",
        "MEASURED",
        "LIVE-C part 2",
    ),
    "agg.min": _v(
        2,
        "SELECT min(x)",
        "min over every sample in the bucket, PromQL takes one sample per series per step: equal only with one sample per series per bucket",
        "DOCS",
        "R-A F30",
    ),
    "agg.max": _v(2, "SELECT max(x)", "as agg.min", "DOCS", "R-A F30"),
    "agg.count": _v(
        2,
        "SELECT uniqueCount(<identifying label>) / cardinality(x)",
        "count(m) counts series, count() in NRQL counts samples; uniqueCount is exact to 256 values",
        "DOCS",
        "R-A F31; R-C 4.2",
    ),
    "agg.stddev": _none("NRQL stddev runs over samples, not over series", "R-A F32"),
    "agg.stdvar": _none("NRQL stdvar runs over samples, not over series", "R-A F32"),
    "agg.quantile": _v(
        2, "SELECT percentile(x, q*100)", "mixes samples and series", "DOCS", "R-A F33"
    ),
    "agg.topk": _v(
        2,
        "FACET l ORDER BY <aggregate> LIMIT n",
        "ranks over the whole window, not at each step; without ORDER BY the facets are picked by frequency for sum()/rate() (N1 sel-facet), which the current converter does not write",
        "DOCS",
        "N1 sel-facet-order; R-C 4.6",
    ),
    "agg.bottomk": _none("FACET ... ORDER BY takes no ASC (N1)", "R-C 4.6"),
    "agg.count_values": _none("no NRQL form", "R-A F36"),
    "agg.group": _none("no NRQL form", "R-A F36"),
    "agg.limitk": _none("no NRQL form", "R-A F36"),
    "agg.by": _v(
        2,
        "FACET a, b LIMIT MAX",
        "at most 5 attributes; at most 5000 facets",
        "DOCS",
        "N1 sel-facet",
    ),
    "agg.ungrouped": _v(1, "no FACET", "", "DOCS"),
    "agg.without": _none(
        "needs the label set of the metric, which only the data has (candidate with live data: FACET over keyset() minus the listed labels, at most 5)",
        "R-A F38",
    ),
    "agg.nested": _v(
        3,
        "SELECT agg(x) FROM (SELECT agg2(y) AS x FROM Metric FACET ... TIMESERIES ...) [FACET ...]",
        "TIMESERIES and FACET at any level, SINCE/UNTIL/COMPARE WITH only outermost (N4); used by 189 quickstart queries; per-step semantics need the inner and outer bucket to agree",
        "DOCS",
        "N4; R-C 4.0",
    ),
    "shape.avg_min_max_of_rate": _v(
        3,
        "SELECT average(r) FROM (SELECT rate(sum(c), 1 second) AS r FROM Metric FACET dimensions() LIMIT MAX TIMESERIES <b>) TIMESERIES <b>",
        "per-series rates first, then the aggregate; FACET dimensions() gives one series per label set (N1 func-dimensions)",
        "DOCS",
        "R-C 4.3",
    ),
    # --- binary operators ---
    "binop.+": _v(
        1, "x + y inside one SELECT", "division by zero gives null, not Inf (N1)", "DOCS", "R-A F57"
    ),
    "binop.-": _v(1, "x - y inside one SELECT", "", "DOCS", "R-A F57"),
    "binop.*": _v(1, "x * y inside one SELECT", "", "DOCS", "R-A F57"),
    "binop./": _v(
        2,
        "x / y inside one SELECT",
        "division by zero gives null, not +Inf/NaN; both sides need the same FACET and bucket",
        "DOCS",
        "R-A F57",
    ),
    "binop.%": _v(2, "mod(x, y)", "function name unconfirmed", "UNVERIFIED", "R-A F57"),
    "binop.^": _v(2, "pow(x, y)", "function name unconfirmed", "UNVERIFIED", "R-A F57"),
    "binop.atan2": _none("no NRQL function", "R-A F49"),
    "arith.scalar_scalar": _v(1, "constant arithmetic", "", "DOCS"),
    "arith.vector_scalar": _v(1, "(aggregate) op number", "", "DOCS", "R-A F57"),
    "arith.vector_vector": _v(
        2,
        "SELECT f(a) op f(b) FROM Metric FACET <shared labels>",
        "exact only when both sides are aggregated to the same label set and the same bucket; different filters per side need filter() (N1 func-filter); different label sets have no form",
        "DOCS",
        "R-C 4.4",
    ),
    "match.implicit": _v(
        2,
        "shared FACET labels",
        "PromQL matches on all labels; NRQL has the labels you FACET by, so both sides must be grouped by identical labels",
        "DOCS",
        "R-C 4.4",
    ),
    "match.explicit": _v(3, "see match.on / match.ignoring", "", "DOCS", "R-C 4.9"),
    "match.on": _v(
        3,
        "A: one SELECT with a shared FACET; B: WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE ...); D: replace by the scalar",
        "intent A and D are code 2; B needs a subquery (single key; two keys need a packed key, UNVERIFIED); C (adds a label) is group_left",
        "DOCS",
        "R-C 4.9; N2",
    ),
    "match.ignoring": _v(
        3,
        "as match.on with the complement of the listed labels",
        "needs the label sets of both metrics (data)",
        "UNVERIFIED",
        "R-C 4.9",
    ),
    "match.group_left": _none(
        "the label-adding intent (info metric joined for display) has no PromQL-style join; the filter-only intent is match.on B (code 3). Candidate: JOIN, UNVERIFIED on Metric ('subquery cannot be a metric row-wise query', N3)",
        "R-C 4.9",
    ),
    "match.group_right": _none("as match.group_left", "R-C 4.9"),
    "cmp.filter.vector_scalar": _v(
        3,
        "SELECT k, v FROM (SELECT <expr> AS v FROM Metric FACET k) WHERE v > n",
        "returns rows (an instant table or stat), not a filtered time series; for a chart only a threshold line is possible",
        "DOCS",
        "N4; R-C 4.11",
    ),
    "cmp.filter.vector_vector": _v(
        3,
        "SELECT k FROM (SELECT latest(a) AS a, latest(b) AS b FROM Metric FACET k) WHERE a > b",
        "comparison of two columns in WHERE is UNVERIFIED; rows, not series",
        "UNVERIFIED",
        "N4",
    ),
    "cmp.bool.vector_scalar": _v(
        2,
        "if(<aggregate> > n, 1, 0)",
        "if() over aggregates inside SELECT is documented (N1 func-if); UNVERIFIED on Metric",
        "UNVERIFIED",
        "R-C 4.10",
    ),
    "cmp.bool.vector_vector": _v(
        2,
        "if(<aggregate a> > <aggregate b>, 1, 0)",
        "as cmp.bool.vector_scalar",
        "UNVERIFIED",
        "R-C 4.10",
    ),
    "cmp.scalar_scalar": _v(1, "constant", "", "DOCS"),
    "set.and": _v(
        3,
        "WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE metricName = 'b')",
        "exact for a single join key; the value test inside b needs a nested aggregation; at most 3 subqueries, 5000 values (N2)",
        "DOCS",
        "N2; R-C 4.10",
    ),
    "set.unless": _v(
        3,
        "WHERE key NOT IN (FROM Metric SELECT uniques(key, 5000) WHERE ...)",
        "as set.and",
        "DOCS",
        "N2; R-C 4.10",
    ),
    "set.or": _none(
        "a series union has no single NRQL query; two columns `latest(a) AS a, latest(b) AS b` only when the label sets agree and both exist",
        "R-C 4.10",
    ),
    "idiom.or_vector": _v(
        2,
        "widget null-to-zero option, or filter(count(*), WHERE ...)",
        "exact for counts, approximate for sums; the null option is a widget setting, not NRQL",
        "UNVERIFIED",
        "R-C 4.10",
    ),
    "unary.-": _v(1, "-x", "", "DOCS", "R-A F64"),
    "unary.+": _v(1, "x", "", "DOCS"),
    "scalar.literal": _v(1, "number", "", "DOCS"),
    "var.as_operand": _v(
        2,
        "{{v}} (number-format variable)",
        "a New Relic variable cannot appear in AS, LIMIT, SINCE, TIMESERIES (N9)",
        "DOCS",
        "N9",
    ),
    "var.format": _none(
        "`${x:csv}` and similar formats: a New Relic variable is substituted whole", "N9"
    ),
    # --- modifiers and windows ---
    "mod.offset": _v(
        2,
        "SINCE ... COMPARE WITH <offset> ago (outermost query only)",
        "gives the earlier period as a second series, not a shifted series: `x / x offset 1d` has no form; N6 supports offset only if every vector has the same offset",
        "DOCS",
        "N1 sel-compare; R-C 4.11",
    ),
    "mod.at": _none("pins evaluation to a timestamp; no NRQL form", ""),
    "subquery": _v(
        3,
        "SELECT agg(r) FROM (SELECT <inner> AS r FROM Metric FACET ... TIMESERIES <step>)",
        "the range becomes the dashboard window (SINCE only on the outer query), so exact only when equal; per-step sliding of the outer needs SLIDE BY (UNVERIFIED on nested)",
        "UNVERIFIED",
        "N4; R-C 4.11",
    ),
    "window.fixed": _v(
        2,
        "TIMESERIES <bucket>",
        "the PromQL window is not the bucket; rate smoothing differs (measured: Prometheus smooths over 5 m, a 60 s bucket does not)",
        "MEASURED",
        "LIVE-C part 2",
    ),
    "window.rate_interval": _v(
        2, "TIMESERIES AUTO", "a variable cannot go in TIMESERIES (N9)", "MEASURED", "LIVE-C part 2"
    ),
    "window.interval": _v(2, "TIMESERIES AUTO", "as window.rate_interval", "DOCS", "N9"),
    "window.range": _v(1, "the dashboard time window", "", "DOCS", "R-C 4.3"),
    "window.variable": _v(
        2,
        "TIMESERIES AUTO",
        "a custom interval variable cannot be placed in TIMESERIES",
        "DOCS",
        "N9",
    ),
    "window.other": _none("an unrecognised window text", ""),
    # --- converter-shape tags (not PromQL features) ---
    "shape.counter_suffix_bare": _v(
        2,
        "latest(g) for a gauge that merely ends in _count/_sum; sum(c) per bucket for a counter read raw",
        "the type is not in the query; it comes from metric metadata (data check). A raw cumulative counter graph has no NRQL equal (measured)",
        "MEASURED",
        "LIVE-C what remains",
    ),
    "shape.huge_expression": _v(
        1,
        "same expression",
        "a safety cap of the current converter (50 operands), not a PromQL feature",
        "n/a",
    ),
    "style.single_quoted_string": _v(
        1,
        "same as double quotes",
        "parser limitation of the current converter, not a PromQL feature",
        "n/a",
    ),
    "style.uppercase_aggregate": _v(
        1,
        "same as lower case",
        "parser limitation of the current converter (PromQL aggregators are case-insensitive)",
        "n/a",
    ),
}


def verdict_for(construct: str) -> Verdict:
    """The verdict for a construct id; an id the table does not know is not expressible."""
    return VERDICTS.get(construct) or _none("not analysed")
