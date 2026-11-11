"""A Datadog widget formula (`query1 / query2 * 100`) as an expression over its named queries.

Only names, numbers, `+ - * /` and parentheses are read. A function in a formula (default_zero,
week_before, top, ...) has no single-SELECT form and is refused with its name. The result uses the
IR expression types with the query names as references; text from the export reaches a reason
only through `shown()`.
"""

import re
from dataclasses import dataclass

from importer.core.ir import BinOp, Expr, Num, Ref
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.reasons import shown

MAX_FORMULA = 500
MAX_DEPTH = 30
_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_.]*)|(?P<op>[-+*/()]))"
)


def _unreadable(why: str) -> Fail:
    return Fail(DR.W_FORMULA_UNREADABLE.format(why=why))


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    while text[pos:].strip():
        match = _TOKEN.match(text, pos)
        if match is None:
            raise _unreadable(f"unexpected {shown(text[pos:].strip()[:1], 4)}")
        kind = match.lastgroup or ""
        out.append((kind, match.group(kind)))
        pos = match.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], names: set[str]) -> None:
        self.tokens = tokens
        self.names = names
        self.pos = 0
        self.depth = 0

    def peek(self) -> tuple[str, str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ("end", "")

    def take(self) -> tuple[str, str]:
        token = self.peek()
        self.pos += 1
        return token

    def expr(self) -> Expr:
        left = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            left = BinOp(op=op, left=left, right=self.term())
        return left

    def term(self) -> Expr:
        left = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            left = BinOp(op=op, left=left, right=self.unary())
        return left

    def unary(self) -> Expr:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise _unreadable("nested too deeply")
        try:
            if self.peek() == ("op", "-"):
                self.take()
                return BinOp(op="-", left=Num(value=0.0), right=self.unary())
            return self.atom()
        finally:
            self.depth -= 1

    def atom(self) -> Expr:
        kind, text = self.take()
        if kind == "num":
            value = float(text)
            if float(f"{value:g}") != value:  # the NRQL writer keeps six significant digits
                raise _unreadable(f"the number {shown(text, 20)} has too many digits")
            return Num(value=value)
        if kind == "name":
            if self.peek() == ("op", "("):
                raise Fail(DR.W_FORMULA_FUNC.format(name=shown(text, 40)))
            if text not in self.names:
                raise Fail(DR.W_FORMULA_NAME.format(name=shown(text, 40)))
            return Ref(name=text)
        if (kind, text) == ("op", "("):
            inner = self.expr()
            if self.take() != ("op", ")"):
                raise _unreadable("a parenthesis is not closed")
            return inner
        raise _unreadable("an operand is missing")


_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_TOP = re.compile(
    r"\s*top\(\s*(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*,\s*(?P<limit>\d{1,5})\s*,"
    r"\s*'(?P<by>[a-z0-9_]{1,12})'\s*,\s*'(?P<dir>[a-z]{1,4})'\s*\)"
    r"(?:\s*\*\s*(?P<factor>\d{1,6}(?:\.\d{1,6})?))?\s*"
)


@dataclass(frozen=True)
class TopFormula:
    """`top(query1, 25, 'max', 'desc')`, optionally `* 8`: a ranked query, not arithmetic."""

    name: str
    limit: int
    by: str
    direction: str
    factor: float | None


def top_formula(text: str) -> TopFormula | None:
    match = _TOP.fullmatch(text) if len(text) <= MAX_FORMULA else None
    if match is None:
        return None
    factor = float(match["factor"]) if match["factor"] else None
    if factor is not None and float(f"{factor:g}") != factor:
        return None
    return TopFormula(match["name"], int(match["limit"]), match["by"], match["dir"], factor)


def parse_formula(text: str, names: set[str]) -> Expr:
    """The formula as an expression whose references are query names, or Fail with the reason."""
    if len(text) > MAX_FORMULA:
        raise _unreadable("it is too long")
    call = _CALL.search(text)
    if call is not None:
        raise Fail(DR.W_FORMULA_FUNC.format(name=shown(call.group(1), 40)))
    parser = _Parser(_tokens(text), names)
    result = parser.expr()
    if parser.pos != len(parser.tokens):
        raise _unreadable("text follows the end of the expression")
    return result


def refs_in(expr: Expr) -> list[str]:
    """The query names an expression uses, in order of first use."""
    if isinstance(expr, Ref):
        return [expr.name]
    if isinstance(expr, BinOp):
        return list(dict.fromkeys([*refs_in(expr.left), *refs_in(expr.right)]))
    return []
