"""Grafana dashboard variables to New Relic dashboard variables (research note, section 6).

The coverage planner decides which variables convert (`dashboards/variables.py`); this module
writes them. A `label_values` query becomes an `nrql` variable over `uniques()`, a custom list an
`enum`, a text box a `string`; a constant is written into the queries and makes no variable;
interval and data source variables are dropped. The variable query is built with `identifier`
and `quote_string` like every other query. Which clauses may hold a `{{variable}}` is checked by
the IR (`core/dashboard_nrql.py`).
"""

import re
from dataclasses import dataclass, field

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
from importer.sources.grafana.dashboard_convert.nrql import cond_sql
from importer.sources.grafana.dashboard_convert.reuse import (
    Unconvertible,
    prom_conditions,
    regex_cond,
    selector,
)
from importer.sources.grafana.dashboards.models import Dashboard, Variable
from importer.sources.grafana.dashboards.variables import CONVERTS, DROPPED, VariablePlan

ALL = "$__all"
LABEL_VALUES = re.compile(r"label_values\( ?(?:(.+?) ?, ?)?([A-Za-z_]\w*) ?\)")
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


def variable_query(variable: Variable, names: NameMap) -> str:
    """The NRQL that lists a Prometheus label's values."""
    query = " ".join(variable.query.split())
    found = LABEL_VALUES.fullmatch(query)
    if found is None or "$" in query:
        raise Unconvertible(r.C_SHAPE)
    try:
        label = identifier(found[2])
    except ValueError:
        raise Unconvertible(r.C_NAME) from None
    event, where = "Metric", []
    if found[1]:
        name, body = _split_selector(found[1])
        entry = names.metrics.get(name) if name else None
        if entry is not None:
            event = entry.event
        if name and event == "Metric":
            where.append(f"metricName = {quote_string(entry.attribute if entry else name)}")
        elif entry is not None:
            where.append(f"{identifier(entry.attribute)} IS NOT NULL")
        where += [cond_sql(c, []) for c in prom_conditions(body, r.C_SHAPE)]
    if variable.regex:
        body = variable.regex.strip()
        if body.startswith("/"):
            body = body[1:].rsplit("/", 1)[0]
        where.append(cond_sql(regex_cond(found[2], body, r.C_SHAPE), []))
    text = f"FROM {identifier(event)} SELECT uniques({label})"
    return text + (" WHERE " + " AND ".join(where) if where else "")


def _nrql(variable: Variable, raw: RawVar, names: NameMap) -> IRVariable:
    multi = variable.multi or variable.include_all
    wants_all = ALL in raw.current or (variable.include_all and not raw.current)
    chosen = [c for c in raw.current if c != ALL][:50]
    defaults = ["*"] if wants_all and multi else (chosen if multi else chosen[:1])
    return IRVariable(
        name=variable.name,
        title=_title(variable, raw),
        type=VariableType.NRQL,
        nrql=variable_query(variable, names),
        multi_select=multi,
        default_values=[clean(d) for d in defaults],
        ignore_time_range=raw.refresh == 1,
    )


def _one(variable: Variable, plan: VariablePlan, raw: RawVar, names: NameMap) -> IRVariable:
    if plan.target == "enum variable":
        return _enum(variable, raw)
    if plan.target == "string variable":
        return _string(variable, raw)
    return _nrql(variable, raw, names)


def convert_variables(
    dash: Dashboard, plans: list[VariablePlan], raws: list[RawVar], names: NameMap
) -> Variables:
    """New Relic variables for the ones the planner converts, and what queries need to know."""
    out = Variables()
    intervals: set[str] = set()
    for variable, plan, raw in zip(dash.variables, plans, raws, strict=False):
        if plan.status == DROPPED:
            out.dropped += 1
            if variable.kind == "interval":
                intervals.add(variable.name)
        elif plan.status != CONVERTS:
            out.manual += 1
        elif plan.target == "substituted into the queries":
            out.options[variable.name] = variable.query
            out.substituted += 1
        else:
            try:
                out.variables.append(_one(variable, plan, raw, names))
            except (
                Unconvertible,
                ValueError,
            ):  # a shape we cannot write, or a value a model refuses
                out.manual += 1
                continue
            out.options[variable.name] = None
    out.intervals = frozenset(intervals)
    out.unavailable = frozenset({v.name for v in dash.variables} - set(out.options) - intervals)
    return out
