"""Which PromQL constructs an expression uses (the vocabulary of the census).

An expression is parsed with `importer.sources.grafana.promql_ast` and every node is mapped to a
construct id such as `fn.rate`, `agg.by`, `matcher.re` or `match.group_left`. The ids are the rows
of `docs/research/promql-to-nrql-census.md`. This module has no I/O.
"""

import re

from importer.sources.grafana.dashboards.regex_matchers import SAFE
from importer.sources.grafana.promql_ast import (
    VARIABLE,
    Aggregate,
    Binary,
    Call,
    Node,
    Number,
    Paren,
    Placeholder,
    Selector,
    Subquery,
    Unary,
    children,
    walk,
)

MAX_PATTERN = 200
MAX_OPERANDS = 50  # the converter's own cap on operands of one expression
SCALAR_FUNCTIONS = frozenset({"time", "scalar", "pi"})
ARITHMETIC = frozenset({"+", "-", "*", "/", "%", "^", "atan2"})
COMPARISONS = frozenset({"==", "!=", ">", "<", ">=", "<="})
BUILTIN_WINDOWS = {
    "__rate_interval": "window.rate_interval",
    "__interval": "window.interval",
    "__interval_ms": "window.interval",
    "__range": "window.range",
    "__range_s": "window.range",
    "__range_ms": "window.range",
}
FIXED_WINDOW = re.compile(r"(?:\d+(?:ms|s|m|h|d|w|y))+|\d+")
RATE_FUNCTIONS = frozenset({"rate", "irate", "increase"})
COUNTER_SUFFIXES = ("_total", "_count", "_sum", "_bucket")
SINGLE_QUOTED = re.compile(r"(?:=|!=|=~|!~)\s*'")
UPPERCASE_AGGREGATE = re.compile(r"\b(?:SUM|AVG|MIN|MAX|COUNT)\b\s*\(")
FORMATTED_VARIABLE = re.compile(r"\$\{\w+:[^}]+\}|\[\[\w+:\w+\]\]")


def is_scalar(node: Node) -> bool:
    """True when PromQL types the node as a scalar (a placeholder is taken to be a number)."""
    if isinstance(node, (Number, Placeholder)):
        return True
    if isinstance(node, (Unary, Paren)):
        return is_scalar(node.expr)
    if isinstance(node, Binary):
        return is_scalar(node.lhs) and is_scalar(node.rhs)
    if isinstance(node, Call):
        return node.func.lower() in SCALAR_FUNCTIONS
    return False


def window_construct(text: str) -> str:
    """The construct for a range or step written as `text`."""
    found = VARIABLE.search(text)
    if found is None:
        return "window.fixed" if FIXED_WINDOW.fullmatch(text.lstrip("-")) else "window.other"
    name = found[1] or found[2] or found[3]
    return BUILTIN_WINDOWS.get(name, "window.variable")


def _selector(node: Selector, out: set[str]) -> None:
    out.add("selector.range" if node.range else "selector.instant")
    if not node.name:
        out.add("selector.no_name")
    if node.variables and VARIABLE.search(node.name):
        out.add("name.variable")
    elif ":" in node.name:
        out.add("name.recording_rule")
    for m in node.matchers:
        out.add(
            {"=": "matcher.eq", "!=": "matcher.ne", "=~": "matcher.re", "!~": "matcher.nre"}[m.op]
        )
        if m.label == "__name__":
            out.add("matcher.name_label")
        if VARIABLE.search(m.value):
            out.add("matcher.value_variable")
        if m.op == "!=" and m.value == "":
            out.add("matcher.ne_empty")
        if m.op == "=" and m.value == "":
            out.add("matcher.eq_empty")
        if m.op in ("=~", "!~") and not VARIABLE.search(m.value) and not _carried_syntax(m.value):
            out.add("matcher.re.complex_syntax")
        if m.op in ("=~", "!~") and _matches_empty(m.value):
            out.add("matcher.re.matches_empty")
    if node.range:
        out.add(window_construct(node.range))
    if node.offset:
        out.add("mod.offset")
    if node.at:
        out.add("mod.at")


def _carried_syntax(pattern: str) -> bool:
    """The small regex syntax the converter turns into RLIKE (regex_matchers.SAFE)."""
    return pattern in ("", ".+") or (
        len(pattern) <= MAX_PATTERN and SAFE.fullmatch(pattern) is not None and "(?" not in pattern
    )


