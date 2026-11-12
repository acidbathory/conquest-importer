"""A Datadog dashboard log query (count or a measure aggregate) as a New Relic Log query.

The search is read by the monitor's log search parser and turned into WHERE filters by the
monitor's own translation (`logs.search_filters`); nothing is duplicated here. The result is
always approximate: log attribute names are unverified and the logs must be forwarded to
New Relic. A search the translator refuses, an aggregate with no plain NRQL form and a template
variable in the search are placeholders.
"""

import re
from typing import Any

from importer.core.emit.nrql import _filter_clause, identifier
from importer.core.ir import SignalKind, TagFilter
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.metric_query import Converted
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.dashboards.variables import (
    TemplateVar,
    UsedVariable,
    _variable_ir,
    mark_for,
    with_variables,
)
from importer.sources.datadog.logs import DATA_PATH, log_attribute, search_filters
from importer.sources.datadog.logsearch import parse_log_search
from importer.sources.datadog.names import NameMap

MAX_LIMIT = 2000
MEASURES = {
    "avg": "average",
    "sum": "sum",
    "min": "min",
    "max": "max",
    "cardinality": "uniqueCount",
}


# a `$name` that is a whole token: nothing of a name, dot or dash may follow it
_VAR = re.compile(r"(?P<before>^|[\s(:\-])\$(?P<name>[A-Za-z0-9_\-]+)(?![\w.\-])")


def _fail(why: str) -> Fail:
    return Fail(DR.W_LOGS.format(why=why))


def _select(compute: Any, names: NameMap, attrs: list[str]) -> str:
    if not isinstance(compute, dict):
        raise _fail("no aggregation")
    aggregation = compute.get("aggregation")
    if aggregation == "count":
        return "count(*)"
    if not isinstance(aggregation, str) or aggregation not in MEASURES:
        raise _fail(f"aggregation {shown(aggregation, 30)} has no plain New Relic form")
    metric = compute.get("metric")
    if not isinstance(metric, str) or not metric:
        raise _fail(f"{aggregation} needs a measure")
    key = log_attribute(names, metric)
    attrs.append(key)
    return f"{MEASURES[aggregation]}({key})"


def _facets(group_by: Any, names: NameMap, attrs: list[str]) -> tuple[str, bool, list[str]]:
    """The FACET (and LIMIT) text, whether a limit was set, and any note."""
    if not group_by:
        return "", False, []
    if not isinstance(group_by, list) or not all(isinstance(g, dict) for g in group_by):
        raise _fail("group by is not readable")
    keys: list[str] = []
    for g in group_by:
        facet = g.get("facet")
        if not isinstance(facet, str) or not facet:
            raise _fail("group by has no facet")
        keys.append(log_attribute(names, facet))
    attrs.extend(keys)
    notes: list[str] = []
    text = " FACET " + ", ".join(identifier(k) for k in keys)
    limit = group_by[0].get("limit")
    limited = isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit <= MAX_LIMIT
    if limited:
        text += f" LIMIT {limit}"
    sort = group_by[0].get("sort")
    if isinstance(sort, dict) and sort.get("order") == "asc":
        notes.append(DR.N_LOG_ASC)
    return text, bool(limited), notes


