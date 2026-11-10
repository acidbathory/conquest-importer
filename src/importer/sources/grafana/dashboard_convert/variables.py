"""Grafana dashboard variables to New Relic dashboard variables (research note, section 6).

The coverage planner decides which variables convert (`dashboards/variables.py`); this module
writes them. A `label_values` query becomes an `nrql` variable over `uniques()`, a custom list an
`enum`, a text box a `string`; a constant is written into the queries and makes no variable;
interval and data source variables are dropped. The variable query is built with `identifier`
and `quote_string` like every other query. Which clauses may hold a `{{variable}}` is checked by
the IR (`core/dashboard_nrql.py`).
"""

import re
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, Field

from importer.core.dashboard_ir import (
    MAX_ITEMS,
    MAX_TEXT,
    IRVariable,
    ReplacementStrategy,
    VariableItem,
    VariableType,
)
from importer.core.emit.nrql import identifier, quote_string
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.nrql import VAR_TOKEN, cond_sql
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, tokenize
from importer.sources.grafana.dashboard_convert.reuse import (
    Cond,
    Unconvertible,
    prom_conditions,
    regex_cond,
    selector,
)
from importer.sources.grafana.dashboards.models import Dashboard, Variable
from importer.sources.grafana.dashboards.query_tier import variables_in
from importer.sources.grafana.dashboards.variables import (
    CONVERTS,
    DROPPED,
    LABEL_VALUES,
    MANUAL,
    MATCH_ALL,
    METRICS,
    QUERY_RESULT,
    VariablePlan,
    query_result_label,
)

ALL = "$__all"
NAME_MATCHER = re.compile(r'(?:^|,)\s*__name__\s*(=~|!~|=|!=)\s*"([^"]*)"\s*(?=,|$)')
METRIC_NAME = "metricName"
COMMA = re.compile(r"(?<!\\),")
KEY_VALUE = re.compile(r"^(.+?)\s+:\s+(.+)$")
MAX_VALUE = 200


class RawVar(BaseModel):
    """What the planner's variable model leaves out: label, current value and saved options."""

    label: str = ""
    current: list[str] = Field(default_factory=list)
    options: list[tuple[str, str]] = Field(default_factory=list)  # (text, value)
    refresh: int | None = None


@dataclass
class Variables:
    variables: list[IRVariable] = field(default_factory=list)
    options: dict[str, str | None] = field(default_factory=dict)  # name -> constant or None
    intervals: frozenset[str] = frozenset()
    unavailable: frozenset[str] = frozenset()  # variables with no New Relic counterpart
    dropped: int = 0
    manual: int = 0
    substituted: int = 0
    nested: int = 0  # variables whose query uses another variable
    query_results: int = 0  # query_result() variables written as uniques() of one label
    no_default: int = 0  # variables that others depend on and that have no default value
    why: list[str] = field(default_factory=list)  # fixed reasons of the variables left manual

    def with_numeric(self, names: set[str]) -> list[IRVariable]:
        """The variables, those used as numbers in a query with the `number` strategy."""
        return [
            v.model_copy(update={"replacement": ReplacementStrategy.NUMBER})
            if v.name in names
            else v
            for v in self.variables
        ]


def custom_items(query: str) -> list[VariableItem]:
    """Items of a custom variable: `a,b,c`, `a : 1, b : 2`, with `\\,` for a comma."""
    items: dict[str, VariableItem] = {}
    for token in COMMA.split(query)[:MAX_ITEMS]:
        text = token.replace("\\,", ",").strip()
        if not text:
            continue
        pair = KEY_VALUE.match(text)
        title, value = (pair[1].strip(), pair[2].strip()) if pair else (None, text)
        value = printable(value, MAX_VALUE + 1)
        if len(value) <= MAX_VALUE and value not in items:
            shown = printable(title, MAX_TEXT) if title else None
            items[value] = VariableItem(title=shown, value=value)
    return list(items.values())


def clean(value: str) -> str:
    """A value shown to people and written to files: no control or direction characters."""
    return printable(value, MAX_VALUE)


def _items(variable: Variable, raw: RawVar) -> list[VariableItem]:
    saved = [
        VariableItem(title=printable(text, MAX_TEXT) if text != value else None, value=clean(value))
        for text, value in raw.options[:MAX_ITEMS]
        if value != ALL and len(value) <= MAX_VALUE
    ]
    return saved or custom_items(variable.query)


def _title(variable: Variable, raw: RawVar) -> str:
    return printable(raw.label or variable.name, MAX_TEXT)


def _enum_defaults(
    items: list[VariableItem], raw: RawVar, multi: bool, include_all: bool
) -> list[str]:
    values = [i.value for i in items]
    if multi and (ALL in raw.current or (include_all and not raw.current)):
        return values
    kept = [clean(c) for c in raw.current if clean(c) in values]
    if not kept:
        return values[:1]
    return kept if multi else kept[:1]


