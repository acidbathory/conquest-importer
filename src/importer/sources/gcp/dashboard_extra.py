"""Dashboard widgets that are not charts: headers, groups, logs panels and the placeholders.

Cloud Monitoring lays groups out as marker tiles: a `collapsibleGroup` or `singleViewGroup` tile
has no children of its own, the tiles below it (until the next marker) belong to it. They become
pages of the New Relic dashboard, so a large dashboard uses the existing multi-page model. A
`sectionHeader` becomes a markdown heading on the page it is on.
"""

from typing import Any

from importer.core.dashboard_ir import (
    MAX_PAGES,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Visualization,
    WidgetKind,
)
from importer.core.emit.nrql import identifier, quote_string
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.gcp.filter import FilterError, parse_filter

Placed = tuple[dict[str, Any], Layout]
PAGE_KINDS = ("collapsibleGroup", "singleViewGroup")
MAX_TITLE = 200
LOG_LINES = 200
SEVERITIES = [
    "DEFAULT",
    "DEBUG",
    "INFO",
    "NOTICE",
    "WARNING",
    "ERROR",
    "CRITICAL",
    "ALERT",
    "EMERGENCY",
]

NOT_SHOWN = {
    "errorReportingPanel": (
        "dashboard widget type has no New Relic equivalent (error reporting panel): "
        "Google Error Reporting data is not collected by New Relic, so no widget can show it"
    ),
    "alertChart": (
        "dashboard widget type has no New Relic equivalent (alert chart): "
        "New Relic has no widget that charts a Google alert policy; chart the metric the policy "
        "watches instead"
    ),
}
ISSUE_NOTE = (
    "an incident list is shown as a table of New Relic issues (NrAiIssue), which exist once the "
    "converted alert conditions are created and fire: it lists the issues of the whole account, "
    "not the incidents of the Google alert policies named in the source widget"
)
LOG_NOTE = (
    "logs panel shown as a table over Log; the logs must be forwarded to New Relic, and the "
    "attribute names of forwarded Google Cloud logs are not confirmed"
)
LOG_FILTER_LOST = (
    "the logs panel filter uses OR, parentheses, a regular expression or a function that "
    "cannot be read, so the table shows every log line: add a WHERE clause by hand"
)


def result(
    title: str,
    layout: Layout,
    kind: str,
    confidence: Confidence,
    reasons: list[str],
    *,
    widget_kind: WidgetKind = WidgetKind.CHART,
    **extra: Any,
) -> IRWidget:
    return IRWidget(
        kind=widget_kind,
        source_type=printable(kind, 30),
        title=title,
        layout=layout,
        confidence=confidence,
        reasons=reasons,
        **extra,
    )


def failed(title: str, layout: Layout, kind: str, reasons: list[str]) -> IRWidget:
    return result(
        title,
        layout,
        kind,
        Confidence.MANUAL,
        sorted(set(reasons)),
        widget_kind=WidgetKind.UNSUPPORTED,
    )


def kind_of(widget: dict[str, Any]) -> str:
    return next((str(k) for k in widget if k not in ("title", "id")), "unknown")


def section_header(body: dict[str, Any], title: str, layout: Layout) -> IRWidget:
    subtitle = body.get("subtitle")
    text = f"## {title}"
    if isinstance(subtitle, str) and subtitle.strip():
        text += "\n\n" + printable(subtitle, 500)
    reasons = ["section header shown as a markdown heading"]
    if body.get("dividerBelow"):
        reasons.append("the divider line below the header is not migrated")
    return result(
        title,
        layout,
        "sectionHeader",
        Confidence.APPROXIMATE,
        reasons,
        widget_kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=text,
    )


def _log_where(filter_text: str) -> list[str] | None:
    """WHERE conditions for a simple logs filter, or None when it cannot be read."""
    try:
        terms = parse_filter(filter_text)
        where: list[str] = []
        for term in terms:
            name = identifier(term.field)
            not_ = "NOT " if term.negate else ""
            if term.field == "severity" and term.op in (">=", ">", "<=", "<"):
                if term.value.upper() not in SEVERITIES:
                    return None
                first = SEVERITIES.index(term.value.upper())
                levels = {
                    ">=": SEVERITIES[first:],
                    ">": SEVERITIES[first + 1 :],
                    "<=": SEVERITIES[: first + 1],
                    "<": SEVERITIES[:first],
                }[term.op]
                if not levels:
                    return None
                where.append(f"{name} {not_}IN ({', '.join(quote_string(v) for v in levels)})")
            elif term.op == "=":
                where.append(f"{name} {'!=' if term.negate else '='} {quote_string(term.value)}")
            elif term.op == "!=":
                where.append(f"{name} {'=' if term.negate else '!='} {quote_string(term.value)}")
            elif term.op in ("=~", "regex_full") and "${" not in term.value:
                # RLIKE matches the whole value; a Cloud Logging =~ matches anywhere in it
                pattern = term.value if term.op == "regex_full" else f".*({term.value}).*"
                where.append(f"{name} {not_}RLIKE {quote_string(pattern)}")
            elif term.op == ":":
                where.append(f"{name} {not_}LIKE {quote_string('%' + term.value + '%')}")
            else:
                return None
        return where
    except (FilterError, ValueError):
        return None


