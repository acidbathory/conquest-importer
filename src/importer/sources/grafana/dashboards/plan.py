"""The coverage plan: what in a set of dashboards could convert, at which tier, and why not.

Reads the models of `models.py` and produces `Plan`. Nothing is emitted for New Relic. A panel
converts at tier N when its type has a widget the IR can express, it is not an unresolved
library panel, has no transformations, and every visible query is Prometheus or LogQL and
reaches tier N or lower (see `query_tier.py`). Panels whose only blocker is a widget type that
New Relic has but the IR cannot create yet are counted separately ("with extra widget types").
"""

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.datasources import CONVERTIBLE, resolve_target
from importer.sources.grafana.dashboards.models import (
    PLAN_MARKER,
    Dashboard,
    Estate,
    Panel,
    Target,
    Unreadable,
)
from importer.sources.grafana.dashboards.panels import (
    EXTRA,
    LOG_TYPES,
    NONE,
    PanelType,
    panel_type,
)
from importer.sources.grafana.dashboards.query_tier import (
    Verdict,
    classify_log_lines,
    classify_query,
    variables_in,
)
from importer.sources.grafana.dashboards.variables import (
    MANUAL,
    VariablePlan,
    classify_variable,
)

MAX_LISTED = 100  # names listed per dashboard in the plan
MAX_PLAN_PANELS = 1000  # panels listed per dashboard in plan.json; counts cover all
TOP_TIER = 3
Lookup = Callable[[int], "PanelPlan | None"]


class PanelPlan(BaseModel):
    id: int | None = None
    title: str = ""
    type: str = ""  # the label from panels.py ("other" for a plugin)
    widget: str = ""
    section: str = ""
    tier: int | None = None  # the tier at which the panel converts; None = manual
    extra_tier: int | None = None  # tier of the queries when only the widget type blocks
    query_tier: int | None = None  # tier of the queries alone, whatever the widget
    reasons: list[str] = Field(default_factory=list)
    datasources: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    queries: int = 0
    query_manual: int = 0
    query_tiers: list[int] = Field(default_factory=list)
    reuses: bool = False
    variables: set[str] = Field(default_factory=set, exclude=True)


class DashboardPlan(BaseModel):
    uid: str
    title: str
    folder: str = ""
    shape: str = "classic"
    schema_version: int | None = None
    panels: list[PanelPlan] = Field(default_factory=list)
    variables: list[VariablePlan] = Field(default_factory=list)
    panel_count: int = 0
    t1: int = 0
    t2: int = 0
    t3: int = 0
    t3_extra: int = 0
    queries: int = 0
    query_t1: int = 0
    query_t2: int = 0
    query_t3: int = 0
    query_manual: int = 0
    panels_omitted: int = 0  # panels counted but not listed in plan.json
    variables_used: list[str] = Field(default_factory=list)
    variables_unused: list[str] = Field(default_factory=list)
    variables_undefined: list[str] = Field(default_factory=list)
    annotations_dropped: int = 0
    links: int = 0
    rows: int = 0
    collapsed_rows: int = 0
    row_repeats: int = 0
    panel_repeats: int = 0
    tabs: int = 0
    warnings: list[str] = Field(default_factory=list)


class Totals(BaseModel):
    dashboards: int = 0
    panels: int = 0
    t1: int = 0
    t2: int = 0
    t3: int = 0
    t3_extra: int = 0
    queries: int = 0
    query_t1: int = 0
    query_t2: int = 0
    query_t3: int = 0
    query_manual: int = 0
    variables: int = 0
    variables_manual: int = 0
    unreadable: int = 0


class Plan(BaseModel):
    kind: str = PLAN_MARKER  # lets the loader recognise a plan file and skip it
    warnings: list[str] = Field(default_factory=list)  # from reading the dashboards
    dashboards: list[DashboardPlan] = Field(default_factory=list)
    unreadable: list[Unreadable] = Field(default_factory=list)
    totals: Totals = Field(default_factory=Totals)
    panel_types: dict[str, int] = Field(default_factory=dict)
    datasources: dict[str, int] = Field(default_factory=dict)  # panels by data source family
    reasons: dict[str, int] = Field(default_factory=dict)  # most common first


