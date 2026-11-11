"""The markdown report of an Azure notification conversion.

Untrusted text (action group, receiver and rule names) goes through `md_cell`. Email addresses,
phone numbers, URLs and secret values never appear: the report shows counts, fixed sentences and
the logical names of the secrets a person has to supply. Receivers that name a person (email, SMS,
voice, app push) are shown as "<kind> N", never by the name stored in Azure.
"""

from collections.abc import Sequence

from importer.core.emit.notifications import secret_variable
from importer.core.remediation import remedies_for
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.sources.azure.notifications.convert import Conversion
from importer.sources.azure.notifications.link import ACTION_GROUP_TAG
from importer.sources.azure.notifications.payload import MAPPING
from importer.sources.azure.notifications.processing import RuleOutcome
from importer.sources.azure.notifications.receivers import GroupResult, Outcome

MAX_ROWS = 200
KIND_TITLE = {
    "emailReceivers": "email",
    "webhookReceivers": "webhook",
    "logicAppReceivers": "logic app",
    "azureFunctionReceivers": "Azure function",
    "automationRunbookReceivers": "automation runbook",
    "itsmReceivers": "ITSM",
    "azureAppPushReceivers": "app push",
    "smsReceivers": "SMS",
    "voiceReceivers": "voice",
    "armRoleReceivers": "ARM role",
    "eventHubReceivers": "Event Hub",
}
HOW_TO_SUPPLY = [
    "Terraform (`notifications.tf`): each secret is a sensitive variable named `nr_secret_` plus "
    "the secret name in lower case. Set it with an environment variable "
    "(`export TF_VAR_nr_secret_<name>=...`) or a `*.tfvars` file that stays out of git. "
    "`secrets.tfvars.example` lists every variable with a placeholder.",
    "`importer apply-notifications` cannot create a destination with a secure URL; use the "
    "Terraform file for these plans. Plans without a secret and without a manual step can be "
    "applied with it.",
]


def _cut(items: Sequence[str], what: str) -> list[str]:
    if len(items) <= MAX_ROWS:
        return list(items)
    return [*items[:MAX_ROWS], f"- and {len(items) - MAX_ROWS} more {what}"]


def _receiver_table(c: Conversion) -> list[str]:
    lines = [
        "## Receivers by type",
        "",
        "A receiver counts as converted only when a destination is in the plan. A receiver with no "
        "usable address or URL (a masked or placeholder value) is manual.",
        "",
        "| Receiver type | Converted | of which approximate | Manual | No New Relic destination "
        "| Skipped (disabled) |",
        "|---|---|---|---|---|---|",
    ]
    totals = [0] * 5
    for kind, count in c.receiver_counts().items():
        row = [
            count["converted"],
            count["approximate"],
            count["manual"],
            count["none"],
            count["skipped"],
        ]
        totals = [a + b for a, b in zip(totals, row, strict=True)]
        lines.append(f"| {KIND_TITLE[kind]} | " + " | ".join(str(n) for n in row) + " |")
    lines.append("| **all** | " + " | ".join(f"**{n}**" for n in totals) + " |")
    return [*lines, ""]


def _rule_table(c: Conversion) -> list[str]:
    n = c.rule_counts()
    rows = [
        ("Alert processing rules read", len(c.rule_results)),
        ("Converted to muting rules", n[RuleOutcome.CONVERTED.value]),
        ("Muting rules planned (disabled)", len(c.muting_rules)),
        ("Manual", n[RuleOutcome.MANUAL.value]),
        (
            "Nothing to mute (no converted condition matches, or the schedule has ended)",
            n["nothing"],
        ),
    ]
    return [
        "| Alert processing rules | Count |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in rows),
        "",
    ]


def _summary(c: Conversion) -> list[str]:
    rows = [
        ("Action groups read", len(c.groups)),
        ("Action groups used by a converted condition (one workflow each)", len(c.plans)),
        ("Workflows planned (disabled)", sum(p.workflow is not None for p in c.plans)),
        ("Action groups no converted condition uses", len(c.unused)),
        ("Action groups named by a condition but not in the export", len(c.not_exported)),
        ("Converted conditions with no action group", len(c.unrouted)),
        ("Secrets a person must supply", len(c.secrets)),
    ]
    return ["| What | Count |", "|---|---|", *(f"| {k} | {v} |" for k, v in rows), ""]


def _secrets(c: Conversion) -> list[str]:
    if not c.secrets:
        return []
    lines = ["## Secrets to supply", "", *HOW_TO_SUPPLY, ""]
    lines += ["| Secret | Terraform variable | Receiver type |", "|---|---|---|"]
    lines += [
        f"| {md_cell(s)} | {md_cell(secret_variable(s))} | {KIND_TITLE[k]} |"
        for s, k in c.secrets[:MAX_ROWS]
    ]
    return [*lines, ""]


