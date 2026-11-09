"""A strict parser for Dynatrace metric selectors. The selector is untrusted text.

The grammar is small and read by hand, one character at a time, so it cannot backtrack: the work
is linear in the length, which is capped. Nesting, operands, transformations and arguments are
capped too. Anything outside the grammar raises `SelectorError`, and the message never repeats
the input (only names that matched the identifier alphabet).

    expr      := product (("+" | "-") product)*
    product   := operand (("*" | "/") operand)*
    operand   := number | series | "(" expr ")" transform*
    series    := key transform*
    transform := ":" name [ "(" [arg ("," arg)*] ")" ]
    arg       := string | number | name [ "(" [arg ("," arg)*] ")" ]
    key       := [namespace ":"] [A-Za-z0-9_.-]+       namespace: builtin ext calc func
    string    := '"' ... '"'  with ~" and ~~ as the only escapes

What a transformation means is decided in selector_query.py; this module only reads syntax.
"""

from dataclasses import dataclass

MAX_LENGTH = 4000
MAX_NESTING = 16
MAX_TRANSFORMS = 40  # per series
MAX_OPERANDS = 40  # series and numbers in one expression
MAX_ARGS = 300  # per call
MAX_STRING = 1000
MAX_KEY = 200
MAX_NUMBER = 40

NAMESPACES = frozenset({"builtin", "ext", "calc", "func"})
LETTERS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
DIGITS = "0123456789"
KEY_CHARS = LETTERS + DIGITS + ".-"
NAME_CHARS = LETTERS + DIGITS
UNIT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_UNIT = 3


class SelectorError(ValueError):
    """The selector is outside the supported grammar; the message is safe to show."""


@dataclass(frozen=True)
class Lit:
    value: str


@dataclass(frozen=True)
class Number:
    value: float
    unit: str = ""


@dataclass(frozen=True)
class Ident:
    name: str


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Arg", ...]


Arg = Lit | Number | Ident | Call


@dataclass(frozen=True)
class Transform:
    name: str
    args: tuple[Arg, ...]


@dataclass(frozen=True)
class Series:
    key: str
    transforms: tuple[Transform, ...]


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Expr"
    right: "Expr"


