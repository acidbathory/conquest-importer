"""One Datadog widget definition to one IR widget: a chart, a markdown widget or a placeholder.

Converted charts are `timeseries`, `query_value`, `toplist` and `query_table` over a single plain
metric query; notes and free text become markdown. Everything else is a placeholder with the
reason it is not converted, so a gap is visible on the dashboard. A converted chart is always
approximate: Datadog and New Relic aggregate differently (see the notes on each widget).
"""

import re
from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import IRWidget, Layout, Visualization, WidgetKind
from importer.core.ir import Confidence
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.datadog.common import Fail
from importer.sources.datadog.dashboards import reasons as DR
from importer.sources.datadog.dashboards.metric_query import (
    Converted,
    as_nrql,
    convert_formula,
    convert_series,
    split_queries,
)
from importer.sources.datadog.dashboards.reasons import shown
from importer.sources.datadog.dashboards.variables import TemplateVar, UsedVariable
from importer.sources.datadog.names import NameMap

MAX_TITLE = 200
MAX_LABEL = 100
MAX_TEXT = 6000
QUERY_VALUE_AGGREGATORS = ("avg", "sum", "min", "max")
CHARTS = {
    "timeseries": Visualization.LINE,
    "query_value": Visualization.BILLBOARD,
    "toplist": Visualization.BAR,
    "query_table": Visualization.TABLE,
    "treemap": Visualization.BAR,
    "sunburst": Visualization.PIE,
}
SHARE_NOTES = {"treemap": DR.N_TREEMAP, "sunburst": DR.N_SUNBURST}
# https only, and none of the characters that end a markdown link or start HTML
IMAGE_URL = re.compile(r"https://[A-Za-z0-9.\-]+(:\d+)?(/[A-Za-z0-9\-._~%/+=&?#@:,;!*]*)?")
MAX_URL = 500
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


@dataclass(frozen=True)
class Spec:
    """One series a widget asks for: a plain query, or a formula over named queries."""

    text: str = ""
    formula: str = ""
    queries: dict[str, str] = field(default_factory=dict)
    alias: str | None = None


MAX_SPECS = 12
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _new_style(request: dict[str, Any]) -> list[Spec]:
    """The series of a request written with `queries` and `formulas`."""
    queries = _dicts(request.get("queries"))
    formulas = _dicts(request.get("formulas"))
    sources = {str(q.get("name")): q.get("data_source") for q in queries}
    texts = {
        str(q.get("name")): q["query"]
        for q in queries
        if q.get("data_source") == "metrics" and isinstance(q.get("query"), str)
    }
    if not formulas:
        if len(queries) != 1:
            raise Fail(DR.W_SEVERAL)
        source = queries[0].get("data_source")
        if source != "metrics":
            raise Fail(DR.W_DATA_SOURCE.format(source=shown(source, 40)))
        return [Spec(text=texts[next(iter(texts))])] if texts else _no_query()
    out: list[Spec] = []
    for f in formulas:
        text = f.get("formula")
        if set(f) - FORMULA_KEYS:
            raise Fail(DR.W_FORMULA)
        if not isinstance(text, str):
            raise Fail(DR.W_NO_QUERY)
        for name in _NAME.findall(text):
            if name in sources and sources[name] != "metrics":
                raise Fail(DR.W_DATA_SOURCE.format(source=shown(sources[name], 40)))
        alias = f.get("alias")
        label = alias if isinstance(alias, str) else None
        if text in texts:  # a formula that only names its query
            out.append(Spec(text=texts[text], alias=label))
        else:
            out.append(Spec(formula=text, queries=texts, alias=label))
    return out


def _no_query() -> list[Spec]:
    raise Fail(DR.W_NO_QUERY)


def _specs(definition: dict[str, Any]) -> tuple[dict[str, Any], list[Spec]]:
    requests = _dicts(definition.get("requests"))
    if not requests:
        raise Fail(DR.W_NO_QUERY)
    specs: list[Spec] = []
    for request in requests:
        if isinstance(request.get("q"), str):
            specs += [Spec(text=t) for t in split_queries(request["q"])]
        elif "queries" in request:
            specs += _new_style(request)
        else:
            for key, label in OTHER_SOURCES.items():
                if key in request:
                    raise Fail(DR.W_DATA_SOURCE.format(source=label))
            raise Fail(DR.W_NO_QUERY)
    if not specs:
        raise Fail(DR.W_NO_QUERY)
    if len(specs) > MAX_SPECS:
        raise Fail(DR.W_SEVERAL)
    return requests[0], specs


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


