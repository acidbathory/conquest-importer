"""Grafana dashboards into the dashboard IR (`IRDashboard`).

`load_items` reads the export with the coverage planner's loader and, from the same files, the
display options the planner's models leave out. `translate_dashboards` plans each dashboard,
converts its variables and panels, lays the widgets out and lists in the dashboard's reasons
what the person must know first: the data has to reach New Relic under the same names.
Nothing here raises on odd input; a panel or dashboard that cannot convert is labelled.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from importer.core.dashboard_ir import IRDashboard, IRVariable, IRWidget, WidgetKind
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.layout import Placed, lay_out
from importer.sources.grafana.dashboard_convert.name_map import NameMap, active_name_map
from importer.sources.grafana.dashboard_convert.panels import Context, Draft, convert_panel
from importer.sources.grafana.dashboard_convert.raw import RawDashboard, raw_for_estate
from importer.sources.grafana.dashboard_convert.variables import (
    RawVar,
    Variables,
    convert_variables,
)
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.models import Dashboard
from importer.sources.grafana.dashboards.plan import DashboardPlan, plan_dashboard

MAX_NAME = 200
MAX_KEY = 10_000
SYSTEM = "grafana"


@dataclass
class Item:
    """One thing the loader found: a dashboard with its extras, or a file that could not be read."""

    dash: Dashboard | None = None
    raw: RawDashboard | None = None
    problem: str = ""
    source: str = ""


@dataclass
class Report:
    """What conversion learned besides the IR; the command prints it."""

    metrics: Counter[str]
    mapped: bool = False


def load_items(path: Path) -> list[Item]:
    """Dashboards from a file or folder, each with its raw extras; unreadable files too."""
    estate = load_estate(path)
    raws = raw_for_estate(path, estate)
    items = [Item(dash=d, raw=raws.get(i)) for i, d in enumerate(estate.dashboards)]
    items += [Item(problem=u.reason, source=u.source) for u in estate.unreadable]
    return items


def _unreadable(item: Item) -> IRDashboard:
    return IRDashboard(
        source_system=SYSTEM,
        source_key=printable(f"unreadable:{item.source}", MAX_KEY),
        name=printable(item.source or "unreadable file", MAX_NAME),
        widgets=[],
        confidence=Confidence.UNSUPPORTED,
        reasons=[r.D_BAD_FILE.format(reason=item.problem)],
    )


def _placed(draft: Draft, dash: Dashboard, index: int) -> Placed:
    panel = dash.panels[index]
    return Placed(draft, panel.x, panel.y, panel.w, panel.h, panel.section)


def _dashboard_reasons(
    dash: Dashboard,
    plan: DashboardPlan,
    raw: RawDashboard,
    counts: tuple[int, int],
    skipped: int,
) -> list[str]:
    done, total = counts
    notes = [r.D_DATA, r.D_PANELS.format(done=done, total=total, placeholders=total - done)]
    pairs = [
        (dash.collapsed_rows, r.D_COLLAPSED.format(count=dash.collapsed_rows)),
        (dash.row_repeats, r.D_REPEAT_ROWS.format(count=dash.row_repeats)),
        (dash.tabs, r.D_TABS.format(count=dash.tabs)),
        (plan.annotations_dropped, r.D_ANNOTATIONS),
        (dash.links, r.D_LINKS),
        (raw.time_range, r.D_TIME.format(text=printable(raw.time_range, 80))),
        (skipped, r.D_WIDGET_CAP.format(count=skipped)),
    ]
    return [*notes, *(text for present, text in pairs if present), *plan.warnings]


def _variable_reasons(found: Variables) -> list[str]:
    """Counts of the variables that did not become New Relic variables."""
    notes: list[str] = []
    if found.dropped:
        notes.append(r.D_VARIABLE_DROPPED.format(count=found.dropped))
    if found.manual:
        notes.append(r.D_VARIABLE_MANUAL.format(count=found.manual))
        notes += found.why  # the fixed reason of each kind of variable left out
    if found.nested:
        notes.append(r.D_VARIABLE_NESTED.format(count=found.nested))
    if found.query_results:
        notes.append(r.D_VARIABLE_QUERY_RESULT.format(count=found.query_results))
    if found.no_default:
        notes.append(r.D_VARIABLE_PARENT_DEFAULT.format(count=found.no_default))
    return notes


@dataclass
class Drafted:
    """The planner's verdicts next to the widget drafts, panel by panel (before layout)."""

    plan: DashboardPlan
    raw: RawDashboard
    variables: Variables
    ctx: Context
    drafts: list[Draft]