@dataclass
class _Queries:
    reasons: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    tiers: list[int] = field(default_factory=list)  # one per query that converts
    classified: int = 0  # visible queries of a panel whose type has a widget
    manual: int = 0
    variables: set[str] = field(default_factory=set)
    visible: int = 0

    @property
    def tier(self) -> int | None:
        return None if self.reasons or not self.visible else max(self.tiers, default=None)


def _flags(panel: Panel, label: str) -> list[str]:
    found = [
        ("repeat", bool(panel.repeat)),
        ("mappings", panel.mappings > 0),
        ("overrides", panel.overrides > 0),
        ("links", panel.links > 0),
        ("html text", panel.text_mode == "html"),
        ("legacy type", bool(panel.type) and panel.type != label),
    ]
    return [name for name, present in found if present]


def _reused(target: Target, lookup: Lookup) -> Verdict:
    source = lookup(target.panel_id) if target.panel_id is not None else None
    if source is None or source.query_tier is None:
        return Verdict(None, r.P_REUSE)
    return Verdict(source.query_tier)


def _verdict(
    target: Target, family: str, known: frozenset[str], logs: bool, lookup: Lookup
) -> Verdict:
    if family == "expression":
        return Verdict(None, r.P_EXPRESSION)
    if family == "dashboard":
        return _reused(target, lookup)
    if family in CONVERTIBLE:
        if target.malformed or not target.expr.strip():
            return Verdict(None, r.P_EMPTY_QUERY)
        if logs and family == "loki":
            return classify_log_lines(target.expr, known)
        return classify_query(target.expr, family, known)
    if family in ("", "mixed"):
        return Verdict(None, r.P_DS_UNRESOLVED)
    return Verdict(None, r.P_DS_UNSUPPORTED.format(label=family))


def _queries(
    panel: Panel,
    dash: Dashboard,
    known: frozenset[str],
    kind: PanelType,
    lookup: Lookup,
    *,
    classify: bool,
) -> _Queries:
    out = _Queries()
    visible = [t for t in panel.targets if not t.hidden]
    out.visible = len(visible)
    logs = kind.label in LOG_TYPES
    for target in visible:
        family = resolve_target(target, panel, dash)
        out.families.append(family or "unresolved")
        if not classify:
            continue
        verdict = _verdict(target, family, known, logs, lookup)
        out.variables |= verdict.variables
        out.classified += 1
        if verdict.tier is None:
            out.manual += 1
            out.reasons.append(verdict.reason)
        else:
            out.tiers.append(verdict.tier)
    out.reasons = list(dict.fromkeys(out.reasons))
    return out


def _template_names(panel: Panel) -> set[str]:
    """Variables a panel names outside its queries: data source templates and the repeat."""
    refs = (panel.ds, *(t.ds for t in panel.targets))
    names = {v for ref in refs if ref for v in variables_in(ref.uid)}
    return names | ({panel.repeat} if panel.repeat else set())


NO_CONVERSION = frozenset({"", "none", "short", "string", "locale", "percent"})
PLAIN_REDUCERS = frozenset({"", "current", "last", "lastNotNull"})
LOWERED_TYPES = frozenset({"stat", "table"})


def _changes_display(panel: Panel) -> bool:
    """Mappings, overrides, a scaling unit or a reducer other than the latest value."""
    reducer = any(c not in PLAIN_REDUCERS for c in panel.calcs)
    return bool(panel.mappings or panel.overrides or reducer or panel.unit not in NO_CONVERSION)


def _notes(panel: Panel) -> list[str]:
    visible = [t for t in panel.targets if not t.hidden]
    notes = [
        (r.N_INSTANT, any(t.instant or t.fmt == "table" for t in visible)),
        (r.N_INTERVAL, any(t.interval for t in visible)),
        (r.N_LEGEND, any(_legend_has_text(t.legend) for t in visible)),
    ]
    return [text for text, present in notes if present]