def _substitute(
    text: str,
    variables: dict[str, TemplateVar],
    seen: dict[str, UsedVariable],
    names: NameMap,
) -> tuple[str, dict[str, UsedVariable], list[str]]:
    """The search with each `$var` made a filter on a marked value (a New Relic dashboard
    variable), or replaced by the default of a variable that has no tag. `$var` after `key:` is
    that key's value; a bare `$var` is `prefix:value`, as in Datadog. Anything else keeps its `$`
    and the caller refuses it."""
    used: dict[str, UsedVariable] = {}
    notes: list[str] = []

    def resolve(var: TemplateVar, key: str) -> str:
        if var.prefix is not None and var.prefix != key.removeprefix("@") and var.prefix != key:
            raise Fail(DR.V_TAGS.format(what=shown(f"${var.name} on {key}", 60)))
        attribute = log_attribute(names, key)
        known = used.get(var.name) or seen.get(var.name)
        if known is not None and known.attribute != attribute:
            raise Fail(DR.V_TAGS.format(what=shown(f"${var.name}", 60)))
        if known is None:
            used[var.name] = UsedVariable(attribute, _variable_ir(var, attribute, "Log", ""))
        if var.source_name:
            notes.append(DR.N_VAR_RENAMED.format(old=shown(var.source_name, 40), new=var.name))
        return mark_for(var.name)

    def sub(m: re.Match[str]) -> str:
        before, name = m["before"], m["name"]
        var = variables.get(name)
        if var is None:
            raise Fail(DR.V_UNDEFINED.format(name=shown(name, 40)))
        if before == ":":
            # the key is the token before the colon
            start = m.start()
            key = re.search(r"[^\s(:\-][^\s():]*$", text[:start])
            if key is None:
                raise Fail(DR.V_FORM.format(what=shown(name, 40)))
            return ":" + resolve(var, key.group(0))
        if var.prefix is None:
            default = var.default.strip()
            if "$" in default:
                raise Fail(DR.V_DEFAULT.format(name=shown(var.name, 40)))
            notes.append(
                DR.N_VAR_NO_TAG.format(name=shown(var.name, 40), default=shown(default or "*", 60))
            )
            return before + ("" if default in ("", "*") else f"({default})")
        return f"{before}{var.prefix}:{resolve(var, var.prefix)}"

    return _VAR.sub(sub, text), used, notes


def convert_log(
    query: dict[str, Any],
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar] | None = None,
    seen: dict[str, UsedVariable] | None = None,
) -> Converted:
    """`grouped` is False for a single value, which cannot show groups."""
    try:
        return _convert(query, timeseries, grouped, names, variables or {}, seen or {})
    except Fail as fail:  # the monitor translator's reasons get the dashboard prefix
        if fail.reason.startswith(DR.W_LOGS.split("{")[0]):
            raise
        raise _fail(fail.reason) from None
    except ValueError as exc:  # an attribute name that is not a valid identifier
        raise _fail(shown(exc, 100)) from None


def _convert(
    query: dict[str, Any],
    timeseries: bool,
    grouped: bool,
    names: NameMap,
    variables: dict[str, TemplateVar],
    seen: dict[str, UsedVariable],
) -> Converted:
    search = query.get("search")
    text = search.get("query") if isinstance(search, dict) else None
    if not isinstance(text, str):
        raise _fail("the query has no search")
    text, used, var_notes = _substitute(text, variables, seen, names)
    if "$" in text:
        raise _fail("the search uses a dashboard template variable in a form that is not converted")
    parsed = parse_log_search(text)
    if not parsed.ok or not parsed.lossless:
        problem = parsed.diagnostics[0].message if parsed.diagnostics else "unreadable"
        raise _fail(f"{R.M_LOG_SEARCH}: {shown(problem, 100)}")
    filters = search_filters(parsed.node, names)
    attrs = [f.key for f in filters]
    select = _select(query.get("compute"), names, attrs)
    facet, limited, notes = _facets(query.get("group_by"), names, attrs)
    if facet and not grouped:
        raise _fail("a single value cannot show groups")
    nrql = f"SELECT {select} FROM Log"  # noqa: S608 - constant select and event names
    if filters:
        nrql += " WHERE " + " AND ".join(_filter_clause(SignalKind.LOG, f) for f in filters)
    marked = [
        TagFilter(key=f.key, value=f.value, negate=f.negate)
        for f in filters
        if f.value.startswith("__datadog_variable_")
    ]
    nrql = with_variables(nrql, marked)
    if "__datadog_variable_" in nrql:  # a mark where no variable can stand (an OR list)
        raise _fail("a template variable is used in a form that is not converted")
    nrql += facet + (" TIMESERIES AUTO" if timeseries else "")
    reasons = [
        R.N_LOG_ATTRS_UNVERIFIED.format(names=shown(", ".join(attrs), 120) or "none"),
        R.N_LOG_CASE,
        DR.N_LOG_FORWARD,
        *notes,
        *var_notes,
    ]
    if any(f.key == "message" for f in filters):
        reasons.append(R.N_LOG_FREE_TEXT)
    indexes = query.get("indexes")
    if isinstance(indexes, list) and indexes and indexes != ["*"]:
        reasons.append(R.N_INDEX.format(index=shown(", ".join(map(str, indexes)), 40)))
    return Converted(
        series=None, nrql=nrql, notes=reasons, data_path=DATA_PATH, variables=used, limited=limited
    )