def _matches_empty(pattern: str) -> bool:
    if VARIABLE.search(pattern):
        return False
    try:
        return re.fullmatch(pattern, "") is not None
    except re.error:
        return False


def _binary(node: Binary, out: set[str]) -> None:
    lhs_scalar, rhs_scalar = is_scalar(node.lhs), is_scalar(node.rhs)
    op = node.op
    if op in ("and", "or", "unless"):
        out.add(f"set.{op}")
    elif op in COMPARISONS:
        kind = "cmp.bool" if node.bool_modifier else "cmp.filter"
        if lhs_scalar and rhs_scalar:
            out.add("cmp.scalar_scalar")
        elif lhs_scalar or rhs_scalar:
            out.add(f"{kind}.vector_scalar")
        else:
            out.add(f"{kind}.vector_vector")
    else:
        out.add(f"binop.{op}")
        if lhs_scalar and rhs_scalar:
            out.add("arith.scalar_scalar")
        elif lhs_scalar or rhs_scalar:
            out.add("arith.vector_scalar")
        else:
            out.add("arith.vector_vector")
    if not (lhs_scalar or rhs_scalar):
        out.add("match.implicit" if node.matching is None else "match.explicit")
    if node.matching is not None:
        if node.matching.kind:
            out.add(f"match.{node.matching.kind}")
        if node.matching.group:
            out.add(f"match.{node.matching.group}")


def _aggregate(node: Aggregate, out: set[str]) -> None:
    out.add(f"agg.{node.op}")
    out.add({"by": "agg.by", "without": "agg.without", None: "agg.ungrouped"}[node.grouping])
    inner = list(walk(node.expr))
    ranking = node.op in ("topk", "bottomk", "limitk")  # ranking a grouped result is not nesting
    if not ranking and any(isinstance(n, Aggregate) for n in inner):
        out.add("agg.nested")
    if node.op in ("avg", "min", "max") and any(
        isinstance(n, Call) and n.func.lower() in RATE_FUNCTIONS for n in inner
    ):
        out.add("shape.avg_min_max_of_rate")
    if node.op in ("sum", "count") and not any(isinstance(n, Call) for n in inner):
        out.add("shape.sum_of_gauge")  # the converter refuses it without a series hint


def constructs_of(node: Node, text: str = "") -> set[str]:
    """Construct ids used anywhere in the tree (and in `text`, for variable formats)."""
    out: set[str] = set()
    for n in walk(node):
        if isinstance(n, Selector):
            _selector(n, out)
        elif isinstance(n, Call):
            out.add(f"fn.{n.func.lower()}")
        elif isinstance(n, Aggregate):
            _aggregate(n, out)
        elif isinstance(n, Binary):
            _binary(n, out)
        elif isinstance(n, Subquery):
            out.add("subquery")
            out.add(window_construct(n.range))
            if n.step:
                out.add(window_construct(n.step))
            if n.offset:
                out.add("mod.offset")
            if n.at:
                out.add("mod.at")
        elif isinstance(n, Unary):
            out.add(f"unary.{n.op}")
        elif isinstance(n, Number):
            out.add("scalar.literal")
        elif isinstance(n, Placeholder):
            out.add("var.as_operand")
    if FORMATTED_VARIABLE.search(text):
        out.add("var.format")
    if SINGLE_QUOTED.search(text):
        out.add("style.single_quoted_string")
    if UPPERCASE_AGGREGATE.search(text):
        out.add("style.uppercase_aggregate")
    out |= _bare_counters(node)
    if sum(isinstance(n, Binary) for n in walk(node)) >= MAX_OPERANDS:
        out.add("shape.huge_expression")
    for n in walk(node):
        if isinstance(n, Binary) and n.op == "or" and _is_vector_literal(n.rhs):
            out.add("idiom.or_vector")
    return out


def _is_vector_literal(node: Node) -> bool:
    while isinstance(node, Paren):
        node = node.expr
    return isinstance(node, Call) and node.func.lower() == "vector"


def _bare_counters(node: Node, inside_rate: bool = False) -> set[str]:
    """`name.counter_suffix_bare`: an instant selector named like a counter, read without rate."""
    if isinstance(node, Selector):
        bare = not inside_rate and node.range is None and node.name.endswith(COUNTER_SUFFIXES)
        return {"shape.counter_suffix_bare"} if bare else set()
    here = inside_rate or (isinstance(node, Call) and node.func.lower() in RATE_FUNCTIONS)
    found: set[str] = set()
    for child in children(node):
        found |= _bare_counters(child, here)
    return found
