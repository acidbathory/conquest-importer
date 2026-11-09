"""The re-instrumentation plan: what Dynatrace monitors today and what replaces it in New Relic.

Input is an entity list (the Entities API response, or the files `extract-dynatrace-inventory`
writes). Output is counts per entity type, per technology and per New Relic target, the rows of
the mapping tables that are UNVERIFIED, and the Dynatrace capabilities that have no migration
path. Nothing here installs anything or talks to a tenant. The mapping tables are data files
(data/entity_types.json and data/technology_agents.json); every row carries a status of DOCS or
UNVERIFIED and a source.
"""

import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.dynatrace.inventory import reasons as r
from importer.sources.dynatrace.inventory.models import Entity, Loaded, Slo

DATA = Path(__file__).parent / "data"
MAX_ROWS = 60  # rows shown for technologies and types that are not in the tables
SUPPORTED_OS = frozenset({"LINUX", "WINDOWS", "DARWIN", "MACOS"})

Status = Literal["DOCS", "UNVERIFIED"]

# What has no migration path (research note section 6.4); fixed text, with its evidence mark.
NO_PATH = (
    (
        "OneAgent deep monitoring (code-level instrumentation, process and service detection)",
        "replaced by New Relic agents or OpenTelemetry, not migrated",
    ),
    (
        "PurePath distributed traces",
        "not exportable in a format New Relic ingests; history stays in Dynatrace",
    ),
    (
        "Davis AI topology, root cause analysis and problems",
        "not portable; New Relic has its own issue correlation",
    ),
    ("Smartscape-driven filters and dashboards", "no equivalent"),
    ("Management zones and security context", "no equivalent; tags and accounts are the nearest"),
    ("Application security and vulnerability findings", "no New Relic source"),
    (
        "Hub extensions (Extensions 2.0)",
        "replace with on-host integrations or OpenTelemetry receivers (UNVERIFIED)",
    ),
)


class Target(BaseModel):
    target: str
    kind: str
    status: Status
    source: str


class TypeRow(BaseModel):
    type: str
    count: int
    target: str
    kind: str
    status: str
    reasons: list[str] = []


class TechnologyRow(BaseModel):
    technology: str
    count: int
    target: str
    status: str
    reasons: list[str] = []


class TargetRow(BaseModel):
    target: str
    count: int
    status: str


class SloSummary(BaseModel):
    """SLOs by shape and by whether the window fits a New Relic rolling window (1, 7, 28 days)."""

    custom: int = 0
    template: int = 0
    classic: int = 0
    rolling_window_fits: int = 0
    other_window: int = 0


class ReinstrumentPlan(BaseModel):
    entities_read: int
    entities_skipped: int
    reported_total: int | None
    types: list[TypeRow] = []
    hosts_by_os: dict[str, int] = {}
    technologies: list[TechnologyRow] = []
    services_without_technology: int = 0
    targets: list[TargetRow] = []
    notes: list[str] = []
    no_path: list[dict[str, str]] = []
    unverified_rows: list[str] = []
    slos: SloSummary | None = None


@lru_cache(maxsize=1)
def _entity_table() -> tuple[dict[str, Target], list[tuple[str, Target]]]:
    raw = json.loads((DATA / "entity_types.json").read_text())
    exact = {row["type"]: Target(**_target(row)) for row in raw["types"]}
    prefixes = [(row["prefix"], Target(**_target(row))) for row in raw["prefixes"]]
    return exact, prefixes


def _target(row: dict[str, str]) -> dict[str, str]:
    return {k: row[k] for k in ("target", "kind", "status", "source")}


@lru_cache(maxsize=1)
def _technology_table() -> tuple[dict[str, tuple[str, Target]], list[str]]:
    """alias (lower case) -> (row name, target), and the UNVERIFIED row names."""
    raw = json.loads((DATA / "technology_agents.json").read_text())
    by_alias: dict[str, tuple[str, Target]] = {}
    unverified: list[str] = []
    for row in raw["rows"]:
        target = Target(**_target(row))
        by_alias.update(dict.fromkeys(row["aliases"], (row["technology"], target)))
        if row["status"] != "DOCS":
            unverified.append(row["technology"])
    return by_alias, unverified


