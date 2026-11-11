"""One Datadog widget definition to one IR widget: a chart, a markdown widget or a placeholder.

Converted charts are `timeseries`, `query_value`, `toplist` and `query_table` over a single plain
metric query; notes and free text become markdown. Everything else is a placeholder with the
reason it is not converted, so a gap is visible on the dashboard. A converted chart is always
approximate: Datadog and New Relic aggregate differently (see the notes on each widget).
"""

from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import IRWidget, Layout, Visualization, WidgetKind
from importer.core.ir import Confidence
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.metric_query import Converted, convert_series
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.dashboards.variables import TemplateVar, UsedVariable
from importer.sources.datadog.names import NameMap

MAX_TITLE = 200
MAX_TEXT = 6000
QUERY_VALUE_AGGREGATORS = ("avg", "sum", "min", "max")
CHARTS = {
    "timeseries": Visualization.LINE,
    "query_value": Visualization.BILLBOARD,
    "toplist": Visualization.BAR,
    "query_table": Visualization.TABLE,
}
MARKDOWN_TEXT_KEY = {"note": "content", "free_text": "text"}
# a request key naming something that is not a metric query -> what to call it
OTHER_SOURCES = {
    "log_query": "logs",
    "apm_query": "APM spans",
    "rum_query": "RUM",
    "process_query": "processes",
    "security_query": "security signals",
    "event_query": "events",
    "network_query": "network data",
    "audit_query": "audit logs",
    "profile_metrics_query": "profiles",
}
# keys of a formula that only change how the value is shown; any other key changes the value
FORMULA_KEYS = {"formula", "alias", "number_format", "cell_display_mode", "conditional_formats"}
SYNTAX_MARKS = ("{{", "<", "$")


@dataclass
class Context:
    """What one dashboard gives its widgets: the name map, its variables and those in use."""

    names: NameMap
    variables: dict[str, TemplateVar]
    used: dict[str, UsedVariable] = field(default_factory=dict)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _new_style(request: dict[str, Any]) -> str:
    """The query text of a request written with `queries` and `formulas`."""
    queries = _dicts(request.get("queries"))
    formulas = _dicts(request.get("formulas"))
    for q in queries:
        source = q.get("data_source")
        if source != "metrics":
            raise Fail(DR.W_DATA_SOURCE.format(source=shown(source, 40)))
    if len(queries) != 1:
        raise Fail(DR.W_FORMULA if formulas else DR.W_SEVERAL)
    only = queries[0]
    if any(set(f) - FORMULA_KEYS or f.get("formula") != only.get("name") for f in formulas):
        raise Fail(DR.W_FORMULA)
    if len(formulas) > 1 or not isinstance(only.get("query"), str):
        raise Fail(DR.W_FORMULA if len(formulas) > 1 else DR.W_NO_QUERY)
    return str(only["query"])


def _request(definition: dict[str, Any]) -> tuple[dict[str, Any], str]:
    requests = _dicts(definition.get("requests"))
    if not requests:
        raise Fail(DR.W_NO_QUERY)
    if len(requests) > 1:
        raise Fail(DR.W_SEVERAL)
    request = requests[0]
    if isinstance(request.get("q"), str):
        return request, request["q"]
    if "queries" in request:
        return request, _new_style(request)
    for key, label in OTHER_SOURCES.items():
        if key in request:
            raise Fail(DR.W_DATA_SOURCE.format(source=label))
    raise Fail(DR.W_NO_QUERY)


def _aggregator(kind: str, request: dict[str, Any]) -> str:
    """The function over the whole window for a single value; empty = the series' own."""
    if kind != "query_value":
        return ""
    name = request.get("aggregator")
    chosen = name if isinstance(name, str) else "avg"
    if chosen not in QUERY_VALUE_AGGREGATORS:
        raise Fail(DR.W_AGGREGATOR.format(name=shown(chosen, 30)))
    return chosen


def _visualization(kind: str, request: dict[str, Any], notes: list[str]) -> Visualization:
    viz = CHARTS[kind]
    display = request.get("display_type")
    if kind == "timeseries" and display == "area":
        return Visualization.AREA
    if kind == "timeseries" and display == "bars":
        notes.append(DR.N_BARS)
        return Visualization.AREA
    return viz