def _legend_has_text(legend: str) -> bool:
    return "{{" in legend and bool(re.sub(r"\{\{[^}]*\}\}", "", legend).strip())


def _lowered(plan: PanelPlan, panel: Panel, kind: PanelType) -> None:
    """Take a converting stat or table down one tier when its options change what it shows."""
    if plan.tier is None or kind.label not in LOWERED_TYPES or not _changes_display(panel):
        return
    if plan.tier >= TOP_TIER:
        plan.tier, plan.reasons = None, [r.P_OPTIONS]
    else:
        plan.tier += 1
        plan.notes.append(r.N_DOWNGRADED)


def plan_panel(
    panel: Panel,
    dash: Dashboard,
    known: frozenset[str],
    lookup: Lookup,
    manual_variables: frozenset[str] = frozenset(),
) -> PanelPlan:
    kind = panel_type(panel.type)
    plan = PanelPlan(
        id=panel.id,
        title=panel.title,
        type=kind.label,
        widget=kind.widget,
        section=panel.section,
        flags=_flags(panel, kind.label),
        variables=_template_names(panel),
    )
    if panel.library_uid and not panel.library_found:
        plan.reasons = [r.P_LIBRARY]
        return plan
    if kind.label == "text":
        plan.tier = 1  # a text panel has no query, so another panel cannot reuse its result
        return plan
    found = _queries(panel, dash, known, kind, lookup, classify=kind.group != NONE)
    plan.datasources = sorted(set(found.families))
    plan.variables |= found.variables
    plan.reuses = any(t.panel_id is not None for t in panel.targets if not t.hidden)
    if kind.group == NONE:
        plan.reasons = [r.P_NO_WIDGET.format(label=kind.label)]
        return plan
    plan.queries, plan.query_tiers, plan.query_manual = found.classified, found.tiers, found.manual
    plan.notes = _notes(panel)
    reasons = found.reasons if found.visible else [r.P_NO_QUERY]
    plan.query_tier = None if reasons else found.tier
    gap = [r.P_WIDGET_GAP.format(label=kind.label)] if kind.group == EXTRA else []
    extra = [r.P_TRANSFORM] if panel.transformations else []
    extra += [r.P_VARIABLE] if plan.variables & manual_variables else []
    extra += [r.P_TIME_OVERRIDE] if panel.time_shift or panel.time_from else []
    plan.reasons = gap + extra + reasons
    if not plan.reasons:
        plan.tier = plan.query_tier
        _lowered(plan, panel, kind)
    elif plan.reasons == gap:
        plan.extra_tier = plan.query_tier
    return plan


def _no_source(_panel_id: int) -> PanelPlan | None:
    return None


def _plan_panels(dash: Dashboard, known: frozenset[str], manual: frozenset[str]) -> list[PanelPlan]:
    """Plan every panel; `-- Dashboard --` panels are planned again once their sources are known."""
    plans = [plan_panel(p, dash, known, _no_source, manual) for p in dash.panels]
    by_id: dict[int, PanelPlan] = {}
    for plan in plans:
        if plan.id is not None and plan.id not in by_id:
            by_id[plan.id] = plan

    def source(panel_id: int) -> PanelPlan | None:
        found = by_id.get(panel_id)
        return None if found is None or found.reuses else found  # one level of reuse only

    for index, plan in enumerate(plans):
        if plan.reuses:
            plans[index] = plan_panel(dash.panels[index], dash, known, source, manual)
    return plans


def _tally(out: DashboardPlan, plans: list[PanelPlan]) -> None:
    for plan in plans:
        out.panel_count += 1
        out.t1 += plan.tier is not None and plan.tier <= 1
        out.t2 += plan.tier is not None and plan.tier <= 2
        out.t3 += plan.tier is not None and plan.tier <= 3
        best = plan.tier if plan.tier is not None else plan.extra_tier
        out.t3_extra += best is not None and best <= 3
        out.queries += plan.queries
        out.query_manual += plan.query_manual
        for tier in plan.query_tiers:
            out.query_t1 += tier == 1
            out.query_t2 += tier == 2
            out.query_t3 += tier == 3


