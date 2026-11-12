"""A tiny model of what the NRQL our converter emits computes, for the differential harness.

Scope: `SELECT <expression> FROM Metric [WHERE ..] [FACET ..] [LIMIT ..] [TIMESERIES ..]` where the
expression is arithmetic over sum, average, max, min, latest, count(*), filter(), rate() and
bucketPercentile().
Storage follows docs/research/telemetry-rig-results.md: names ending `_total`, `_count`, `_sum`,
`_bucket` arrive as delta counts per sample (a reset stores the new value), everything else as a
gauge holding the sample. The query is evaluated for the single window that ends at `end`.
cardinality(metric) is modelled too, see `_cardinality` for the assumptions (UNVERIFIED live).
Sub-queries, `if()`, `SLIDE BY` effects and unknown functions raise `Unmodelled`.
"""

import re
from dataclasses import dataclass, field

from importer.parity.diff_promql import quantile_from_buckets
from importer.parity.diff_series import Labels, Series, Unmodelled, Vector, delta_stored

UNIT = {"second": 1, "seconds": 1, "minute": 60, "minutes": 60, "hour": 3600, "hours": 3600}
AGG = {"sum", "average", "max", "min", "latest", "count", "cardinality"}
TOKEN = re.compile(
    r"""\s*(?:(?P<id>`[^`]+`|[A-Za-z_][\w.:]*)|(?P<num>\d+(?:\.\d+)?)|(?P<str>'(?:[^'\\]|\\.)*')
    |(?P<op>>=|<=|!=|=|<|>|[-+*/(),]))""",
    re.VERBOSE,
)


@dataclass
class Cond:
    kind: str  # cmp and or
    attr: str = ""
    op: str = ""
    values: list[str] = field(default_factory=list)
    parts: list["Cond"] = field(default_factory=list)


@dataclass
class Expr:
    kind: str  # num bin call
    value: float = 0.0
    name: str = ""
    args: list["Expr"] = field(default_factory=list)
    where: Cond | None = None
    text: str = ""


@dataclass
class Query:
    select: Expr
    where: Cond | None
    facet: list[str]
    bucket: int | None = None