def draft_panels(dash: Dashboard, raw: RawDashboard | None, names: NameMap) -> Drafted:
    """Plan the dashboard, convert its variables and turn every panel into a draft."""
    known = raw or RawDashboard(displays=[None] * len(dash.panels), matched=False)
    plan = plan_dashboard(dash)
    raw_variables = known.variables + [RawVar()] * max(
        0, len(dash.variables) - len(known.variables)
    )
    variables = convert_variables(dash, plan.variables, raw_variables, names)
    ctx = Context(dash=dash, name_map=names, variables=variables)
    drafts = [
        convert_panel(
            panel,
            plan.panels[index],
            known.displays[index] if index < len(known.displays) else None,
            ctx,
        )
        for index, panel in enumerate(dash.panels)
    ]
    return Drafted(plan, known, variables, ctx, drafts)


def translate_dashboard(item: Item, names: NameMap, metrics: Counter[str]) -> IRDashboard:
    dash = item.dash
    if dash is None:
        return _unreadable(item)
    found = draft_panels(dash, item.raw, names)
    placed = [_placed(d, dash, i) for i, d in enumerate(found.drafts)]
    widgets, skipped = lay_out(placed)
    done = sum(d.kind != WidgetKind.UNSUPPORTED for d in found.drafts)
    metrics.update(found.ctx.metrics)
    reasons = _dashboard_reasons(dash, found.plan, found.raw, (done, len(placed)), skipped)
    reasons += _variable_reasons(found.variables)
    variables = found.variables.with_numeric(found.ctx.numeric)
    return _build(dash, item, widgets, variables, reasons, done)


def _build(
    dash: Dashboard,
    item: Item,
    widgets: list[IRWidget],
    variables: list[IRVariable],
    reasons: list[str],
    done: int,
) -> IRDashboard:
    if not dash.panels:
        confidence, reasons = Confidence.UNSUPPORTED, [*reasons, "dashboard has no panels"]
    else:
        confidence = Confidence.APPROXIMATE if done else Confidence.MANUAL
    key = dash.uid or dash.title or item.source or "dashboard"
    return IRDashboard(
        source_system=SYSTEM,
        source_key=printable(key, MAX_KEY),
        name=printable(dash.title or "Grafana dashboard", MAX_NAME),
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
        variables=variables,
    )


def convert_all(
    items: list[Item], names: NameMap | None = None
) -> tuple[list[IRDashboard], Report]:
    """Every dashboard as IR, with the metric names the queries use."""
    known = names if names is not None else active_name_map()
    metrics: Counter[str] = Counter()
    out: list[IRDashboard] = []
    used: set[str] = set()
    for item in items:
        translated = translate_dashboard(item, known, metrics)
        key, n = translated.source_key, 1
        while translated.source_key in used:  # a repeated key gets #2, #3 so files stay apart
            n += 1
            translated = translated.model_copy(update={"source_key": f"{key}#{n}"})
        used.add(translated.source_key)
        out.append(translated)
    return out, Report(metrics, bool(known.metrics))


def translate_dashboards(items: list[Item], names: NameMap | None = None) -> list[IRDashboard]:
    return convert_all(items, names)[0]