def target_for_type(entity_type: str) -> Target | None:
    exact, prefixes = _entity_table()
    if entity_type in exact:
        return exact[entity_type]
    return next((t for prefix, t in prefixes if entity_type.startswith(prefix)), None)


def target_for_technology(name: str) -> tuple[str, Target] | None:
    return _technology_table()[0].get(name.strip().lower())


def _type_rows(entities: list[Entity]) -> list[TypeRow]:
    rows: list[TypeRow] = []
    counts = Counter(e.type for e in entities)
    for entity_type, count in counts.most_common():
        target = target_for_type(entity_type)
        shown = printable(entity_type, 80)
        if target is None:
            rows.append(
                TypeRow(
                    type=shown,
                    count=count,
                    target="none",
                    kind="unmapped",
                    status="UNVERIFIED",
                    reasons=[r.I_TYPE],
                )
            )
            continue
        notes = [r.I_PROCESS] if target.kind == "no-path" else []
        notes += [r.I_ROW] if target.status != "DOCS" else []
        rows.append(
            TypeRow(
                type=shown,
                count=count,
                target=target.target,
                kind=target.kind,
                status=target.status,
                reasons=notes,
            )
        )
    return rows


def _technology_rows(entities: list[Entity]) -> tuple[list[TechnologyRow], Counter[str], int]:
    """Rows per technology name, the New Relic target counts for services, and untagged services."""
    counts: Counter[str] = Counter()
    targets: Counter[str] = Counter()
    without = 0
    for entity in entities:
        if entity.type != "SERVICE":
            continue
        if not entity.technologies:
            without += 1
            continue
        counts.update(entity.technologies)
        found = {t.target for tech in entity.technologies if (t := _target_of(tech))}
        targets.update(found or {"unmapped"})
    rows: list[TechnologyRow] = []
    for name, count in counts.most_common(MAX_ROWS):
        match = target_for_technology(name)
        if match is None:
            rows.append(
                TechnologyRow(
                    technology=printable(name, 80),
                    count=count,
                    target="none",
                    status="UNVERIFIED",
                    reasons=[r.I_TECH],
                )
            )
        else:
            _, target = match
            notes = [r.I_ROW] if target.status != "DOCS" else []
            rows.append(
                TechnologyRow(
                    technology=printable(name, 80),
                    count=count,
                    target=target.target,
                    status=target.status,
                    reasons=notes,
                )
            )
    return rows, targets, without


def _target_of(technology: str) -> Target | None:
    match = target_for_technology(technology)
    return match[1] if match else None


def _target_rows(entities: list[Entity], service_targets: Counter[str]) -> list[TargetRow]:
    totals: Counter[str] = Counter()
    status: dict[str, str] = {}
    for entity in entities:
        target = target_for_type(entity.type)
        if target is None or target.kind in ("by-technology", "no-path"):
            continue
        totals[target.target] += 1
        status[target.target] = target.status
    for name, count in service_targets.items():
        totals[name] += count
        match = next((t for _, t in _technology_table()[0].values() if t.target == name), None)
        status[name] = match.status if match else "UNVERIFIED"
    return [TargetRow(target=t, count=c, status=status[t]) for t, c in totals.most_common()]


WINDOW = re.compile(r"^(?:now)?-(\d{1,4})([dw])$")
ROLLING_DAYS = frozenset({1, 7, 28})


def summarize_slos(slos: Loaded[Slo]) -> SloSummary:
    """Count SLOs by shape; the window fits when it is -1d, -7d or -28d (or -1w, -4w)."""
    summary = SloSummary()
    for slo in slos.items:
        setattr(summary, slo.kind, getattr(summary, slo.kind) + 1)
        match = WINDOW.match(slo.timeframe)
        days = int(match.group(1)) * (7 if match and match.group(2) == "w" else 1) if match else 0
        if days in ROLLING_DAYS:
            summary.rolling_window_fits += 1
        elif slo.timeframe:
            summary.other_window += 1
    return summary


