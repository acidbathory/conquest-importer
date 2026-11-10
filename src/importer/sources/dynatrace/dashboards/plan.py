"""Plan every dashboard: one verdict per tile and per variable, totals, and a report.

Nothing here creates a New Relic dashboard. The plan says what could be created, in what
confidence, and what a person must do for the rest.
"""

from collections import Counter
from pathlib import Path

from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable
from importer.sources.dynatrace.dashboards import reasons as r
from importer.sources.dynatrace.dashboards.classic import classify_tiles
from importer.sources.dynatrace.dashboards.models import (
    BAD_FIELD,
    Document,
    PlatformDashboard,
)
from importer.sources.dynatrace.dashboards.plan_models import (
    Counts,
    DashboardPlan,
    DashboardPlans,
    TilePlan,
    VariablePlan,
    Verdict,
)
from importer.sources.dynatrace.dashboards.report_util import table
from importer.sources.dynatrace.dashboards.tiles import classify_tile, unreadable_tile
from importer.sources.dynatrace.dashboards.variables import classify_variable

DATA_VERDICTS = (Verdict.EXACT, Verdict.DRAFT, Verdict.MANUAL)


def count_tiles(tiles: list[TilePlan], variables: list[VariablePlan]) -> Counts:
    c = Counts()
    for tile in tiles:
        if tile.kind == "markdown" or tile.verdict is Verdict.CONVERT:
            c.markdown += 1
            continue
        c.data += 1
        setattr(c, tile.verdict.value, getattr(c, tile.verdict.value) + 1)
        c.no_data_source += 1 if tile.no_data_source else 0
    for variable in variables:
        name = f"variables_{variable.verdict.value}"
        setattr(c, name, getattr(c, name) + 1)
    return c


def _platform(doc: Document, platform: PlatformDashboard) -> DashboardPlan:
    tiles = [classify_tile(t) for t in platform.tiles]
    limit_names = {name for tile in tiles for name in tile.limit_variables}
    variables = [classify_variable(v, limit_names) for v in platform.variables]
    unreadable = [u for u in platform.unreadable if not u.ref.startswith("variable ")]
    tiles += [unreadable_tile(u) for u in unreadable]
    bad_variables = [u for u in platform.unreadable if u.ref.startswith("variable ")]
    variables += [
        VariablePlan(
            key=printable(u.ref, 80),
            type="unknown",
            verdict=Verdict.MANUAL,
            reasons=[r.VAR_UNREADABLE],
        )
        for u in bad_variables
    ]
    notes = ["a default timeframe is set on the dashboard"] if platform.default_timeframe else []
    return DashboardPlan(
        ref=printable(doc.ref, 120),
        name=printable(doc.name, 120),
        kind="platform",
        app=printable(doc.app, 80),
        private=doc.private,
        notes=notes,
        counts=count_tiles(tiles, variables),
        tiles=tiles,
        variables=variables,
        unreadable_tiles=len(unreadable),
    )


def plan_document(doc: Document) -> DashboardPlan:
    base = {
        "ref": printable(doc.ref, 120),
        "name": printable(doc.name, 120),
        "app": printable(doc.app, 80),
        "private": doc.private,
    }
    if doc.kind == "platform" and doc.platform is not None:
        return _platform(doc, doc.platform)
    if doc.kind == "classic" and doc.classic is not None:
        tiles = classify_tiles(doc.classic)
        notes = [r.C_ZONE] if doc.classic.management_zone else []
        return DashboardPlan(
            **base,
            kind="classic",
            notes=notes,
            counts=count_tiles(tiles, []),
            tiles=tiles,
            unreadable_tiles=len(doc.classic.unreadable),
        )
    reason = (
        r.FILE_SHAPE
        if doc.error in ("not-a-dashboard", "not-an-object", BAD_FIELD)
        else r.FILE_UNREADABLE
    )
    return DashboardPlan(**base, kind="unreadable", reasons=[reason])


def plan_documents(documents: list[Document], copies: list[str] | None = None) -> DashboardPlans:
    copies = copies or []
    plans = DashboardPlans(copies_skipped=len(copies), copy_names=copies[:50])
    for doc in documents:
        plan = plan_document(doc)
        plans.dashboards.append(plan)
        plans.totals.add(plan.counts)
    return plans


def _reason_counts(plans: DashboardPlans) -> Counter[str]:
    found: Counter[str] = Counter()
    for dash in plans.dashboards:
        found.update(dash.reasons)
        for tile in dash.tiles:
            found.update(tile.reasons)
        for variable in dash.variables:
            found.update(variable.reasons)
    return found


def _markdown_lines(plans: DashboardPlans) -> list[str]:
    tiles = [t for d in plans.dashboards for t in d.tiles if t.kind in ("markdown", "MARKDOWN")]
    flagged = sum(1 for t in tiles if "unsafe-link" in t.flags or "html" in t.flags)
    return [
        f"Markdown tiles with an unsafe-link or html flag: {flagged} of {len(tiles)}. A tile "
        "without a flag was not found unsafe by these checks; that is not a proof it is safe.",
        "",
    ]


