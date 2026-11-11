"""Parser for the part of KQL that has a New Relic form.

    [let name = <scalar>;]... Table
      | where <predicate> | extend alias = column | project ... | summarize <aggregates> [by ...]
      | where <result column> <comparison> <number> | take / top / order by / sort by (ignored)

Predicates: `==`, `!=`, `<`, `<=`, `>`, `>=`, `contains`, `has`, `startswith`, `endswith` (each
also negated), `in`, `!in`, `between`, `matches regex`, `isempty`, `isnotempty`, `isnull`,
`isnotnull`, `ago()` on TimeGenerated, `and`, `or`, `not()` and parentheses. Aggregates: `count`,
`countif`, `dcount`, `dcountif`, `avg`, `sum`, `min`, `max`, `avgif`, `sumif`, `minif`, `maxif`,
`percentile`, `stdev`, `any`. Everything else raises `KqlUnsupported` naming the operator, so a
reason can say exactly what stopped the conversion.
"""

from dataclasses import dataclass, field

from importer.sources.azure.kql_lex import KqlSyntax, KqlUnsupported, Token, tokenize

Scalar = str | float | bool


@dataclass(frozen=True)
class Cmp:
    column: str
    op: str  # == != < <= > >= contains !contains has !has startswith ... endswith
    value: Scalar


@dataclass(frozen=True)
class InList:
    column: str
    values: tuple[Scalar, ...]
    negate: bool = False


@dataclass(frozen=True)
class Between:
    column: str
    low: float
    high: float
    negate: bool = False


@dataclass(frozen=True)
class Regex:
    column: str
    pattern: str
    negate: bool = False


@dataclass(frozen=True)
class Empty:
    column: str
    empty: bool  # True: isempty / isnull; False: isnotempty / isnotnull
    null_only: bool = False  # isnull and isnotnull do not test for an empty string


@dataclass(frozen=True)
class Not:
    inner: "Pred"


@dataclass(frozen=True)
class And:
    items: tuple["Pred", ...]


@dataclass(frozen=True)
class Or:
    items: tuple["Pred", ...]


@dataclass(frozen=True)
class AgoFilter:
    """`TimeGenerated > ago(span)`: the rule's window is the time range, so it is dropped."""

    seconds: int


Pred = Cmp | InList | Between | Regex | Empty | Not | And | Or | AgoFilter


def walk(pred: Pred) -> list[Pred]:
    """The leaf predicates under pred (comparisons, lists, regexes, empties)."""
    if isinstance(pred, And | Or):
        return [leaf for item in pred.items for leaf in walk(item)]
    if isinstance(pred, Not):
        return [Not(leaf) for leaf in walk(pred.inner)]
    return [pred]


def is_negated(leaf: Pred) -> bool:
    """A leaf that is true for a record where the column is missing in KQL but not in NRQL."""
    if isinstance(leaf, Not):
        return True
    if isinstance(leaf, Cmp):
        return leaf.op == "!=" or leaf.op.startswith("!")
    return isinstance(leaf, InList | Regex) and leaf.negate


Expr = tuple[tuple[str, str], ...]  # ("col", name) | ("num", text) | ("op", "+-*/()")


@dataclass(frozen=True)
class Agg:
    alias: str
    fn: str  # count countif dcount dcountif avg sum min max avgif sumif minif maxif percentile ...
    column: str = ""
    arg: float | None = None  # the percentile
    cond: Pred | None = None  # the condition of a ...if aggregate
    expr: Expr = ()  # an `extend` arithmetic the aggregate is taken over (column is its alias)


@dataclass(frozen=True)
class GroupBy:
    column: str
    bin_seconds: int | None = None


@dataclass
class Query:
    table: str
    rows: list[Pred] = field(default_factory=list)  # where terms before any summarize
    aggs: list[Agg] = field(default_factory=list)
    groups: list[GroupBy] = field(default_factory=list)
    summarized: bool = False
    result_filter: list[Pred] = field(default_factory=list)  # where terms after the summarize
    time_filter: int | None = None  # seconds of a dropped `TimeGenerated > ago()`
    ignored: list[str] = field(default_factory=list)  # operators that do not change an alert


