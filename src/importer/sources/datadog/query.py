"""Hand-written parser for the Datadog metric monitor query, plus the other query forms.

Metric form (docs/research/datadog-verified.md section 4):

    time_aggr(window):space_aggr:metric{scope} [by {tag,...}][.fn(args)...] op number

with nested function calls, arithmetic between series, change alerts
(`pct_change(avg(last_5m),last_30m):...`) and keyword arguments such as the `anomalies()` options.
The parser never raises on text: it returns a result with `ok`, a `lossless` flag and a list of
diagnostics. `lossless` is true only when the whole text was read into the tree and nothing had to
be guessed or skipped; a translator must not rely on a result that is not lossless. Length, depth
and node count are capped, so a hostile query ends as `ok=False` with a diagnostic.

Also here: the event-search call chains (`logs(...)`, `events(...)`, `audits(...)`, ...), the
service-check chain, the composite boolean expression of monitor ids, and the SLO query.
"""

import re
from dataclasses import dataclass, replace

MAX_QUERY_CHARS = 10_000
MAX_DEPTH = 40
MAX_NODES = 2_000
MAX_SCOPE_ITEMS = 500

UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800, "mo": 2_592_000}
WINDOW = re.compile(r"(last|current|next)_(\d{1,6})(m|h|d|w|mo)")
SHIFT_AGO = re.compile(r"(\d{1,6})(m|h|d|w)_ago")
EVENT_WINDOW = re.compile(r"(\d{1,6})(m|h|d|w)")
COMPARATOR = re.compile(r"(>=|<=|==|!=|>|<)\s*([^\s<>=!]+)\s*$")
NUMBER = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")
TIME_AGGREGATORS = ("avg", "sum", "min", "max")
CHANGE_AGGREGATORS = ("change", "pct_change")
OPERATORS = {">", ">=", "<", "<=", "==", "!="}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    fatal: bool = False  # nothing usable was read
    lossy: bool = False  # something was skipped or guessed


# -- scope ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Star:
    """`{*}`: no restriction."""


@dataclass(frozen=True)
class Tag:
    key: str | None  # None: a tag without a colon (`{production}`)
    value: str
    negate: bool = False

    @property
    def wildcard(self) -> bool:
        return "*" in self.value


@dataclass(frozen=True)
class InSet:
    key: str
    values: tuple[str, ...]
    negate: bool = False


@dataclass(frozen=True)
class Not:
    item: "ScopeNode"


@dataclass(frozen=True)
class BoolOp:
    op: str  # "and" | "or"
    items: tuple["ScopeNode", ...]


ScopeNode = Star | Tag | InSet | Not | BoolOp


# -- expression ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Word:
    """A bare word or quoted string argument (`max`, `'agile'`, `null`)."""

    text: str


@dataclass(frozen=True)
class Brace:
    """A `{a, b}` argument (the by-list of a nested space aggregation)."""

    items: tuple[str, ...]


@dataclass(frozen=True)
class Kw:
    name: str
    value: "Node"


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Series:
    aggr: str  # space aggregator; "avg" when the text has none
    metric: str
    scope: ScopeNode
    group_by: tuple[str, ...] = ()
    functions: tuple[Call, ...] = ()  # the `.as_count()` style chain, in order
    explicit_aggr: bool = True


@dataclass(frozen=True)
class Bin:
    op: str
    left: "Node"
    right: "Node"


Node = Num | Word | Brace | Kw | Call | Series | Bin


@dataclass(frozen=True)
class Window:
    kind: str  # last | current | next
    seconds: int
    text: str


@dataclass(frozen=True)
class Change:
    kind: str  # change | pct_change
    inner_aggr: str
    shift_seconds: int | None
    shift_text: str


@dataclass(frozen=True)
class MetricQuery:
    ok: bool
    lossless: bool
    diagnostics: tuple[Diagnostic, ...]
    time_aggr: str = ""
    window: Window | None = None
    change: Change | None = None
    expr: Node | None = None
    op: str = ""
    threshold: float | None = None

    def series(self) -> list[Series]:
        return list(walk_series(self.expr)) if self.expr is not None else []

    def calls(self) -> list[Call]:
        return list(walk_calls(self.expr)) if self.expr is not None else []

    @property
    def metrics(self) -> list[str]:
        return list(dict.fromkeys(s.metric for s in self.series()))