def plan_reinstrumentation(
    loaded: Loaded[Entity], slos: Loaded[Slo] | None = None
) -> ReinstrumentPlan:
    entities = loaded.items
    technologies, service_targets, without = _technology_rows(entities)
    hosts = Counter(e.os or "unknown" for e in entities if e.type == "HOST")
    plan = ReinstrumentPlan(
        entities_read=len(entities),
        entities_skipped=loaded.skipped,
        reported_total=loaded.reported_total,
        types=_type_rows(entities),
        hosts_by_os={printable(k, 40): v for k, v in hosts.most_common(20)},
        technologies=technologies,
        services_without_technology=without,
        targets=_target_rows(entities, service_targets),
        no_path=[{"capability": c, "path": p} for c, p in NO_PATH],
        unverified_rows=sorted(_technology_table()[1]),
        slos=summarize_slos(slos) if slos is not None and slos.items else None,
    )
    if loaded.reported_total is not None and loaded.reported_total > len(entities):
        plan.notes.append(r.I_PARTIAL)
    if any(os not in SUPPORTED_OS and os != "unknown" for os in hosts):
        plan.notes.append(r.I_OS)
    if without:
        plan.notes.append(r.I_NO_TECH)
    return plan


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    return lines + ["| " + " | ".join(row) + " |" for row in rows]


def _slo_lines(plan: ReinstrumentPlan) -> list[str]:
    if plan.slos is None:
        return []
    s = plan.slos
    return [
        "## SLOs",
        "",
        f"Custom SLI: {s.custom}; from a template: {s.template}; classic: {s.classic}. "
        f"Window fits a New Relic rolling window (1, 7 or 28 days): {s.rolling_window_fits}; "
        f"another window: {s.other_window}. The SLO plan itself is "
        "`importer convert --source dynatrace`.",
        "",
    ]


def render_report(plan: ReinstrumentPlan) -> str:
    lines = [
        "# Dynatrace re-instrumentation plan",
        "",
        f"Entities read: **{plan.entities_read}** (skipped: {plan.entities_skipped}"
        + (f"; the API reported {plan.reported_total}" if plan.reported_total is not None else "")
        + "). Counts are of what Dynatrace monitors today; nothing is installed by this plan.",
        "",
        "## By entity type",
        "",
        *_table(
            ["Entity type", "Count", "New Relic target", "Status"],
            [
                [md_cell(t.type, 60), str(t.count), md_cell(t.target, 200), t.status]
                for t in plan.types
            ],
        ),
        "",
        "## What to install, by New Relic target",
        "",
        *_table(
            ["Target", "Count", "Status"],
            [[md_cell(t.target, 200), str(t.count), t.status] for t in plan.targets],
        ),
        "",
        "## Hosts by operating system",
        "",
        *_table(["OS", "Hosts"], [[md_cell(k, 40), str(v)] for k, v in plan.hosts_by_os.items()]),
        "",
        "## Services by technology",
        "",
        *_table(
            ["Technology", "Services", "New Relic target", "Status"],
            [
                [md_cell(t.technology, 60), str(t.count), md_cell(t.target, 200), t.status]
                for t in plan.technologies
            ],
        ),
        "",
        f"Services that list no technology: {plan.services_without_technology}.",
        "",
        *_slo_lines(plan),
        "## Has no migration path",
        "",
        *[f"- {md_cell(i['capability'], 200)}: {md_cell(i['path'], 300)}" for i in plan.no_path],
    ]
    reasons = [x for t in plan.types for x in t.reasons] + [
        x for t in plan.technologies for x in t.reasons
    ]
    reasons = [*dict.fromkeys([*reasons, *plan.notes])]
    if reasons:
        lines += ["", "## Notes", "", *[f"- {md_cell(x, 300)}" for x in reasons]]
        lines += ["", "## What to do", ""]
        for remedy in remedies_for(reasons):
            lines.append(f"### {md_cell(remedy.title)}")
            lines += [f"- {md_cell(step, 600)}" for step in remedy.steps]
            lines.append("")
    lines += [
        "",
        "## Rows of the mapping tables that are UNVERIFIED",
        "",
        *[f"- {md_cell(name, 100)}" for name in plan.unverified_rows],
        "- every entity type row marked UNVERIFIED in data/entity_types.json",
    ]
    return "\n".join(lines) + "\n"


def write_plan(plan: ReinstrumentPlan, out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} already has files; pass a new folder")
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(plan.model_dump_json(indent=2) + "\n")
    (out / "report.md").write_text(render_report(plan))