def _enum(variable: Variable, raw: RawVar) -> IRVariable:
    items = _items(variable, raw)
    multi = variable.multi or variable.include_all
    return IRVariable(
        name=variable.name,
        title=_title(variable, raw),
        type=VariableType.ENUM,
        items=items,
        multi_select=multi,
        default_values=_enum_defaults(items, raw, multi, variable.include_all),
    )


def _string(variable: Variable, raw: RawVar) -> IRVariable:
    current = next((c for c in raw.current if c != ALL), variable.query)
    default = [clean(current)] if current else []
    return IRVariable(
        name=variable.name,
        title=_title(variable, raw),
        type=VariableType.STRING,
        default_values=default,
    )


def _split_selector(text: str) -> tuple[str | None, str]:
    """(metric name or None, text between the braces) of the selector in label_values()."""
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return None, text[1:-1]
    return selector(text, r.C_SHAPE)


@dataclass(frozen=True)
class VariableQuery:
    nrql: str
    uses: list[str]  # the dashboard variables the query refers to, in order
    query_result: bool = False


def _name_conditions(body: str, names: NameMap) -> tuple[str, list[Cond]]:
    """`__name__` matchers of a selector as conditions on metricName, and the other matchers."""
    found = list(NAME_MATCHER.finditer(body))
    if not found:
        return body, []
    if names.metrics:
        raise Unconvertible(r.C_METRICS_RENAMED)
    conds: list[Cond] = []
    for m in found:
        op, value = m[1], m[2]
        if VAR_TOKEN.search(value):
            raise Unconvertible(r.C_VAR_NAME)
        if op in ("=", "!="):
            conds.append(Cond(METRIC_NAME, "eq", (value,), negate=op == "!="))
        else:
            cond = regex_cond(METRIC_NAME, value, r.C_SHAPE)
            conds.append(replace(cond, negate=op == "!~"))
    return NAME_MATCHER.sub("", body).strip(" ,"), conds


def _checked_label(label: str) -> str:
    if VAR_TOKEN.search(label):
        raise Unconvertible(r.C_VAR_NAME)
    if label == METRIC_NAME:
        raise Unconvertible(r.C_METRICNAME)
    try:
        return identifier(label)
    except ValueError:
        raise Unconvertible(r.C_NAME) from None


def _selector_where(text: str, names: NameMap, used: list[str]) -> tuple[str, list[str]]:
    """(event, WHERE terms) of the metric selector inside label_values() or query_result()."""
    name, body = _split_selector(text)
    if name and VAR_TOKEN.search(name):
        raise Unconvertible(r.C_VAR_NAME)
    body, name_conds = _name_conditions(body, names)
    matchers = prom_conditions(body, r.C_SHAPE)
    if any(c.key == METRIC_NAME for c in matchers):
        raise Unconvertible(r.C_METRICNAME)
    event, where = "Metric", []
    entry = names.metrics.get(name) if name else None
    if entry is not None:
        event = entry.event
    if name and event == "Metric":
        where.append(f"{METRIC_NAME} = {quote_string(entry.attribute if entry else name)}")
    elif entry is not None:
        where.append(f"{identifier(entry.attribute)} IS NOT NULL")
    where += [cond_sql(c, used) for c in [*name_conds, *matchers]]
    return event, where


def _metrics_list(argument: str, names: NameMap) -> VariableQuery:
    """`metrics(regex)`: the Prometheus metric names that match."""
    if names.metrics:
        raise Unconvertible(r.C_METRICS_RENAMED)
    where: list[str] = []
    if argument != MATCH_ALL:
        body = argument.strip()
        where = [cond_sql(regex_cond(METRIC_NAME, body, r.C_SHAPE), [])]
    text = f"FROM Metric SELECT uniques({METRIC_NAME})"
    return VariableQuery(text + (" WHERE " + " AND ".join(where) if where else ""), [])


