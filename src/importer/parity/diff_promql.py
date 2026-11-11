"""A small PromQL reference evaluator, written for the offline differential harness.

It reads the subset the converter's rewrite rules produce: rate, irate, increase, `*_over_time`,
`sum|avg|max|min|count|topk|bottomk` with `by`/`without`, `histogram_quantile`, arithmetic, and
comparison filters. It follows the documented Prometheus algorithms (extrapolated rate with
counter-reset correction, five-minute lookback), not the Prometheus source. Anything else raises
`Unmodelled`. Evidence from this module is therefore evidence against our reading of PromQL.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import pairwise
from typing import cast

from importer.parity.diff_series import Labels, Series, Unmodelled, Vector

LOOKBACK = 300
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
OVER_TIME = {"avg", "max", "min", "sum", "count"}
AGGS = {"sum", "avg", "max", "min", "count", "topk", "bottomk"}
CMP = ("==", "!=", ">=", "<=", ">", "<")
TOKEN = re.compile(
    r"""\s*(?:(?P<num>\d+(?:\.\d+)?(?:e[+-]?\d+)?)(?![\w:])|(?P<id>[A-Za-z_:][\w:.]*)|(?P<str>"(?:[^"\\]|\\.)*")
    |(?P<op>==|!=|>=|<=|=~|!~|[-+*/%^<>=(){}\[\],]))""",
    re.VERBOSE,
)


@dataclass
class Node:
    kind: str  # num sel call agg bin neg
    value: float = 0.0
    name: str = ""
    matchers: list[tuple[str, str, str]] = field(default_factory=list)
    range_s: int = 0
    args: list["Node"] = field(default_factory=list)
    by: list[str] | None = None
    without: bool = False
    bool_mod: bool = False


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.strip()
    while pos < len(text):
        if text[pos] == "[":
            end = text.index("]", pos) if "]" in text[pos:] else -1
            if end < 0:
                raise Unmodelled("unclosed range")
            out.append(("range", text[pos + 1 : end]))
            pos = end + 1
            continue
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Unmodelled(f"cannot read {text[pos : pos + 12]!r}")
        pos = m.end()
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    def __init__(self, text: str) -> None:
        if "$" in text:
            raise Unmodelled("placeholder or variable in the query")
        self.toks = _tokens(text)
        self.i = 0

    def peek(self, k: int = 0) -> tuple[str, str]:
        return self.toks[self.i + k] if self.i + k < len(self.toks) else ("end", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if tok[0] == "end" or (value is not None and tok[1] != value):
            raise Unmodelled(f"expected {value!r}, found {tok[1]!r}")
        self.i += 1
        return tok

    def parse(self) -> Node:
        node = self.cmp()
        if self.peek()[0] != "end":
            raise Unmodelled(f"unsupported syntax near {self.peek()[1]!r}")
        return node

    def cmp(self) -> Node:
        left = self.add()
        if self.peek()[1] in CMP:
            op = self.take()[1]
            flag = False
            if self.peek()[1] == "bool":
                self.take()
                flag = True
            return Node("bin", name=op, args=[left, self.add()], bool_mod=flag)
        return left

    def add(self) -> Node:
        left = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            left = Node("bin", name=op, args=[left, self.mul()])
        return left

    def mul(self) -> Node:
        left = self.unary()
        while self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            left = Node("bin", name=op, args=[left, self.unary()])
        return left

    def unary(self) -> Node:
        if self.peek()[1] == "-":
            self.take()
            return Node("neg", args=[self.unary()])
        return self.primary()

    def primary(self) -> Node:
        kind, text = self.peek()
        if kind == "num":
            self.take()
            return Node("num", value=float(text))
        if text == "(":
            self.take()
            inner = self.cmp()
            self.take(")")
            return inner
        if kind != "id":
            raise Unmodelled(f"unsupported syntax near {text!r}")
        if text in AGGS:
            return self.aggregation()
        if self.peek(1)[1] == "(":
            return self.call()
        return self.selector()

    def aggregation(self) -> Node:
        op = self.take()[1]
        by: list[str] | None = None
        without = False
        if self.peek()[1] in ("by", "without"):
            without, by = self.grouping()
        self.take("(")
        args = [self.cmp()]
        while self.peek()[1] == ",":
            self.take()
            args.append(self.cmp())
        self.take(")")
        if self.peek()[1] in ("by", "without"):
            without, by = self.grouping()
        if (op in ("topk", "bottomk")) != (len(args) == 2):
            raise Unmodelled(f"{op} takes the wrong number of arguments")
        return Node("agg", name=op, args=args, by=by, without=without)

    def grouping(self) -> tuple[bool, list[str]]:
        word = self.take()[1]
        self.take("(")
        names: list[str] = []
        while self.peek()[1] != ")":
            names.append(self.take()[1])
            if self.peek()[1] == ",":
                self.take()
        self.take(")")
        return word == "without", names

    def call(self) -> Node:
        name = self.take()[1]
        ok = name in ("rate", "irate", "increase", "histogram_quantile") or (
            name.endswith("_over_time") and name[:-10] in OVER_TIME
        )
        if not ok:
            raise Unmodelled(f"function {name}")
        self.take("(")
        args = [self.cmp()]
        while self.peek()[1] == ",":
            self.take()
            args.append(self.cmp())
        self.take(")")
        return Node("call", name=name, args=args)

    def selector(self) -> Node:
        name = self.take()[1]
        node = Node("sel", name=name)
        if self.peek()[1] == "{":
            self.take()
            while self.peek()[1] != "}":
                label = self.take()[1]
                op = self.take()[1]
                if op not in ("=", "!=", "=~", "!~"):
                    raise Unmodelled(f"matcher {op}")
                raw = self.take()[1]
                node.matchers.append((label, op, raw[1:-1]))
                if self.peek()[1] == ",":
                    self.take()
            self.take("}")
        if self.peek()[0] == "range":
            node.range_s = _seconds(self.take()[1])
        if self.peek()[1] == "offset":
            raise Unmodelled("offset")
        return node


def _seconds(text: str) -> int:
    m = re.fullmatch(r"(\d+)([smhd])", text)
    if not m:
        raise Unmodelled(f"range {text!r}")
    return int(m.group(1)) * UNITS[m.group(2)]


def parse_promql(text: str) -> Node:
    return _Parser(text).parse()


def walk(node: Node) -> list[Node]:
    out = [node]
    for arg in node.args:
        out.extend(walk(arg))
    return out


def metric_names(node: Node) -> list[str]:
    return sorted({n.name for n in walk(node) if n.kind == "sel"})


def label_names(node: Node) -> list[str]:
    names = {lab for n in walk(node) for lab, _, _ in n.matchers}
    for n in walk(node):
        names.update(n.by or [])
    return sorted(names)


def first_range(node: Node) -> int:
    return next((n.range_s for n in walk(node) if n.kind == "sel" and n.range_s), 0)


def matches(labels: dict[str, str], matchers: list[tuple[str, str, str]]) -> bool:
    for label, op, want in matchers:
        have = labels.get(label, "")
        hit = {
            "=": have == want,
            "!=": have != want,
            "=~": re.fullmatch(want, have) is not None,
            "!~": re.fullmatch(want, have) is None,
        }[op]
        if not hit:
            return False
    return True


def _window(series: Series, start: float, end: float) -> list[tuple[int, float]]:
    return [(t, v) for t, v in series.samples if start < t <= end]


def _extrapolated(
    points: list[tuple[int, float]], end: float, width: int, rate: bool
) -> float | None:
    if len(points) < 2:
        return None
    result = points[-1][1] - points[0][1]
    for (_, a), (_, b) in pairwise(points):
        if b < a:
            result += a
    first_t, last_t = points[0][0], points[-1][0]
    sampled = last_t - first_t
    avg = sampled / (len(points) - 1)
    to_start, to_end = first_t - (end - width), end - last_t
    limit = avg * 1.1
    if to_start >= limit:
        to_start = avg / 2
    if to_end >= limit:
        to_end = avg / 2
    if result > 0 and points[0][1] >= 0:
        to_start = min(to_start, sampled * (points[0][1] / result))
    result *= (sampled + to_start + to_end) / sampled
    return result / width if rate else result


def _instant_rate(points: list[tuple[int, float]]) -> float | None:
    if len(points) < 2:
        return None
    (t0, a), (t1, b) = points[-2], points[-1]
    return (b if b < a else b - a) / (t1 - t0)


def _over_time(fn: str, points: list[tuple[int, float]]) -> float | None:
    if not points:
        return None
    vals = [v for _, v in points]
    return {
        "avg": sum(vals) / len(vals),
        "max": max(vals),
        "min": min(vals),
        "sum": sum(vals),
        "count": float(len(vals)),
    }[fn]


def _selector_vector(node: Node, data: list[Series], t: float) -> Vector:
    out: Vector = {}
    for s in data:
        if s.name != node.name or not matches(dict(s.labels), node.matchers):
            continue
        if node.range_s:
            raise Unmodelled("range selector outside a function")
        pts = _window(s, t - LOOKBACK, t)
        if pts:
            out[s.labels] = pts[-1][1]
    return out


def _call(node: Node, data: list[Series], t: float) -> Vector:
    if node.name == "histogram_quantile":
        return _quantile(node, data, t)
    sel = node.args[0]
    if sel.kind != "sel" or not sel.range_s:
        raise Unmodelled(f"{node.name} needs a range selector")
    out: Vector = {}
    for s in data:
        if s.name != sel.name or not matches(dict(s.labels), sel.matchers):
            continue
        pts = _window(s, t - sel.range_s, t)
        if node.name == "rate":
            got = _extrapolated(pts, t, sel.range_s, True)
        elif node.name == "increase":
            got = _extrapolated(pts, t, sel.range_s, False)
        elif node.name == "irate":
            got = _instant_rate(pts)
        else:
            got = _over_time(node.name[:-10], pts)
        if got is not None:
            out[s.labels] = got
    return out


def quantile_from_buckets(q: float, buckets: list[tuple[float, float]]) -> float | None:
    """Prometheus histogram_quantile on cumulative buckets (upper bound, count)."""
    buckets = sorted(buckets)
    if len(buckets) < 2 or not math.isinf(buckets[-1][0]):
        return None
    total = buckets[-1][1]
    if total == 0:
        return None
    rank = q * total
    idx = next(i for i, (_, c) in enumerate(buckets) if c >= rank)
    if idx == len(buckets) - 1:
        return buckets[-2][0]
    lower = buckets[idx - 1][0] if idx else 0.0
    prev = buckets[idx - 1][1] if idx else 0.0
    upper, count = buckets[idx]
    if count == prev:
        return upper
    return lower + (upper - lower) * (rank - prev) / (count - prev)


def _quantile(node: Node, data: list[Series], t: float) -> Vector:
    q = evaluate_value(node.args[0], data, t)
    if not isinstance(q, float):
        raise Unmodelled("histogram_quantile needs a number")
    inner = evaluate_value(node.args[1], data, t)
    if isinstance(inner, float):
        raise Unmodelled("histogram_quantile needs a vector")
    groups: dict[Labels, list[tuple[float, float]]] = {}
    for labels, value in inner.items():
        d = dict(labels)
        le = d.pop("le", None)
        if le is None:
            continue
        key = tuple(sorted(d.items()))
        groups.setdefault(key, []).append((math.inf if le == "+Inf" else float(le), value))
    out: Vector = {}
    for key, buckets in groups.items():
        got = quantile_from_buckets(q, buckets)
        if got is not None:
            out[key] = got
    return out


def _key(labels: Labels, by: list[str] | None, without: bool) -> Labels:
    if by is None:
        return ()
    if without:
        return tuple((k, v) for k, v in labels if k not in by)
    return tuple((k, v) for k, v in labels if k in by)


def _agg(node: Node, data: list[Series], t: float) -> Vector:
    if node.name in ("topk", "bottomk"):
        raise Unmodelled(f"{node.name} outside an alert threshold")
    inner = evaluate_value(node.args[0], data, t)
    if isinstance(inner, float):
        raise Unmodelled("aggregation of a scalar")
    groups: dict[Labels, list[float]] = {}
    for labels, value in inner.items():
        groups.setdefault(_key(labels, node.by, node.without), []).append(value)
    reduce: dict[str, Callable[[list[float]], float]] = {
        "sum": sum,
        "avg": lambda v: sum(v) / len(v),
        "max": max,
        "min": min,
        "count": lambda v: float(len(v)),
    }
    return {k: reduce[node.name](v) for k, v in groups.items()}


def _apply(op: str, a: float, b: float) -> float | None:
    if op == "/":
        return a / b if b else None
    return {
        "+": a + b,
        "-": a - b,
        "*": a * b,
        "==": float(a == b),
        "!=": float(a != b),
        ">": float(a > b),
        "<": float(a < b),
        ">=": float(a >= b),
        "<=": float(a <= b),
    }[op]


def _binary(node: Node, data: list[Series], t: float) -> Vector | float:
    left = evaluate_value(node.args[0], data, t)
    right = evaluate_value(node.args[1], data, t)
    compare = node.name in CMP
    if isinstance(left, float) and isinstance(right, float):
        if compare:
            raise Unmodelled("comparison of two scalars")
        got = _apply(node.name, left, right)
        return math.nan if got is None else got
    pairs: list[tuple[Labels, float, float]]
    if isinstance(left, float):
        pairs = [(k, left, v) for k, v in cast(Vector, right).items()]
    elif isinstance(right, float):
        pairs = [(k, v, right) for k, v in left.items()]
    else:
        pairs = [(k, v, right[k]) for k, v in left.items() if k in right]
    out: Vector = {}
    for key, a, b in pairs:
        got = _apply(node.name, a, b)
        if got is None:
            continue
        if compare and not node.bool_mod:
            if got:
                out[key] = a if not isinstance(left, float) else b
        else:
            out[key] = got
    return out


def evaluate_value(node: Node, data: list[Series], t: float) -> Vector | float:
    if node.kind == "num":
        return node.value
    if node.kind == "sel":
        return _selector_vector(node, data, t)
    if node.kind == "call":
        return _call(node, data, t)
    if node.kind == "agg":
        return _agg(node, data, t)
    if node.kind == "neg":
        inner = evaluate_value(node.args[0], data, t)
        return -inner if isinstance(inner, float) else {k: -v for k, v in inner.items()}
    return _binary(node, data, t)


def evaluate(node: Node, data: list[Series], t: float) -> Vector:
    """The instant vector at time t. A scalar result becomes a one-element vector."""
    got = evaluate_value(node, data, t)
    return {(): got} if isinstance(got, float) else got
