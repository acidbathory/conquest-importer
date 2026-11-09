"""Classify dashboard variables against what New Relic dashboards offer (research note, section 6).

Nothing is created here. A variable `converts` (to a New Relic `nrql`, `enum` or `string`
variable, or by substituting its value), is `dropped` (interval and data source variables have no
use once the queries are converted) or stays `manual` with one fixed reason.
"""

import re

from pydantic import BaseModel

from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.datasources import resolve
from importer.sources.grafana.dashboards.models import MAX_TEXT, Dashboard, Variable
from importer.sources.grafana.dashboards.query_tier import RE2_UNSUPPORTED, variables_in

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
LABEL_VALUES = re.compile(r"label_values\( ?(?:(.+?) ?, ?)?[A-Za-z_]\w* ?\)")
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


def _query_variable(variable: Variable, dash: Dashboard, known: set[str]) -> tuple[str, str]:
    """(reason, target) for a query variable: label_values on Prometheus becomes `uniques()`."""
    family = resolve(variable.ds, "", dash)
    if family not in ("", "prometheus"):
        return r.V_QUERY_DS, ""
    if variable.regex and RE2_UNSUPPORTED.search(variable.regex):
        return r.V_QUERY_REGEX, ""
    query = " ".join(variable.query.split())
    if (variables_in(query) & known) - {variable.name}:
        return r.V_NESTED, ""
    if LABEL_VALUES.fullmatch(query) is None:
        return r.V_QUERY_SHAPE, ""
    return "", "nrql variable"


def classify_variable(
    variable: Variable, dash: Dashboard, known: set[str], seen: set[str] | None = None
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
    reason, target = _query_variable(variable, dash, known)
    return VariablePlan(**base, status=MANUAL if reason else CONVERTS, reason=reason, target=target)