@dataclass
class Row:
    metric: str
    attrs: dict[str, str]
    t: int
    value: float


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Unmodelled(f"NRQL: cannot read {text[pos : pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind).strip("`") if kind == "id" else m.group(kind)))
    return out


class _P:
    def __init__(self, text: str) -> None:
        self.toks = _tokens(text)
        self.i = 0

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i] if self.i < len(self.toks) else ("end", "")

    def take(self, want: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok[0] == "end" or (want is not None and tok[1].upper() != want):
            raise Unmodelled(f"NRQL: expected {want!r}, found {tok[1]!r}")
        self.i += 1
        return tok

    def done(self) -> None:
        if self.peek()[0] != "end":
            raise Unmodelled(f"NRQL: unsupported syntax near {self.peek()[1]!r}")

    def expr(self) -> Expr:
        left = self.term()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            left = Expr("bin", name=op, args=[left, self.term()])
        return left

    def term(self) -> Expr:
        left = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            left = Expr("bin", name=op, args=[left, self.unary()])
        return left

    def unary(self) -> Expr:
        if self.peek()[1] == "-":
            self.take()
            return Expr("bin", name="-", args=[Expr("num"), self.unary()])
        return self.primary()

    def primary(self) -> Expr:
        kind, text = self.peek()
        if kind == "num":
            self.take()
            return Expr("num", value=float(text))
        if text == "(":
            self.take()
            inner = self.expr()
            self.take(")")
            return inner
        if kind == "id" and self.toks[self.i + 1 : self.i + 2] == [("op", "(")]:
            return self.call()
        raise Unmodelled(f"NRQL: unsupported syntax near {text!r}")

    def call(self) -> Expr:
        name = self.take()[1]
        self.take("(")
        low = name.lower()
        if low in AGG:
            arg = self.take()[1]
            self.take(")")
            return Expr("call", name="average" if low == "average" else low, text=arg)
        if low == "filter":
            inner = self.call()
            self.take(",")
            self.take("WHERE")
            cond = self.cond()
            self.take(")")
            return Expr("call", name="filter", args=[inner], where=cond)
        if low == "rate":
            inner = self.call()
            self.take(",")
            amount = float(self.take()[1])
            unit = self.take()[1].lower()
            self.take(")")
            if unit not in UNIT:
                raise Unmodelled(f"NRQL: rate unit {unit}")
            return Expr("call", name="rate", args=[inner], value=amount * UNIT[unit])
        if low == "bucketpercentile":
            arg = self.take()[1]
            self.take(",")
            pct = float(self.take()[1])
            self.take(")")
            return Expr("call", name="bucketpercentile", text=arg, value=pct)
        raise Unmodelled(f"NRQL: function {name}")

    def cond(self) -> Cond:
        left = self.atom()
        while self.peek()[1].upper() in ("AND", "OR"):
            word = self.take()[1].lower()
            left = Cond(word, parts=[left, self.atom()])
        return left

    def atom(self) -> Cond:
        attr = self.take()[1]
        negate = False
        if self.peek()[1].upper() == "NOT":
            self.take()
            negate = True
        op = self.take()[1].upper()
        if op == "IN":
            self.take("(")
            vals = [self.take()[1]]
            while self.peek()[1] == ",":
                self.take()
                vals.append(self.take()[1])
            self.take(")")
        elif op in ("=", "!=", ">", "<", ">=", "<=", "RLIKE"):
            vals = [self.take()[1]]
        else:
            raise Unmodelled(f"NRQL: operator {op}")
        if negate:
            op = f"NOT {op}"
        return Cond("cmp", attr=attr, op=op, values=[v.strip("'") for v in vals])


def parse_nrql(text: str) -> Query:
    if re.search(r"\bFROM\s*\(", text, re.IGNORECASE):
        raise Unmodelled("NRQL: sub-query")
    m = re.fullmatch(r"\s*SELECT\s+(.+?)\s+FROM\s+Metric\b(.*)", text, re.DOTALL | re.IGNORECASE)
    if not m:
        raise Unmodelled("NRQL: not a SELECT .. FROM Metric query")
    select = _P(m.group(1))
    expr = select.expr()
    select.done()
    where: Cond | None = None
    facet: list[str] = []
    bucket: int | None = None
    parts = re.split(r"\b(WHERE|FACET|LIMIT|TIMESERIES|SINCE)\b", m.group(2), flags=re.IGNORECASE)
    for word, body in zip(parts[1::2], parts[2::2], strict=True):
        word = word.upper()
        if word == "WHERE":
            p = _P(body)
            where = p.cond()
            p.done()
        elif word == "FACET":
            facet = [t.strip().strip("`") for t in body.split(",")]
        elif word == "TIMESERIES":
            m2 = re.match(r"\s*(\d+)\s+(\w+)", body)
            if m2 and m2.group(2).lower() in UNIT:
                bucket = int(m2.group(1)) * UNIT[m2.group(2).lower()]
        elif word == "SINCE":
            raise Unmodelled("NRQL: SINCE")
    if parts[0].strip():
        raise Unmodelled("NRQL: text before the clauses")
    return Query(expr, where, facet, bucket)


def stored_rows(series: Series) -> list[Row]:
    """What New Relic holds for the series: deltas for count-like names, else the samples."""
    attrs = dict(series.labels)
    if not delta_stored(series.name):
        return [Row(series.name, attrs, t, v) for t, v in series.samples]
    rows: list[Row] = []
    for (_, prev), (t, v) in zip(series.samples, series.samples[1:], strict=False):
        rows.append(Row(series.name, attrs, t, v if v < prev else v - prev))
    return rows


def _test(cond: Cond | None, row: Row) -> bool:
    if cond is None:
        return True
    if cond.kind == "and":
        return all(_test(c, row) for c in cond.parts)
    if cond.kind == "or":
        return any(_test(c, row) for c in cond.parts)
    if cond.attr == row.metric:
        have: str | float | None = row.value
    else:
        have = row.attrs.get(cond.attr)
    if have is None:
        return False
    positive = cond.op.removeprefix("NOT ")
    if positive == "IN":
        hit = str(have) in cond.values
    elif positive == "RLIKE":
        hit = re.fullmatch(cond.values[0], str(have)) is not None
    elif isinstance(have, float):
        want = float(cond.values[0])
        hit = {
            "=": have == want,
            "!=": have != want,
            ">": have > want,
            "<": have < want,
            ">=": have >= want,
            "<=": have <= want,
        }[positive]
    else:
        hit = {"=": have == cond.values[0], "!=": have != cond.values[0]}.get(positive, False)
        if positive not in ("=", "!="):
            raise Unmodelled("NRQL: numeric comparison on a label")
    return not hit if cond.op.startswith("NOT ") else hit


def _cardinality(rows: list[Row]) -> float:
    """cardinality(metric): the number of distinct dimension combinations among the rows.

    Source: docs.newrelic.com NRQL reference, "cardinality()", raw page
    https://raw.githubusercontent.com/newrelic/docs-website/develop/src/content/docs/nrql/nrql-syntax-clauses-functions.mdx
    which says only: "obtain the number of combinations of all the dimensions (attributes) on a
    metric". UNVERIFIED against a live account. Assumptions, all ours:
    1. It is evaluated over the rows of each FACET group and each TIMESERIES bucket (the rows that
       reported in that slice), so a series with no sample in the bucket is not counted.
    2. A dimension combination is the full label set of the series, so one combination is one
       series; a label that is not in the FACET still separates combinations.
    3. Samples are not weighted: a series counts once however many samples it has.
    """
    return float(len({tuple(sorted(r.attrs.items())) for r in rows}))


def _call(e: Expr, rows: list[Row], window: int) -> float | None:
    if e.name == "filter":
        return _call(e.args[0], [r for r in rows if _test(e.where, r)], window)
    if e.name == "rate":
        inner = _call(e.args[0], rows, window)
        return None if inner is None else inner / window * e.value
    if e.name == "bucketpercentile":
        sums: dict[str, float] = {}
        for r in rows:
            if r.metric == e.text and "le" in r.attrs:
                sums[r.attrs["le"]] = sums.get(r.attrs["le"], 0.0) + r.value
        buckets = [(float("inf") if k == "+Inf" else float(k), v) for k, v in sums.items()]
        return quantile_from_buckets(e.value / 100, buckets)
    mine = [r for r in rows if e.text == "*" or r.metric == e.text]
    if not mine:
        return None
    vals = [r.value for r in mine]
    if e.name == "cardinality":
        return _cardinality(mine)
    if e.name == "count":
        return float(len(mine))
    if e.name == "latest":
        return max(mine, key=lambda r: r.t).value
    reduced = {
        "sum": sum(vals),
        "average": sum(vals) / len(vals),
        "max": max(vals),
        "min": min(vals),
    }
    return reduced[e.name]


def _eval(e: Expr, rows: list[Row], window: int) -> float | None:
    if e.kind == "num":
        return e.value
    if e.kind == "call":
        return _call(e, rows, window)
    a = _eval(e.args[0], rows, window)
    b = _eval(e.args[1], rows, window)
    if a is None or b is None:
        return None
    if e.name == "/":
        return a / b if b else None
    return {"+": a + b, "-": a - b, "*": a * b}[e.name]


def run_nrql(query: Query, data: list[Series], end: float, window: int) -> Vector:
    """One value per FACET group for the window (end - window, end]."""
    rows = [
        r
        for s in data
        for r in stored_rows(s)
        if end - window < r.t <= end and _test(query.where, r)
    ]
    groups: dict[Labels, list[Row]] = {}
    for r in rows:
        key = tuple(sorted((f, r.attrs.get(f, "")) for f in query.facet))
        groups.setdefault(key, []).append(r)
    out: Vector = {}
    for key, members in groups.items():
        got = _eval(query.select, members, window)
        if got is not None:
            out[key] = got
    return out


def run_nrql_buckets(
    query: Query, data: list[Series], start: int, end: int, bucket: int
) -> dict[int, Vector]:
    """TIMESERIES: one Vector per bucket (t - bucket, t] for each bucket end t in (start, end]."""
    return {t: run_nrql(query, data, t, bucket) for t in range(start + bucket, end + 1, bucket)}


def facet_names(query: Query) -> list[str]:
    return query.facet


def where_attrs(query: Query) -> set[str]:
    found: set[str] = set()
    stack = [query.where]
    while stack:
        c = stack.pop()
        if c is None:
            continue
        if c.kind == "cmp":
            found.add(c.attr)
        stack.extend(c.parts)
    return found