Expr = Series | Number | Binary


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0
        self.operands = 0

    def peek(self) -> str:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1
        return self.s[self.i] if self.i < len(self.s) else ""

    def take(self, expected: str) -> None:
        if self.peek() != expected:
            raise SelectorError(f"expected {expected!r} in the metric selector")
        self.i += 1

    def run(self) -> Expr:
        found = self.expr(0)
        rest = self.peek()
        if rest == ",":
            raise SelectorError("several metrics in one selector are not supported")
        if rest:
            raise SelectorError("unexpected text after the metric selector expression")
        return found

    def expr(self, depth: int) -> Expr:
        self._deep(depth)
        left = self.product(depth)
        while self.peek() in ("+", "-"):
            op = self.s[self.i]
            self.i += 1
            left = Binary(op, left, self.product(depth))
        return left

    def product(self, depth: int) -> Expr:
        left = self.operand(depth)
        while self.peek() in ("*", "/"):
            op = self.s[self.i]
            self.i += 1
            left = Binary(op, left, self.operand(depth))
        return left

    def _deep(self, depth: int) -> None:
        if depth > MAX_NESTING:
            raise SelectorError(f"the metric selector is nested more than {MAX_NESTING} levels")

    def _operand_counted(self) -> None:
        self.operands += 1
        if self.operands > MAX_OPERANDS:
            raise SelectorError(f"the metric selector has more than {MAX_OPERANDS} operands")

    def operand(self, depth: int) -> Expr:
        self._operand_counted()
        char = self.peek()
        if char == "(":
            self.i += 1
            inner = self.expr(depth + 1)
            self.take(")")
            if self.s[self.i : self.i + 1] != ":":
                return inner
            if not isinstance(inner, Series):
                raise SelectorError("a transformation after arithmetic is not supported")
            return Series(inner.key, (*inner.transforms, *self.transforms(depth)))
        if char and (char in DIGITS or char == "-"):
            return self.number()
        if char and char in LETTERS:
            return Series(self.key(), self.transforms(depth))
        raise SelectorError("expected a metric key, a number or '(' in the metric selector")

    def _run_of(self, chars: str, limit: int, what: str) -> str:
        start = self.i
        while self.i < len(self.s) and self.s[self.i] in chars:
            self.i += 1
            if self.i - start > limit:
                raise SelectorError(f"a {what} in the metric selector is too long")
        return self.s[start : self.i]

    def key(self) -> str:
        first = self._run_of(KEY_CHARS, MAX_KEY, "metric key")
        if first in NAMESPACES and self.s[self.i : self.i + 1] == ":":
            self.i += 1
            rest = self._run_of(KEY_CHARS, MAX_KEY, "metric key")
            if not rest:
                raise SelectorError("a metric key ends after its namespace")
            return f"{first}:{rest}"
        return first

    def name(self) -> str:
        if self.i >= len(self.s) or self.s[self.i] not in LETTERS:
            raise SelectorError("expected a transformation name after ':'")
        return self._run_of(NAME_CHARS, MAX_KEY, "name")

    def number(self) -> Number:
        start = self.i
        if self.s[self.i] == "-":
            self.i += 1
        digits = self._run_of(DIGITS, MAX_NUMBER, "number")
        if not digits:
            raise SelectorError("expected digits in a number in the metric selector")
        if self.s[self.i : self.i + 1] == ".":
            self.i += 1
            if not self._run_of(DIGITS, MAX_NUMBER, "number"):
                raise SelectorError("expected digits after '.' in a number")
        text = self.s[start : self.i]
        unit = self._run_of(UNIT_CHARS, MAX_UNIT, "unit")
        return Number(float(text), unit)

    def string(self) -> Lit:
        self.i += 1  # the opening quote
        out: list[str] = []
        while self.i < len(self.s):
            char = self.s[self.i]
            self.i += 1
            if char == '"':
                return Lit("".join(out))
            if ord(char) < 32:
                raise SelectorError("a control character in a string in the metric selector")
            if char == "~":
                follower = self.s[self.i : self.i + 1]
                if follower not in ('"', "~"):
                    raise SelectorError("an unknown escape in a string in the metric selector")
                char = follower
                self.i += 1
            out.append(char)
            if len(out) > MAX_STRING:
                raise SelectorError("a string in the metric selector is too long")
        raise SelectorError("a string in the metric selector is not closed")

    def transforms(self, depth: int) -> tuple[Transform, ...]:
        found: list[Transform] = []
        while self.s[self.i : self.i + 1] == ":":
            self.i += 1
            name = self.name()
            args = self.arguments(depth) if self.s[self.i : self.i + 1] == "(" else ()
            found.append(Transform(name, args))
            if len(found) > MAX_TRANSFORMS:
                raise SelectorError(
                    f"a series has more than {MAX_TRANSFORMS} transformations in the selector"
                )
        return tuple(found)

    def arguments(self, depth: int) -> tuple[Arg, ...]:
        self._deep(depth + 1)
        self.take("(")
        args: list[Arg] = []
        if self.peek() == ")":
            self.i += 1
            return ()
        while True:
            args.append(self.arg(depth + 1))
            if len(args) > MAX_ARGS:
                raise SelectorError(f"a call has more than {MAX_ARGS} arguments in the selector")
            char = self.peek()
            self.i += 1
            if char == ")":
                return tuple(args)
            if char != ",":
                raise SelectorError("expected ',' or ')' in the metric selector")

    def arg(self, depth: int) -> Arg:
        self._deep(depth)
        char = self.peek()
        if char == '"':
            return self.string()
        if char and (char in DIGITS or char == "-"):
            return self.number()
        if char and char in LETTERS:
            name = self._run_of(NAME_CHARS, MAX_KEY, "name")
            if self.s[self.i : self.i + 1] == "(":
                return Call(name, self.arguments(depth))
            return Ident(name)
        raise SelectorError("expected an argument in the metric selector")


def parse_selector(text: str) -> Expr:
    """The selector as an AST; raises SelectorError for anything outside the grammar."""
    if len(text) > MAX_LENGTH:
        raise SelectorError(f"the metric selector is longer than {MAX_LENGTH} characters")
    if not text.strip():
        raise SelectorError("the metric selector is empty")
    return _Parser(text).run()