def _usage(
    dash: Dashboard, known: frozenset[str], plans: list[PanelPlan], out: DashboardPlan
) -> None:
    used = {v for plan in plans for v in plan.variables}
    mentioned: set[str] = set()
    for panel in dash.panels:
        for target in panel.targets:
            mentioned |= variables_in(target.expr)
    for variable in dash.variables:
        mentioned |= variables_in(variable.query)
    out.variables_used = sorted(known & (used | mentioned))[:MAX_LISTED]
    out.variables_unused = sorted(known - used - mentioned)[:MAX_LISTED]
    out.variables_undefined = sorted(mentioned - known)[:MAX_LISTED]


def plan_dashboard(dash: Dashboard) -> DashboardPlan:
    known = frozenset(v.name for v in dash.variables if v.name)
    variables: list[VariablePlan] = []
    seen: set[str] = set()
    for variable in dash.variables:
        variables.append(classify_variable(variable, dash, set(known), seen))
        seen.add(variable.name)
    manual = frozenset(v.name for v in variables if v.status == MANUAL)
    plans = _plan_panels(dash, known, manual)
    out = DashboardPlan(
        uid=dash.uid,
        title=dash.title,
        folder=dash.folder,
        shape=dash.shape,
        schema_version=dash.schema_version,
        panels=plans,
        variables=variables,
        annotations_dropped=sum(not a.builtin for a in dash.annotations),
        links=dash.links,
        rows=dash.rows,
        collapsed_rows=dash.collapsed_rows,
        row_repeats=dash.row_repeats,
        panel_repeats=sum(bool(p.repeat) for p in dash.panels),
        tabs=dash.tabs,
        warnings=dash.warnings,
    )
    _tally(out, plans)
    _usage(dash, known, plans, out)
    return out


def _totals(plan: Plan) -> Totals:
    totals = Totals(dashboards=len(plan.dashboards), unreadable=len(plan.unreadable))
    for d in plan.dashboards:
        totals.panels += d.panel_count
        totals.t1 += d.t1
        totals.t2 += d.t2
        totals.t3 += d.t3
        totals.t3_extra += d.t3_extra
        totals.queries += d.queries
        totals.query_t1 += d.query_t1
        totals.query_t2 += d.query_t2
        totals.query_t3 += d.query_t3
        totals.query_manual += d.query_manual
        totals.variables += len(d.variables)
        totals.variables_manual += sum(v.status == MANUAL for v in d.variables)
    return totals


def _inventories(plan: Plan) -> None:
    types: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for d in plan.dashboards:
        for panel in d.panels:
            types[panel.type] += 1
            sources.update(panel.datasources)
            reasons.update(panel.reasons)
        reasons.update(v.reason for v in d.variables if v.reason)
        reasons[r.A_DROPPED] += d.annotations_dropped > 0
    plan.panel_types = dict(types.most_common())
    plan.datasources = dict(sources.most_common())
    ranked = sorted((n, text) for text, n in reasons.items() if n)
    plan.reasons = {text: n for n, text in sorted(ranked, key=lambda x: (-x[0], x[1]))}


def build_plan(estate: Estate) -> Plan:
    """The coverage plan for everything the loader read."""
    plan = Plan(unreadable=estate.unreadable, warnings=estate.warnings)
    plan.dashboards = [plan_dashboard(d) for d in estate.dashboards]
    plan.totals = _totals(plan)
    _inventories(plan)
    for d in plan.dashboards:  # the lists are cut last: every count above used all panels
        d.panels_omitted = max(0, len(d.panels) - MAX_PLAN_PANELS)
        del d.panels[MAX_PLAN_PANELS:]
    return plan
