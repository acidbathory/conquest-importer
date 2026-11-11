"""Parser for the Datadog log (and event) search syntax.

Covers what the documentation describes (docs/research/datadog-verified.md section 5): `key:value`
and `@attribute:value` terms, `*` and `?` wildcards, `@x:*` (the attribute exists), comparisons
(`@Severity:>=40`), ranges (`@d:[1 TO 5]`), quoted phrases, free text, `AND` (the default), `OR`,
`NOT` or a leading `-`, parentheses and `key:(a OR b)`. Operators are case sensitive, as in
Datadog. Backslash escapes the next character. The complete grammar is not published, so anything
the parser cannot read is reported in the diagnostics and clears `lossless`.
"""

import re
from dataclasses import dataclass

from importer.sources.datadog.query import MAX_DEPTH, Diagnostic

MAX_SEARCH_CHARS = 10_000
MAX_TERMS = 500
_SPECIAL = set('()":[]')


@dataclass(frozen=True)
class LTerm:
    field: str | None  # `@attr`, `service`, ... ; None for free text
    kind: str  # eq | exists | cmp | range | text
    value: str
    op: str = ""  # for cmp: > >= < <=
    hi: str = ""  # for range
    phrase: bool = False  # the value was quoted
    wildcard: bool = False  # an unescaped * or ? in an eq or text value


@dataclass(frozen=True)
class LNot:
    item: "LNode"


@dataclass(frozen=True)
class LAnd:
    items: tuple["LNode", ...]


@dataclass(frozen=True)
class LOr:
    items: tuple["LNode", ...]


LNode = LTerm | LNot | LAnd | LOr


@dataclass(frozen=True)
class LogSearch:
    ok: bool
    lossless: bool
    diagnostics: tuple[Diagnostic, ...]
    node: LNode | None = None  # None: matches everything

    def terms(self) -> list[LTerm]:
        out: list[LTerm] = []

        def walk(n: LNode | None) -> None:
            if isinstance(n, LTerm):
                out.append(n)
            elif isinstance(n, LNot):
                walk(n.item)
            elif isinstance(n, LAnd | LOr):
                for i in n.items:
                    walk(i)

        walk(self.node)
        return out


