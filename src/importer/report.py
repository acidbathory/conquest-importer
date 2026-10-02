"""Gap report: what converted, what needs review, what did not convert, and why."""

from collections import Counter

from importer.models.ir import Confidence, IRMonitor

GLOBAL_NOTES = [
    "Datadog evaluates one value over the whole window (for example avg over 5 minutes). "
    "The New Relic conditions evaluate 1-minute values and require a breach for the full "
    "duration. The behaviour is close but not identical; review before enabling.",
    "All conditions are created DISABLED. Enable them after review.",
    "Notification channels, handles and secrets are never migrated. Attach a workflow "
    "to the policy manually.",
    "Metric and tag mappings are unverified against a live New Relic account.",
]


def md_cell(text: str, limit: int = 120) -> str:
    """Make untrusted text safe to drop into a markdown table cell."""
    text = " ".join(text.split())[:limit]
    for ch in ("|", "`", "<", ">", "[", "]"):
        text = text.replace(ch, " ")
    return text


def build_report(monitors: list[IRMonitor], emitted_ids: set[int]) -> str:
    counts = Counter(m.confidence for m in monitors)
    total = len(monitors)
    converted = len(emitted_ids)
    lines = [
        "# Conversion report",
        "",
        f"Monitors read: **{total}**. Emitted as conditions: **{converted}**.",
        "",
        "| Confidence | Count |",
        "|---|---|",
    ]
    lines += [f"| {c.value} | {counts[c]} |" for c in Confidence]
    lines += ["", "## Notes that apply to every converted monitor", ""]
    lines += [f"- {n}" for n in GLOBAL_NOTES]
    lines += [
        "",
        "## Monitors",
        "",
        "| ID | Name | Type | Confidence | Reasons |",
        "|---|---|---|---|---|",
    ]
    for m in sorted(monitors, key=lambda x: x.source_id):
        reasons = list(m.reasons)
        lines.append(
            f"| {m.source_id} | {md_cell(m.name)} | {md_cell(m.source_type)} | "
            f"{m.confidence.value} | {md_cell('; '.join(reasons), 300)} |"
        )
    unmapped = [m for m in monitors if m.unmapped]
    if unmapped:
        lines += ["", "## Settings that were not migrated", ""]
        for m in sorted(unmapped, key=lambda x: x.source_id):
            keys = ", ".join(md_cell(k, 40) for k in sorted(m.unmapped))
            lines.append(f"- {m.source_id}: {keys}")
    return "\n".join(lines) + "\n"