def _summary(plans: DashboardPlans) -> list[str]:
    t = plans.totals
    kinds = Counter(d.kind for d in plans.dashboards)
    return [
        f"Dashboards read: **{len(plans.dashboards)}** (platform {kinds['platform']}, classic "
        f"{kinds['classic']}, unreadable {kinds['unreadable']}); exact copies skipped: "
        f"{plans.copies_skipped}.",
        "",
        "No New Relic dashboard is created from this plan. It says what could be created and "
        "what a person must do for the rest.",
        "",
        *table(
            ["Tiles", "Count"],
            [
                ["data tiles", str(t.data)],
                ["exact (same meaning in NRQL, nothing dropped)", str(t.exact)],
                ["draft (a clause dropped or not verified, with a note)", str(t.draft)],
                ["manual", str(t.manual)],
                ["markdown (convert; spacers are dropped)", str(t.markdown)],
                [
                    "tiles that read Dynatrace-only data (no New Relic data source)",
                    str(t.no_data_source),
                ],
            ],
        ),
        "",
        *_markdown_lines(plans),
        *table(
            ["Variables", "Count"],
            [
                ["exact", str(t.variables_exact)],
                ["draft", str(t.variables_draft)],
                ["manual", str(t.variables_manual)],
            ],
        ),
    ]


def _per_dashboard(plans: DashboardPlans) -> list[str]:
    rows = [
        [
            md_cell(d.name),
            d.kind,
            md_cell(d.app, 40),
            str(d.counts.data),
            str(d.counts.exact),
            str(d.counts.draft),
            str(d.counts.manual),
            str(d.counts.markdown),
            f"{d.counts.variables_exact}/{d.counts.variables_draft}/{d.counts.variables_manual}",
        ]
        for d in plans.dashboards
    ]
    return table(
        [
            "Dashboard",
            "Kind",
            "App",
            "Data",
            "Exact",
            "Draft",
            "Manual",
            "Markdown",
            "Variables e/d/m",
        ],
        rows,
    )


def _tile_rows(plans: DashboardPlans) -> list[str]:
    lines: list[str] = []
    for dash in plans.dashboards:
        if not dash.tiles and not dash.variables and not dash.reasons:
            continue
        lines += ["", f"### {md_cell(dash.name)}", ""]
        if dash.reasons:
            lines += [f"- {md_cell(reason, 300)}" for reason in dash.reasons] + [""]
        rows = [
            [
                md_cell(t.id, 40),
                md_cell(t.kind, 40),
                md_cell(t.title, 60),
                t.verdict.value + (f" ({t.action})" if t.action else ""),
                t.widget,
                md_cell("; ".join([*t.reasons, *t.notes, *t.flags]), 400),
            ]
            for t in dash.tiles
        ]
        if rows:
            lines += table(
                ["Tile", "Type", "Title", "Verdict", "Widget", "Reasons and notes"], rows
            )
        vrows = [
            [
                md_cell(v.key, 40),
                md_cell(v.type, 40),
                v.verdict.value,
                md_cell("; ".join([*v.reasons, *v.notes]), 400),
            ]
            for v in dash.variables
        ]
        if vrows:
            lines += ["", *table(["Variable", "Type", "Verdict", "Reasons and notes"], vrows)]
    return lines


def _steps(plans: DashboardPlans) -> list[str]:
    counts = _reason_counts(plans)
    if not counts:
        return []
    lines = ["", "## Manual reasons, most frequent first", ""]
    lines += [f"- {n} x {md_cell(reason, 300)}" for reason, n in counts.most_common()]
    lines += ["", "## What to do", ""]
    for remedy in remedies_for([reason for reason, _ in counts.most_common()]):
        lines.append(f"### {md_cell(remedy.title)}")
        lines += [f"- {md_cell(step, 600)}" for step in remedy.steps]
        lines.append("")
    return lines


def _copies(plans: DashboardPlans) -> list[str]:
    if not plans.copy_names:
        return []
    shown = [f"- {md_cell(name, 200)}" for name in plans.copy_names]
    more = plans.copies_skipped - len(plans.copy_names)
    tail = [f"- and {more} more"] if more > 0 else []
    return ["", "## Skipped copies (same content, different name or id)", "", *shown, *tail]


def render_report(plans: DashboardPlans) -> str:
    lines = [
        *header_lines("Dynatrace dashboard plan", "dynatrace"),
        "",
        *_summary(plans),
        *_copies(plans),
    ]
    lines += ["", "## By dashboard", ""]
    lines += _per_dashboard(plans)
    lines += _steps(plans)
    lines += ["", "## Tiles and variables", *_tile_rows(plans)]
    return "\n".join(lines) + "\n"


def write_plan(plans: DashboardPlans, out: Path) -> None:
    """Write report.md and plan.json into a new or empty folder."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(plans.model_dump_json(indent=2, exclude_defaults=True) + "\n")
    (out / "report.md").write_text(render_report(plans))