class _Stop(Exception):
    def __init__(self, diagnostic: Diagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class _Search:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.terms = 0
        self.diags: list[Diagnostic] = []

    def skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def at_end(self) -> bool:
        self.skip()
        return self.pos >= len(self.text)

    def peek_word(self) -> str:
        self.skip()
        m = re.compile(r"[A-Za-z]+(?=[\s()]|$)").match(self.text, self.pos)
        return m[0] if m else ""

    def lossy(self, code: str, message: str) -> None:
        self.diags.append(Diagnostic(code, message, lossy=True))

    def or_expr(self, depth: int) -> LNode | None:
        items = [self.and_expr(depth)]
        while self.peek_word() == "OR":
            self.pos = self.text.index("OR", self.pos) + 2
            items.append(self.and_expr(depth))
        found = [i for i in items if i is not None]
        if not found:
            return None
        return found[0] if len(found) == 1 else LOr(tuple(found))

    def and_expr(self, depth: int) -> LNode | None:
        items: list[LNode] = []
        while not self.at_end():
            if self.text[self.pos] == ")":
                break
            word = self.peek_word()
            if word == "OR":
                break
            if word == "AND":
                self.pos = self.text.index("AND", self.pos) + 3
                continue
            node = self.unary(depth)
            if node is not None:
                items.append(node)
        if not items:
            return None
        return items[0] if len(items) == 1 else LAnd(tuple(items))

    def unary(self, depth: int) -> LNode | None:
        if depth > MAX_DEPTH:
            raise _Stop(Diagnostic("too_deep", "the search is nested too deeply", fatal=True))
        self.skip()
        ch = self.text[self.pos]
        if ch == "-" and self.pos + 1 < len(self.text) and not self.text[self.pos + 1].isspace():
            self.pos += 1
            return self.negate(self.unary(depth + 1))
        if self.peek_word() == "NOT":
            self.pos = self.text.index("NOT", self.pos) + 3
            if self.at_end():
                self.lossy("syntax", "NOT without a term")
                return None
            return self.negate(self.unary(depth + 1))
        if ch == "(":
            self.pos += 1
            inner = self.or_expr(depth + 1)
            if not self.at_end() and self.text[self.pos] == ")":
                self.pos += 1
            else:
                self.lossy("syntax", "an unclosed parenthesis")
            return inner
        if ch == ")":
            self.pos += 1
            self.lossy("syntax", "an unmatched closing parenthesis")
            return None
        return self.term()

    @staticmethod
    def negate(node: LNode | None) -> LNode | None:
        return None if node is None else (node.item if isinstance(node, LNot) else LNot(node))

    def count(self) -> None:
        self.terms += 1
        if self.terms > MAX_TERMS:
            raise _Stop(Diagnostic("too_long", "the search has too many terms", fatal=True))

    def read_value(self) -> tuple[str, bool, bool]:
        """(text, quoted, has an unescaped wildcard). Reads to whitespace or a closing paren."""
        out: list[str] = []
        wildcard = False
        if self.pos < len(self.text) and self.text[self.pos] == '"':
            self.pos += 1
            while self.pos < len(self.text) and self.text[self.pos] != '"':
                if self.text[self.pos] == "\\" and self.pos + 1 < len(self.text):
                    self.pos += 1
                out.append(self.text[self.pos])
                self.pos += 1
            if self.pos >= len(self.text):
                self.lossy("syntax", "an unclosed quote")
            else:
                self.pos += 1
            return "".join(out), True, False
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch.isspace() or ch == ")":
                break
            if ch == "\\" and self.pos + 1 < len(self.text):
                out.append(self.text[self.pos + 1])
                self.pos += 2
                continue
            if ch in "*?":
                wildcard = True
            out.append(ch)
            self.pos += 1
        return "".join(out), False, wildcard

    def term(self) -> LNode | None:
        self.count()
        key = re.compile(r"(@?[A-Za-z_][A-Za-z0-9_.\-]*):").match(self.text, self.pos)
        if key is None:
            text, quoted, wild = self.read_value()
            if text == "*" and not quoted:
                return None  # a bare * matches everything
            if not text:
                self.pos += 1
                self.lossy("syntax", "an unreadable character")
                return None
            return LTerm(None, "text", text, phrase=quoted, wildcard=wild)
        field = key[1]
        self.pos = key.end()
        if self.pos >= len(self.text) or self.text[self.pos].isspace():
            self.lossy("syntax", f"{field}: has no value")
            return None
        ch = self.text[self.pos]
        if ch == "(":
            self.pos += 1
            return self.value_group(field)
        if ch in "[{":
            return self.range_term(field)
        cmp = re.compile(r"(>=|<=|>|<)").match(self.text, self.pos)
        if cmp:
            self.pos = cmp.end()
            value, _, _ = self.read_value()
            return LTerm(field, "cmp", value, op=cmp[1])
        value, quoted, wild = self.read_value()
        if value == "*" and not quoted:
            return LTerm(field, "exists", "*")
        return LTerm(field, "eq", value, phrase=quoted, wildcard=wild)

    def value_group(self, field: str) -> LNode | None:
        values: list[LNode] = []
        while not self.at_end() and self.text[self.pos] != ")":
            word = self.peek_word()
            if word in ("OR", "AND"):
                self.pos = self.text.index(word, self.pos) + len(word)
                continue
            self.count()
            value, quoted, wild = self.read_value()
            if value:
                values.append(LTerm(field, "eq", value, phrase=quoted, wildcard=wild))
            else:
                self.pos += 1
        if not self.at_end() and self.text[self.pos] == ")":
            self.pos += 1
        else:
            self.lossy("syntax", "an unclosed value group")
        if not values:
            return None
        return values[0] if len(values) == 1 else LOr(tuple(values))

    def range_term(self, field: str) -> LNode | None:
        close = self.text.find("]" if self.text[self.pos] == "[" else "}", self.pos)
        if close < 0:
            self.lossy("syntax", "an unclosed range")
            self.pos = len(self.text)
            return None
        inner = self.text[self.pos + 1 : close]
        self.pos = close + 1
        m = re.fullmatch(r"\s*(\S+)\s+TO\s+(\S+)\s*", inner)
        if m is None:
            self.lossy("syntax", "a range without TO")
            return None
        return LTerm(field, "range", m[1], hi=m[2])


def parse_log_search(text: str) -> LogSearch:
    """Parse a log search. Never raises; `lossless` is false when anything was skipped."""
    if len(text) > MAX_SEARCH_CHARS:
        d = Diagnostic("too_long", f"the search is longer than {MAX_SEARCH_CHARS} characters", True)
        return LogSearch(False, False, (d,))
    parser = _Search(text)
    try:
        node = parser.or_expr(0)
        while not parser.at_end():  # a stray closing parenthesis at the top level
            parser.pos += 1
            parser.lossy("syntax", "text after the search")
            node = parser.or_expr(0) or node
    except _Stop as stop:
        return LogSearch(False, False, (stop.diagnostic,))
    return LogSearch(True, not parser.diags, tuple(parser.diags), node)
