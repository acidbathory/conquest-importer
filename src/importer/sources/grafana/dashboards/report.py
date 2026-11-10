"""The coverage plan as a Markdown report and as JSON. Untrusted text goes through `cell` only."""

import json
from collections.abc import Callable

from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.taxonomy import classify
from importer.core.text import printable
from importer.sources.grafana.dashboards.panels import TABLE, panel_type
from importer.sources.grafana.dashboards.plan import DashboardPlan, PanelPlan, Plan
from importer.sources.grafana.dashboards.variables import CONVERTS, DROPPED, MANUAL

MAX_MANUAL_ROWS = 25  # manual panels listed per dashboard
MAX_MANUAL_TOTAL = 500  # and in the whole report
MAX_DASHBOARD_ROWS = 200
MAX_REASON_ROWS = 60
CAVEAT = (
    "Tiers say whether a conversion could be written for the query shape. They do not say "
    "whether the metric exists in New Relic, whether names match, or whether the result looks "
    "the same. Nothing was converted or sent anywhere; this is a plan."
)


def cell(text: str, limit: int = 120) -> str:
    """Untrusted text for a table cell: printable characters, then the Markdown escaping."""
    return md_cell(printable(text, limit), limit)


def _share(count: int, total: int) -> str:
    return f"{count} ({100 * count / total:.1f}%)" if total else "0"


def _header(plan: Plan) -> list[str]:
    t = plan.totals
    return [
        *header_lines("Grafana dashboards: coverage plan", "grafana"),
        "",
        CAVEAT,
        "",
        f"Dashboards read: **{t.dashboards}**. Could not be read: **{t.unreadable}**. "
        f"Panels (rows excluded): **{t.panels}**. Queries looked at: **{t.queries}**.",
        "",
        "## Coverage by tier",
        "",
        "Cumulative: a panel that converts at tier 1 also counts at tiers 2 and 3.",
        "",
        "| Tier | Rule | Panels |",
        "|---|---|---|",
        f"| T1 | what the alert translator reads | {_share(t.t1, t.panels)} |",
        f"| T2 | T1 plus rate, increase, histogram_quantile, topk | {_share(t.t2, t.panels)} |",
        f"| T3 | T2 plus arithmetic and count | {_share(t.t3, t.panels)} |",
        f"| T3 with extra widget types | T3 plus gauge, bar gauge, bar chart, heatmap, "
        f"histogram, logs | {_share(t.t3_extra, t.panels)} |",
        "",
        f"Single queries that convert, by the tier they reach: T1 {t.query_t1}, "
        f"T2 {t.query_t2}, T3 {t.query_t3}, manual {t.query_manual}, of {t.queries}.",
        "",
        *_warnings(plan),
    ]


def _warnings(plan: Plan) -> list[str]:
    lines = [f"- {cell(w, 200)}" for w in plan.warnings]
    lines += [f"- {cell(d.title)}: {cell(w, 200)}" for d in plan.dashboards for w in d.warnings[:3]]
    return ["## Notes from reading", "", *lines[:50], ""] if lines else []


def _dashboard_row(d: DashboardPlan) -> str:
    manual = sum(v.status == MANUAL for v in d.variables)
    return (
        f"| {cell(d.title)} | {cell(d.uid, 40)} | {d.shape} | {d.schema_version or ''} | "
        f"{d.panel_count} | {d.t1} | {d.t2} | {d.t3} | {d.t3_extra} | "
        f"{len(d.variables)} ({manual} manual) | {d.rows} | {d.annotations_dropped} |"
    )


