"""Gap report: what converted, what needs review, what did not convert, and why."""

from collections import Counter

from importer.core.ir import Confidence, IRMonitor
from importer.core.report_brand import header_lines

COMMON_NOTES = [
    "All conditions are created DISABLED. Enable them after review.",
    "Notification channels, handles and secrets are never migrated. Attach a workflow "
    "to the policy manually.",
    "Metric and tag mappings are unverified against a live New Relic account.",
]
SOURCE_NOTES = {
    "cloudwatch": [
        "CloudWatch periods, evaluation periods and datapoints map directly to New Relic "
        "aggregation windows and breach durations. 'M of N' alarms (0 < M < N) are approximated "
        "as M consecutive breaches.",
        "Metric names assume New Relic AWS Metric Streams (aws.<service>.<Metric>). Customers "
        "using the older API-polling integration will have different names.",
    ],
}


def md_cell(text: str, limit: int = 120) -> str:
    """Make untrusted text safe to drop into a markdown table cell."""
    text = " ".join(text.split())[:limit]
    for ch in ("|", "`", "<", ">", "[", "]"):
        text = text.replace(ch, " ")
    return text


def data_path_section(paths: list[str | None]) -> list[str]:
    """Counts of converted items by the data path they need; empty when none states one.

    A path of `native` needs nothing beyond the platform link, so an estate with only native
    items gets no section and its report stays as it was.
    """
    counts = Counter(p for p in paths if p)
    if not counts or set(counts) == {"native"}:
        return []
    lines = ["", "## Data path", "", "| Data path | Converted items |", "|---|---:|"]
    lines += [f"| {md_cell(p, 60)} | {n} |" for p, n in sorted(counts.items())]
    lines += [
        "",
        "Items other than `native` return data only after the data is sent; see "
        "`plan-gcp-datapath`.",
    ]
    return lines


def build_report(monitors: list[IRMonitor], emitted_ids: set[int], source: str) -> str:
    counts = Counter(m.confidence for m in monitors)
    total = len(monitors)
    converted = len(emitted_ids)
    lines = [
        *header_lines("Conversion report", source),
        "",
        f"Monitors read: **{total}**. Emitted as conditions: **{converted}**.",
        "",
        "| Confidence | Count |",
        "|---|---|",
    ]
    lines += [f"| {c.value} | {counts[c]} |" for c in Confidence]
    lines += ["", "## Notes that apply to every converted monitor", ""]
    lines += [f"- {n}" for n in [*SOURCE_NOTES.get(source, []), *COMMON_NOTES]]
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
    lines += data_path_section([m.data_path for m in monitors])
    unmapped = [m for m in monitors if m.unmapped]
    if unmapped:
        lines += ["", "## Settings that were not migrated", ""]
        for m in sorted(unmapped, key=lambda x: x.source_id):
            keys = ", ".join(md_cell(k, 40) for k in sorted(m.unmapped))
            lines.append(f"- {m.source_id}: {keys}")
    return "\n".join(lines) + "\n"
