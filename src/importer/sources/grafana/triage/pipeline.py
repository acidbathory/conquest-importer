"""Run the triage: signals, the converter's own results, optional usage, then score and bucket.

Widget counts come from converting each dashboard with the existing offline converter, never
from an estimate. A widget counts as converted when it is not a "Not migrated" placeholder.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.sources.grafana.dashboard_convert.translate import convert_all, load_items
from importer.sources.grafana.triage.score import Bucket, Verdict, decide
from importer.sources.grafana.triage.signals import Signals, duplicate_groups, read_signals
from importer.sources.grafana.triage.usage import Usage

ORDER = [Bucket.NOW, Bucket.QUICKSTART, Bucket.LATER, Bucket.SKIP]


@dataclass
class Row:
    signals: Signals
    verdict: Verdict
    widgets: int
    converted: int
    usage: Usage | None
    duplicate_of: str = ""


@dataclass
class Result:
    rows: list[Row]
    headline: str
    all_widgets: int
    all_converted: int
    now_widgets: int
    now_converted: int
    unmatched_usage: int
    with_usage: bool


def _counts(ir: IRDashboard | None) -> tuple[int, int]:
    widgets = [w for w in ir.widgets if w.source_type != "row"] if ir else []
    return len(widgets), sum(w.kind != WidgetKind.UNSUPPORTED for w in widgets)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _headline(rows: list[Row], r: Result) -> str:
    now = [x for x in rows if x.verdict.bucket is Bucket.NOW]
    n = len(rows)
    count = {0: "none are", 1: "1 is"}.get(len(now), f"{len(now)} are")
    return (
        f"Of {n} dashboards, {count} worth migrating now; converting those gives "
        f"{_pct(r.now_converted, r.now_widgets)} of widgets converted "
        f"({r.now_converted} of {r.now_widgets}) vs {_pct(r.all_converted, r.all_widgets)} "
        f"for all ({r.all_converted} of {r.all_widgets})."
    )


def _keepers(sigs: list[Signals], usage: dict[str, Usage]) -> dict[str, str]:
    """For each duplicate, the uid of the dashboard to keep: most viewed, starred, newest."""
    by = {s.uid: s for s in sigs}

    def rank(uid: str) -> tuple[int, bool, int, str]:
        u, s = usage.get(uid), by[uid]
        return (-(u.views_30d if u else 0), not s.starred, -(s.version or 0), uid)

    dup_of: dict[str, str] = {}
    for group in duplicate_groups(sigs):
        keep = min(group, key=rank)
        dup_of.update({uid: keep for uid in group if uid != keep})
    return dup_of


def run_triage(path: Path, usage: dict[str, Usage], *, today: date) -> Result:
    sigs = read_signals(path)
    items = load_items(path)
    irs, _ = convert_all(items)
    ir_by = {d.source_key: d for d in irs}
    dup_of = _keepers(sigs, usage)
    rows: list[Row] = []
    for s in sigs:
        widgets, done = _counts(ir_by.get(s.uid))
        u = usage.get(s.uid)
        v = decide(
            s,
            u,
            dup_of=dup_of.get(s.uid, ""),
            convertible=done / widgets if widgets else 0.0,
            today=today,
        )
        rows.append(Row(s, v, widgets, done, u, dup_of.get(s.uid, "")))
    rows.sort(key=lambda r: (ORDER.index(r.verdict.bucket), -r.verdict.score, r.signals.uid))
    now = [r for r in rows if r.verdict.bucket is Bucket.NOW]
    result = Result(
        rows=rows,
        headline="",
        all_widgets=sum(r.widgets for r in rows),
        all_converted=sum(r.converted for r in rows),
        now_widgets=sum(r.widgets for r in now),
        now_converted=sum(r.converted for r in now),
        unmatched_usage=len(set(usage) - {s.uid for s in sigs}),
        with_usage=bool(usage),
    )
    result.headline = _headline(rows, result)
    return result
