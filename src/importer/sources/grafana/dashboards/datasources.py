"""Which data source family a panel or query uses, from a reference that may be a template.

A reference is an object (type and uid), a name, a template such as `${DS_PROMETHEUS}` or
`$datasource`, a pseudo source (`-- Mixed --`, `-- Dashboard --`, expressions) or nothing. The
answer is a family word: prometheus, loki, one of the labels of the alert translator's table
(cloudwatch, sql, ...), mixed, dashboard, grafana, expression, other, or "" when it cannot be
told. Only these words ever reach a reason.
"""

import re

from importer.sources.grafana.dashboards.models import Dashboard, DsRef, Panel, Target
from importer.sources.grafana.dashboards.reasons import OTHER_LABEL
from importer.sources.grafana.translate import EXPRESSION_UIDS, OTHER_DATASOURCES, UID_HINTS

CONVERTIBLE = ("prometheus", "loki")
TEMPLATE = re.compile(r"\$\{(\w+)(?::[^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
PSEUDO = {"-- mixed --": "mixed", "-- dashboard --": "dashboard", "-- grafana --": "grafana"}


def family(plugin: str) -> str:
    """The family word for a plugin id: prometheus and loki as they are, else the table label."""
    if plugin in CONVERTIBLE:
        return plugin
    if plugin in OTHER_DATASOURCES:
        return OTHER_DATASOURCES[plugin]
    return OTHER_LABEL if plugin else ""


def _hint(name: str) -> str:
    """The plugin a data source name suggests (`prom-main` is Prometheus), else ""."""
    lowered = name.lower()
    return next((plugin for needle, plugin in UID_HINTS if needle in lowered), "")


def _template_plugin(uid: str, dash: Dashboard) -> str:
    found = TEMPLATE.fullmatch(uid)
    if found is None:
        return ""
    name = found[1] or found[2] or found[3]
    if name in dash.inputs:
        return dash.inputs[name]
    for variable in dash.variables:
        if variable.kind == "datasource" and variable.name == name:
            return variable.query
        if variable.kind == "constant" and variable.name == name:
            return _hint(variable.query)  # a constant holding a data source name
    return ""


def _from_reference(ref: DsRef, dash: Dashboard) -> str:
    uid = ref.uid
    if ref.type == "__expr__" or uid in EXPRESSION_UIDS:
        return "expression"
    pseudo = PSEUDO.get(uid.lower())
    if pseudo:
        return pseudo
    if ref.type == "grafana" or (ref.type == "datasource" and uid in ("grafana", "")):
        return "grafana"
    if ref.type and ref.type != "datasource":
        return family(ref.type)
    plugin = _template_plugin(uid, dash)
    if plugin:
        return family(plugin)
    return family(_hint(uid)) if _hint(uid) else ""


def resolve(ref: DsRef | None, hint: str, dash: Dashboard) -> str:
    """The family of one reference; the key-based `hint` is used only when there is no reference."""
    if ref is None or not (ref.type or ref.uid):
        return family(hint)
    return _from_reference(ref, dash)


def _has_reference(ref: DsRef | None) -> bool:
    return ref is not None and bool(ref.type or ref.uid)


def resolve_target(target: Target, panel: Panel, dash: Dashboard) -> str:
    """A query's family: its own reference, else the panel's (unless Mixed), else its keys."""
    own = resolve(target.ds, "", dash)
    if own:
        return own
    if _has_reference(target.ds):
        return ""
    inherited = resolve(panel.ds, "", dash)
    if inherited and inherited != "mixed":
        return inherited
    if _has_reference(panel.ds) and inherited != "mixed":
        return ""
    return family(target.hint)