AGG_FUNCTIONS = {
    "count": 0,
    "countif": 1,
    "dcount": 1,
    "dcountif": 2,
    "avg": 1,
    "sum": 1,
    "min": 1,
    "max": 1,
    "avgif": 2,
    "sumif": 2,
    "minif": 2,
    "maxif": 2,
    "percentile": 2,
    "stdev": 1,
    "any": 1,
    "take_any": 1,
}
IGNORED_AFTER = {"project", "project-away", "project-keep", "project-reorder", "project-rename"}
ORDERING = {"order", "sort", "order-by", "take", "limit", "top", "render", "project-smart"}
UNSUPPORTED_STAGES = {
    "join": "join",
    "union": "union",
    "mv-expand": "mv-expand",
    "mv-apply": "mv-apply",
    "parse": "parse",
    "parse-where": "parse",
    "parse-kv": "parse",
    "make-series": "make-series",
    "evaluate": "evaluate",
    "materialize": "materialize",
    "lookup": "lookup",
    "invoke": "invoke",
    "search": "search",
    "find": "find",
    "distinct": "distinct",
    "sample": "sample",
    "sample-distinct": "sample-distinct",
    "serialize": "serialize",
    "scan": "scan",
    "fork": "fork",
    "as": "as",
    "getschema": "getschema",
    "externaldata": "externaldata",
    "reduce": "reduce",
    "mv-expand-bag": "mv-expand",
}
CROSS_RESOURCE = {"workspace", "app", "resource", "cluster", "database", "adx", "arg", "table"}
TEXT_OPS = {"contains", "has", "startswith", "endswith"}
CASE_VARIANTS = ("_cs", "~")
COMPARE_OPS = {"==", "!=", "<>", "<", "<=", ">", ">="}
NEGATED = {"!contains", "!has", "!startswith", "!endswith"}
LAST_SEEN_FUNCTIONS = ("min", "max")
LET_SCALARS = ("ago", "dynamic", "true", "false", "toscalar", "materialize")
EXPR_AGGREGATES = ("sum", "avg", "min", "max")  # NRQL takes arithmetic inside these


class _Stream:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    @property
    def peek(self) -> Token:
        return self.tokens[self.pos]

    def rest(self) -> list[Token]:
        return self.tokens[self.pos :]

    def ahead(self, n: int = 1) -> Token:
        return self.tokens[min(self.pos + n, len(self.tokens) - 1)]

    def next(self) -> Token:
        token = self.tokens[self.pos]
        if token.kind != "eof":
            self.pos += 1
        return token

    def is_op(self, text: str) -> bool:
        return self.peek.kind == "op" and self.peek.text == text

    def is_word(self, text: str) -> bool:
        return self.peek.kind == "id" and self.peek.text.lower() == text

    def accept_op(self, text: str) -> bool:
        if self.is_op(text):
            self.pos += 1
            return True
        return False

    def accept_word(self, text: str) -> bool:
        if self.is_word(text):
            self.pos += 1
            return True
        return False

    def expect_op(self, text: str) -> None:
        if not self.accept_op(text):
            raise KqlSyntax(f"expected {text!r} but found {self.peek.text or 'the end'!r}")

    def ident(self) -> str:
        token = self.next()
        if token.kind != "id":
            raise KqlSyntax(f"expected a name but found {token.text or 'the end'!r}")
        return token.text

    @property
    def at_end(self) -> bool:
        return self.peek.kind == "eof"


def _split(tokens: list[Token], sep: str) -> list[list[Token]]:
    """Split at `sep` outside parentheses and brackets."""
    parts: list[list[Token]] = [[]]
    depth = 0
    for token in tokens:
        if token.kind == "eof":
            continue
        if token.kind == "op" and token.text in "([":
            depth += 1
        elif token.kind == "op" and token.text in ")]":
            depth -= 1
        if token.kind == "op" and token.text == sep and depth == 0:
            parts.append([])
        else:
            parts[-1].append(token)
    return [[*p, Token("eof", "")] for p in parts]


