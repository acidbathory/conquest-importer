"""Replace instead of translate: match Grafana dashboards to New Relic quickstarts.

`quickstarts.json` lists quickstarts read from the public newrelic-quickstarts repository. A
dashboard matches an entry by its grafana.com id or title ("exact name match"), or by the
metric families its queries use ("signature match"). A name is never trusted on its own:
for a quickstart on the same Prometheus data path the dashboard's metric names are compared
with the names the quickstart's dashboards query (the overlap check), and a match that shares
too few names is only "related". Nothing is installed or converted here.
"""

import json
import re
from functools import cache
from pathlib import Path

from pydantic import BaseModel, Field

from importer.core.report_brand import header_lines
from importer.sources.grafana.dashboards.load import FOLDERS_FILE, LIBRARY_FILE, _read_json
from importer.sources.grafana.dashboards.models import MAX_FILES, Dashboard, Estate
from importer.sources.grafana.dashboards.plan import DashboardPlan, plan_dashboard

DATA = Path(__file__).with_name("quickstarts.json")
SAME_NAMES = "prometheus-same-names"
REPLACE = "replace with quickstart"
CHANGE = "replace after changing collection"
RELATED = "related quickstart"
RANK = {REPLACE: 0, CHANGE: 1, RELATED: 2}
EXACT = "exact name match"
SIGNATURE = "signature match"
MIN_SHARED = 3  # names in common before a signature or a replacement is claimed
MIN_SHARE = 0.5  # share of the dashboard's names the quickstart must also use
MIN_HITS = 3  # distinct names of one family before a collector-change signature is claimed
GENERIC = ("process_", "go_", "scrape_", "promhttp_", "select_")  # every exporter has these
CAVEAT = (
    "Metric names, units and dimensions differ between a Grafana dashboard and the quickstart, "
    "so check the queries before you rely on it."
)
KEYWORDS = frozenset({"group_left", "group_right"})
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|`[^`]*`|\'(?:\\.|[^\'\\])*\'')
_BRACES = re.compile(r"\{[^{}]*\}")
_GROUPS = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)")
_VARIABLE = re.compile(r"\$\{[^}]*\}|\$\w+|\[[^\]]*\]")
_IDENT = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")


class Quickstart(BaseModel):
    title: str
    slug: str
    id: str
    url: str
    config_url: str
    dashboards: list[str] = Field(default_factory=list)
    alert_policies: list[str] = Field(default_factory=list)


class DashboardOnly(BaseModel):
    name: str
    url: str
    alert_policies_url: str = ""


class Also(BaseModel):
    title: str
    slug: str
    url: str
    note: str


class Entry(BaseModel):
    id: str
    grafana_ids: list[int] = Field(default_factory=list)
    title_patterns: list[str] = Field(default_factory=list)
    metric_prefixes: list[str] = Field(default_factory=list)
    exclude_prefixes: list[str] = Field(default_factory=list)
    data_path: str
    needs: str
    mapping_verified: bool = False
    mapping_basis: str = ""
    caveat: str
    quickstart: Quickstart | None = None
    dashboard_only: DashboardOnly | None = None
    expected_metrics: list[str] = Field(default_factory=list)
    also: list[Also] = Field(default_factory=list)
    verified: bool = False
    evidence: list[str] = Field(default_factory=list)


@cache
def entries() -> tuple[Entry, ...]:
    data = json.loads(DATA.read_text())
    return tuple(Entry.model_validate(e) for e in data["entries"])


def promql_metrics(expr: str) -> set[str]:
    """Metric names (with an underscore) in a PromQL expression, without labels or functions."""
    text = _STRING.sub('""', expr)
    text = _VARIABLE.sub(" ", text)
    for _ in range(4):  # nested label matchers are rare; a few passes cover them
        text = _BRACES.sub(" ", text)
    text = _GROUPS.sub(" ", text)
    names: set[str] = set()
    for match in _IDENT.finditer(text):
        name = match.group(0)
        if "_" not in name or name.startswith("__") or name in KEYWORDS:
            continue
        if text[match.end() :].lstrip().startswith("("):
            continue  # a function
        names.add(name)
    return names


def dashboard_metrics(dash: Dashboard) -> set[str]:
    names: set[str] = set()
    for panel in dash.panels:
        for target in panel.targets:
            if not target.hidden and target.expr:
                names |= promql_metrics(target.expr)
    return {n for n in names if not n.startswith(GENERIC)}


class Overlap(BaseModel):
    shared: int = 0
    total: int = 0  # names the dashboard uses, generic ones left out
    share: float = 0.0


def overlap(names: set[str], expected: set[str] | list[str]) -> Overlap:
    kept = {n for n in names if not n.startswith(GENERIC)}
    shared = len(kept & set(expected))
    return Overlap(shared=shared, total=len(kept), share=shared / len(kept) if kept else 0.0)


class Match(BaseModel):
    entry: str
    confidence: str
    verdict: str
    overlap: Overlap
    data_path: str
    quickstart: Quickstart | None = None
    dashboard_only: DashboardOnly | None = None
    needs: str
    caveat: str
    mapping_verified: bool
    also: list[Also] = Field(default_factory=list)
    why: str = ""


def _exact(entry: Entry, title: str, gnet_id: int | None) -> str:
    if gnet_id is not None and gnet_id in entry.grafana_ids:
        return f"grafana.com id {gnet_id}"
    low = title.lower()
    for pattern in entry.title_patterns:
        if re.search(pattern, low):
            return f"title matches /{pattern}/"
    return ""


def _hits(entry: Entry, names: set[str]) -> int:
    return sum(
        n.startswith(tuple(entry.metric_prefixes))
        and not n.startswith(tuple(entry.exclude_prefixes))
        for n in names
    )


def _match(entry: Entry, title: str, gnet_id: int | None, names: set[str]) -> Match | None:
    ov = overlap(names, entry.expected_metrics)
    why = _exact(entry, title, gnet_id)
    same = entry.data_path == SAME_NAMES
    if why:
        confidence = EXACT
    elif same and ov.shared >= MIN_SHARED:
        confidence, why = SIGNATURE, f"{ov.shared} metric names in common"
    elif not same and _hits(entry, names) >= MIN_HITS:
        confidence, why = SIGNATURE, f"{_hits(entry, names)} {entry.metric_prefixes[0]}* names"
    else:
        return None  # a prefix alone is a name collision, not a match
    if not same:
        verdict = CHANGE
    elif ov.shared >= MIN_SHARED and ov.share >= MIN_SHARE:
        verdict = REPLACE
    else:
        verdict = RELATED
    return Match(
        entry=entry.id,
        confidence=confidence,
        verdict=verdict,
        overlap=ov,
        data_path=entry.data_path,
        quickstart=entry.quickstart,
        dashboard_only=entry.dashboard_only,
        needs=entry.needs,
        caveat=entry.caveat,
        mapping_verified=entry.mapping_verified,
        also=entry.also,
        why=why,
    )


def match_dashboard(dash: Dashboard, gnet_id: int | None) -> list[Match]:
    """Every entry the dashboard matches, best verdict first."""
    names = dashboard_metrics(dash)
    found = [m for e in entries() if (m := _match(e, dash.title, gnet_id, names))]
    return sorted(found, key=lambda m: (RANK[m.verdict], -m.overlap.share, m.entry))


class QuickstartRow(BaseModel):
    uid: str
    title: str
    gnet_id: int | None = None
    verdict: str = ""  # best match, or "" when none
    action: str  # replace, both or convert
    converts: int = 0  # panels the converter handles (any tier)
    panels: int = 0
    matches: list[Match] = Field(default_factory=list)


class QuickstartPlan(BaseModel):
    kind: str = "grafana-quickstart-plan"
    source: str = "https://github.com/newrelic/newrelic-quickstarts"
    caveat: str = CAVEAT
    coverage: str = ""
    replace: int = 0
    change: int = 0
    related: int = 0
    dashboards: list[QuickstartRow] = Field(default_factory=list)


def _row(dash: Dashboard, gnet_id: int | None, planned: DashboardPlan) -> QuickstartRow:
    matches = match_dashboard(dash, gnet_id)
    best = matches[0].verdict if matches else ""
    native = best in (REPLACE, CHANGE)
    converts = planned.t3
    action = ("both" if converts else "replace") if native else "convert"
    return QuickstartRow(
        uid=dash.uid,
        title=dash.title,
        gnet_id=gnet_id,
        verdict=best,
        action=action,
        converts=converts,
        panels=planned.panel_count,
        matches=matches,
    )


def plan_quickstarts(estate: Estate, gnet_ids: dict[str, int]) -> QuickstartPlan:
    plan = QuickstartPlan()
    for dash in estate.dashboards:
        plan.dashboards.append(_row(dash, gnet_ids.get(dash.uid), plan_dashboard(dash)))
    plan.replace = sum(r.verdict == REPLACE for r in plan.dashboards)
    plan.change = sum(r.verdict == CHANGE for r in plan.dashboards)
    plan.related = sum(r.verdict == RELATED for r in plan.dashboards)
    native, total = plan.replace + plan.change, len(plan.dashboards)
    plan.coverage = f"{native} of {total} dashboards have a native replacement"
    return plan


def _gnet(node: object) -> tuple[str, int] | None:
    for _ in range(3):  # envelope, wrapper, body
        if not isinstance(node, dict):
            return None
        gnet = node.get("gnetId")
        if (
            isinstance(node.get("uid"), str)
            and isinstance(gnet, int)
            and not isinstance(gnet, bool)
        ):
            return node["uid"], gnet
        node = node.get("dashboard") or node.get("spec")
    return None


def read_gnet_ids(source: Path) -> dict[str, int]:
    """grafana.com ids by dashboard uid, read from the export files."""
    files = [source] if source.is_file() else sorted(source.rglob("*.json"))[:MAX_FILES]
    found: dict[str, int] = {}
    for path in files:
        if path.name in (LIBRARY_FILE, FOLDERS_FILE):
            continue
        data, _ = _read_json(path)
        items = data if isinstance(data, list) else [data]
        for item in items:
            pair = _gnet(item)
            if pair:
                found[pair[0]] = pair[1]
    return found


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()[:160]


def _best_cells(best: Match | None) -> tuple[str, str]:
    if best is None:
        return "", ""
    if best.quickstart:
        link = f"[{_cell(best.quickstart.title)}]({best.quickstart.url})"
    else:
        link = _cell(best.dashboard_only.name) if best.dashboard_only else ""
    share = (
        f"{best.overlap.shared} of {best.overlap.total}" if best.data_path == SAME_NAMES else "n/a"
    )
    return link, share


def quickstart_report(plan: QuickstartPlan) -> str:
    lines = [
        *header_lines("Grafana dashboards: native New Relic replacements", "grafana"),
        "",
        f"**{plan.coverage}.**",
        "",
        f"- Same data path, enough metric names in common: {plan.replace}",
        f"- A quickstart exists but collection must change: {plan.change}",
        f"- Related quickstart, too few names in common: {plan.related}",
        "",
        f"{CAVEAT} Source: {plan.source} (read on the date in `quickstarts.json`).",
        "",
        "| Dashboard | grafana.com id | Action | Verdict | Quickstart | Names in common "
        "| Panels converting |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in plan.dashboards:
        best = row.matches[0] if row.matches else None
        link, share = _best_cells(best)
        lines.append(
            f"| {_cell(row.title)} | {row.gnet_id or ''} | {row.action} | {row.verdict or 'none'} "
            f"| {link} | {share} | {row.converts} of {row.panels} |"
        )
    lines += ["", "## What each match needs", ""]
    for row in plan.dashboards:
        for m in row.matches:
            lines.append(f"### {_cell(row.title)}: {m.entry} ({m.verdict}, {m.confidence})")
            lines += ["", f"- Why: {m.why}", f"- Needs: {m.needs}", f"- Caveat: {m.caveat}"]
            if m.dashboard_only:
                lines.append(f"- Dashboard JSON: {m.dashboard_only.url}")
            for a in m.also:
                lines.append(f"- Alternative: [{_cell(a.title)}]({a.url}): {_cell(a.note)}")
            lines.append("")
    return "\n".join(lines) + "\n"


def quickstart_json(plan: QuickstartPlan) -> str:
    return plan.model_dump_json(indent=2) + "\n"
