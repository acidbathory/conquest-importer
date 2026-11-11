"""Dashboard gap report: per dashboard, which widgets converted and why the others did not."""

from collections import Counter
from collections.abc import Callable

from importer.core.dashboard_ir import IRDashboard, IRWidget, WidgetKind
from importer.core.ir import Confidence
from importer.core.report import data_path_section, md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable

NOTES = [
    "Dashboards are created PRIVATE by the generated Terraform/NerdGraph; review before sharing.",
    "Widgets that could not be converted are kept as a markdown placeholder reading "
    "'Not migrated', so the gap is visible on the dashboard itself.",
    "Queries and NerdGraph/Terraform output are unverified against a live New Relic account.",
    "Layout is approximated from the source grid; adjust by hand.",
]


def _cell(text: str, limit: int = 120) -> str:
    return md_cell(printable(text, 10_000), limit)


def build_dashboard_report(
    dashboards: list[IRDashboard],
    emitted_keys: set[str],
    source: str = "",
    parity: Callable[[IRDashboard, IRWidget], str] | None = None,
) -> str:
    """The gap report. With `parity` (a text per widget, such as "verified on 2026-11-10") the
    widget tables gain a Parity column; without it the report is exactly as before."""
    widgets = [w for d in dashboards for w in d.widgets]
    converted = [w for w in widgets if w.kind != WidgetKind.UNSUPPORTED]
    counts = Counter(w.confidence for w in widgets)
    lines = [
        *header_lines("Dashboard conversion report", source),
        "",
        f"Dashboards read: **{len(dashboards)}**. Emitted: **{len(emitted_keys)}**. "
        f"Widgets: **{len(widgets)}**, converted: **{len(converted)}**.",
        "",
        "| Widget confidence | Count |",
        "|---|---|",
    ]
    lines += [f"| {c.value} | {counts[c]} |" for c in Confidence]
    lines += ["", "## Notes", ""] + [f"- {n}" for n in NOTES]
    lines += data_path_section([w.data_path for w in converted])
    for d in sorted(dashboards, key=lambda x: x.source_key):
        done = sum(w.kind != WidgetKind.UNSUPPORTED for w in d.widgets)
        lines += [
            "",
            f"## {_cell(d.name, 80)}",
            "",
            f"{d.confidence.value}: {done} of {len(d.widgets)} widgets converted. "
            + _cell("; ".join(d.reasons), 300),
            "",
            "| Widget | Type | Confidence | Reasons |" + (" Parity |" if parity else ""),
            "|---|---|---|---|" + ("---|" if parity else ""),
        ]
        lines += [
            f"| {_cell(w.title, 60)} | {_cell(w.source_type, 30)} | {w.confidence.value} | "
            f"{_cell('; '.join(w.reasons), 300)} |"
            + (f" {_cell(parity(d, w), 60)} |" if parity else "")
            for w in d.widgets
        ]
    return "\n".join(lines) + "\n"