class _Parser:
    def __init__(self) -> None:
        self.lets: dict[str, Scalar | tuple[Scalar, ...] | tuple[str, int]] = {}
        self.renames: dict[str, str] = {}
        self.exprs: dict[str, tuple[Expr, bool]] = {}  # extend arithmetic, by the name it was given
        self.query = Query(table="")

    # -- names and values ---------------------------------------------------------------------
    def column(self, s: _Stream, text_ok: bool = False) -> str:
        """A plain column. `text_ok` also reads tostring(column) (a group or an extend only)."""
        name = s.ident()
        if text_ok and name.lower() == "tostring" and s.is_op("("):
            s.next()
            inner = self.column(s)
            s.expect_op(")")
            return inner
        if s.is_op("("):
            raise KqlUnsupported(f"function {name}()", "only a plain column is read here")
        if name in self.lets:
            raise KqlUnsupported("let value used as a column")
        if name in self.exprs:
            raise KqlUnsupported("extend value used outside an aggregate", name)
        return self.renames.get(name, name)

    # -- arithmetic of an extend ---------------------------------------------------------------
    def arithmetic(self, tokens: list[Token]) -> tuple[Expr, bool]:
        """`a * 2 + b` as parts; division only where KQL cannot divide integers (a real operand)."""
        s = _Stream(tokens)
        parts, real = self._sum(s)
        if not any(kind == "col" for kind, _ in parts):
            raise KqlUnsupported("extend that computes a value", "it has no column")
        if not s.at_end:
            raise KqlUnsupported("extend that computes a value", "only + - * / on columns")
        return parts, real

    def _sum(self, s: _Stream) -> tuple[Expr, bool]:
        parts, real = self._product(s)
        while s.peek.kind == "op" and s.peek.text in ("+", "-"):
            op = s.next().text
            right, right_real = self._product(s)
            parts, real = (*parts, ("op", op), *right), real or right_real
        return parts, real

    def _product(self, s: _Stream) -> tuple[Expr, bool]:
        parts, real = self._factor(s)
        while s.peek.kind == "op" and s.peek.text in ("*", "/", "%"):
            op = s.next().text
            right, right_real = self._factor(s)
            if op == "%":
                raise KqlUnsupported("modulo (%)", "NRQL arithmetic has no remainder")
            if op == "/":
                if not (real or right_real):
                    raise KqlUnsupported(
                        "integer division",
                        "KQL divides two integers as integers; write 1.0 in the division",
                    )
                if not (len(right) == 1 and right[0][0] == "num"):
                    raise KqlUnsupported(
                        "division by a column", "a zero denominator reads differently in NRQL"
                    )
                if float(right[0][1]) == 0:
                    raise KqlUnsupported("division by zero")
            parts, real = (*parts, ("op", op), *right), real or right_real
        return parts, real

    def _factor(self, s: _Stream) -> tuple[Expr, bool]:
        token = s.next()
        if token.kind == "num":
            return (("num", token.text),), "." in token.text
        if token.kind == "op" and token.text == "-":
            inner, real = self._factor(s)
            return (("op", "-"), *inner), real
        if token.kind == "op" and token.text == "(":
            inner, real = self._sum(s)
            s.expect_op(")")
            return (("op", "("), *inner, ("op", ")")), real
        if token.kind == "id" and not s.is_op("("):
            if token.text in self.exprs:
                held, real = self.exprs[token.text]
                return (("op", "("), *held, ("op", ")")), real
            if token.text in self.lets:
                raise KqlUnsupported("let value used as a column")
            return (("col", self.renames.get(token.text, token.text)),), False
        raise KqlUnsupported("extend that computes a value", "only + - * / on columns")

    def scalar(self, s: _Stream) -> Scalar:
        token = s.next()
        if token.kind == "str":
            return token.text
        if token.kind == "num":
            return token.value
        if token.kind == "op" and token.text == "-" and s.peek.kind == "num":
            return -s.next().value
        if token.kind == "id" and token.text in ("true", "false") and not s.is_op("("):
            return token.text == "true"
        if token.kind == "id" and token.text in self.lets and not s.is_op("("):
            held = self.lets[token.text]
            if isinstance(held, str | float | bool):
                return held
            raise KqlUnsupported("let value that is a list or a time span", "used as a value")
        if token.kind == "id" and s.is_op("("):
            raise KqlUnsupported(f"function {token.text}()", "only a literal value is read here")
        if token.kind == "id":
            raise KqlUnsupported("comparison of two columns")
        raise KqlSyntax(f"expected a value but found {token.text or 'the end'!r}")

    def seconds(self, s: _Stream) -> int:
        token = s.next()
        if token.kind == "span":
            return int(token.value)
        if token.kind == "id" and token.text in self.lets:
            held = self.lets[token.text]
            if isinstance(held, tuple) and len(held) == 2 and held[0] == "span":
                return int(held[1])
        raise KqlUnsupported("time span", "write it as 5m, 1h or 1d")

    def ago(self, s: _Stream) -> int:
        """`ago(5m)`, or a let name that holds one."""
        if s.peek.kind == "id" and s.peek.text in self.lets and s.ahead().text != "(":
            held = self.lets[s.next().text]
            if isinstance(held, tuple) and len(held) == 2 and held[0] == "ago":
                return int(held[1])
            raise KqlUnsupported("let value used as a time filter")
        if not s.accept_word("ago"):
            raise KqlUnsupported("time filter", "only TimeGenerated > ago(span) is read")
        s.expect_op("(")
        seconds = self.seconds(s)
        s.expect_op(")")
        return seconds

    def value_list(self, s: _Stream) -> tuple[Scalar, ...]:
        if s.accept_op("("):
            if s.is_word("dynamic") or self._is_let_list(s):
                values = self._list_operand(s)
                s.expect_op(")")
                return values
            return self._items(s, ")")
        return self._list_operand(s)

    def _is_let_list(self, s: _Stream) -> bool:
        return s.peek.kind == "id" and s.peek.text in self.lets and s.ahead().text == ")"

    def _list_operand(self, s: _Stream) -> tuple[Scalar, ...]:
        if s.accept_word("dynamic"):
            s.expect_op("(")
            s.expect_op("[")
            values = self._items(s, "]")
            s.expect_op(")")
            return values
        if s.peek.kind == "id" and s.peek.text in self.lets:
            held = self.lets[s.next().text]
            if isinstance(held, tuple) and not (len(held) == 2 and held[0] in ("ago", "span")):
                return tuple(held)
        raise KqlUnsupported("list", "write it as (a, b) or dynamic([a, b])")

    def _items(self, s: _Stream, close: str) -> tuple[Scalar, ...]:
        values: list[Scalar] = []
        while True:
            values.append(self.scalar(s))
            if s.accept_op(","):
                continue
            s.expect_op(close)
            break
        if len(values) > 200:
            raise KqlUnsupported("list with more than 200 values")
        return tuple(values)

    # -- predicates ---------------------------------------------------------------------------
    def predicate(self, s: _Stream) -> Pred:
        items = [self._and(s)]
        while s.accept_word("or"):
            items.append(self._and(s))
        return items[0] if len(items) == 1 else Or(tuple(items))

    def _and(self, s: _Stream) -> Pred:
        items = [self._not(s)]
        while s.accept_word("and"):
            items.append(self._not(s))
        return items[0] if len(items) == 1 else And(tuple(items))

    def _not(self, s: _Stream) -> Pred:
        if s.is_word("not") and s.ahead().text == "(":
            s.next()
            s.expect_op("(")
            inner = self.predicate(s)
            s.expect_op(")")
            return Not(inner)
        return self._primary(s)

    def _primary(self, s: _Stream) -> Pred:
        if s.accept_op("("):
            inner = self.predicate(s)
            s.expect_op(")")
            return inner
        head = s.peek
        if head.kind == "id" and s.ahead().text == "(":
            return self._function_predicate(s)
        column = self.column(s)
        if column == "TimeGenerated":
            return self._time(s)
        return self._comparison(s, column)

    def _function_predicate(self, s: _Stream) -> Pred:
        name = s.ident()
        lowered = name.lower()
        if lowered not in ("isempty", "isnotempty", "isnull", "isnotnull"):
            raise KqlUnsupported(f"function {name}()", "in a where term")
        s.expect_op("(")
        column = self.column(s)
        s.expect_op(")")
        return Empty(
            column,
            empty=lowered in ("isempty", "isnull"),
            null_only=lowered in ("isnull", "isnotnull"),
        )

    def _time(self, s: _Stream) -> Pred:
        token = s.next()
        if token.text in (">", ">=") and token.kind == "op":
            return AgoFilter(self.ago(s))
        raise KqlUnsupported("time filter", "only TimeGenerated > ago(span) is read")

    def _comparison(self, s: _Stream, column: str) -> Pred:
        token = s.next()
        word = token.text.lower() if token.kind == "id" else token.text
        if word in ("=~", "!~"):
            raise KqlUnsupported("case-insensitive comparison (=~)", "NRQL = is case-sensitive")
        if any(word.endswith(v) for v in CASE_VARIANTS) and word.lstrip("!") not in ("in", "!in"):
            raise KqlUnsupported(f"operator {word}", "case-sensitivity variants have no form")
        if word in ("in", "!in"):
            return InList(column, self.value_list(s), negate=word == "!in")
        if word in ("between", "!between"):
            return self._between(s, column, word == "!between")
        if word == "matches":
            if not s.accept_word("regex"):
                raise KqlSyntax("expected 'regex' after 'matches'")
            return Regex(column, str(self._string(s)))
        if word in TEXT_OPS or word in NEGATED:
            return Cmp(column, word, self._string(s))
        if word in COMPARE_OPS:
            op = "!=" if word == "<>" else word
            if s.is_word("ago"):
                raise KqlUnsupported(
                    "absence check (comparing a column with ago())",
                    "it needs a loss-of-signal condition",
                )
            value = self.scalar(s)
            if op in ("<", "<=", ">", ">=") and not isinstance(value, float):
                raise KqlUnsupported("ordered comparison with text", "only numbers are compared")
            return Cmp(column, op, value)
        raise KqlUnsupported(f"operator {word or 'end of query'}")

    def _string(self, s: _Stream) -> str:
        value = self.scalar(s)
        if not isinstance(value, str):
            raise KqlUnsupported("text operator on a number")
        return value

    def _between(self, s: _Stream, column: str, negate: bool) -> Pred:
        s.expect_op("(")
        low = self.scalar(s)
        s.expect_op("..")
        high = self.scalar(s)
        s.expect_op(")")
        if not isinstance(low, float) or not isinstance(high, float):
            raise KqlUnsupported("between", "only numbers are read")
        return Between(column, low, high, negate)

    # -- stages -------------------------------------------------------------------------------
    def where(self, tokens: list[Token]) -> None:
        s = _Stream(tokens)
        pred = self.predicate(s)
        if not s.at_end:
            raise KqlSyntax(f"unexpected {s.peek.text!r} in a where")
        if self.query.summarized:
            self.query.result_filter.append(pred)
            return
        for part in pred.items if isinstance(pred, And) else (pred,):
            if isinstance(part, AgoFilter):
                self.query.time_filter = max(self.query.time_filter or 0, part.seconds)
            else:
                self._no_nested_time(part)
                self.query.rows.append(part)

    def _no_nested_time(self, pred: Pred) -> None:
        if isinstance(pred, AgoFilter):
            raise KqlUnsupported("time filter", "inside or / not")
        for child in (pred.items if isinstance(pred, And | Or) else ()) + (
            (pred.inner,) if isinstance(pred, Not) else ()
        ):
            self._no_nested_time(child)

    def rename(self, tokens: list[Token], stage: str) -> None:
        for item in _split(tokens, ","):
            s = _Stream(item)
            if s.at_end:
                continue
            if s.peek.kind == "id" and s.ahead().text == "=":
                alias = s.ident()
                s.next()
                if s.peek.kind == "id" and s.ahead().text == "(":
                    name = s.peek.text
                    if name.startswith("series_"):
                        raise KqlUnsupported("series_* function", name)
                    if name.lower() == "tostring":
                        self.renames[alias] = self.column(s, text_ok=True)
                        self.exprs.pop(alias, None)
                        if not s.at_end:
                            raise KqlUnsupported(f"{stage} that computes a value")
                        continue
                    raise KqlUnsupported(f"function {name}()", f"in {stage}")
                if s.peek.kind != "id" or s.ahead().kind != "eof":
                    if stage == "extend" and s.peek.kind in ("id", "num", "op"):
                        self.exprs[alias] = self.arithmetic(s.rest())
                        self.renames.pop(alias, None)
                        continue
                    raise KqlUnsupported(f"{stage} that computes a value")
                self.renames[alias] = self.renames.get(s.peek.text, s.peek.text)
                self.exprs.pop(alias, None)
            elif stage == "extend":
                raise KqlUnsupported("extend that computes a value")

    def summarize(self, tokens: list[Token]) -> None:
        if self.query.summarized:
            raise KqlUnsupported("second summarize")
        sides = self._split_by(tokens)
        for item in _split(sides[0], ","):
            if len(item) > 1:
                self.query.aggs.append(self._agg(_Stream(item)))
        if not self.query.aggs and len(sides) == 1:
            raise KqlSyntax("summarize without an aggregate")
        if len(sides) > 1:
            for item in _split(sides[1], ","):
                if len(item) > 1:
                    self.query.groups.append(self._group(_Stream(item)))
        self.query.summarized = True

    @staticmethod
    def _split_by(tokens: list[Token]) -> list[list[Token]]:
        depth = 0
        for i, token in enumerate(tokens):
            if token.kind == "op" and token.text in "([":
                depth += 1
            elif token.kind == "op" and token.text in ")]":
                depth -= 1
            elif depth == 0 and token.kind == "id" and token.text.lower() == "by":
                return [[*tokens[:i], Token("eof", "")], [*tokens[i + 1 :]]]
        return [tokens]

    def _agg(self, s: _Stream) -> Agg:
        alias = ""
        if s.peek.kind == "id" and s.ahead().text == "=":
            alias = s.ident()
            s.next()
        name = s.ident()
        lowered = name.lower()
        if lowered not in AGG_FUNCTIONS or not s.is_op("("):
            raise KqlUnsupported(f"aggregate {name}()" if s.is_op("(") else "summarize item")
        s.expect_op("(")
        column, arg, cond = "", None, None
        expr: Expr = ()
        if lowered in EXPR_AGGREGATES and s.peek.kind == "id" and s.peek.text in self.exprs:
            column = s.ident()
            expr = self.exprs[column][0]
        elif lowered == "count":
            pass
        elif lowered == "countif":
            cond = self.predicate(s)
        else:
            column = self.column(s)
            if lowered in ("avgif", "sumif", "minif", "maxif", "dcountif"):
                s.expect_op(",")
                cond = self.predicate(s)
            elif lowered == "percentile":
                s.expect_op(",")
                value = self.scalar(s)
                if not isinstance(value, float) or not 0 <= value <= 100:
                    raise KqlUnsupported("percentile", "the percentile must be a number 0 to 100")
                arg = value
            elif lowered == "dcount" and s.accept_op(","):
                self.scalar(s)  # the accuracy level has no effect on the result's meaning
        s.expect_op(")")
        if not s.at_end:
            raise KqlUnsupported("expression around an aggregate")
        fn = "any" if lowered == "take_any" else lowered
        return Agg(alias or _default_alias(fn, column, arg), fn, column, arg, cond, expr)

    def _group(self, s: _Stream) -> GroupBy:
        alias = ""
        if s.peek.kind == "id" and s.ahead().text == "=":
            alias = s.ident()
            s.next()
        if s.is_word("bin") and s.ahead().text == "(":
            s.next()
            s.expect_op("(")
            column = self.column(s)
            s.expect_op(",")
            seconds = self.seconds(s)
            s.expect_op(")")
            if column != "TimeGenerated":
                raise KqlUnsupported("bin()", "only bin(TimeGenerated, span) is read")
            return GroupBy(column, seconds)
        column = self.column(s, text_ok=True)
        if not s.at_end:
            raise KqlUnsupported("expression in summarize by", "group by a plain column")
        if alias:
            self.renames[alias] = column
        return GroupBy(column)

    def stage(self, tokens: list[Token]) -> None:
        head = tokens[0]
        if head.kind != "id":
            raise KqlSyntax(f"a stage starts with {head.text or 'nothing'!r}, not an operator")
        word = head.text.lower()
        body = tokens[1:]
        if word in UNSUPPORTED_STAGES:
            raise KqlUnsupported(UNSUPPORTED_STAGES[word])
        if word.startswith("series_"):
            raise KqlUnsupported("series_* function")
        if word in ("where", "filter"):
            self.where(body)
        elif word == "extend":
            self.rename(body, "extend")
        elif word == "summarize":
            self.summarize(body)
        elif word == "count":
            if self.query.summarized:
                raise KqlUnsupported("count after a summarize")
            self.summarize([Token("id", "count_"), Token("op", "="), *self._count_call()])
        elif word in IGNORED_AFTER:
            if word in ("project", "project-rename"):
                self.rename(body, word)
            self.query.ignored.append(word)
        elif word in ORDERING:
            if (word in ("take", "limit", "top")) and not self.query.summarized:
                raise KqlUnsupported(word, "it limits the rows before they are counted")
            self.query.ignored.append(word)
        else:
            raise KqlUnsupported(f"operator {head.text}")

    @staticmethod
    def _count_call() -> list[Token]:
        return [Token("id", "count"), Token("op", "("), Token("op", ")"), Token("eof", "")]

    # -- statements ---------------------------------------------------------------------------
    def let(self, tokens: list[Token]) -> None:
        s = _Stream(tokens)
        s.next()  # let
        name = s.ident()
        s.expect_op("=")
        scalar_start = s.peek.kind in ("str", "num", "span") or s.peek.text in (
            "ago",
            "dynamic",
            "true",
            "false",
            "-",
        )
        if not scalar_start:
            if any(tok.text.lower() == "materialize" for tok in tokens):
                raise KqlUnsupported("materialize")
            raise KqlUnsupported("let that defines a table or function")
        if s.is_word("ago"):
            self.lets[name] = ("ago", self.ago(s))
        elif s.is_word("dynamic"):
            self.lets[name] = self._list_operand(s)
        elif s.peek.kind == "span":
            self.lets[name] = ("span", int(s.next().value))
        else:
            self.lets[name] = self.scalar(s)
        if not s.at_end:
            raise KqlUnsupported("let with an expression")

    def head(self, tokens: list[Token]) -> None:
        first = tokens[0]
        if first.kind != "id":
            raise KqlSyntax("the query does not start with a table name")
        if first.text.lower() in CROSS_RESOURCE and tokens[1].text == "(":
            raise KqlUnsupported("cross-resource query", f"{first.text}()")
        if first.text.lower() in ("union", "datatable", "print", "range", "externaldata"):
            raise KqlUnsupported(first.text.lower())
        if tokens[1].kind != "eof":
            raise KqlSyntax("the query does not start with a plain table name")
        if first.text in self.lets:
            raise KqlUnsupported("let that defines a table or function")
        self.query.table = first.text


