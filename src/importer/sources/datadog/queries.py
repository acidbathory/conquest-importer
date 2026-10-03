"""Parsers for the Datadog query forms we can convert.

Metric:     avg(last_5m):avg:system.cpu.user{env:prod} by {host} > 90
Formula:    avg(last_10m):avg:system.disk.used{*} / avg:system.disk.total{*} * 100 > 90
Log:        logs("status:error service:web").index("*").rollup("count").last("5m") > 50
APM:        trace-analytics("service:web status:error").index("trace-search")
            .rollup("count").last("5m") > 10
Heartbeat:  "datadog.agent.up".over("*").by("host").last(2).count_by_status()

Anything else raises UnsupportedQuery with a reason. Monitor text is untrusted,
so every name and value is validated against a strict character set here; the
NRQL builder validates again (defence in depth).
"""

import re
from typing import Any

from pydantic import BaseModel

from importer.core.ir import (
    Aggregation,
    BinOp,
    CountFilter,
    Expr,
    Num,
    Operator,
    Ref,
    TagFilter,
)

UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
OPERATORS = {
    ">": Operator.ABOVE,
    ">=": Operator.ABOVE_OR_EQUALS,
    "<": Operator.BELOW,
    "<=": Operator.BELOW_OR_EQUALS,
    "==": Operator.EQUALS,
    "!=": Operator.NOT_EQUALS,
}
MAX_TOKENS = 100

_NUM = r"-?\d+(?:\.\d+)?"
_OP = r"(?P<op>>=|<=|==|!=|>|<)"
_WINDOW = r"(?P<n>\d+)(?P<unit>[smhdw])"

