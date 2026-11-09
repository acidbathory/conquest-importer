"""The markdown report of a conversion: what was made, what a person must still do.

Untrusted text (contact point names, receivers, rule titles) goes through `md_cell`. Email
addresses, URLs and secret values never appear: the report shows counts, fixed sentences, and the
logical names of the secrets a person has to supply.
"""

from collections.abc import Sequence

from importer.core.emit.notifications import secret_variable
from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notify_convert.convert import Conversion
from importer.sources.grafana.notify_convert.destinations import Point

MAX_ROWS = 200
HOW_TO_SUPPLY = [
    "Terraform (`notifications.tf`): each secret is a sensitive variable named `nr_secret_` plus "
    "the secret name in lower case. Set it with an environment variable "
    "(`export TF_VAR_nr_secret_<name>=...`) or a `*.tfvars` file that stays out of git. "
    "`secrets.tfvars.example` lists every variable with a placeholder.",
    "`importer apply-notifications` does not take secrets yet and cannot create a destination "
    "with a secure URL; use the Terraform file for these plans.",
]


def _cut(items: Sequence[str], what: str) -> list[str]:
    if len(items) <= MAX_ROWS:
        return list(items)
    return [*items[:MAX_ROWS], f"- and {len(items) - MAX_ROWS} more {what}"]


def _outcome(p: Point) -> str:
    if p.skipped:
        return "skipped (destinations map)"
    if p.destination is not None:
        return f"{p.destination.type.value} destination"
    if p.manual is not None:
        return f"manual ({p.manual.nr_type or 'no New Relic type'})"
    return "no New Relic equivalent"


def _summary(c: Conversion) -> list[str]:
    n = c.counts
    rows = [
        ("Contact points read", n.contact_points),
        ("Contact points converted to a destination", n.points_with_destination),
        ("Contact point names no route uses", n.unused_points),
        ("Destinations made, one per workflow (a channel each)", n.destinations),
        ("Contact points that need a person", n.manual_points),
        ("Contact points with no New Relic equivalent", n.no_equivalent_points),
        ("Contact points skipped by the destinations map", n.skipped_points),
        ("Routes", n.routes),
        ("Workflows planned (disabled)", n.workflows),
        ("Muting rules planned (disabled)", n.muting_rules),
        ("Rules routed", n.rules_routed),
        ("Rules not converted, so not routed", n.rules_not_converted),
        ("Routes with nothing New Relic can create", n.routes_without_destination),
        ("Rules the policy could not decide", n.rules_undecidable),
        ("Secrets a person must supply", n.secrets),
    ]
    return ["| What | Count |", "|---|---|", *(f"| {k} | {v} |" for k, v in rows), ""]


def _secrets(c: Conversion) -> list[str]:
    pairs = [(s, p) for p in c.points for s in p.secrets]
    if not pairs:
        return []
    lines = ["## Secrets to supply", "", *HOW_TO_SUPPLY, ""]
    lines += ["| Secret | Terraform variable | For contact point |", "|---|---|---|"]
    lines += [
        f"| {md_cell(s)} | {md_cell(secret_variable(s))} | "
        f"{md_cell(p.cp.name)} ({md_cell(p.cp.type)}) |"
        for s, p in pairs[:MAX_ROWS]
    ]
    return [*lines, ""]


def _points(c: Conversion) -> list[str]:
    lines = [
        "## Contact points",
        "",
        "| Contact point | Type | Verdict | Result |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {md_cell(p.cp.name)} | {md_cell(p.cp.type)} | {p.verdict.verdict.value} | "
        f"{md_cell(_outcome(p))} |"
        for p in c.points[:MAX_ROWS]
    ]
    lines.append("")
    for p in c.points[:MAX_ROWS]:
        extra = list(p.manual.steps) if p.manual else list(p.steps)
        if p.destination is None and p.manual is None:
            extra = list(p.verdict.manual_steps)
        if not (extra or p.unverified):
            continue
        lines.append(f"### {md_cell(p.cp.name)} ({md_cell(p.cp.type)})")
        lines += [f"- {md_cell(s, 400)}" for s in dict.fromkeys(extra)]
        lines += [f"- unverified: {md_cell(u, 400)}" for u in dict.fromkeys(p.unverified)]
        lines.append("")
    return lines


def _routes(c: Conversion) -> list[str]:
    lines = [
        "## Routes",
        "",
        "| Route | Receiver | Rules | Workflows | Destinations | Manual | Muting rules |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {o.number} | {md_cell(o.receiver)} | {o.rules} | {o.workflows} | {o.destinations} | "
        f"{o.manual} | {o.muting_rules} |"
        for o in c.outcomes[:MAX_ROWS]
    ]
    lines.append("")
    for o in c.outcomes[:MAX_ROWS]:
        lines += [f"- route {o.number} ({md_cell(o.receiver)}): {reason}" for reason in o.reasons]
    return [*lines, ""]


def _undecided(c: Conversion) -> list[str]:
    if not (c.undecidable or c.unrouted):
        return []
    lines = ["## Rules that were not routed", ""]
    lines += _cut([f"- {md_cell(u.rule)}: {u.reason}" for u in c.undecidable], "rules")
    lines += _cut([f"- {md_cell(r)}: {R.R_POLICY_NO_RECEIVER}" for r in c.unrouted], "rules")
    return [*lines, ""]


def _mutes(c: Conversion) -> list[str]:
    if not c.mute_plans:
        return []
    lines = ["## Mute timings", "", "| Mute timing | Schedules | Not converted |", "|---|---|---|"]
    lines += [
        f"| {md_cell(name)} | {len(plan.schedules)} | {md_cell('; '.join(plan.manual))} |"
        for name, plan in list(c.mute_plans.items())[:MAX_ROWS]
    ]
    lines.append("")
    if c.expired_schedules:
        lines.append(f"- {c.expired_schedules} schedules ended before today and made no rule.")
    if c.unscoped_schedules:
        lines.append(
            f"- {c.unscoped_schedules} schedules made no rule because the route's filter cannot be "
            "a muting rule scope."
        )
    return [*lines, ""]


def _steps(c: Conversion) -> list[str]:
    if not c.reasons:
        return []
    lines = ["## What each reason means and what to do", ""]
    for remedy in remedies_for(c.reasons):
        lines.append(f"### {remedy.title}")
        lines += [f"- {md_cell(s, 600)}" for s in remedy.steps]
        lines.append("")
    return lines


def render_report(c: Conversion) -> str:
    lines = [
        "# Grafana notification conversion",
        "",
        "Nothing was created. Workflows and muting rules are planned disabled. Every secret is a "
        "name, never a value.",
        "",
        *_summary(c),
    ]
    if c.violations:
        lines += [
            "## Problems that stop apply",
            "",
            *(f"- {md_cell(v, 300)}" for v in c.violations),
            "",
        ]
    if c.export_problems:
        lines += [
            "## Problems reading the export (the plans below may be incomplete)",
            "",
            *_cut([f"- {md_cell(p, 300)}" for p in c.export_problems], "problems"),
            "",
        ]
    lines += [
        *_secrets(c),
        *_points(c),
        *_routes(c),
        *_undecided(c),
        *_mutes(c),
        *_steps(c),
    ]
    if c.unused_map_entries:
        lines.append(
            f"{c.unused_map_entries} entries of the destinations map matched no contact point."
        )
    return "\n".join(lines) + "\n"
