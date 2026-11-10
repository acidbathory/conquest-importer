"""triage-report.md and triage-report.json. Untrusted text goes through `cell` only."""

import json
from collections import Counter

from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable
from importer.sources.grafana.triage.pipeline import ORDER, Result, Row

CAVEAT_NO_USAGE = (
    "No usage file was given. Without view counts this ranking rests on weak hints (stars, "
    "titles, duplicates, conversion). Only starred dashboards reach 'migrate now'. Export "
    "usage and re-run before deciding what to skip for good."
)
CAVEAT_USAGE = (
    "Usage figures come from the file you supplied; they were not checked against Grafana."
)
CAVEAT_COVERAGE = (
    "Widget percentages come from the offline converter: they say how many widgets get a "
    "query, not that the data exists in New Relic or looks the same."
)


def cell(text: str, limit: int = 100) -> str:
    return md_cell(printable(text, limit), limit)


def _hints(r: Row) -> str:
    s = r.signals
    parts = [
        f"folder {s.folder}" if s.folder else "",
        f"tags {', '.join(s.tags[:4])}" if s.tags else "",
        "starred" if s.starred else "",
        "provisioned" if s.provisioned else "",
        "community" if s.community else "",
        f"v{s.version}" if s.version else "",
        f"updated {s.updated}" if s.updated else "",
        ", ".join(f"{n} {t}" for t, n in s.panel_types.most_common(3)),
        f"ds {', '.join(s.datasources[:3])}" if s.datasources else "",
    ]
    return "; ".join(p for p in parts if p)


def _row(r: Row) -> str:
    s, v = r.signals, r.verdict
    views = str(r.usage.views_30d) if r.usage else ""
    cols = [
        cell(s.title),
        cell(s.uid, 60),
        v.bucket.value,
        str(v.score),
        f"{r.converted}/{r.widgets}",
        views,
        cell(_hints(r), 200),
        cell("; ".join(v.reasons), 200),
        cell(r.duplicate_of, 60),
    ]
    return "| " + " | ".join(cols) + " |"


def build_markdown(result: Result) -> str:
    counts = Counter(r.verdict.bucket for r in result.rows)
    lines = [
        *header_lines("Grafana dashboard triage", "grafana"),
        "",
        f"**{result.headline}**",
        "",
        CAVEAT_USAGE if result.with_usage else CAVEAT_NO_USAGE,
        CAVEAT_COVERAGE,
    ]
    if result.unmatched_usage:
        lines += ["", f"{result.unmatched_usage} usage rows matched no dashboard uid."]
    lines += ["", "| Bucket | Dashboards |", "|---|---|"]
    lines += [f"| {b.value} | {counts[b]} |" for b in ORDER]
    lines += [
        "",
        "| Dashboard | Uid | Bucket | Score | Widgets converted | Views 30d | Signals | Why "
        "| Duplicate of |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_row(r) for r in result.rows]
    return "\n".join(lines) + "\n"


def build_json(result: Result) -> str:
    def entry(r: Row) -> dict[str, object]:
        s = r.signals
        return {
            "uid": s.uid,
            "title": s.title,
            "bucket": r.verdict.bucket.value,
            "score": r.verdict.score,
            "reasons": r.verdict.reasons,
            "widgets": r.widgets,
            "widgets_converted": r.converted,
            "views_30d": r.usage.views_30d if r.usage else None,
            "last_viewed": r.usage.last_viewed.isoformat()
            if r.usage and r.usage.last_viewed
            else None,
            "duplicate_of": r.duplicate_of or None,
            "folder": s.folder,
            "tags": list(s.tags),
            "starred": s.starred,
            "version": s.version,
            "updated": s.updated,
            "provisioned": s.provisioned,
            "community": s.community,
            "panel_count": s.panel_count,
            "panel_types": dict(s.panel_types),
            "datasources": list(s.datasources),
            "title_flags": list(s.title_flags),
            "empty": s.empty,
        }

    data = {
        "headline": result.headline,
        "usage_given": result.with_usage,
        "totals": {
            "dashboards": len(result.rows),
            "widgets": result.all_widgets,
            "widgets_converted": result.all_converted,
            "now_widgets": result.now_widgets,
            "now_widgets_converted": result.now_converted,
        },
        "dashboards": [entry(r) for r in result.rows],
    }
    return json.dumps(data, indent=2) + "\n"