def logs_panel(body: dict[str, Any], title: str, layout: Layout) -> IRWidget:
    raw = body.get("filter")
    text = raw if isinstance(raw, str) else ""
    reasons = [LOG_NOTE]
    where = _log_where(text) if text.strip() else []
    if where is None:
        where = []
        reasons.append(LOG_FILTER_LOST)
    if body.get("resourceNames"):
        reasons.append("the projects and resources the panel reads are not migrated")
    query = "SELECT timestamp, message FROM Log"
    query += (" WHERE " + " AND ".join(where) if where else "") + f" LIMIT {LOG_LINES}"
    return result(
        title,
        layout,
        "logsPanel",
        Confidence.APPROXIMATE,
        reasons,
        visualization=Visualization.TABLE,
        raw_series=[RawNrqlSeries(nrql=query)],
    )


def incident_list(title: str, layout: Layout) -> IRWidget:
    """Google's incident list as a table over the issues New Relic opens from alert conditions."""
    query = (
        "SELECT latest(title) AS 'Issue', latest(priority) AS 'Priority', "
        "latest(timestamp) AS 'Last update' FROM NrAiIssue FACET issueId LIMIT 50"
    )
    return result(
        title,
        layout,
        "incidentList",
        Confidence.APPROXIMATE,
        [ISSUE_NOTE],
        visualization=Visualization.TABLE,
        raw_series=[RawNrqlSeries(nrql=query)],
    )


def _unique(title: str, taken: set[str]) -> str:
    name, n = title, 1
    while name in taken:
        n += 1
        name = printable(f"{title} ({n})", MAX_TITLE)
    taken.add(name)
    return name


def split_pages(
    placed: list[Placed], first_page: str, limit: int
) -> tuple[list[tuple[dict[str, Any], Layout, str | None]], list[str]]:
    """Tiles with the page each belongs to, and notes. `placed` is in reading order.

    A page marker starts a page; tiles before the first marker stay on the dashboard's own page
    (`None`). Rows restart at 1 on every page. A blank tile is dropped. Beyond the page limit a
    marker becomes a heading on the current page; a page holds PAGE_WIDGETS widgets at most.
    """
    taken = {first_page}
    page: str | None = None
    pages = 1
    out: list[tuple[dict[str, Any], Layout, str | None]] = []
    notes: list[str] = []
    used: dict[str | None, int] = {}
    dropped = 0
    for widget, layout in placed:
        kind = kind_of(widget)
        if kind == "blank":
            continue
        if kind in PAGE_KINDS:
            raw = widget.get("title")
            title = printable(raw if isinstance(raw, str) and raw.strip() else "", MAX_TITLE)
            if pages >= MAX_PAGES:
                out.append(({"title": title or kind, "sectionHeader": {}}, layout, page))
                notes.append(
                    f"more than {MAX_PAGES} groups: the rest are headings on the last page"
                )
                continue
            page = _unique(title or f"Group {pages}", taken)
            pages += 1
            continue
        if used.get(page, 0) >= limit:
            dropped += 1
            continue
        used[page] = used.get(page, 0) + 1
        out.append((widget, layout, page))
    if dropped:
        notes.append(f"{dropped} widgets over the limit of {limit} per page were not converted")
    if pages > 1:
        notes.append(
            f"{pages - 1} groups became pages; New Relic pages cannot be collapsed, and the "
            "tiles below a group header are taken to belong to it"
        )
    # rows restart on each page
    tops: dict[str | None, int] = {}
    for _w, lay, pg in out:
        tops[pg] = min(tops.get(pg, lay.row), lay.row)
    fixed = [(w, lay.model_copy(update={"row": lay.row - tops[pg] + 1}), pg) for w, lay, pg in out]
    return fixed, sorted(set(notes))
