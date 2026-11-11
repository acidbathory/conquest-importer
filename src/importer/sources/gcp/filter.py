"""Parse the filter strings in Cloud Monitoring and Cloud Logging conditions and dashboards.

One grammar serves both languages: terms are `field OP value`, a bare function such as
`log_id("x")`, or a dashboard variable such as `${job}`; terms join with AND, OR or an implicit
AND (white space); `NOT` and a leading `-` negate; parentheses group. Values are double-quoted
strings (with backslash escapes), bare words, or a function call: `one_of(...)`, `starts_with(...)`,
`ends_with(...)`, `has_substring(...)` and `monitoring.regex.full_match(...)`. A field may contain
quoted segments (`metric.label."type"`, `labels."a.b/c"`), which come back with the quotes removed.

The parser only reads. It builds a tree of `Term` and `Group` and pushes every NOT down onto the
terms, so a caller sees a negate flag instead of a NOT node. Mixing AND and OR at one level without
parentheses is refused, because the two languages do not agree on which binds tighter. What a
term means in New Relic is decided by the caller (`scope.py` for metrics, `translate.py` for logs).
"""

import re
from dataclasses import dataclass, replace

MAX_LENGTH = 20_000
MAX_DEPTH = 30
KEYWORDS = {"AND", "OR", "NOT"}
FUNCTIONS = {
    "one_of": "in",
    "starts_with": "starts_with",
    "ends_with": "ends_with",
    "has_substring": "has_substring",
    "monitoring.regex.full_match": "regex_full",
}
SEGMENT = r'(?:[A-Za-z_@][\w@\-]*|"(?:[^"\\]|\\.)*")'
_FIELD = re.compile(rf"{SEGMENT}(?:\.{SEGMENT})*")
_OP = re.compile(r">=|<=|!=|=~|!~|=|:|>|<")
_WORD = re.compile(r'[^\s"(),]+')
_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
_FUNC_NAME = re.compile(r"[A-Za-z_][\w.]*(?=\()")
_VARIABLE = re.compile(r"\$\{([A-Za-z_]\w*)\}")
_CONNECTOR = re.compile(r"(AND|OR)(?=[\s(\"]|$)")
_NOT = re.compile(r"NOT(?=[\s(])")
_SPACE = re.compile(r"\s*")
_PART = re.compile(SEGMENT)


class FilterError(ValueError):
    """The filter uses something the converter does not read."""


@dataclass(frozen=True)
class Term:
    """One restriction.

    `op` is `=` (equals `value`), `in` (one of `values`), `starts_with`, `ends_with`,
    `has_substring`, `regex_full` (the whole value matches), `=~` (a search regex, Cloud
    Logging), `:` (contains), an ordering (`>`, `>=`, `<`, `<=`), `func` (a bare function such as
    `log_id`, with its arguments in `values`) or `variable` (a `${name}` placeholder).
    """

    field: str
    op: str
    value: str = ""
    negate: bool = False
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    op: str  # AND or OR
    items: tuple["Node", ...]


Node = Term | Group


def unquote_field(text: str) -> str:
    """A field reference without segment quotes: `metric.label."type"` is `metric.label.type`.

    Raises FilterError unless the whole text is one field reference.
    """
    match = _FIELD.fullmatch(text.strip())
    if match is None:
        raise FilterError(f"cannot read the field {text[:40]!r}")
    return ".".join(_segment(m.group(0)) for m in _PART.finditer(match.group(0)))