def _default_alias(fn: str, column: str, arg: float | None) -> str:
    if fn in ("count", "countif"):
        return "count_"
    if fn == "percentile":
        return f"percentile_{column}_{arg:g}" if arg is not None else f"percentile_{column}"
    return f"{fn}_{column}"


def _table_let(statement: list[Token]) -> tuple[str, list[Token]] | None:
    """`let name = Table | stages` as (name, its tokens without the end), else None."""
    if len(statement) < 4 or statement[2].text != "=":
        return None
    body = statement[3:-1]
    if body[0].kind != "id" or body[0].text in LET_SCALARS:
        return None
    if len(body) > 1 and body[1].text != "|":
        return None  # a function call such as toscalar(...) or a `Table(...)` is not a table
    return statement[1].text, body


def _use_count(name: str, statements: list[list[Token]]) -> int:
    return sum(1 for st in statements for t in st if t.kind == "id" and t.text == name)


def _inline_tables(statements: list[list[Token]]) -> list[list[Token]]:
    """Replace each table `let` by its text where the next statement starts with its name.

    Only when the name is used exactly once afterwards, as the first word of a statement: the
    same rows are then read once, so the query means the same. A `let` used twice (or not at all)
    is left alone and refused by `Parser.let` with its own reason.
    """
    out = list(statements)
    i = 0
    while i < len(out) - 1:
        found = _table_let(out[i]) if out[i][0].text.lower() == "let" else None
        later = out[i + 1 :]
        if found and _use_count(found[0], later) == 1 and later[0][0].text == found[0]:
            joined = [*found[1], *later[0][1:]]
            out[i : i + 2] = [joined]
            i = max(i - 1, 0)  # the new statement may itself start with an earlier let's name
            continue
        i += 1
    return out


def parse(text: str) -> Query:
    """The query in the subset, or KqlSyntax / KqlUnsupported saying what was not understood."""
    statements = _split(tokenize(text), ";")
    if statements and statements[-1] == [Token("eof", "")]:
        statements.pop()
    if not statements:
        raise KqlSyntax("the query is empty")
    statements = _inline_tables(statements)
    parser = _Parser()
    for statement in statements[:-1]:
        if not statement[0].text.lower() == "let":
            raise KqlUnsupported("statement", f"{statement[0].text} before the query")
        parser.let(statement)
    stages = _split(statements[-1], "|")
    parser.head(stages[0])
    for stage in stages[1:]:
        if stage[0].kind == "eof":
            raise KqlSyntax("an empty stage between pipes")
        parser.stage(stage)
    return parser.query