def _image(definition: dict[str, Any], layout: Layout, page: str | None) -> IRWidget:
    url = definition.get("url")
    if not isinstance(url, str) or not url:
        raise Fail(DR.W_IMAGE.format(why="no address"))
    if len(url) > MAX_URL or IMAGE_URL.fullmatch(url) is None:
        raise Fail(DR.W_IMAGE.format(why="not a plain secure address"))
    title = title_of(definition, "image")
    return _widget(
        title,
        layout,
        "image",
        Confidence.APPROXIMATE,
        [DR.N_IMAGE],
        page,
        kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=f"![{md_cell(title, 80)}]({url})",
    )


def _label(spec: Spec, many: bool) -> str | None:
    """The legend name: the formula alias, used when it tells series apart or names a formula."""
    if spec.alias is None or not (many or spec.formula):
        return None
    return printable(" ".join(spec.alias.split()), MAX_LABEL) or None


def _convert_all(
    specs: list[Spec], time_aggr: str, timeseries: bool, grouped: bool, ctx: Context
) -> list[Converted]:
    done: list[Converted] = []
    seen = dict(ctx.used)  # variables used by earlier series of this widget count as committed
    for spec in specs:
        label = _label(spec, len(specs) > 1)
        if spec.formula:
            one = convert_formula(
                spec.formula, spec.queries, time_aggr, timeseries, grouped, ctx.names,
                ctx.variables, seen, label,
            )  # fmt: skip
        else:
            one = convert_series(
                spec.text, time_aggr, timeseries, grouped, ctx.names, ctx.variables, seen, label
            )
        seen.update(one.variables)
        done.append(one)
    return done


def _chart(
    definition: dict[str, Any], layout: Layout, kind: str, page: str | None, ctx: Context
) -> IRWidget:
    request, specs = _specs(definition)
    if len(specs) > 1 and kind != "timeseries":
        raise Fail(DR.W_SEVERAL)
    notes = _display_notes(definition, request)
    if kind in SHARE_NOTES:
        notes.append(SHARE_NOTES[kind])
    viz = _visualization(kind, request, notes)
    timeseries = viz in (Visualization.LINE, Visualization.AREA)
    results = _convert_all(
        specs, _aggregator(kind, request), timeseries, kind != "query_value", ctx
    )
    grouped = any(
        bool((r.series and r.series.signal.group_by) or (r.nrql and " FACET " in r.nrql))
        for r in results
    )
    if kind in SHARE_NOTES and not grouped:
        raise Fail(DR.W_SHARE_GROUP.format(kind=kind))
    if grouped and kind in ("timeseries", "query_table") and not all(r.limited for r in results):
        notes.append(DR.N_FACET_LIMIT)
    fields: dict[str, Any]
    if all(r.series for r in results):
        fields = {"series": [r.series for r in results]}
    else:
        fields = {"raw_series": [{"nrql": as_nrql(r, timeseries)} for r in results]}
    reasons = [n for r in results for n in r.notes]
    built = _widget(
        title_of(definition, kind),
        layout,
        kind,
        Confidence.APPROXIMATE,
        [*reasons, *notes],
        page,
        kind=WidgetKind.CHART,
        visualization=viz,
        data_path=(
            "name-map" if all(r.data_path == "name-map" for r in results) else "datadog-same-names"
        ),
        **fields,
    )
    for r in results:  # only once the widget exists, so a refused one adds none
        ctx.used.update(r.variables)
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
        if kind == "image":
            return _image(definition, layout, page)
        if kind not in CHARTS:
            raise Fail(f"{DR.W_TYPE}: {shown(kind, 40)}")
        return _chart(definition, layout, kind, page, ctx)
    except Fail as fail:
        return placeholder(title, layout, kind, [fail.reason], page)
    except ValueError as exc:  # the IR refused what was built; never let it stop the dashboard
        return placeholder(title, layout, kind, [DR.W_REJECTED.format(why=shown(exc, 100))], page)
