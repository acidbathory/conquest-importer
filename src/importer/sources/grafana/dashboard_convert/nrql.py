"""Expression nodes and the NRQL text built from them.

Every name goes through `identifier` and every literal through `quote_string`; nothing from the
dashboard is formatted into a query any other way. A dashboard variable arrives as a token
(`VARV<n>`) and leaves as `{{name}}`.
"""

import re
from dataclasses import dataclass

from importer.core.emit.nrql import identifier, quote_string
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.reuse import Cond, Unconvertible

VAR_TOKEN = re.compile(r"VARV(\d+)")
MAX_FACETS = 5  # a FACET takes at most five attributes (research note, NRQL reference)
LOG_EVENT = "Log"


@dataclass(frozen=True)
class Leaf:
    """One aggregate, such as `rate(sum(x), 1 second)`, with the filters that belong to it."""

    core: str  # the aggregate call, for example sum(x) or bucketPercentile(x_bucket, 95)
    prefix: str = ""
    suffix: str = ""
    where: tuple[str, ...] = ()
    facet: tuple[str, ...] = ()
    event: str = "Metric"
    kept: bool = False  # the query does not aggregate, so every label is still there
    filterable: bool = True  # filter() can wrap it
    approx: bool = False  # sum or count of the series of a gauge written with cardinality()


@dataclass(frozen=True)
class Num:
    text: str


@dataclass(frozen=True)
class Bin:
    op: str
    left: "Node"
    right: "Node"


Node = Leaf | Num | Bin


def leaves(node: Node) -> list[Leaf]:
    if isinstance(node, Leaf):
        return [node]
    if isinstance(node, Bin):
        return [*leaves(node.left), *leaves(node.right)]
    return []


def _resolve(token_text: str, names: list[str]) -> str:
    """`{{name}}` for a whole token, else raise: a variable is substituted whole."""
    found = VAR_TOKEN.fullmatch(token_text)
    if found is None:
        raise Unconvertible(r.C_VAR_MATCHER)
    return "{{" + names[int(found[1])] + "}}"


def _in_item(value: str, names: list[str]) -> str:
    if VAR_TOKEN.search(value):
        return _resolve(value, names)
    return quote_string(value)


def cond_sql(cond: Cond, names: list[str]) -> str:
    """One WHERE term. `names` maps `VARV<n>` tokens to dashboard variable names."""
    try:
        attr = identifier(cond.key)
    except ValueError:
        raise Unconvertible(r.C_NAME) from None
    if cond.kind == "absent":
        return f"{attr} IS NOT NULL" if cond.negate else f"{attr} IS NULL"
    if cond.kind == "rlike":
        operator = "NOT RLIKE" if cond.negate else "RLIKE"
        return f"{attr} {operator} {quote_string(cond.values[0])}"
    if cond.kind == "like":
        pattern = cond.values[0]
        if "%" in pattern:
            raise Unconvertible(r.C_LIKE_PERCENT)
        if VAR_TOKEN.search(pattern):
            raise Unconvertible(r.C_VAR_MATCHER)
        operator = "NOT LIKE" if cond.negate else "LIKE"
        return f"{attr} {operator} {quote_string(pattern.replace('*', '%'))}"
    items = ", ".join(_in_item(v, names) for v in cond.values)
    has_variable = any(VAR_TOKEN.search(v) for v in cond.values)
    if cond.kind == "in" or has_variable:
        return f"{attr} {'NOT IN' if cond.negate else 'IN'} ({items})"
    return f"{attr} {'!=' if cond.negate else '='} {quote_string(cond.values[0])}"


def _render_leaf(leaf: Leaf, extra: list[str]) -> str:
    core = leaf.core
    if extra:
        if not leaf.filterable:
            raise Unconvertible(r.C_FILTERS)
        core = f"filter({core}, WHERE {' AND '.join(extra)})"
    return f"{leaf.prefix}{core}{leaf.suffix}"


def render(node: Node, common: list[str]) -> str:
    """The SELECT expression; conditions in `common` are in the outer WHERE and left out."""
    if isinstance(node, Num):
        return node.text
    if isinstance(node, Leaf):
        return _render_leaf(node, [w for w in node.where if w not in common])
    left, right = render(node.left, common), render(node.right, common)
    return f"({left} {node.op} {right})"


def hoisted(found: list[Leaf]) -> list[str]:
    """Conditions every leaf has, in the order of the first leaf."""
    if not found:
        return []
    return [w for w in found[0].where if all(w in other.where for other in found[1:])]


def facet_attributes(labels: list[str]) -> list[str]:
    try:
        return [identifier(label) for label in dict.fromkeys(labels)]
    except ValueError:
        raise Unconvertible(r.C_NAME) from None


def interval_text(seconds: int) -> str:
    """`5 minutes`, `1 second`: the unit NRQL reads for the interval of rate()."""
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        if seconds % size == 0:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    raise AssertionError("unreachable: every whole number of seconds divides by 1")


def strip_outer(select: str) -> str:
    """Drop the parentheses around a whole expression; they are only there for nesting."""
    if not (select.startswith("(") and select.endswith(")")):
        return select
    depth, quoted, escaped = 0, False, False
    for i, ch in enumerate(select):
        if quoted:
            quoted = not (ch == "'" and not escaped)
            escaped = ch == "\\" and not escaped
            continue
        quoted = ch == "'"
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(select) - 1:
            return select
    return select[1:-1]
