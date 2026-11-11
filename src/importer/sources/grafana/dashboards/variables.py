"""Classify dashboard variables against what New Relic dashboards offer (research note, section 6).

Nothing is created here. A variable `converts` (to a New Relic `nrql`, `enum` or `string`
variable, or by substituting its value), is `dropped` (interval and data source variables have no
use once the queries are converted) or stays `manual` with one fixed reason.

A variable whose query uses another variable (a chained variable) converts when every variable it
uses converts, because New Relic accepts `{{parent}}` in a variable's query on some plans
(`link_variables`); a cycle, or a parent that stays manual, makes it manual.
"""

import re

from pydantic import BaseModel

from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.datasources import resolve
from importer.sources.grafana.dashboards.models import MAX_TEXT, Dashboard, Variable
from importer.sources.grafana.dashboards.query_tier import (
    RE2_UNSUPPORTED,
    plain_regex,
    variables_in,
)

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
LABEL_VALUES = re.compile(r"label_values\( ?(?:(.+?) ?, ?)?([A-Za-z_]\w*) ?\)")
QUERY_RESULT = re.compile(r"query_result\( ?(.+?) ?\)")
METRICS = re.compile(r"metrics\( ?(.*?) ?\)")
# The regex people write to read one label out of `metric{a="1",b="2"} 1 1700000000`.
CAPTURE_LABEL = re.compile(r'/(?:\.\*)?([A-Za-z_]\w*)="\(\[\^"\][+*]\)(?:"?\.\*)?/')
# `sum by (l) (selector)` and `sum(selector) by (l)`: the series that exist, grouped by one label.
GROUP_OPS = "sum|min|max|avg|count|group"
GROUPED_BEFORE = re.compile(rf"(?:{GROUP_OPS}) ?by ?\( ?([A-Za-z_]\w*) ?\) ?\((.+)\)")
GROUPED_AFTER = re.compile(rf"(?:{GROUP_OPS}) ?\((.+)\) ?by ?\( ?([A-Za-z_]\w*) ?\)")
SELECTOR = re.compile(r"([A-Za-z_:][A-Za-z0-9_:]*)?(?:\{(.*)\})?", re.DOTALL)
MATCH_ALL = ".*"
CONVERTS, DROPPED, MANUAL = "converts", "dropped", "manual"
# type -> (status, what it becomes)
SIMPLE_KINDS = {
    "custom": (CONVERTS, "enum variable"),
    "switch": (CONVERTS, "enum variable"),
    "textbox": (CONVERTS, "string variable"),
    "constant": (CONVERTS, "substituted into the queries"),
    "interval": (DROPPED, ""),
    "datasource": (DROPPED, ""),
}


class VariablePlan(BaseModel):
    name: str
    kind: str
    status: str
    reason: str = ""
    target: str = ""


def _plain_variable_regex(regex: str) -> bool:
    """A Grafana variable regex (`/.../flags`) the converter can carry: plain words only.

    Anything else, including a capture group (Grafana keeps only the group), needs RLIKE
    verification and stays manual.
    """
    body = regex.strip()
    if body.startswith("/"):
        body = body[1:].rsplit("/", 1)[0]
    if RE2_UNSUPPORTED.search(body):
        return False
    return plain_regex(body)


def query_result_label(regex: str) -> str | None:
    """The label a `query_result()` variable's regex captures, or None for any other regex."""
    found = CAPTURE_LABEL.fullmatch(regex.strip())
    return found[1] if found else None


def is_selector(text: str) -> bool:
    """A metric name with optional matchers, or matchers alone: nothing else (no functions)."""
    found = SELECTOR.fullmatch(text.strip())
    return bool(found and (found[1] or found[2] is not None) and "(" not in (found[1] or ""))


def grouped_selector(text: str) -> tuple[str, str] | None:
    """(grouped label, selector) of `sum by (l) (selector)` or `sum(selector) by (l)`."""
    text = text.strip()
    if (found := GROUPED_BEFORE.fullmatch(text)) is not None:
        label, inner = found[1], found[2]
    elif (found := GROUPED_AFTER.fullmatch(text)) is not None:
        inner, label = found[1], found[2]
    else:
        return None
    return (label, inner.strip()) if is_selector(inner) else None


def query_result_selector(inner: str, captured: str) -> str | None:
    """The selector whose series carry the captured label, or None when the shapes differ.

    A bare selector reads like label_values(selector, label). A selector grouped by exactly the
    captured label lists the same values, because grouping keeps one series per value.
    """
    if is_selector(inner):
        return inner.strip()
    grouped = grouped_selector(inner)
    return grouped[1] if grouped and grouped[0] == captured else None