_METRIC_OUTER = re.compile(
    rf"^(?P<agg>avg|min|max|sum)\(last_{_WINDOW}\):(?P<body>.+?)\s*{_OP}\s*(?P<thr>{_NUM})$"
)
_TERM = re.compile(
    r"(?P<space>avg|min|max|sum):(?P<metric>[A-Za-z0-9_.]+)\{(?P<scope>[^{}]*)\}"
    r"(?:\s+by\s+\{(?P<by>[^{}]*)\})?(?P<rate>\.as_(?:count|rate)\(\))?"
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_COUNT = re.compile(
    r'^(?P<fn>logs|trace-analytics)\("(?P<search>[^"]*)"\)\.index\("(?P<index>[^"]*)"\)'
    r'\.rollup\("count"\)(?:\.by\("(?P<by>[^"]*)"\))?\.last\("' + _WINDOW + r'"\)'
    rf"\s*{_OP}\s*(?P<thr>{_NUM})$"
)
_SERVICE = re.compile(
    r'^"(?P<check>[A-Za-z0-9_.]+)"\.over\("(?P<over>[^"]*)"\)(?:\.by\("(?P<by>[^"]*)"\))?'
    r"\.last\((?P<n>\d+)\)\.count_by_status\(\)$"
)
_SEARCH_TOKEN = re.compile(r"^(?P<neg>-?)@?(?P<key>[A-Za-z0-9_.]+):(?P<value>[A-Za-z0-9_.\-/*]+)$")

_FUNCTION_REASONS = {
    "anomalies(": "anomaly detection is not converted: baseline conditions behave differently "
    "and need review on real data; rebuild as a New Relic baseline condition",
    "forecast(": "forecast monitors are not supported",
    "outliers(": "outlier detection is not supported",
}
_TOKEN = re.compile(r"^[A-Za-z0-9_.\-/]+$")


class UnsupportedQuery(ValueError):
    """The query form cannot be converted automatically. The message is the reason."""


class ParsedTerm(BaseModel):
    space_aggregation: Aggregation
    metric: str
    scope: list[TagFilter]
    group_by: list[str]
    rate_hint: bool = False  # .as_count() / .as_rate() was present and ignored


class ParsedMetricQuery(BaseModel):
    time_aggregation: Aggregation
    terms: dict[str, ParsedTerm]
    expression: Expr | None  # None for a single term
    window_seconds: int
    operator: Operator
    threshold: float


class ParsedCountQuery(BaseModel):
    kind: str  # "logs" or "trace-analytics"
    filters: list[CountFilter]
    group_by: list[str]
    index: str
    window_seconds: int
    operator: Operator
    threshold: float


class ParsedServiceCheck(BaseModel):
    check: str
    group_by: list[str]
    last_n: int


def _token(text: str, what: str) -> str:
    text = text.strip()
    if not _TOKEN.match(text):
        raise UnsupportedQuery(f"{what} contains unsupported characters")
    return text


def _parse_scope(scope: str) -> list[TagFilter]:
    scope = scope.strip()
    if scope in ("", "*"):
        return []
    filters = []
    for part in scope.split(","):
        key, sep, value = part.strip().partition(":")
        if not sep or value.startswith("!") or key.startswith("!"):
            raise UnsupportedQuery("scope uses a form that is not supported (negation or bare tag)")
        filters.append(TagFilter(key=_token(key, "scope key"), value=_token(value, "scope value")))
    return filters


def _parse_group_by(text: str | None) -> list[str]:
    if not text:
        return []
    return [_token(g, "group-by key") for g in text.split(",")]


def _window(n: str, unit: str) -> int:
    return int(n) * UNIT_SECONDS[unit]


# --- metric queries and formulas -------------------------------------------------


def _tokenize(body: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(body):
        if len(tokens) > MAX_TOKENS:
            raise UnsupportedQuery("formula is too long")
        if body[pos].isspace():
            pos += 1
        elif m := _TERM.match(body, pos):
            tokens.append(("term", m))
            pos = m.end()
        elif m := _NUMBER.match(body, pos):
            tokens.append(("num", float(m.group())))
            pos = m.end()
        elif body[pos] in "+-*/()":
            tokens.append(("op", body[pos]))
            pos += 1
        else:
            raise UnsupportedQuery("query uses functions or syntax that is not supported yet")
    return tokens


class _FormulaParser:
    """expr := product (('+'|'-') product)* ; product := atom (('*'|'/') atom)*"""

    def __init__(self, tokens: list[tuple[str, Any]]) -> None:
        self._tokens = tokens
        self._pos = 0
        self.terms: dict[str, ParsedTerm] = {}

    def parse(self) -> Expr:
        expr = self._expr()
        if self._pos != len(self._tokens):
            raise UnsupportedQuery("formula could not be parsed")
        return expr

    def _peek(self) -> tuple[str, Any] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take_op(self, allowed: str) -> str | None:
        tok = self._peek()
        if tok and tok[0] == "op" and tok[1] in allowed:
            self._pos += 1
            return str(tok[1])
        return None

    def _expr(self) -> Expr:
        left = self._product()
        while op := self._take_op("+-"):
            left = BinOp(op=op, left=left, right=self._product())
        return left

    def _product(self) -> Expr:
        left = self._atom()
        while op := self._take_op("*/"):
            left = BinOp(op=op, left=left, right=self._atom())
        return left

    def _atom(self) -> Expr:
        tok = self._peek()
        if tok is None:
            raise UnsupportedQuery("formula ended unexpectedly")
        self._pos += 1
        kind, value = tok
        if kind == "num":
            return Num(value=value)
        if kind == "term":
            name = chr(ord("a") + len(self.terms))
            self.terms[name] = ParsedTerm(
                space_aggregation=Aggregation(value["space"]),
                metric=value["metric"],
                scope=_parse_scope(value["scope"]),
                group_by=_parse_group_by(value["by"]),
                rate_hint=value["rate"] is not None,
            )
            return Ref(name=name)
        if value == "(":
            inner = self._expr()
            if not self._take_op(")"):
                raise UnsupportedQuery("unbalanced parentheses in formula")
            return inner
        raise UnsupportedQuery("formula could not be parsed")


def parse_metric_query(query: str) -> ParsedMetricQuery:
    for marker, reason in _FUNCTION_REASONS.items():
        if marker in query:
            raise UnsupportedQuery(reason)
    outer = _METRIC_OUTER.match(query.strip())
    if not outer:
        raise UnsupportedQuery(f"query form not supported yet: {query[:80]!r}")
    parser = _FormulaParser(_tokenize(outer["body"]))
    expression = parser.parse()
    if not parser.terms:
        raise UnsupportedQuery("formula contains no metrics")
    if len(parser.terms) > 6:
        raise UnsupportedQuery("formula has more than 6 metrics")
    return ParsedMetricQuery(
        time_aggregation=Aggregation(outer["agg"]),
        terms=parser.terms,
        expression=None if isinstance(expression, Ref) else expression,
        window_seconds=_window(outer["n"], outer["unit"]),
        operator=OPERATORS[outer["op"]],
        threshold=float(outer["thr"]),
    )


# --- log and APM counts ---------------------------------------------------------


def _parse_search(search: str) -> list[CountFilter]:
    filters = []
    for raw in search.split():
        if raw == "AND":
            continue
        m = _SEARCH_TOKEN.match(raw)
        if not m:
            raise UnsupportedQuery(f"search syntax not supported: {raw[:40]!r}")
        value = m["value"]
        filters.append(
            CountFilter(key=m["key"], value=value, negate=bool(m["neg"]), wildcard="*" in value)
        )
    return filters


def parse_count_query(query: str) -> ParsedCountQuery:
    m = _COUNT.match(query.strip())
    if not m:
        raise UnsupportedQuery('only count rollups of the form rollup("count") are supported')
    return ParsedCountQuery(
        kind=m["fn"],
        filters=_parse_search(m["search"]),
        group_by=_parse_group_by(m["by"]),
        index=m["index"],
        window_seconds=_window(m["n"], m["unit"]),
        operator=OPERATORS[m["op"]],
        threshold=float(m["thr"]),
    )


# --- service checks ------------------------------------------------------------


def parse_service_check(query: str) -> ParsedServiceCheck:
    m = _SERVICE.match(query.strip())
    if not m:
        raise UnsupportedQuery("service check form not supported")
    if m["over"].strip() != "*":
        raise UnsupportedQuery("service checks scoped with over(...) are not supported yet")
    last_n = int(m["n"])
    if not 1 <= last_n <= 60:
        raise UnsupportedQuery("service check last(n) must be between 1 and 60")
    return ParsedServiceCheck(check=m["check"], group_by=_parse_group_by(m["by"]), last_n=last_n)
