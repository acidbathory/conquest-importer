"""Dashboard variables: which can become New Relic variables (research note section 3.3).

- exact: a list of one dimension of one metric, or a plain text or CSV variable.
- draft: the list needs the metric name map (`fetch metric.series`), or has a Smartscape filter
  that is dropped, or depends on another variable.
- manual: the values come from Smartscape, entities, security events or a field snapshot, the
  variable sets a LIMIT, or the query is not a shape the planner reads.
"""

from importer.sources.dynatrace.dashboards import reasons as r
from importer.sources.dynatrace.dashboards.dql_rules import (
    Analysis,
    filter_clause,
    read_timeseries,
)
from importer.sources.dynatrace.dashboards.dql_scan import (
    ScanError,
    Scanned,
    head,
    rest,
    scan,
    split_at,
    unwrap,
)
from importer.sources.dynatrace.dashboards.models import Variable
from importer.sources.dynatrace.dashboards.plan_models import VariablePlan, Verdict

# commands that shape a list of values; their arguments cannot change what the list holds
LIST_COMMANDS = frozenset({"fields", "fieldsKeep", "fieldsRename", "sort", "summarize", "expand"})
DYNATRACE_ONLY_DATASETS = ("security.events", "dt.entity", "dt.davis")


def _subquery(commands: list[Scanned]) -> Scanned | None:
    """The bracketed query after `data record(...) | append [ ... ]`, or None."""
    if len(commands) == 2 and head(commands[0]) == "data" and head(commands[1]) == "append":
        return unwrap(rest(commands[1]))
    return None


def _list_pipeline(commands: list[Scanned], a: Analysis) -> None:
    """Check the source of a list query and the commands after it."""
    source = commands[0]
    name = head(source)
    if name == "timeseries":
        read_timeseries(rest(source), a)
    elif name == "fetch":
        dataset = head(rest(source))
        if dataset == "metric.series":
            a.draft(r.D_VAR_NAMES)
        elif dataset.startswith(DYNATRACE_ONLY_DATASETS):
            a.block(r.VAR_DYNATRACE_ONLY)
        else:
            a.block(r.VAR_SHAPE)
    elif name in ("smartscapeNodes", "smartscapeEdges", "fieldsSnapshot"):
        a.block(r.VAR_DYNATRACE_ONLY)
    else:
        a.block(r.VAR_SHAPE)
    if a.blockers:
        return  # the source already rules the variable out; more reasons would be noise
    for command in commands[1:]:
        if head(command) == "filter":
            filter_clause(rest(command), a)
        elif head(command) not in LIST_COMMANDS:
            a.block(r.VAR_SHAPE)


def _analyse_list(query: str) -> Analysis:
    a = Analysis()
    try:
        scanned = scan(query)
        commands = split_at(scanned, "|")
    except ScanError:
        a.block(r.VAR_SHAPE)
        return a
    if not commands:
        a.block(r.VAR_SHAPE)
        return a
    inner = _subquery(commands)
    if inner is None:
        _list_pipeline(commands, a)
        return a
    _list_pipeline(split_at(inner, "|"), a)
    return a


def _finish(variable: Variable, a: Analysis, limit_variables: set[str]) -> VariablePlan:
    others = [name for name in a.variables if name != variable.key]
    blockers = list(a.blockers)
    notes = [r.D_VAR_ENTITY if n == r.D_ENTITY else n for n in a.drafts]
    if others and not blockers:
        notes.append(r.D_VAR_DEPENDS)
    if variable.key in limit_variables:
        blockers.append(r.VAR_LIMIT)
    verdict = Verdict.MANUAL if blockers else Verdict.DRAFT if notes else Verdict.EXACT
    return VariablePlan(
        key=variable.key,
        type=variable.type,
        verdict=verdict,
        reasons=blockers,
        notes=notes,
        no_data_source=a.dynatrace_only,
        depends_on=others,
    )


def classify_variable(variable: Variable, limit_variables: set[str]) -> VariablePlan:
    if variable.type in ("text", "csv"):
        return _finish(variable, Analysis(), limit_variables)
    if variable.type != "query":
        return _finish(variable, _blocked(r.VAR_SHAPE), limit_variables)
    return _finish(variable, _analyse_list(variable.input), limit_variables)


def _blocked(reason: str) -> Analysis:
    a = Analysis()
    a.block(reason)
    return a