def _query_result(variable: Variable, inner: str) -> tuple[str, str]:
    """`query_result(selector)` with a capture regex reads like label_values(selector, label)."""
    label = query_result_label(variable.regex)
    if label is None:
        return r.V_QUERY_RESULT, ""
    if query_result_selector(inner, label) is not None:
        return "", "nrql variable"
    grouped = grouped_selector(inner)
    return (r.V_QUERY_RESULT_LABEL if grouped else r.V_QUERY_RESULT), ""


def _query_variable(variable: Variable, dash: Dashboard) -> tuple[str, str]:
    """(reason, target) for a query variable: label_values, metrics() and query_result(selector)
    on Prometheus become `uniques()`."""
    family = resolve(variable.ds, "", dash)
    if family not in ("", "prometheus"):
        return r.V_QUERY_DS, ""
    query = " ".join(variable.query.split())
    if (found := QUERY_RESULT.fullmatch(query)) is not None:
        return _query_result(variable, found[1])
    if variable.regex and not _plain_variable_regex(variable.regex):
        return r.V_QUERY_REGEX, ""
    metrics = METRICS.fullmatch(query)
    if metrics is not None:
        if metrics[1] != MATCH_ALL and not _plain_variable_regex(metrics[1]):
            return r.V_QUERY_REGEX, ""
        return "", "nrql variable"
    if LABEL_VALUES.fullmatch(query) is None:
        return r.V_QUERY_SHAPE, ""
    return "", "nrql variable"


def classify_variable(
    variable: Variable, dash: Dashboard, seen: set[str] | None = None
) -> VariablePlan:
    """The plan for one variable; `seen` holds the names already planned in this dashboard."""
    base = {"name": variable.name[:MAX_TEXT], "kind": variable.kind}
    if not NAME.fullmatch(variable.name):
        return VariablePlan(**base, status=MANUAL, reason=r.V_NAME)
    if seen and variable.name in seen:
        return VariablePlan(**base, status=MANUAL, reason=r.V_DUPLICATE)
    if variable.kind in SIMPLE_KINDS:
        status, target = SIMPLE_KINDS[variable.kind]
        return VariablePlan(**base, status=status, target=target)
    if variable.kind == "adhoc":
        return VariablePlan(**base, status=MANUAL, reason=r.V_ADHOC)
    if variable.kind != "query":
        return VariablePlan(**base, status=MANUAL, reason=r.V_KIND)
    if variable.name in variables_in(variable.query):
        return VariablePlan(**base, status=MANUAL, reason=r.V_CYCLE)
    reason, target = _query_variable(variable, dash)
    return VariablePlan(**base, status=MANUAL if reason else CONVERTS, reason=reason, target=target)


def _reaches(start: str, goal: str, uses: dict[str, list[str]]) -> bool:
    """True when `goal` can be reached from `start` through the variables each one uses."""
    seen: set[str] = set()
    todo = list(uses.get(start, []))
    while todo:
        name = todo.pop()
        if name == goal:
            return True
        if name not in seen:
            seen.add(name)
            todo += uses.get(name, [])
    return False


def _demoted(plan: VariablePlan, reason: str) -> VariablePlan:
    return plan.model_copy(update={"status": MANUAL, "reason": reason, "target": ""})


def link_variables(variables: list[Variable], plans: list[VariablePlan]) -> list[VariablePlan]:
    """Plans after the variables that use each other are taken into account.

    A variable in a cycle is manual (`V_CYCLE`); a variable that uses a manual variable is manual
    (`V_NESTED`), down the chain. Parents may come after their children in the list: the
    converter orders them. A second variable with a used name is already manual and is left out
    of the graph.
    """
    first = {p.name: i for i, p in reversed(list(enumerate(plans)))}
    uses: dict[str, list[str]] = {}
    for i, (v, p) in enumerate(zip(variables, plans, strict=True)):
        if v.kind == "query" and first[p.name] == i:
            uses[v.name] = sorted((variables_in(v.query) & first.keys()) - {v.name})
    out = list(plans)
    for name in uses:
        i = first[name]
        if out[i].status != MANUAL and _reaches(name, name, uses):
            out[i] = _demoted(out[i], r.V_CYCLE)
    changed = True
    while changed:
        changed = False
        for name, parents in uses.items():
            i = first[name]
            if out[i].status != MANUAL and any(out[first[p]].status == MANUAL for p in parents):
                out[i] = _demoted(out[i], r.V_NESTED)
                changed = True
    return out