def _display_notes(definition: dict[str, Any], request: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    time = definition.get("time")
    span = time.get("live_span") if isinstance(time, dict) else None
    if isinstance(span, str):
        notes.append(DR.N_LIVE_SPAN.format(span=shown(span, 20)))
    formulas = _dicts(request.get("formulas"))
    shown_differently = any(set(f) - {"formula", "alias"} for f in formulas)
    if request.get("conditional_formats") or definition.get("markers") or shown_differently:
        notes.append(DR.N_FORMATS)
    return notes


def title_of(definition: dict[str, Any], kind: str) -> str:
    title = definition.get("title")
    text = title if isinstance(title, str) else ""
    return printable(" ".join(text.split()), MAX_TITLE) or f"Datadog {shown(kind, 40)}"


def _widget(
    title: str,
    layout: Layout,
    source_type: str,
    confidence: Confidence,
    reasons: list[str],
    page: str | None,
    **extra: Any,
) -> IRWidget:
    return IRWidget(
        source_type=printable(source_type, 30),
        title=title,
        layout=layout,
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        page=page,
        **extra,
    )


def placeholder(
    title: str, layout: Layout, kind: str, reasons: list[str], page: str | None
) -> IRWidget:
    return _widget(
        title,
        layout,
        kind,
        Confidence.MANUAL,
        sorted(set(reasons)),
        page,
        kind=WidgetKind.UNSUPPORTED,
    )


def clean_text(text: str) -> str:
    """Markdown with line breaks kept and every other control character removed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or c.isprintable())[:MAX_TEXT]


def heading(title: str, layout: Layout, page: str | None) -> IRWidget:
    """A section heading for a nested group; it is not an item of the export."""
    text = "## " + (md_cell(title, 120) or "Group")
    return _widget(
        md_cell(title, 80) or "Group",
        layout,
        "group",
        Confidence.EXACT,
        [],
        page,
        kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=text,
    )


def _markdown(definition: dict[str, Any], layout: Layout, kind: str, page: str | None) -> IRWidget:
    raw = definition.get(MARKDOWN_TEXT_KEY[kind])
    text = clean_text(raw) if isinstance(raw, str) else ""
    if not text.strip():
        return placeholder(title_of(definition, kind), layout, kind, [DR.W_NO_QUERY], page)
    notes = [DR.N_NOTE_SYNTAX] if any(m in text for m in SYNTAX_MARKS) else []
    return _widget(
        title_of(definition, kind),
        layout,
        kind,
        Confidence.APPROXIMATE if notes else Confidence.EXACT,
        notes,
        page,
        kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=text,
    )


def _chart(
    definition: dict[str, Any], layout: Layout, kind: str, page: str | None, ctx: Context
) -> IRWidget:
    request, text = _request(definition)
    notes = _display_notes(definition, request)
    viz = _visualization(kind, request, notes)
    timeseries = viz in (Visualization.LINE, Visualization.AREA)
    done: Converted = convert_series(
        text,
        _aggregator(kind, request),
        timeseries,
        kind != "query_value",
        ctx.names,
        ctx.variables,
        ctx.used,
    )
    series = done.series
    grouped = bool((series and series.signal.group_by) or (done.nrql and " FACET " in done.nrql))
    if grouped and kind in ("timeseries", "query_table") and not done.limited:
        notes.append(DR.N_FACET_LIMIT)
    fields: dict[str, Any] = (
        {"series": [series]} if series else {"raw_series": [{"nrql": done.nrql}]}
    )
    built = _widget(
        title_of(definition, kind),
        layout,
        kind,
        Confidence.APPROXIMATE,
        [*done.notes, *notes],
        page,
        kind=WidgetKind.CHART,
        visualization=viz,
        data_path=done.data_path,
        **fields,
    )
    ctx.used.update(done.variables)  # only once the widget exists, so a refused one adds none
    return built


def translate_widget(
    definition: dict[str, Any], layout: Layout, page: str | None, ctx: Context
) -> IRWidget:
    """Never raises: a widget that cannot be converted comes back as a placeholder."""
    raw_kind = definition.get("type")
    kind = raw_kind if isinstance(raw_kind, str) else "unknown"
    title = title_of(definition, kind)
    try:
        if kind in MARKDOWN_TEXT_KEY:
            return _markdown(definition, layout, kind, page)
        if kind not in CHARTS:
            raise Fail(f"{DR.W_TYPE}: {shown(kind, 40)}")
        return _chart(definition, layout, kind, page, ctx)
    except Fail as fail:
        return placeholder(title, layout, kind, [fail.reason], page)
    except ValueError as exc:  # the IR refused what was built; never let it stop the dashboard
        return placeholder(title, layout, kind, [DR.W_REJECTED.format(why=shown(exc, 100))], page)