def walk_series(node: Node | None):  # type: ignore[no-untyped-def]  # a small generator
    if isinstance(node, Series):
        yield node
        for fn in node.functions:
            yield from walk_series(fn)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from walk_series(arg)
    elif isinstance(node, Bin):
        yield from walk_series(node.left)
        yield from walk_series(node.right)
    elif isinstance(node, Kw):
        yield from walk_series(node.value)


def walk_calls(node: Node | None):  # type: ignore[no-untyped-def]
    if isinstance(node, Series):
        for fn in node.functions:
            yield fn
            yield from walk_calls(fn)
    elif isinstance(node, Call):
        yield node
        for arg in node.args:
            yield from walk_calls(arg)
    elif isinstance(node, Bin):
        yield from walk_calls(node.left)
        yield from walk_calls(node.right)
    elif isinstance(node, Kw):
        yield from walk_calls(node.value)


class _Abort(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def window_seconds(text: str) -> Window | None:
    """`last_5m`, `current_1h`, `next_12h` to a Window; None if it is not one."""
    m = WINDOW.fullmatch(text.strip())
    if m is None:
        return None
    return Window(m[1], int(m[2]) * UNIT_SECONDS[m[3]], text.strip())


# -- scope parser --------------------------------------------------------------------------------
_SCOPE_TOKEN = re.compile(r"\(|\)|,|[^\s(),]+")


class _ScopeParser:
    def __init__(self, text: str, diags: list[Diagnostic]) -> None:
        self.tokens = _SCOPE_TOKEN.findall(text)
        if len(self.tokens) > MAX_SCOPE_ITEMS * 4:
            raise _Abort(Diagnostic("too_long", "the scope has too many items", fatal=True))
        self.pos = 0
        self.diags = diags

    def peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str:
        tok: str = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> ScopeNode:
        if not self.tokens:
            return Star()
        node = self.or_expr(0)
        if self.peek() is not None:
            self.diags.append(Diagnostic("scope_syntax", "text after the scope", lossy=True))
        return node

    def or_expr(self, depth: int) -> ScopeNode:
        items = [self.and_expr(depth)]
        while self.peek() == "OR":
            self.take()
            items.append(self.and_expr(depth))
        return items[0] if len(items) == 1 else BoolOp("or", tuple(items))

    def and_expr(self, depth: int) -> ScopeNode:
        items = [self.unary(depth)]
        while True:
            nxt = self.peek()
            if nxt in (None, ")", "OR"):
                break
            if nxt in (",", "AND"):
                self.take()
                if self.peek() in (None, ")"):
                    break
            items.append(self.unary(depth))
        items = [i for i in items if not isinstance(i, Star)] or [Star()]
        return items[0] if len(items) == 1 else BoolOp("and", tuple(items))

    def unary(self, depth: int) -> ScopeNode:
        if depth > MAX_DEPTH:
            raise _Abort(Diagnostic("too_deep", "the scope is nested too deeply", fatal=True))
        tok = self.peek()
        if tok is None or tok in (")", ",", "OR", "AND"):
            self.diags.append(Diagnostic("scope_syntax", "an empty scope term", lossy=True))
            return Star()
        if tok == "NOT":
            self.take()
            return self.negated(self.unary(depth + 1))
        if tok == "(":
            self.take()
            inner = self.or_expr(depth + 1)
            if self.peek() == ")":
                self.take()
            else:
                self.diags.append(Diagnostic("scope_syntax", "an unclosed parenthesis", lossy=True))
            return inner
        return self.atom()

    @staticmethod
    def negated(node: ScopeNode) -> ScopeNode:
        if isinstance(node, Tag):
            return Tag(node.key, node.value, not node.negate)
        if isinstance(node, InSet):
            return InSet(node.key, node.values, not node.negate)
        if isinstance(node, Not):
            return node.item
        return Not(node)

    def atom(self) -> ScopeNode:
        word = self.take()
        if self.peek() == "IN" or (
            self.peek() == "NOT"
            and self.pos + 1 < len(self.tokens)
            and self.tokens[self.pos + 1] == "IN"
        ):
            negate = self.peek() == "NOT"
            self.pos += 2 if negate else 1
            return self.in_list(word, negate)
        negate = False
        while word[:1] in ("!", "-") and len(word) > 1:
            negate = not negate
            word = word[1:]
        if word == "*":
            return Star()
        if ":" in word:
            key, _, value = word.partition(":")
            return Tag(key, value, negate)
        return Tag(None, word, negate)

    def in_list(self, key: str, negate: bool) -> ScopeNode:
        if self.peek() != "(":
            self.diags.append(Diagnostic("scope_syntax", "IN without a list", lossy=True))
            return Tag(None, key, negate)
        self.take()
        values: list[str] = []
        while self.peek() not in (None, ")"):
            tok = self.take()
            if tok != ",":
                values.append(tok)
        if self.peek() == ")":
            self.take()
        return InSet(key, tuple(values), negate)


def parse_scope(text: str, diags: list[Diagnostic] | None = None) -> ScopeNode:
    """Parse the inside of `{...}`. Problems are appended to `diags`; never raises on text."""
    found = diags if diags is not None else []
    try:
        return _ScopeParser(text, found).parse()
    except _Abort as stop:
        found.append(stop.diagnostic)
        return Star()


# -- expression parser ---------------------------------------------------------------------------
class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.nodes = 0
        self.diags: list[Diagnostic] = []

    def fail(self, code: str, message: str) -> _Abort:
        return _Abort(Diagnostic(code, f"{message} (at character {self.pos})", fatal=True))

    def skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def peek(self) -> str:
        self.skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def count(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise _Abort(Diagnostic("too_long", "the query has too many parts", fatal=True))

    def expr(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise _Abort(Diagnostic("too_deep", "the query is nested too deeply", fatal=True))
        left = self.term(depth)
        while self.peek() in ("+", "-"):
            op = self.text[self.pos]
            self.pos += 1
            self.count()
            left = Bin(op, left, self.term(depth))
        return left

    def term(self, depth: int) -> Node:
        left = self.unary(depth)
        while self.peek() in ("*", "/"):
            op = self.text[self.pos]
            self.pos += 1
            self.count()
            left = Bin(op, left, self.unary(depth))
        return left

    def unary(self, depth: int) -> Node:
        if self.peek() == "-":
            self.pos += 1
            self.count()
            inner = self.unary(depth + 1)
            if isinstance(inner, Num):
                return Num(-inner.value)
            return Bin("-", Num(0.0), inner)
        return self.postfix(depth)

    def postfix(self, depth: int) -> Node:
        node = self.primary(depth)
        while self.peek() == ".":
            name = IDENT.match(self.text, self.pos + 1)
            if name is None:
                break
            self.pos = name.end()
            if self.peek() != "(":
                raise self.fail("syntax", "a function name without arguments")
            call = Call(name[0], self.args(depth + 1))
            self.count()
            if isinstance(node, Series):
                node = replace(node, functions=(*node.functions, call))
            else:
                node = Call(call.name, (node, *call.args))
        return node

    def args(self, depth: int) -> tuple[Node, ...]:
        """Parse `( a, b, ... )` starting at the opening parenthesis."""
        if depth > MAX_DEPTH:
            raise _Abort(Diagnostic("too_deep", "the query is nested too deeply", fatal=True))
        self.pos += 1  # (
        out: list[Node] = []
        while True:
            ch = self.peek()
            if ch == ")":
                self.pos += 1
                return tuple(out)
            if ch == "":
                raise self.fail("syntax", "an unclosed parenthesis")
            out.append(self.arg(depth))
            ch = self.peek()
            if ch == ",":
                self.pos += 1
            elif ch != ")":
                raise self.fail("syntax", "expected a comma or a closing parenthesis")

    def arg(self, depth: int) -> Node:
        ch = self.peek()
        self.count()
        if ch in ("'", '"'):
            return Word(self.string())
        if ch == "{":
            return Brace(self.brace())
        kw = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*").match(self.text, self.pos)
        if kw:
            self.pos = kw.end()
            return Kw(kw[1], self.arg(depth + 1))
        return self.expr(depth + 1)

    def string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        out: list[str] = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < len(self.text):
                out.append(self.text[self.pos + 1])
                self.pos += 2
                continue
            self.pos += 1
            if ch == quote:
                return "".join(out)
            out.append(ch)
        raise self.fail("syntax", "an unclosed string")

    def brace(self) -> tuple[str, ...]:
        end = self.text.find("}", self.pos)
        if end < 0:
            raise self.fail("syntax", "an unclosed brace")
        inner = self.text[self.pos + 1 : end]
        self.pos = end + 1
        return tuple(i.strip() for i in inner.split(",") if i.strip())

    def primary(self, depth: int) -> Node:
        ch = self.peek()
        self.count()
        if ch == "":
            raise self.fail("syntax", "the query ends too early")
        if ch == "(":
            self.pos += 1
            inner = self.expr(depth + 1)
            if self.peek() != ")":
                raise self.fail("syntax", "an unclosed parenthesis")
            self.pos += 1
            return inner
        if ch in ("'", '"'):
            return Word(self.string())
        num = NUMBER.match(self.text, self.pos)
        if num and (ch.isdigit() or ch == "."):
            self.pos = num.end()
            return Num(float(num[0]))
        name = IDENT.match(self.text, self.pos)
        if name is None:
            raise self.fail("syntax", f"unexpected character {ch!r}")
        self.pos = name.end()
        word = name[0]
        nxt = self.text[self.pos : self.pos + 1]
        if nxt == "(":
            return Call(word, self.args(depth + 1))
        if nxt == ":":
            self.pos += 1
            metric = IDENT.match(self.text, self.pos)
            if metric is None:
                raise self.fail("syntax", "a space aggregator without a metric name")
            self.pos = metric.end()
            return self.series(word, metric[0], explicit=True)
        if self.text[self.pos : self.pos + 1] == "{":
            return self.series("avg", word, explicit=False)
        return Word(word)

    def series(self, aggr: str, metric: str, *, explicit: bool) -> Series:
        scope: ScopeNode = Star()
        if self.text[self.pos : self.pos + 1] == "{":
            end = self.text.find("}", self.pos)
            if end < 0:
                raise self.fail("syntax", "an unclosed scope")
            scope = parse_scope(self.text[self.pos + 1 : end], self.diags)
            self.pos = end + 1
        else:
            self.diags.append(Diagnostic("no_scope", "the metric has no {scope}", lossy=True))
        group_by: tuple[str, ...] = ()
        by = re.compile(r"\s*by\s*\{").match(self.text, self.pos)
        if by:
            self.pos = by.end() - 1
            group_by = self.brace()
        return Series(aggr, metric, scope, group_by, (), explicit)


def _split_head(text: str) -> tuple[str, str] | None:
    """`avg(last_5m)` and the body after the colon; None when the text has no such head."""
    first = text.find("(")
    if first <= 0 or not re.fullmatch(r"[a-z_]+", text[:first].strip()):
        return None
    depth = 0
    for i in range(first, min(len(text), 200)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                rest = text[i + 1 :].lstrip()
                if rest.startswith(":"):
                    return text[: i + 1].strip(), rest[1:]
                return None
    return None


def _parse_head(head: str) -> tuple[str, Window | None, Change | None] | None:
    outer, _, inner = head.partition("(")
    inner = inner.removesuffix(")").strip()
    outer = outer.strip()
    if outer in TIME_AGGREGATORS:
        win = window_seconds(inner)
        return (outer, win, None) if win else None
    if outer in CHANGE_AGGREGATORS:
        m = re.fullmatch(r"\s*(avg|sum|min|max)\((last_\w+)\)\s*,\s*(\S+)\s*", inner)
        if m is None:
            return None
        win = window_seconds(m[2])
        shift = SHIFT_AGO.fullmatch(m[3])
        shift_window = window_seconds(m[3])
        seconds = (
            int(shift[1]) * UNIT_SECONDS[shift[2]]
            if shift
            else (shift_window.seconds if shift_window else None)
        )
        return (m[1], win, Change(outer, m[1], seconds, m[3])) if win else None
    return None


def parse_metric_query(text: str) -> MetricQuery:
    """Parse a `query alert` query. Never raises; see the module docstring for `lossless`."""
    if len(text) > MAX_QUERY_CHARS:
        d = Diagnostic("too_long", f"the query is longer than {MAX_QUERY_CHARS} characters", True)
        return MetricQuery(False, False, (d,))
    stripped = text.strip()
    cmp = COMPARATOR.search(stripped)
    if cmp is None:
        return MetricQuery(False, False, (Diagnostic("no_comparator", "no comparison", True),))
    if not NUMBER.fullmatch(cmp[2]):
        d = Diagnostic("threshold_not_number", "the threshold is not a number", fatal=True)
        return MetricQuery(False, False, (d,))
    split = _split_head(stripped[: cmp.start()])
    parsed = _parse_head(split[0]) if split else None
    if split is None or parsed is None:
        d = Diagnostic("bad_head", "no time aggregator and window before the colon", fatal=True)
        return MetricQuery(False, False, (d,))
    parser = _Parser(split[1])
    try:
        expr = parser.expr(0)
        if parser.peek() != "":
            raise parser.fail("trailing_text", "unread text after the query")
    except _Abort as stop:
        return MetricQuery(False, False, (stop.diagnostic, *parser.diags))
    aggr, win, change = parsed
    diags = tuple(parser.diags)
    if any(d.fatal for d in diags):  # a scope that was too big or too deep: nothing usable
        return MetricQuery(False, False, diags)
    return MetricQuery(
        True,
        not any(d.lossy or d.fatal for d in diags),
        diags,
        aggr,
        win,
        change,
        expr,
        cmp[1],
        float(cmp[2]),
    )


# -- event search chains: logs(...), events(...), audits(...), service checks, SLO ---------------
@dataclass(frozen=True)
class ChainCall:
    name: str
    args: tuple[str | float, ...]


@dataclass(frozen=True)
class ChainQuery:
    ok: bool
    lossless: bool
    diagnostics: tuple[Diagnostic, ...]
    head: str = ""  # logs | events | formula | error_budget | "" for a service check
    head_args: tuple[str | float, ...] = ()
    calls: tuple[ChainCall, ...] = ()
    op: str = ""
    threshold: float | None = None

    def call(self, name: str) -> ChainCall | None:
        return next((c for c in self.calls if c.name == name), None)


class _Chain(_Parser):
    def literal(self) -> str | float:
        ch = self.peek()
        if ch in ("'", '"'):
            return self.string()
        num = NUMBER.match(self.text, self.pos)
        if num:
            self.pos = num.end()
            return float(num[0])
        word = IDENT.match(self.text, self.pos)
        if word is None:
            raise self.fail("syntax", "an argument is not a string, number or word")
        self.pos = word.end()
        return word[0]

    def arglist(self) -> tuple[str | float, ...]:
        self.pos += 1  # (
        out: list[str | float] = []
        while True:
            ch = self.peek()
            if ch == ")":
                self.pos += 1
                return tuple(out)
            if ch == "":
                raise self.fail("syntax", "an unclosed parenthesis")
            out.append(self.literal())
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() != ")":
                raise self.fail("syntax", "expected a comma or a closing parenthesis")
            self.count()

    def chain(self) -> tuple[str, tuple[str | float, ...], tuple[ChainCall, ...]]:
        head = ""
        head_args: tuple[str | float, ...] = ()
        if self.peek() in ("'", '"'):
            head_args = (self.string(),)
        else:
            name = IDENT.match(self.text, self.pos) or re.compile(r"[a-z][a-z0-9_-]*").match(
                self.text, self.pos
            )
            if name is None or self.text[name.end() : name.end() + 1] != "(":
                raise self.fail("syntax", "the query does not start with a call or a string")
            self.pos = name.end()
            head = name[0]
            head_args = self.arglist()
        calls: list[ChainCall] = []
        while self.peek() == ".":
            self.pos += 1
            name = IDENT.match(self.text, self.pos)
            if name is None or self.text[name.end() : name.end() + 1] != "(":
                raise self.fail("syntax", "a method without parentheses")
            self.pos = name.end()
            calls.append(ChainCall(name[0], self.arglist()))
            if len(calls) > 40:
                raise self.fail("too_long", "too many chained calls")
        return head, head_args, tuple(calls)


def parse_chain(text: str, *, comparator: bool = True) -> ChainQuery:
    """Parse `logs("..").index("*").rollup("count").by("a").last("5m") > 3` and its relatives."""
    if len(text) > MAX_QUERY_CHARS:
        d = Diagnostic("too_long", f"the query is longer than {MAX_QUERY_CHARS} characters", True)
        return ChainQuery(False, False, (d,))
    stripped = text.strip()
    op, threshold = "", None
    if comparator:
        cmp = COMPARATOR.search(stripped)
        if cmp is None or not NUMBER.fullmatch(cmp[2]):
            d = Diagnostic("no_comparator", "no comparison with a number", fatal=True)
            return ChainQuery(False, False, (d,))
        op, threshold = cmp[1], float(cmp[2])
        stripped = stripped[: cmp.start()]
    parser = _Chain(stripped)
    try:
        head, head_args, calls = parser.chain()
        if parser.peek() != "":
            raise parser.fail("trailing_text", "unread text after the query")
    except _Abort as stop:
        return ChainQuery(False, False, (stop.diagnostic,))
    return ChainQuery(True, True, (), head, head_args, calls, op, threshold)


def event_window_seconds(text: str | float) -> int | None:
    """`5m`, `1h`, `1d` (the `.last("5m")` argument) to seconds."""
    if not isinstance(text, str):
        return None
    m = EVENT_WINDOW.fullmatch(text.strip())
    return int(m[1]) * UNIT_SECONDS[m[2]] if m else None


# -- composite -----------------------------------------------------------------------------------
@dataclass(frozen=True)
class MonitorRef:
    monitor_id: int


@dataclass(frozen=True)
class CompositeNot:
    item: "CompositeNode"


@dataclass(frozen=True)
class CompositeOp:
    op: str  # "&&" | "||"
    items: tuple["CompositeNode", ...]


CompositeNode = MonitorRef | CompositeNot | CompositeOp


@dataclass(frozen=True)
class CompositeQuery:
    ok: bool
    lossless: bool
    diagnostics: tuple[Diagnostic, ...]
    expr: CompositeNode | None = None

    @property
    def monitor_ids(self) -> list[int]:
        found: list[int] = []

        def walk(node: CompositeNode) -> None:
            if isinstance(node, MonitorRef):
                found.append(node.monitor_id)
            elif isinstance(node, CompositeNot):
                walk(node.item)
            else:
                for item in node.items:
                    walk(item)

        if self.expr is not None:
            walk(self.expr)
        return list(dict.fromkeys(found))


_COMPOSITE_TOKEN = re.compile(r"\s*(\d+|&&|\|\||!|\(|\)|\S)")


def parse_composite(text: str) -> CompositeQuery:
    """`!1234 || (5678 && 91011)`: boolean expression of monitor ids. Never raises."""
    if len(text) > MAX_QUERY_CHARS:
        d = Diagnostic("too_long", "the expression is too long", True)
        return CompositeQuery(False, False, (d,))
    tokens = _COMPOSITE_TOKEN.findall(text)
    pos = 0

    def peek() -> str:
        return tokens[pos] if pos < len(tokens) else ""

    def or_expr(depth: int) -> CompositeNode:
        nonlocal pos
        items = [and_expr(depth)]
        while peek() == "||":
            pos += 1
            items.append(and_expr(depth))
        return items[0] if len(items) == 1 else CompositeOp("||", tuple(items))

    def and_expr(depth: int) -> CompositeNode:
        nonlocal pos
        items = [unary(depth)]
        while peek() == "&&":
            pos += 1
            items.append(unary(depth))
        return items[0] if len(items) == 1 else CompositeOp("&&", tuple(items))

    def unary(depth: int) -> CompositeNode:
        nonlocal pos
        if depth > MAX_DEPTH:
            raise _Abort(Diagnostic("too_deep", "the expression is nested too deeply", True))
        tok = peek()
        pos += 1
        if tok == "!":
            return CompositeNot(unary(depth + 1))
        if tok == "(":
            inner = or_expr(depth + 1)
            if peek() != ")":
                raise _Abort(Diagnostic("syntax", "an unclosed parenthesis", True))
            pos += 1
            return inner
        if tok.isdigit():
            return MonitorRef(int(tok))
        raise _Abort(Diagnostic("syntax", "expected a monitor id", True))

    try:
        expr = or_expr(0)
        if pos != len(tokens):
            raise _Abort(Diagnostic("trailing_text", "unread text after the expression", True))
    except _Abort as stop:
        return CompositeQuery(False, False, (stop.diagnostic,))
    return CompositeQuery(True, True, (), expr)
