"""NRQL text for KQL predicates and aggregates (the subset in `kql_parse.py`).

Nothing is formatted into the query except through `quote_string` and `identifier`; a column is
turned into an attribute by the caller (`name_of`) before it reaches here. A pattern that NRQL
cannot hold safely raises ValueError, which the caller reports as a manual reason.
"""

import re
from collections.abc import Callable

from importer.core.emit.nrql import identifier, quote_string
from importer.sources.azure.kql_parse import (
    Agg,
    And,
    Between,
    Cmp,
    Empty,
    Expr,
    InList,
    Not,
    Or,
    Pred,
    Regex,
    Scalar,
)

NameOf = Callable[[str], str]
LOOSE_NAME = re.compile(r"[A-Za-z0-9_%][A-Za-z0-9_ %./:()\-]{0,99}")
LIKE_FORMS = {
    "contains": "%{}%",
    "has": "%{}%",
    "startswith": "{}%",
    "endswith": "%{}",
}
FLIP = {"==": "!=", "!=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
NRQL_OPS = {"==": "=", "!=": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
FUNCTIONS = {
    "avg": "average",
    "avgif": "average",
    "sum": "sum",
    "sumif": "sum",
    "min": "min",
    "maxif": "max",
    "minif": "min",
    "max": "max",
    "stdev": "stddev",
    "dcount": "uniqueCount",
    "dcountif": "uniqueCount",
    "any": "latest",
}


def ref(name: str) -> str:
    """An attribute reference: a plain name, or backticks for a name with spaces or symbols."""
    try:
        return identifier(name)
    except ValueError:
        if LOOSE_NAME.fullmatch(name):
            return f"`{name}`"
        raise


def number(value: float) -> str:
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else repr(value)


def literal(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return number(value)
    return quote_string(value)


def negate(pred: Pred) -> Pred:
    """The opposite predicate, with the negation pushed down to the leaves (De Morgan)."""
    if isinstance(pred, Not):
        return pred.inner
    if isinstance(pred, And):
        return Or(tuple(negate(p) for p in pred.items))
    if isinstance(pred, Or):
        return And(tuple(negate(p) for p in pred.items))
    if isinstance(pred, Cmp):
        if pred.op in FLIP:
            return Cmp(pred.column, FLIP[pred.op], pred.value)
        flipped = pred.op[1:] if pred.op.startswith("!") else "!" + pred.op
        return Cmp(pred.column, flipped, pred.value)
    if isinstance(pred, InList):
        return InList(pred.column, pred.values, not pred.negate)
    if isinstance(pred, Between):
        return Between(pred.column, pred.low, pred.high, not pred.negate)
    if isinstance(pred, Regex):
        return Regex(pred.column, pred.pattern, not pred.negate)
    if isinstance(pred, Empty):
        return Empty(pred.column, not pred.empty, pred.null_only)
    raise ValueError("a time filter cannot be negated")


def _leaf(pred: Pred, name_of: NameOf) -> str:
    if isinstance(pred, Cmp):
        attr = ref(name_of(pred.column))
        if pred.op in NRQL_OPS:
            return f"{attr} {NRQL_OPS[pred.op]} {literal(pred.value)}"
        text = str(pred.value)
        if "%" in text:
            raise ValueError("a % in a LIKE pattern cannot be escaped")
        base = pred.op.lstrip("!")
        like = "NOT LIKE" if pred.op.startswith("!") else "LIKE"
        return f"{attr} {like} {quote_string(LIKE_FORMS[base].format(text))}"
    if isinstance(pred, InList):
        items = ", ".join(literal(v) for v in pred.values)
        return f"{ref(name_of(pred.column))} {'NOT IN' if pred.negate else 'IN'} ({items})"
    if isinstance(pred, Between):
        attr = ref(name_of(pred.column))
        if pred.negate:
            return f"({attr} < {number(pred.low)} OR {attr} > {number(pred.high)})"
        return f"({attr} >= {number(pred.low)} AND {attr} <= {number(pred.high)})"
    if isinstance(pred, Regex):
        if "'" in pred.pattern or "\n" in pred.pattern or "\r" in pred.pattern:
            raise ValueError("a regular expression with a quote cannot be written as r'...'")
        op = "NOT RLIKE" if pred.negate else "RLIKE"
        # KQL finds the pattern anywhere in the value; NRQL RLIKE must match all of it
        return f"{ref(name_of(pred.column))} {op} r'.*(?:{pred.pattern}).*'"
    if isinstance(pred, Empty):
        attr = ref(name_of(pred.column))
        if pred.null_only:
            return f"{attr} IS NULL" if pred.empty else f"{attr} IS NOT NULL"
        if pred.empty:
            return f"({attr} IS NULL OR {attr} = '')"
        return f"({attr} IS NOT NULL AND {attr} != '')"
    raise ValueError("predicate has no NRQL form")


def predicate_sql(pred: Pred, name_of: NameOf, parent: str = "") -> str:
    if isinstance(pred, Not):
        return predicate_sql(negate(pred.inner), name_of, parent)
    if isinstance(pred, And | Or):
        word = "AND" if isinstance(pred, And) else "OR"
        text = f" {word} ".join(predicate_sql(p, name_of, word) for p in pred.items)
        return f"({text})" if parent and parent != word else text
    return _leaf(pred, name_of)


def where_sql(preds: list[Pred], name_of: NameOf) -> str:
    if len(preds) == 1:
        return predicate_sql(preds[0], name_of)
    return " AND ".join(predicate_sql(p, name_of, "AND") for p in preds)


def expression_sql(expr: Expr, name_of: NameOf) -> str:
    """The arithmetic of an `extend`: columns through `name_of` and `ref`, numbers as written."""
    out: list[str] = []
    for kind, text in expr:
        if kind == "col":
            out.append(ref(name_of(text)))
        elif kind == "num":
            out.append(number(float(text)) if "." not in text else text)
        else:
            out.append(text)
    glued = " ".join(out)
    return glued.replace("( ", "(").replace(" )", ")")


def aggregate_sql(agg: Agg, name_of: NameOf, rows: list[Pred]) -> str:
    """The NRQL aggregate; the rows filter and an `...if` condition go in filter(..., WHERE)."""
    preds = [*rows, *([agg.cond] if agg.cond is not None else [])]
    if agg.fn in ("count", "countif"):
        core = "count(*)"
    elif agg.fn == "percentile":
        core = f"percentile({ref(name_of(agg.column))}, {number(agg.arg or 0)})"
    elif agg.expr:
        core = f"{FUNCTIONS[agg.fn]}({expression_sql(agg.expr, name_of)})"
    else:
        core = f"{FUNCTIONS[agg.fn]}({ref(name_of(agg.column))})"
    return f"filter({core}, WHERE {where_sql(preds, name_of)})" if preds else core
