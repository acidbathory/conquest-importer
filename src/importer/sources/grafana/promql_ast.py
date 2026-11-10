"""A small PromQL tokenizer and parser that returns an AST (research note: promql-to-nrql-census).

This is not an evaluator and not the converter. It reads the grammar of Prometheus 2.x/3.x
(selectors with the four matcher operators, range vectors, subqueries, `offset`, `@`, function
calls, aggregations with `by`/`without` before or after the arguments, binary operators with
`bool`, `on`/`ignoring` and `group_left`/`group_right`, set operators, unary signs) plus the
Grafana variables a dashboard query contains (`$x`, `${x}`, `${x:fmt}`, `[[x]]`, and the
built-ins `$__interval`, `$__rate_interval`, `$__range`). A variable is kept as text in a range,
a name or a matcher value, and becomes a `Placeholder` where an operand would stand. Nothing is
looked up by name here: a function is a `Call` whatever its name, so a rule decides what it means.

Input is untrusted: length and nesting are capped and every failure is a `PromQLSyntaxError`.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace

MAX_LENGTH = 20_000
MAX_DEPTH = 100

AGGREGATORS = frozenset(
    {
        "sum",
        "min",
        "max",
        "avg",
        "group",
        "stddev",
        "stdvar",
        "count",
        "count_values",
        "bottomk",
        "topk",
        "quantile",
        "limitk",
        "limit_ratio",
    }
)
PARAMETER_AGGREGATORS = frozenset(
    {"count_values", "bottomk", "topk", "quantile", "limitk", "limit_ratio"}
)
SET_OPERATORS = frozenset({"and", "or", "unless"})
COMPARISONS = frozenset({"==", "!=", ">", "<", ">=", "<="})
PRECEDENCE = {
    "or": 1,
    "and": 2,
    "unless": 2,
    "==": 3,
    "!=": 3,
    ">": 3,
    "<": 3,
    ">=": 3,
    "<=": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
    "atan2": 5,
    "^": 6,
}
VARIABLE = re.compile(r"\$\{(\w+)(?::[^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
_VAR_REF = re.compile(r"\$\{\w+(?::[^}]*)?\}|\$\w+")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_DURATION = re.compile(r"(?:\d+(?:ms|s|m|h|d|w|y))+(?![\w$])")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_:]")
_OPERATORS = ("==", "!=", ">=", "<=", "=~", "!~")
_SINGLE = "=><+-*/%^@(){}[],:"


class PromQLSyntaxError(ValueError):
    """The text is not PromQL this parser reads."""


# --- the tree ---


@dataclass(frozen=True)
class Number:
    text: str


@dataclass(frozen=True)
class StringLit:
    value: str


@dataclass(frozen=True)
class Placeholder:
    """A Grafana variable standing where an operand (a number or a whole metric) would."""

    name: str


@dataclass(frozen=True)
class Matcher:
    label: str
    op: str  # = != =~ !~
    value: str


def variables_in(text: str) -> tuple[str, ...]:
    """Names of the Grafana variables written in `text`, in order, without repeats."""
    names = [m[1] or m[2] or m[3] for m in VARIABLE.finditer(text)]
    return tuple(dict.fromkeys(names))


@dataclass(frozen=True)
class Selector:
    """A vector selector; with `range` set it is a range vector (`x[5m]`)."""

    name: str
    matchers: tuple[Matcher, ...] = ()
    range: str | None = None
    offset: str | None = None
    at: str | None = None

    @property
    def variables(self) -> tuple[str, ...]:
        parts = [self.name, *(m.value for m in self.matchers), self.range or "", self.offset or ""]
        return variables_in(" ".join(parts))


@dataclass(frozen=True)
class Subquery:
    expr: "Node"
    range: str
    step: str  # "" when the default step is used
    offset: str | None = None
    at: str | None = None


@dataclass(frozen=True)
class Call:
    func: str
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Aggregate:
    op: str
    expr: "Node"
    param: "Node | None" = None
    grouping: str | None = None  # "by", "without" or None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Matching:
    kind: str | None  # "on", "ignoring" or None (only a group modifier was written)
    labels: tuple[str, ...] = ()
    group: str | None = None  # "group_left", "group_right" or None
    group_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Binary:
    op: str
    lhs: "Node"
    rhs: "Node"
    bool_modifier: bool = False
    matching: Matching | None = None


@dataclass(frozen=True)
class Unary:
    op: str
    expr: "Node"


@dataclass(frozen=True)
class Paren:
    expr: "Node"


Node = (
    Number
    | StringLit
    | Placeholder
    | Selector
    | Subquery
    | Call
    | Aggregate
    | Binary
    | Unary
    | Paren
)


def children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, Binary):
        return (node.lhs, node.rhs)
    if isinstance(node, Aggregate):
        return (node.param, node.expr) if node.param is not None else (node.expr,)
    if isinstance(node, Call):
        return node.args
    if isinstance(node, (Subquery, Unary, Paren)):
        return (node.expr,)
    return ()


def walk(node: Node) -> Iterator[Node]:
    """Every node, parents before children, left to right."""
    yield node
    for child in children(node):
        yield from walk(child)


# --- tokens ---


@dataclass(frozen=True)
class Token:
    kind: str  # num dur str word var op end
    text: str
    pos: int


def _scan_word(text: str, i: int) -> int:
    """End of a name starting at `i`: letters, digits, `_`, `:` and embedded variables."""
    start, j = i, i
    while j < len(text):
        found = _VAR_REF.match(text, j)
        if found:
            j = found.end()
            continue
        if _NAME_CHAR.match(text[j]):
            if text[j] == ":" and j == _end_of_lone_variable(text, start):
                break  # `$w:5m` in a subquery: the colon is the separator
            j += 1
            continue
        break
    return j


def _end_of_lone_variable(text: str, start: int) -> int:
    found = _VAR_REF.match(text, start)
    return found.end() if found else -1


def _scan_string(text: str, start: int) -> int:
    """Index of the quote that closes the string opened at `start`."""
    quote, j = text[start], start + 1
    while j < len(text) and text[j] != quote:
        j += 2 if text[j] == "\\" and quote != "`" else 1
    if j >= len(text):
        raise PromQLSyntaxError(f"unterminated string at {start}")
    return j


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_LENGTH:
        raise PromQLSyntaxError("expression is too long")
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "#":
            while i < len(text) and text[i] != "\n":
                i += 1
        elif ch in "\"'`":
            end = _scan_string(text, i)
            tokens.append(Token("str", text[i + 1 : end], i))
            i = end + 1
        elif (found := VARIABLE.match(text, i)) and found.group(0).startswith("[["):
            tokens.append(Token("var", found.group(0), i))
            i = found.end()
        elif found := _DURATION.match(text, i):
            tokens.append(Token("dur", found.group(0), i))
            i = found.end()
        elif (found := _NUMBER.match(text, i)) and not _NAME_CHAR.match(
            text[found.end() : found.end() + 1] or " "
        ):
            tokens.append(Token("num", found.group(0), i))
            i = found.end()
        elif ch.isalpha() or ch in "_$" or (ch == ":" and text[i + 1 : i + 2].isalpha()):
            j = _scan_word(text, i)
            if j == i:
                raise PromQLSyntaxError(f"unexpected {ch!r} at {i}")
            word = text[i:j]
            lone = _VAR_REF.fullmatch(word)
            tokens.append(Token("var" if lone else "word", word, i))
            i = j
        elif text[i : i + 2] in _OPERATORS:
            tokens.append(Token("op", text[i : i + 2], i))
            i += 2
        elif ch in _SINGLE:
            tokens.append(Token("op", ch, i))
            i += 1
        else:
            raise PromQLSyntaxError(f"unexpected {ch!r} at {i}")
    tokens.append(Token("end", "", len(text)))
    return tokens


def _variable_name(raw: str) -> str:
    found = VARIABLE.fullmatch(raw)
    if found is None:
        return raw
    return found[1] or found[2] or found[3]


# --- the parser ---


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0
        self.depth = 0

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def peek(self, n: int = 1) -> Token:
        return self.tokens[min(self.i + n, len(self.tokens) - 1)]

    def next(self) -> Token:
        token = self.tok
        self.i += 1
        return token

    def fail(self, what: str) -> PromQLSyntaxError:
        return PromQLSyntaxError(f"{what} at {self.tok.pos}")

    def is_op(self, text: str) -> bool:
        return self.tok.kind == "op" and self.tok.text == text

    def is_word(self, *words: str) -> bool:
        return self.tok.kind == "word" and self.tok.text.lower() in words

    def expect_op(self, text: str) -> None:
        if not self.is_op(text):
            raise self.fail(f"expected {text!r}")
        self.i += 1

    # -- expressions --

    def parse(self) -> Node:
        node = self.expr(1)
        if self.tok.kind != "end":
            raise self.fail("unexpected input")
        return node

    def operator(self) -> str | None:
        token = self.tok
        if token.kind == "op" and token.text in PRECEDENCE:
            return token.text
        if token.kind == "word" and token.text.lower() in (*SET_OPERATORS, "atan2"):
            return token.text.lower()
        return None

    def expr(self, min_precedence: int) -> Node:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise PromQLSyntaxError("expression is nested too deeply")
        lhs = self.unary()
        while (op := self.operator()) is not None and PRECEDENCE[op] >= min_precedence:
            self.i += 1
            bool_modifier = False
            if op in COMPARISONS and self.is_word("bool"):
                bool_modifier = True
                self.i += 1
            matching = self.matching()
            rhs = self.expr(PRECEDENCE[op] + (0 if op == "^" else 1))
            lhs = Binary(op, lhs, rhs, bool_modifier, matching)
        self.depth -= 1
        return lhs

    def matching(self) -> Matching | None:
        kind: str | None = None
        labels: tuple[str, ...] = ()
        if self.is_word("on", "ignoring"):
            kind = self.next().text.lower()
            labels = self.label_list()
        group: str | None = None
        group_labels: tuple[str, ...] = ()
        if self.is_word("group_left", "group_right"):
            group = self.next().text.lower()
            if self.is_op("("):
                group_labels = self.label_list()
        if kind is None and group is None:
            return None
        return Matching(kind, labels, group, group_labels)

    def label_list(self) -> tuple[str, ...]:
        self.expect_op("(")
        labels: list[str] = []
        while not self.is_op(")"):
            if self.tok.kind not in ("word", "var", "str"):
                raise self.fail("expected a label name")
            labels.append(self.next().text)
            if self.is_op(","):
                self.i += 1
            elif not self.is_op(")"):
                raise self.fail("expected ',' or ')'")
        self.i += 1
        return tuple(labels)

    def unary(self) -> Node:
        if self.tok.kind == "op" and self.tok.text in "+-":
            op = self.next().text
            return Unary(op, self.expr(PRECEDENCE["^"]))
        return self.postfix(self.primary())

    # -- operands --

    def primary(self) -> Node:
        token = self.tok
        if token.kind == "num":
            self.i += 1
            return Number(token.text)
        if token.kind == "str":
            self.i += 1
            return StringLit(token.text)
        if token.kind == "op" and token.text == "(":
            self.i += 1
            inner = self.expr(1)
            self.expect_op(")")
            return Paren(inner)
        if token.kind == "op" and token.text == "{":
            return Selector("", self.matchers())
        if token.kind == "var":
            self.i += 1
            if self.is_op("{"):
                return Selector(token.text, self.matchers())
            return Placeholder(_variable_name(token.text))
        if token.kind == "word":
            return self.word()
        raise self.fail("expected an expression")

    def word(self) -> Node:
        token = self.next()
        lower = token.text.lower()
        if lower in AGGREGATORS and (self.is_op("(") or self.is_word("by", "without")):
            return self.aggregate(lower)
        if self.is_op("("):
            return Call(token.text, self.arguments())
        if lower in ("inf", "nan") and not self.is_op("{"):
            return Number(token.text)
        matchers = self.matchers() if self.is_op("{") else ()
        return Selector(token.text, matchers)

    def arguments(self) -> tuple[Node, ...]:
        self.expect_op("(")
        args: list[Node] = []
        while not self.is_op(")"):
            args.append(self.expr(1))
            if self.is_op(","):
                self.i += 1
                if self.is_op(")"):
                    raise self.fail("trailing comma")
            elif not self.is_op(")"):
                raise self.fail("expected ',' or ')'")
        self.i += 1
        return tuple(args)

    def aggregate(self, op: str) -> Aggregate:
        grouping: str | None = None
        labels: tuple[str, ...] = ()
        if self.is_word("by", "without"):
            grouping = self.next().text.lower()
            labels = self.label_list()
        args = self.arguments()
        if grouping is None and self.is_word("by", "without"):
            grouping = self.next().text.lower()
            labels = self.label_list()
        want = 2 if op in PARAMETER_AGGREGATORS else 1
        if len(args) != want:
            raise self.fail(f"{op} takes {want} argument(s)")
        if want == 2:
            return Aggregate(op, args[1], args[0], grouping, labels)
        return Aggregate(op, args[0], None, grouping, labels)

    def matchers(self) -> tuple[Matcher, ...]:
        self.expect_op("{")
        found: list[Matcher] = []
        while not self.is_op("}"):
            label = self.next()
            if label.kind not in ("word", "var", "str"):
                raise self.fail("expected a label name")
            if self.tok.kind == "op" and self.tok.text in ("=", "!=", "=~", "!~"):
                op = self.next().text
                if self.tok.kind != "str":
                    raise self.fail("expected a quoted label value")
                found.append(Matcher(label.text, op, self.next().text))
            elif label.kind == "str":
                found.append(Matcher("__name__", "=", label.text))  # a quoted metric name
            else:
                raise self.fail("expected a matcher operator")
            if self.is_op(","):
                self.i += 1
            elif not self.is_op("}"):
                raise self.fail("expected ',' or '}'")
        self.i += 1
        return tuple(found)

    # -- range, subquery, offset, @ --

    def duration(self) -> str:
        if self.tok.kind in ("dur", "var"):
            return self.next().text
        if self.tok.kind == "num" and self.tok.text.isdigit():
            return self.next().text  # a bare number of seconds
        raise self.fail("expected a duration")

    def postfix(self, node: Node) -> Node:
        while True:
            if self.is_op("["):
                self.i += 1
                window = self.duration()
                if self.is_op(":"):
                    self.i += 1
                    step = "" if self.is_op("]") else self.duration()
                    self.expect_op("]")
                    node = Subquery(node, window, step)
                    continue
                self.expect_op("]")
                if not isinstance(node, Selector) or node.range is not None:
                    raise self.fail("a range needs a plain selector")
                node = replace(node, range=window)
            elif self.is_word("offset"):
                self.i += 1
                negative = self.is_op("-")
                if negative:
                    self.i += 1
                value = ("-" if negative else "") + self.duration()
                node = self.modify(node, offset=value)
            elif self.is_op("@"):
                self.i += 1
                node = self.modify(node, at=self.at_value())
            else:
                return node

    def modify(self, node: Node, offset: str | None = None, at: str | None = None) -> Node:
        if not isinstance(node, (Selector, Subquery)):
            raise self.fail("offset and @ need a selector or a subquery")
        return replace(node, offset=offset or node.offset, at=at or node.at)

    def at_value(self) -> str:
        sign = ""
        if self.tok.kind == "op" and self.tok.text in "+-":
            sign = self.next().text
        if self.tok.kind == "num":
            return sign + self.next().text
        if self.is_word("start", "end") and self.peek().text == "(":
            name = self.next().text.lower()
            self.expect_op("(")
            self.expect_op(")")
            return f"{name}()"
        raise self.fail("expected a timestamp, start() or end()")


def parse(text: str) -> Node:
    """The AST of one PromQL expression, or `PromQLSyntaxError`."""
    return _Parser(tokenize(text)).parse()


# --- text ---


def _labels(labels: tuple[str, ...]) -> str:
    return "(" + ", ".join(labels) + ")"


def _operand(node: Node) -> str:
    text = format_expr(node)
    return f"({text})" if isinstance(node, Binary) else text


def _quote(value: str) -> str:
    return '"' + value + '"'  # the raw text kept by the tokenizer, escapes included


def format_expr(node: Node) -> str:
    """PromQL text for a tree; binary operands are bracketed so precedence is visible."""
    if isinstance(node, Number):
        return node.text
    if isinstance(node, StringLit):
        return _quote(node.value)
    if isinstance(node, Placeholder):
        return "$" + node.name
    if isinstance(node, Selector):
        body = ", ".join(f"{m.label}{m.op}{_quote(m.value)}" for m in node.matchers)
        text = node.name + ("{" + body + "}" if body or not node.name else "")
        text += f"[{node.range}]" if node.range else ""
        return text + _suffix(node.offset, node.at)
    if isinstance(node, Subquery):
        return f"{format_expr(node.expr)}[{node.range}:{node.step}]" + _suffix(node.offset, node.at)
    if isinstance(node, Call):
        return f"{node.func}({', '.join(format_expr(a) for a in node.args)})"
    if isinstance(node, Aggregate):
        args = ", ".join(format_expr(a) for a in (node.param, node.expr) if a is not None)
        group = f" {node.grouping} {_labels(node.labels)}" if node.grouping else ""
        return f"{node.op}{group}({args})"
    if isinstance(node, Binary):
        mod = " bool" if node.bool_modifier else ""
        if node.matching is not None:
            m = node.matching
            mod += f" {m.kind} {_labels(m.labels)}" if m.kind else ""
            mod += f" {m.group} {_labels(m.group_labels)}" if m.group else ""
        return f"{_operand(node.lhs)} {node.op}{mod} {_operand(node.rhs)}"
    if isinstance(node, Unary):
        return node.op + _operand(node.expr)
    return f"({format_expr(node.expr)})"


def _suffix(offset: str | None, at: str | None) -> str:
    return (f" offset {offset}" if offset else "") + (f" @ {at}" if at else "")