def _segment(part: str) -> str:
    return _unescape(part[1:-1]) if part.startswith('"') else part


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, what: str = "") -> FilterError:
        return FilterError(what or f"cannot read {self.text[self.pos : self.pos + 40]!r}")

    def skip(self) -> None:
        self.pos = _SPACE.match(self.text, self.pos).end()  # type: ignore[union-attr]

    def peek(self) -> str:
        return self.text[self.pos : self.pos + 1]

    def parse(self) -> Node:
        node = self.sequence(0)
        self.skip()
        if self.pos < len(self.text):
            raise self.fail()
        return node

    def sequence(self, depth: int) -> Node:
        """Terms joined by one kind of connector; the kinds must not be mixed."""
        if depth > MAX_DEPTH:
            raise self.fail("the filter is nested too deeply")
        items = [self.unary(depth)]
        kinds: set[str] = set()
        while True:
            self.skip()
            if self.pos >= len(self.text) or self.peek() == ")":
                break
            found = _CONNECTOR.match(self.text, self.pos)
            if found:
                kinds.add(found.group(1))
                self.pos = found.end()
            else:
                kinds.add("AND")
            if len(kinds) > 1:
                raise self.fail(
                    "the filter mixes AND and OR without parentheses; Google's languages do "
                    "not agree on which binds tighter, so the grouping is not guessed"
                )
            items.append(self.unary(depth))
        if len(items) == 1:
            return items[0]
        return _group(kinds.pop(), items)

    def unary(self, depth: int) -> Node:
        self.skip()
        negate = False
        while True:
            if (found := _NOT.match(self.text, self.pos)) is not None:
                self.pos = found.end()
            elif self.peek() == "-":
                self.pos += 1
            else:
                break
            negate = not negate
            self.skip()
        if self.peek() == "(":
            self.pos += 1
            node = self.sequence(depth + 1)
            self.skip()
            if self.peek() != ")":
                raise self.fail("a parenthesis is not closed")
            self.pos += 1
        else:
            node = self.comparison()
        return _negated(node) if negate else node

    def comparison(self) -> Node:
        if (var := _VARIABLE.match(self.text, self.pos)) is not None:
            self.pos = var.end()
            return Term(var.group(1), "variable")
        found = _FIELD.match(self.text, self.pos)
        if found is None:
            raise self.fail()
        name = unquote_field(found.group(0))
        self.pos = found.end()
        if self.peek() == "(" and '"' not in found.group(0):
            return self.standalone(name)
        self.skip()
        op = _OP.match(self.text, self.pos)
        if op is None:
            raise self.fail()
        self.pos = op.end()
        self.skip()
        if self.peek() == "(":
            return self.value_group(name, op.group(0))
        return self.term(name, op.group(0), self.value())

    def standalone(self, name: str) -> Term:
        """A bare function term. Only `log_id()` is read; an SLO selector gets its own message."""
        if name.startswith("select_slo_"):
            raise self.fail(
                f"{name}() selects a service level objective; the SLO objects are not read from "
                "Google yet, so recreate the SLO as a New Relic service level and alert on that"
            )
        if name != "log_id":
            raise self.fail(f"the function {name}() is not supported")
        return Term(name, "func", values=self.arguments())

    def value_group(self, name: str, op: str) -> Node:
        """`field = ("a" OR "b")`: the same field and operator for every value."""
        self.pos += 1
        parts: list[Node] = []
        kinds: set[str] = set()
        while True:
            self.skip()
            parts.append(self.term(name, op, self.value()))
            self.skip()
            if self.peek() == ")":
                self.pos += 1
                break
            found = _CONNECTOR.match(self.text, self.pos)
            if found is None:
                raise self.fail()
            kinds.add(found.group(1))
            self.pos = found.end()
        if len(kinds) > 1:
            raise self.fail("the value list mixes AND and OR")
        return parts[0] if len(parts) == 1 else _group(kinds.pop(), parts)

    def value(self) -> str | tuple[str, tuple[str, ...]]:
        """A string, or (function name, arguments)."""
        quoted = _QUOTED.match(self.text, self.pos)
        if quoted:
            self.pos = quoted.end()
            return _unescape(quoted.group(1))
        name = _FUNC_NAME.match(self.text, self.pos)
        if name:
            self.pos = name.end()
            if name.group(0) not in FUNCTIONS:
                raise self.fail(f"the function {name.group(0)}() is not supported")
            return name.group(0), self.arguments()
        word = _WORD.match(self.text, self.pos)
        if word is None:
            raise self.fail()
        if word.group(0).upper() in KEYWORDS:
            raise self.fail("the filter uses OR")
        self.pos = word.end()
        return word.group(0)

    def arguments(self) -> tuple[str, ...]:
        """The quoted or bare arguments of a call; the cursor is on the opening parenthesis."""
        self.pos += 1
        out: list[str] = []
        while True:
            self.skip()
            quoted = _QUOTED.match(self.text, self.pos)
            word = _WORD.match(self.text, self.pos)
            if quoted:
                out.append(_unescape(quoted.group(1)))
                self.pos = quoted.end()
            elif word:
                out.append(word.group(0))
                self.pos = word.end()
            elif self.peek() == ")" and not out:
                self.pos += 1
                return ()
            else:
                raise self.fail()
            self.skip()
            if self.peek() == ",":
                self.pos += 1
            elif self.peek() == ")":
                self.pos += 1
                return tuple(out)
            else:
                raise self.fail()

    def term(self, name: str, op: str, value: str | tuple[str, tuple[str, ...]]) -> Term:
        negate = op in ("!=", "!~")
        op = {"!=": "=", "!~": "=~"}.get(op, op)
        if isinstance(value, str):
            return Term(name, op, value, negate)
        function, args = value
        if op not in ("=", "!=") or not args:
            raise self.fail(f"the function {function}() only works after = or !=")
        kind = FUNCTIONS[function]
        if kind == "in":
            return Term(name, "in", negate=negate, values=args)
        if len(args) != 1:
            raise self.fail(f"{function}() takes one argument here")
        return Term(name, kind, args[0], negate)


def _group(op: str, items: list[Node]) -> Group:
    """A group with nested groups of the same kind merged in."""
    flat: list[Node] = []
    for item in items:
        if isinstance(item, Group) and item.op == op:
            flat.extend(item.items)
        else:
            flat.append(item)
    return Group(op, tuple(flat))


def _negated(node: Node) -> Node:
    """NOT pushed down to the terms (De Morgan)."""
    if isinstance(node, Term):
        if node.op in ("func", "variable"):
            raise FilterError(f"NOT {node.field}(...) is not supported")
        return replace(node, negate=not node.negate)
    return _group("OR" if node.op == "AND" else "AND", [_negated(i) for i in node.items])


def parse_expression(text: str) -> Node:
    """The filter as a Term or a Group of them; raises FilterError for anything unreadable."""
    text = text.strip()
    if not text:
        raise FilterError("the filter is empty")
    if len(text) > MAX_LENGTH:
        raise FilterError("the filter is too long")
    return _Parser(text).parse()


def conjuncts(node: Node) -> list[Node]:
    """The parts that must all hold: the items of a top-level AND, or the node itself."""
    return list(node.items) if isinstance(node, Group) and node.op == "AND" else [node]


def parse_filter(text: str) -> list[Term]:
    """A filter that is only terms joined by AND, as a list; raises FilterError on OR."""
    terms = conjuncts(parse_expression(text))
    if any(isinstance(t, Group) for t in terms):
        raise FilterError("the filter uses OR")
    return [t for t in terms if isinstance(t, Term)]


def placeholders(node: Node) -> list[str]:
    """Names of `${NAME}` template placeholders inside the values of a filter, in order."""
    if isinstance(node, Group):
        found = [n for item in node.items for n in placeholders(item)]
    else:
        found = [m.group(1) for v in (node.value, *node.values) for m in _VARIABLE.finditer(v)]
    return list(dict.fromkeys(found))
