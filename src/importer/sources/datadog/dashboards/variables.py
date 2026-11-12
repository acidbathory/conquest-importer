"""Template variables: Datadog `template_variables` to dashboard variables, and `$var` in scopes.

A variable with a tag prefix (`host`) is a list of values of that tag: it becomes a New Relic
NRQL variable over `uniques(host)` and `$host` in a scope becomes `host IN ({{host}})`. A variable
with no prefix stands for a whole scope text (`$scope` = `env:prod,host:a`), which no New Relic
variable can hold; its default is used instead and the widget says so. A variable in any other
place (group by, OR, NOT, IN lists, wildcards, `$x.value`) is a placeholder. Values reach NRQL
only through the TagFilter emitters (`quote_string`, `identifier`); a variable's name is checked
against New Relic's pattern before it is used.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import IRVariable, VariableType
from importer.core.dashboard_nrql import VARIABLE_NAME
from importer.core.emit.nrql import identifier, quote_string
from importer.core.ir import TagFilter
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.metric import scope_filters
from importer.sources.datadog.names import NameMap, resolve_attribute
from importer.sources.datadog.query import BoolOp, InSet, Not, ScopeNode, Star, Tag, parse_scope

BARE = re.compile(r"\$([A-Za-z0-9_\-]+)")
MAX_DEFAULT = 200
VALUES_LIMIT = 200


@dataclass(frozen=True)
class TemplateVar:
    name: str  # the New Relic variable name (the export's name when it is already valid)
    prefix: str | None
    default: str
    source_name: str = ""  # the name in the export, when it had to be changed


def _valid_name(name: str, taken: set[str]) -> str:
    """The name if New Relic accepts it, else the same with `_` for each other character."""
    if re.fullmatch(VARIABLE_NAME, name) and name not in taken:
        return name
    base = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not re.match(r"[A-Za-z_]", base):
        base = "_" + base
    base = base[:56]
    chosen, n = base, 1
    while chosen in taken:
        n += 1
        chosen = f"{base}_{n}"
    return chosen


def read_variables(raw: list[dict[str, Any]]) -> dict[str, TemplateVar]:
    """The variables by their name in the export; a name New Relic rejects is renamed."""
    out: dict[str, TemplateVar] = {}
    taken = {
        i["name"]
        for i in raw
        if isinstance(i.get("name"), str) and re.fullmatch(VARIABLE_NAME, i["name"])
    }
    for item in raw:
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        prefix = item.get("prefix")
        default = item.get("default")
        defaults = item.get("defaults")
        if default is None and isinstance(defaults, list) and defaults:
            default = defaults[0]
        nr_name = _valid_name(name, taken) if not re.fullmatch(VARIABLE_NAME, name) else name
        taken.add(nr_name)
        out[name] = TemplateVar(
            nr_name,
            prefix if isinstance(prefix, str) and prefix else None,
            default if isinstance(default, str) else "*",
            name if nr_name != name else "",
        )
    return out


@dataclass(frozen=True)
class UsedVariable:
    """A variable a converted widget uses: the tag it filters, and the variable to create."""

    attribute: str
    variable: IRVariable


def mark_for(name: str) -> str:
    return f"__datadog_variable_{name}__"


@dataclass
class Scope:
    """A scope split into what is a plain filter and what is a dashboard variable."""

    fixed: ScopeNode
    defaults: list[TagFilter] = field(default_factory=list)  # from prefix-less variables
    variable_filters: list[TagFilter] = field(default_factory=list)  # value is a mark
    used: dict[str, UsedVariable] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _has_variable(node: object) -> bool:
    if isinstance(node, Tag):
        return "$" in node.value or "$" in (node.key or "")
    if isinstance(node, InSet):
        return "$" in node.key or any("$" in v for v in node.values)
    if isinstance(node, Not):
        return _has_variable(node.item)
    if isinstance(node, BoolOp):
        return any(_has_variable(i) for i in node.items)
    return False


def _variable_ir(var: TemplateVar, attribute: str, event: str, metric: str) -> IRVariable:
    query = f"SELECT uniques({identifier(attribute)}, {VALUES_LIMIT}) FROM {identifier(event)}"  # noqa: S608
    if event == "Metric":
        query += f" WHERE metricName = {quote_string(metric)}"
    default = var.default.strip()
    try:
        return IRVariable(
            name=var.name,
            title=var.name,
            type=VariableType.NRQL,
            nrql=query,
            multi_select=True,
            default_values=["*"] if default in ("", "*") else [shown(default, MAX_DEFAULT)],
        )
    except ValueError:
        raise Fail(DR.V_INVALID.format(name=shown(var.name, 40))) from None


@dataclass
class Resolver:
    """Resolves the `$var` items of one widget's scope against the dashboard's variables."""

    variables: dict[str, TemplateVar]
    names: NameMap
    committed: dict[str, UsedVariable]  # variables earlier widgets already use
    event: str
    metric: str

    def split(self, node: ScopeNode) -> Scope:
        items = node.items if isinstance(node, BoolOp) and node.op == "and" else (node,)
        plain = [i for i in items if not _has_variable(i)]
        scope = Scope(fixed=_join(plain))
        for item in items:
            if _has_variable(item):
                self._variable_item(item, scope)
        return scope

    def _variable_item(self, item: ScopeNode, scope: Scope) -> None:
        if isinstance(item, InSet):
            raise Fail(DR.V_FORM.format(what="a variable in an IN list"))
        if not isinstance(item, Tag):
            raise Fail(f"{R.M_SCOPE}: a template variable inside OR or NOT")
        if item.wildcard:
            raise Fail(DR.V_FORM.format(what="a variable in a wildcard"))
        match = BARE.fullmatch(item.value)
        if match is None or "$" in (item.key or ""):
            raise Fail(DR.V_FORM.format(what=shown(f"{item.key or ''}:{item.value}", 60)))
        var = self._defined(match[1])
        if item.key is None and var.prefix is None:
            self._default(var, item, scope)
            return
        key = item.key if item.key is not None else var.prefix
        if key is None or (var.prefix is not None and var.prefix != key):
            raise Fail(DR.V_TAGS.format(what=shown(f"${var.name} on {key}", 60)))
        self._variable(var, key, item.negate, scope)

    def _defined(self, name: str) -> TemplateVar:
        var = self.variables.get(name)
        if var is None:
            raise Fail(DR.V_UNDEFINED.format(name=shown(name, 40)))
        return var

    def _variable(self, var: TemplateVar, key: str, negate: bool, scope: Scope) -> None:
        attribute = resolve_attribute(self.names, key)
        if attribute is None:
            raise Fail(f"{R.M_TAG}: {shown(key, 40)}")
        known = scope.used.get(var.name) or self.committed.get(var.name)
        if known is not None and known.attribute != attribute:
            raise Fail(DR.V_TAGS.format(what=shown(f"${var.name}", 60)))
        if known is None:
            scope.used[var.name] = UsedVariable(
                attribute, _variable_ir(var, attribute, self.event, self.metric)
            )
        scope.variable_filters.append(
            TagFilter(key=attribute, value=mark_for(var.name), negate=negate)
        )
        if var.source_name:
            scope.notes.append(
                DR.N_VAR_RENAMED.format(old=shown(var.source_name, 40), new=var.name)
            )

    def _default(self, var: TemplateVar, item: Tag, scope: Scope) -> None:
        if item.negate:
            raise Fail(DR.V_FORM.format(what=shown(f"!${var.name}", 60)))
        text = var.default.strip()
        if text not in ("", "*"):
            diagnostics: list[Any] = []
            node = parse_scope(text, diagnostics)
            try:
                if diagnostics or _has_variable(node):
                    raise Fail(R.M_SCOPE)
                scope.defaults += scope_filters(node, self.names)
            except Fail:
                raise Fail(DR.V_DEFAULT.format(name=shown(var.name, 40))) from None
        scope.notes.append(
            DR.N_VAR_NO_TAG.format(name=shown(var.name, 40), default=shown(text or "*", 60))
        )


def _join(items: list[ScopeNode]) -> ScopeNode:
    if not items:
        return Star()
    return items[0] if len(items) == 1 else BoolOp("and", tuple(items))


def with_variables(nrql: str, filters: list[TagFilter]) -> str:
    """The query with each marked comparison turned into `IN ({{name}})`.

    The marks were written by `mark_for` into a TagFilter, so the query holds them only as the
    escaped literal `attr = '<mark>'`; anything else is refused.
    """
    for f in filters:
        name = f.value.removeprefix("__datadog_variable_").removesuffix("__")
        literal = quote_string(f.value)
        op, word = ("!=", "NOT IN") if f.negate else ("=", "IN")
        before = f"{identifier(f.key)} {op} {literal}"
        if before not in nrql:
            raise Fail(DR.V_FORM.format(what=shown(f"${name}", 40)))
        nrql = nrql.replace(before, f"{identifier(f.key)} {word} ({{{{{name}}}}})")
    return nrql