def _dashboards(plan: Plan) -> list[str]:
    lines = [
        "## Dashboards",
        "",
        "| Title | Uid | Shape | Schema | Panels | T1 | T2 | T3 | T3+ | Variables | Rows "
        "| Annotations dropped |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_dashboard_row(d) for d in plan.dashboards[:MAX_DASHBOARD_ROWS]]
    if len(plan.dashboards) > MAX_DASHBOARD_ROWS:
        more = len(plan.dashboards) - MAX_DASHBOARD_ROWS
        lines.append(f"| ... {more} more in plan.json |" + " |" * 11)
    return [*lines, ""]


def _inventory(plan: Plan) -> list[str]:
    lines = [
        "## Panel types",
        "",
        "| Type | Panels | New Relic widget | Group |",
        "|---|---|---|---|",
    ]
    for label, count in plan.panel_types.items():
        group = TABLE.get(label, ("none", ""))
        widget = panel_type(label).widget or "none"
        lines.append(f"| {cell(label, 40)} | {count} | {cell(widget, 40)} | {group[0]} |")
    lines += ["", "## Data sources", "", "| Family | Panels |", "|---|---|"]
    lines += [f"| {cell(family, 40)} | {count} |" for family, count in plan.datasources.items()]
    return [*lines, ""]


def _reasons(plan: Plan) -> list[str]:
    lines = [
        "## Why things do not convert",
        "",
        "| Count | Reason | Category | What to do |",
        "|---|---|---|---|",
    ]
    for reason, count in list(plan.reasons.items())[:MAX_REASON_ROWS]:
        titles = ", ".join(x.title for x in remedies_for([reason])) or "no guidance yet"
        lines.append(
            f"| {count} | {cell(reason, 160)} | {classify(reason).value} | {cell(titles)} |"
        )
    return [*lines, ""]


def _variables(plan: Plan) -> list[str]:
    status = {CONVERTS: 0, DROPPED: 0, MANUAL: 0}
    undefined = unused = 0
    for d in plan.dashboards:
        for v in d.variables:
            status[v.status] = status.get(v.status, 0) + 1
        undefined += bool(d.variables_undefined)
        unused += bool(d.variables_unused)
    return [
        "## Variables and other dashboard objects",
        "",
        f"Variables: {status[CONVERTS]} convert, {status[DROPPED]} are dropped (interval and "
        f"data source variables), {status[MANUAL]} stay manual.",
        f"Dashboards that use a variable they do not define: {undefined}. Dashboards with a "
        f"variable nothing uses: {unused}.",
        "",
        "| Dashboards with | Count |",
        "|---|---|",
        *_object_rows(plan),
        "",
    ]


def _object_rows(plan: Plan) -> list[str]:
    checks: list[tuple[str, Callable[[DashboardPlan], int]]] = [
        ("annotation queries (dropped)", lambda d: d.annotations_dropped),
        ("dashboard links", lambda d: d.links),
        ("rows", lambda d: d.rows),
        ("collapsed rows", lambda d: d.collapsed_rows),
        ("repeated rows or panels", lambda d: d.row_repeats + d.panel_repeats),
        ("tabs", lambda d: d.tabs),
    ]
    return [
        f"| {label} | {sum(count(d) > 0 for d in plan.dashboards)} |" for label, count in checks
    ]


def _manual_row(panel: PanelPlan) -> str:
    first = panel.reasons[0] if panel.reasons else ""
    more = f" (+{len(panel.reasons) - 1})" if len(panel.reasons) > 1 else ""
    return f"| {cell(panel.title)} | {cell(panel.type, 40)} | {cell(first, 160)}{more} |"


def _manual(plan: Plan) -> list[str]:
    lines = ["## Panels that stay manual", ""]
    budget = MAX_MANUAL_TOTAL  # rows for the whole report, so a large estate stays readable
    skipped = 0
    for d in plan.dashboards:
        manual = [p for p in d.panels if p.tier is None]
        if not manual:
            continue
        shown = manual[: min(MAX_MANUAL_ROWS, budget)]
        if not shown:
            skipped += 1
            continue
        budget -= len(shown)
        lines += [f"### {cell(d.title)}", "", "| Panel | Type | First reason |", "|---|---|---|"]
        lines += [_manual_row(p) for p in shown]
        if len(manual) > len(shown):
            lines.append(f"| ... {len(manual) - len(shown)} more in plan.json | | |")
        lines.append("")
    if skipped:
        lines += [f"{skipped} more dashboards with manual panels are listed in plan.json.", ""]
    return lines


def _unreadable(plan: Plan) -> list[str]:
    if not plan.unreadable:
        return []
    lines = ["## Could not be read", "", "| File | Entry | Reason |", "|---|---|---|"]
    lines += [
        f"| {cell(u.source, 100)} | {u.position} | {cell(u.reason, 160)} |"
        for u in plan.unreadable[:MAX_DASHBOARD_ROWS]
    ]
    return [*lines, ""]


def build_report(plan: Plan) -> str:
    sections = [_header(plan), _dashboards(plan), _inventory(plan), _reasons(plan)]
    sections += [_variables(plan), _manual(plan), _unreadable(plan)]
    return "\n".join(line for part in sections for line in part).rstrip() + "\n"


def plan_json(plan: Plan) -> str:
    """The whole plan as JSON with every non-ASCII character escaped."""
    return json.dumps(plan.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n"