def _group_lines(g: GroupResult) -> list[str]:
    lines = [f"### {md_cell(g.name)}", ""]
    if not g.enabled:
        lines += ["Disabled in Azure.", ""]
    lines += ["| Receiver | Result | Logical name |", "|---|---|---|"]
    for r in g.results:
        if r.outcome is Outcome.CONVERTED:
            result = "converted" + (" (approximate)" if r.approximate else "")
        elif r.outcome is Outcome.SKIPPED:
            result = "skipped (disabled in Azure)"
        else:
            nr = r.manual.nr_type if r.manual else ""
            result = f"manual ({nr})" if r.outcome is Outcome.MANUAL and nr else r.outcome.value
            if r.outcome is Outcome.NONE:
                result = "no New Relic destination: manual"
        lines.append(
            f"| {KIND_TITLE[r.kind]}: {md_cell(r.label)} | {md_cell(result)} | "
            f"{md_cell(r.logical)} |"
        )
    seen: set[str] = set()
    for r in g.results:
        for step in r.steps:
            if step not in seen:
                seen.add(step)
                lines.append(f"- {md_cell(step, 600)}")
    if g.unknown_kinds:
        lines.append(f"- {g.unknown_kinds} receiver list(s) of a type this importer does not read.")
    return [*lines, ""]


def _groups(c: Conversion) -> list[str]:
    lines = ["## Action groups", ""]
    for g in c.groups[:MAX_ROWS]:
        lines += _group_lines(g)
    if len(c.groups) > MAX_ROWS:
        lines.append(f"- and {len(c.groups) - MAX_ROWS} more action groups")
    return lines


def _routing(c: Conversion) -> list[str]:
    how = (
        f"issues carrying the tag `{ACTION_GROUP_TAG}`"
        if c.route_by == "tag"
        else "the converted condition names"
    )
    lines = [
        "## Workflows",
        "",
        f"One workflow per action group that a converted condition uses. Its issue filter matches "
        f"{how}. Contract with the alert conversion: `docs/dev/azure-notification-link.md`.",
        "",
        "| Action group | Conditions routed | Destinations | Needs a person |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {md_cell(p.topic)} | {len(c.routes.get(g.key, []))} | {len(p.typed_destinations)} | "
        f"{len(p.manual_destinations)} |"
        for p, g in zip(c.plans, [g for g in c.groups if g.key in c.routes], strict=True)
    ][:MAX_ROWS]
    lines.append("")
    if c.not_exported:
        lines += [
            "Action groups named by a converted condition but missing from the export:",
            *_cut([f"- {md_cell(k)}" for k in c.not_exported], "action groups"),
            "",
        ]
    if c.unrouted:
        lines += [
            "Converted conditions that name no action group (nothing will notify for them):",
            *_cut([f"- {md_cell(n)}" for n in c.unrouted], "conditions"),
            "",
        ]
    return lines


def _rules(c: Conversion) -> list[str]:
    if not c.rule_results:
        return []
    lines = [
        "## Alert processing rules",
        "",
        *_rule_table(c),
        "| Rule | Azure state | Result | Muting rules |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {md_cell(r.name)} | {'enabled' if r.azure_enabled else 'disabled'} | "
        f"{r.outcome.value} | {len(r.rules)} |"
        for r in c.rule_results[:MAX_ROWS]
    ]
    lines.append("")
    for r in c.rule_results[:MAX_ROWS]:
        if r.action_groups:
            lines.append(
                f"- {md_cell(r.name)} adds: " + ", ".join(md_cell(k) for k in r.action_groups)
            )
    return [*lines, ""]


def _payload() -> list[str]:
    lines = [
        "## Webhook body",
        "",
        "Azure sends the common alert schema; New Relic sends its own payload. The destinations "
        "carry a template laid out like the schema, filled from New Relic variables where there "
        "is an honest source:",
        "",
        "| Azure field | New Relic source |",
        "|---|---|",
    ]
    return [*lines, *(f"| {a} | {b} |" for a, b in MAPPING), ""]


def _steps(c: Conversion) -> list[str]:
    if not c.reasons:
        return []
    lines = ["## What each reason means and what to do", ""]
    for remedy in remedies_for(c.reasons):
        lines.append(f"### {remedy.title}")
        lines += [f"- {md_cell(s, 800)}" for s in remedy.steps]
        lines.append("")
    return lines


def render_report(c: Conversion) -> str:
    lines = [
        *header_lines("Azure notification conversion", "azure"),
        "",
        "Nothing was created. Workflows and muting rules are planned disabled. Every secret is a "
        "name, never a value; phone numbers and email addresses of people are not in this report.",
        "",
        *_summary(c),
        *_receiver_table(c),
    ]
    if c.violations:
        lines += [
            "## Problems that stop apply",
            "",
            *(f"- {md_cell(v, 300)}" for v in c.violations),
            "",
        ]
    if c.problems:
        lines += [
            "## Problems reading the export (the plans below may be incomplete)",
            "",
            *_cut([f"- {md_cell(p, 300)}" for p in c.problems], "problems"),
            "",
        ]
    lines += [*_secrets(c), *_routing(c), *_groups(c), *_rules(c), *_payload(), *_steps(c)]
    return "\n".join(lines) + "\n"