def variable_query(variable: Variable, opts: QueryOptions) -> VariableQuery:
    """The NRQL that lists a Prometheus variable's values.

    `label_values(selector, label)`, `label_values(label)`, `metrics(regex)` and
    `query_result(selector)` with a regex that captures one label. A `$parent` in a matcher
    becomes `label IN ({{parent}})`; a constant parent is written in. Anything else raises.
    """
    names = opts.name_map
    text, used = tokenize(" ".join(variable.query.split()), opts)
    if "VARB" in text or "$" in text or "[[" in text:
        raise Unconvertible(r.C_SHAPE)
    listed = METRICS.fullmatch(text)
    if listed is not None:
        return _metrics_list(listed[1], names)
    result = QUERY_RESULT.fullmatch(text)
    if result is not None:
        picked = query_result_label(variable.regex)
        if picked is None:
            raise Unconvertible(r.C_SHAPE)
        source, label = result[1], picked
    else:
        found = LABEL_VALUES.fullmatch(text)
        if found is None:
            raise Unconvertible(r.C_SHAPE)
        source, label = found[1], found[2]
    attribute = _checked_label(label)
    event, where = _selector_where(source, names, used) if source else ("Metric", [])
    if variable.regex and result is None:
        body = variable.regex.strip()
        if body.startswith("/"):
            body = body[1:].rsplit("/", 1)[0]
        where.append(cond_sql(regex_cond(label, body, r.C_SHAPE), []))
    text = f"FROM {identifier(event)} SELECT uniques({attribute})"
    nrql = text + (" WHERE " + " AND ".join(where) if where else "")
    return VariableQuery(nrql, used, query_result=result is not None)


def _nrql(variable: Variable, raw: RawVar, built: VariableQuery) -> IRVariable:
    multi = variable.multi or variable.include_all
    wants_all = ALL in raw.current or (variable.include_all and not raw.current)
    chosen = [c for c in raw.current if c != ALL][:50]
    defaults = ["*"] if wants_all and multi else (chosen if multi else chosen[:1])
    return IRVariable(
        name=variable.name,
        title=_title(variable, raw),
        type=VariableType.NRQL,
        nrql=built.nrql,
        multi_select=multi,
        default_values=[clean(d) for d in defaults],
        ignore_time_range=raw.refresh == 1,
    )


def _conversion_order(variables: list[Variable]) -> list[int]:
    """Indexes in list order, each one after the variables its query uses (a depth-first walk)."""
    first: dict[str, int] = {}
    for i, v in enumerate(variables):
        first.setdefault(v.name, i)
    order: list[int] = []
    started: set[int] = set()

    def visit(i: int) -> None:
        if i in started:
            return
        started.add(i)
        if variables[i].kind == "query":
            parents = {first[n] for n in variables_in(variables[i].query) if n in first}
            for j in sorted(parents - {i}):
                visit(j)
        order.append(i)

    for i in range(len(variables)):
        visit(i)
    return order


def _note(out: Variables, reason: str) -> None:
    out.manual += 1
    if reason and reason not in out.why:
        out.why.append(reason)


def _convert_one(
    out: Variables, variable: Variable, plan: VariablePlan, raw: RawVar, opts: QueryOptions
) -> IRVariable:
    if plan.target == "enum variable":
        return _enum(variable, raw)
    if plan.target == "string variable":
        return _string(variable, raw)
    built = variable_query(variable, opts)
    out.nested += bool(built.uses)
    out.query_results += built.query_result
    return _nrql(variable, raw, built)


def convert_variables(
    dash: Dashboard, plans: list[VariablePlan], raws: list[RawVar], names: NameMap
) -> Variables:
    """New Relic variables for the ones the planner converts, and what queries need to know.

    A variable is converted after the variables its query uses, so the list is in the order New
    Relic needs (parents first) and a child can tell which parents exist.
    """
    out = Variables()
    intervals: set[str] = set()
    every = frozenset(v.name for v in dash.variables)
    for i in _conversion_order(dash.variables):
        if i >= len(plans) or i >= len(raws):
            continue
        variable, plan, raw = dash.variables[i], plans[i], raws[i]
        if plan.status == DROPPED:
            out.dropped += 1
            if variable.kind == "interval":
                intervals.add(variable.name)
        elif plan.status != CONVERTS:
            _note(out, plan.reason if plan.status == MANUAL else "")
        elif plan.target == "substituted into the queries":
            out.options[variable.name] = variable.query
            out.substituted += 1
        else:
            opts = QueryOptions(
                variables=out.options,
                name_map=names,
                timeseries=False,
                intervals=frozenset(intervals),
                unavailable=every - set(out.options) - intervals - {variable.name},
            )
            try:
                out.variables.append(_convert_one(out, variable, plan, raw, opts))
            except Unconvertible as exc:  # a shape we cannot write
                _note(out, exc.reason)
                continue
            except ValueError:  # a value a model refuses
                _note(out, "")
                continue
            out.options[variable.name] = None
    out.intervals = frozenset(intervals)
    out.unavailable = frozenset(every - set(out.options) - intervals)
    out.no_default = _parents_without_default(out)
    return out


def _parents_without_default(out: Variables) -> int:
    """How many variables that another variable's query uses have no default value."""
    parents = {name for v in out.variables for name in v.depends_on()}
    return sum(1 for v in out.variables if v.name in parents and not v.default_values)
