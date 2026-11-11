"""Workbooks (Microsoft.Insights/workbooks) to the dashboard IR.

A workbook is a list of items that flows top to bottom. What converts:

- type 1 text: a markdown widget;
- type 10 metrics: a chart over `Metric`, with the metric name rules of the alert converter;
- type 3 query: KQL goes through `dashboard_kql.translate_kql` (the swap-in translator), and its
  `visualization` picks the chart; Resource Graph, ARM, merge, JSON and Data Explorer queries are
  placeholders with a reason of their own;
- type 9 parameters: resource pickers, static dropdowns and dropdowns whose KQL translates become
  NRQL or enum variables, and only the ones a converted widget uses are emitted;
- type 11 links: tabs make pages; other links are shown as text;
- type 12 groups: a tab group is a page, any other group a heading followed by its items.

Everything else is a placeholder that says why: conditional visibility (a New Relic widget is always
shown), a parameter that depends on another or was not migrated, unknown item types. A placeholder
is never counted as converted.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import (
    MAX_PAGED_WIDGETS,
    MAX_PAGES,
    MAX_VARIABLES,
    MAX_WIDGETS,
    PAGE_WIDGETS,
    TIMESERIES_VISUALIZATIONS,
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    RawNrqlSeries,
    VariableItem,
    VariableType,
    Visualization,
    WidgetKind,
)
from importer.core.dashboard_nrql import VARIABLE_NAME
from importer.core.emit.nrql import quote_string
from importer.core.ir import Confidence
from importer.core.text import printable
from importer.sources.azure import dashboard_kql
from importer.sources.azure import dashboard_reasons as DR
from importer.sources.azure.common import Fail
from importer.sources.azure.dashboard_common import (
    AGGREGATION_NUMBERS,
    HEIGHT,
    MAX_TITLE,
    MetricScope,
    MetricSpec,
    Pages,
    chart_widget,
    markdown,
    metric_chart,
    placeholder,
    result,
)
from importer.sources.azure.dashboard_kql import KqlNrql
from importer.sources.azure.dashboard_models import AzureDashboard
from importer.sources.azure.names import type_of_resource_id, type_prefix

MAX_DEPTH = 12
MAX_ITEMS = 2000
PARAM_REF = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::[A-Za-z0-9_.]+)?\}")
TEXT_REF = re.compile(r"(?<![\w$])\{[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_.]+)?\}")
TIME_PARAM = 4
OPTIONS_GROUP = 10
RESOURCE_PICKER = 5
DROPDOWN = 2
ARG_QUERY, JSON_QUERY = 1, 8
METRIC_CHARTS = {
    0: (Visualization.AREA, ""),
    1: (Visualization.AREA, DR.N_BAR),
    2: (Visualization.LINE, ""),
    3: (Visualization.LINE, DR.N_SCATTER),
}
QUERY_PLACEHOLDERS = {
    1: DR.QUERY_ARG,
    12: DR.QUERY_ARM,
    7: DR.QUERY_MERGE,
    8: DR.QUERY_JSON,
    9: DR.QUERY_ADX,
}
KQL_VIZ = {
    "table": Visualization.TABLE,
    "grid": Visualization.TABLE,
    "tiles": Visualization.BILLBOARD,
    "stat": Visualization.BILLBOARD,
    "card": Visualization.BILLBOARD,
    "linechart": Visualization.LINE,
    "timechart": Visualization.LINE,
    "areachart": Visualization.AREA,
    "scatterchart": Visualization.LINE,
    "barchart": Visualization.BAR,
    "unstackedbar": Visualization.BAR,
    "piechart": Visualization.PIE,
}
GROUP = "workbook group"


@dataclass
class Param:
    name: str
    label: str
    kind: int
    query: str
    query_type: int | None
    multi: bool
    types: list[str]
    options: list[tuple[str, str]]  # a static list: (value, label)


@dataclass
class State:
    dashboard: AzureDashboard
    params: dict[str, Param] = field(default_factory=dict)
    variables: dict[str, IRVariable] = field(default_factory=dict)  # every one that converts
    used: set[str] = field(default_factory=set)
    unmigrated: dict[str, str] = field(default_factory=dict)  # parameter -> why
    tabs: dict[str, dict[str, str]] = field(default_factory=dict)
    pages: Pages = field(default_factory=Pages)
    page_names: set[str] = field(default_factory=set)
    widgets: list[IRWidget] = field(default_factory=list)
    notes: set[str] = field(default_factory=set)
    count: int = 0


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _content(item: dict[str, Any]) -> dict[str, Any]:
    """An item's content; old workbooks keep it as a string holding JSON."""
    raw = item.get("content")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            return {}
    return _dict(raw)


def _items(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in _list(content.get("items")) if isinstance(i, dict)]


# -- parameters ----------------------------------------------------------------------------------


def _static_options(param: dict[str, Any]) -> list[tuple[str, str]]:
    """The fixed list of a dropdown: `jsonData`, or a JSON-provider query holding an array."""
    raw = param.get("jsonData")
    if not isinstance(raw, str) and param.get("queryType") == JSON_QUERY:
        try:
            raw = _dict(json.loads(_str(param.get("query")))).get("content")
        except (ValueError, RecursionError):
            raw = None
    try:
        rows = json.loads(raw) if isinstance(raw, str) else []
    except (ValueError, RecursionError):
        rows = []
    options = []
    for row in _list(rows):
        fields = _dict(row)
        value = fields.get("value", fields.get("id"))
        if isinstance(value, str) and value and "{" not in value:
            options.append((value, _str(fields.get("label")) or value))
    return options[:200]


def _read_param(raw: object) -> Param | None:
    p = _dict(raw)
    name = _str(p.get("name"))
    if not name:
        return None
    types = _dict(_dict(p.get("typeSettings")).get("resourceTypeFilter"))
    return Param(
        name=name,
        label=_str(p.get("label")) or name,
        kind=_int(p.get("type")) or 0,
        query=_str(p.get("query")),
        query_type=_int(p.get("queryType")),
        multi=bool(p.get("multiSelect")),
        types=[t for t in types if isinstance(t, str)],
        options=_static_options(p),
    )


def _collect(items: list[dict[str, Any]], state: State, depth: int = 0) -> None:
    """Every parameter and every tab definition, wherever it sits in the workbook."""
    for item in items:
        content = _content(item)
        kind = item.get("type")
        if kind == 9:
            for raw in _list(content.get("parameters")):
                param = _read_param(raw)
                if param is not None:
                    state.params.setdefault(param.name, param)
                    if param.kind == OPTIONS_GROUP and param.options:
                        state.tabs.setdefault(param.name, dict(param.options))
        elif kind == 11 and _str(content.get("style")) == "tabs":
            for raw in _list(content.get("links")):
                link = _dict(raw)
                target = _str(link.get("parameterName")) or _str(link.get("cellValue"))
                if link.get("linkTarget") == "parameter" and target:
                    value = _str(link.get("parameterValue")) or _str(link.get("subTarget"))
                    state.tabs.setdefault(target, {})[value] = _str(link.get("linkLabel")) or value
        elif kind == 12 and depth < MAX_DEPTH:
            _collect(_items(content), state, depth + 1)


def _picker_nrql(param: Param) -> str:
    query = (
        "SELECT uniques(azure.resourceId, 200) FROM Metric WHERE collector.name = 'azure-monitor'"
    )
    prefixes = [type_prefix(t) for t in param.types]
    if len(prefixes) == 1 and prefixes[0]:
        query += f" AND metricName LIKE {quote_string(prefixes[0] + '.%')}"
    return query


def _kql_variable(param: Param, title: str) -> IRVariable | str:
    found = dashboard_kql.translate_kql(param.query, ())
    if isinstance(found, str):
        return found
    try:
        return IRVariable(
            name=param.name,
            title=title,
            type=VariableType.NRQL,
            nrql=found.nrql,
            multi_select=param.multi,
            default_values=["*"] if param.multi else [],
        )
    except ValueError:
        return "its translated query is not accepted as a variable"


def _convert_param(param: Param, state: State) -> IRVariable | str:
    """The variable for a parameter, or the reason it is not migrated."""
    if not re.fullmatch(VARIABLE_NAME, param.name):
        return "its name cannot be a New Relic variable name"
    depends = [r for r in PARAM_REF.findall(param.query) if r in state.params and r != param.name]
    if depends:
        return f"it depends on {', '.join(sorted(set(depends)))}"
    if param.kind == TIME_PARAM:
        return "a time range is replaced by the dashboard time picker"
    title = printable(param.label, 200) or param.name
    if param.kind == RESOURCE_PICKER:
        if param.query or param.query_type not in (None, 0):
            return "its resource list comes from a query"
        return IRVariable(
            name=param.name,
            title=title,
            type=VariableType.NRQL,
            nrql=_picker_nrql(param),
            multi_select=param.multi,
            default_values=["*"] if param.multi else [],
        )
    if param.options:
        return IRVariable(
            name=param.name,
            title=title,
            type=VariableType.ENUM,
            items=[VariableItem(title=lab[:200], value=val[:200]) for val, lab in param.options],
            multi_select=param.multi,
        )
    if param.query_type in (None, 0) and param.query and param.kind == DROPDOWN:
        return _kql_variable(param, title)
    if param.query_type == ARG_QUERY:
        return "its list comes from an Azure Resource Graph query"
    return "this parameter type has no New Relic variable"


def _convert_params(state: State) -> None:
    for param in state.params.values():
        done = _convert_param(param, state)
        if isinstance(done, str):
            state.unmigrated[param.name] = done
        else:
            state.variables[param.name] = done


# -- layout --------------------------------------------------------------------------------------


def _width(item: dict[str, Any]) -> int:
    custom = item.get("customWidth")
    if isinstance(custom, str) and custom.isdigit():
        return max(1, min(12, round(12 * int(custom) / 100)))
    return 6 if item.get("halfWidth") else 12


def _place(
    state: State, page: str | None, item: dict[str, Any], kind: str, extra: int = 0
) -> Layout:
    return state.pages.place(page, _width(item), min(HEIGHT[kind] + extra, 12))


def _title(item: dict[str, Any], content: dict[str, Any], fallback: str) -> str:
    for candidate in (content.get("title"), content.get("chartTitle"), item.get("name")):
        if isinstance(candidate, str) and candidate.strip() and "{" not in candidate:
            return printable(candidate.strip(), MAX_TITLE)
    return fallback


# -- items ---------------------------------------------------------------------------------------


def _fail_widget(
    state: State, item: dict[str, Any], page: str | None, reasons: list[str], kind: str = "table"
) -> None:
    title = _title(item, _content(item), f"Item {state.count}")
    layout = _place(state, page, item, kind)
    state.widgets.append(
        placeholder(title, layout, f"workbook item {item.get('type')}", reasons, page)
    )


def _hidden(item: dict[str, Any], state: State, page: str | None, reason: str) -> None:
    """A placeholder for every leaf under an item that cannot convert (groups are walked)."""
    if item.get("type") == 12:
        for child in _items(_content(item)):
            state.count += 1
            _hidden(child, state, page, reason)
    elif item.get("type") != 9:
        _fail_widget(state, item, page, [reason])


def _rules(item: dict[str, Any]) -> list[dict[str, Any]]:
    one = item.get("conditionalVisibility")
    return [
        _dict(r) for r in [*([one] if one else []), *_list(item.get("conditionalVisibilities"))]
    ]


def _tab_page(item: dict[str, Any], state: State) -> tuple[str | None, list[str]] | None:
    """(tab label or None, notes) when the item's visibility can be honoured.

    "This tab is selected" gives the tab's label. A rule that only asks that another parameter has
    a value gives no label (the item stays on its page). Such a rule is kept as a note, because a
    New Relic widget is shown whether or not that parameter is set.
    """
    tab: str | None = None
    notes: list[str] = []
    for rule in _rules(item):
        tabs = state.tabs.get(_str(rule.get("parameterName")), {})
        value = _str(rule.get("value"))
        if rule.get("comparison") == "isEqualTo" and value in tabs and tab is None:
            tab = tabs[value] or value
        elif rule.get("comparison") == "isNotEqualTo" and not value:
            notes.append(DR.N_COND_SET.format(name=DR.shown(rule.get("parameterName"), 40)))
        else:
            return None
    return (tab, notes) if tab or notes else None


def _unique_page(label: str, state: State) -> str | None:
    base = printable(label, MAX_TITLE) or "Tab"
    if base in state.page_names:
        return base  # two groups under one tab share its page
    if len(state.page_names) >= MAX_PAGES - 1:
        return None
    state.page_names.add(base)
    return base


def _walk(items: list[dict[str, Any]], state: State, page: str | None, depth: int) -> None:
    for item in items:
        state.count += 1
        if state.count > MAX_ITEMS:
            state.notes.add(f"only the first {MAX_ITEMS} workbook items were read")
            return
        _item(item, state, page, depth)


def _item(item: dict[str, Any], state: State, page: str | None, depth: int) -> None:
    if not (item.get("conditionalVisibility") or item.get("conditionalVisibilities")):
        _convert(item, state, page, depth)
        return
    tab = _tab_page(item, state)
    named = (
        page
        if tab and tab[0] is None
        else (_unique_page(tab[0], state) if tab and tab[0] else None)
    )
    if tab is None or (named is None and tab[0] is not None):
        _hidden(item, state, page, DR.COND_VIS)
        return
    before = len(state.widgets)
    _convert(item, state, named, depth)
    for index in range(before, len(state.widgets)):
        w = state.widgets[index]
        if tab[1] and w.kind is not WidgetKind.UNSUPPORTED:
            state.widgets[index] = w.model_copy(
                update={"reasons": [*w.reasons, *tab[1]], "confidence": Confidence.APPROXIMATE}
            )


def _convert(item: dict[str, Any], state: State, page: str | None, depth: int) -> None:
    kind = item.get("type")
    content = _content(item)
    if kind == 1:
        _text(item, content, state, page)
    elif kind == 10:
        _metrics(item, content, state, page)
    elif kind == 3:
        _query(item, content, state, page)
    elif kind == 11:
        _links(item, content, state, page)
    elif kind == 12:
        _group(item, content, state, page, depth)
    elif kind != 9:
        _fail_widget(state, item, page, [f"{DR.ITEM_TYPE}: {DR.shown(kind, 20)}"])


def _text(item: dict[str, Any], content: dict[str, Any], state: State, page: str | None) -> None:
    body = _str(content.get("json"))
    if not body.strip():
        return
    notes = [DR.N_TEXT_PARAMS] if TEXT_REF.search(body) else []
    first = next((ln.strip("# ").strip() for ln in body.splitlines() if ln.strip()), "Text")
    layout = _place(state, page, item, "markdown", body.count("\n") // 3)
    state.widgets.append(markdown(first or "Text", layout, "workbook text", body, notes, page))


def _links(item: dict[str, Any], content: dict[str, Any], state: State, page: str | None) -> None:
    links = [_dict(link) for link in _list(content.get("links"))]
    if _str(content.get("style")) == "tabs" and all(
        link.get("linkTarget") == "parameter" for link in links
    ):
        state.notes.add(DR.N_TABS)
        return
    lines = []
    for link in links:
        label = printable(_str(link.get("linkLabel")), 100)
        url = _str(link.get("url"))
        if label:
            safe = re.fullmatch(r"https://[^\s()<>]{1,500}", url)
            lines.append(f"- [{label}]({url})" if safe else f"- {label}")
    if lines:
        layout = _place(state, page, item, "markdown", len(lines) // 2)
        text = "\n".join(lines)
        state.widgets.append(markdown("Links", layout, "workbook links", text, [DR.N_LINKS], page))


def _group(
    item: dict[str, Any], content: dict[str, Any], state: State, page: str | None, depth: int
) -> None:
    if _str(content.get("groupType")) == "template" or content.get("loadFromTemplateId"):
        _fail_widget(state, item, page, [DR.GROUP_TEMPLATE])
        return
    if depth >= MAX_DEPTH:
        reason = f"{DR.ITEM_TYPE}: groups nested more than {MAX_DEPTH} deep"
        _fail_widget(state, item, page, [reason])
        return
    title = _title(item, content, "")
    if title:
        layout = state.pages.place(page, 12, 1)
        heading = markdown(title, layout, GROUP, f"## {title}", [], page)
        state.widgets.append(heading)
    _walk(_items(content), state, page, depth + 1)


# -- metrics -------------------------------------------------------------------------------------


def _metric_id(namespace: str, raw: str) -> str:
    """The metric name from the workbook's `namespace-Category-Name` id (or a plain name)."""
    if namespace and raw.lower().startswith(namespace.lower() + "-"):
        return raw[len(namespace) + 1 :].rsplit("-", 1)[-1]
    return raw


def _scope(content: dict[str, Any], state: State) -> MetricScope:
    ids = [i for i in _list(content.get("resourceIds")) if isinstance(i, str)]
    literal = [i for i in ids if "{" not in i and type_of_resource_id(i)]
    if ids and len(literal) == len(ids):
        return MetricScope(resource_ids=literal, several=len(literal) > 1)
    names = [r for i in ids for r in PARAM_REF.findall(i)]
    if len(ids) == 1 and len(names) == 1:
        variable = state.variables.get(names[0])
        if variable is not None and variable.nrql and PARAM_REF.fullmatch(ids[0]):
            state.used.add(names[0])
            return MetricScope(variable=names[0], several=variable.multi_select)
    what = (
        ", ".join(f"{{{n}}}" for n in dict.fromkeys(names)) or "by resource group or subscription"
    )
    return MetricScope(several=True, notes=[DR.N_RESOURCES.format(what=what)])


def _aggregation(metric: dict[str, Any], name: str, notes: set[str]) -> str:
    number = _int(metric.get("aggregation"))
    if number is None:
        notes.add(DR.N_AGG_DEFAULT.format(name=DR.shown(name, 40)))
        return "avg"
    if number not in AGGREGATION_NUMBERS:
        raise Fail(f"{DR.METRIC}: aggregation {number} has no New Relic function")
    return AGGREGATION_NUMBERS[number]


def _specs(content: dict[str, Any], state: State) -> list[MetricSpec]:
    specs: list[MetricSpec] = []
    for raw in _list(content.get("metrics")):
        m = _dict(raw)
        namespace = _str(m.get("namespace")) or _str(content.get("resourceType"))
        metric = _str(m.get("metric"))
        split = _str(m.get("splitBy")) or None
        for text in (namespace, metric, split):
            for ref in PARAM_REF.findall(text or ""):
                why = state.unmigrated.get(ref, "not defined in the workbook")
                raise Fail(f"{DR.PARAM_DEP}: {DR.shown(ref, 40)} ({DR.shown(why, 80)})")
        if not metric:
            raise Fail(f"{DR.METRIC}: the metric has no name")
        name = _metric_id(namespace, metric)
        label = _str(m.get("columnName")) or _str(m.get("label")) or None
        specs.append(MetricSpec(namespace, name, _aggregation(m, name, state.notes), split, label))
    if not specs:
        raise Fail(f"{DR.METRIC}: the item names no metric")
    return specs


def _metrics(item: dict[str, Any], content: dict[str, Any], state: State, page: str | None) -> None:
    kind = _int(content.get("chartType"))
    viz, note = METRIC_CHARTS.get(-1 if kind is None else kind, (Visualization.TABLE, ""))
    try:
        specs = _specs(content, state)
        chart = metric_chart(specs, _scope(content, state), state.dashboard.names, viz)
    except Fail as fail:
        _fail_widget(state, item, page, [fail.reason], "chart")
        return
    title = _title(item, content, f"Metrics: {specs[0].name}")
    layout = _place(state, page, item, "table" if viz is Visualization.TABLE else "chart")
    notes = [*([note] if note else []), DR.N_TIME]
    if content.get("filters"):
        notes.append(DR.N_FILTERS)
    state.widgets.append(chart_widget(title, layout, "workbook metrics", viz, chart, page, notes))


# -- queries -------------------------------------------------------------------------------------


def _query_view(shown: object, found: KqlNrql) -> tuple[Visualization, list[str]]:
    key = shown.lower() if isinstance(shown, str) else None
    if key is not None and key not in KQL_VIZ:
        return Visualization.TABLE, [DR.N_VIZ.format(kind=DR.shown(key, 30))]
    viz = KQL_VIZ.get(key or "table", Visualization.TABLE)
    if viz is Visualization.BAR and found.timeseries:
        return Visualization.AREA, [DR.N_BAR]
    if viz in TIMESERIES_VISUALIZATIONS and not found.timeseries:
        return Visualization.TABLE, [DR.N_VIZ.format(kind="over time (no time bucket)")]
    if found.timeseries and viz in (Visualization.PIE, Visualization.BILLBOARD):
        return Visualization.TABLE, [DR.N_VIZ.format(kind=viz.value)]
    return viz, [DR.N_TILES] if key == "tiles" else []


def _query(item: dict[str, Any], content: dict[str, Any], state: State, page: str | None) -> None:
    query_type = _int(content.get("queryType"))
    text = _str(content.get("query"))
    if query_type in QUERY_PLACEHOLDERS:
        _fail_widget(state, item, page, [QUERY_PLACEHOLDERS[query_type]])
    elif query_type not in (None, 0):
        _fail_widget(state, item, page, [f"{DR.QUERY_OTHER}: {DR.shown(query_type, 20)}"])
    elif not text.strip():
        _fail_widget(state, item, page, [DR.QUERY_EMPTY])
    else:
        _kql_query(item, content, text, state, page)


def _kql_query(
    item: dict[str, Any], content: dict[str, Any], text: str, state: State, page: str | None
) -> None:
    found = dashboard_kql.translate_kql(text, tuple(state.variables))
    if isinstance(found, str):
        _fail_widget(state, item, page, [found])
        return
    missing = [v for v in found.variables if v not in state.variables]
    if missing:
        _fail_widget(state, item, page, [f"{DR.PARAM_DEP}: {DR.shown(missing[0], 40)}"])
        return
    try:
        raw = RawNrqlSeries(nrql=found.nrql)
    except ValueError:
        reason = f"{DR.QUERY_OTHER}: the translated query is not accepted"
        _fail_widget(state, item, page, [reason])
        return
    state.used.update(found.variables)
    viz, notes = _query_view(content.get("visualization"), found)
    kind = {Visualization.TABLE: "table", Visualization.BILLBOARD: "billboard"}.get(viz, "chart")
    layout = _place(state, page, item, kind)
    title = _title(item, content, f"Query {state.count}")
    state.widgets.append(
        result(
            title,
            layout,
            "workbook query",
            Confidence.APPROXIMATE,
            [*found.notes, *notes, DR.N_TIME],
            visualization=viz,
            raw_series=[raw],
            page=page,
            data_path=found.data_path,
        )
    )


# -- the dashboard --------------------------------------------------------------------------------


def _cap(widgets: list[IRWidget]) -> tuple[list[IRWidget], int]:
    per_page = PAGE_WIDGETS if any(w.page for w in widgets) else MAX_WIDGETS
    kept: list[IRWidget] = []
    counts: dict[str | None, int] = {}
    for w in widgets:
        if counts.get(w.page, 0) >= per_page or len(kept) >= MAX_PAGED_WIDGETS:
            continue
        counts[w.page] = counts.get(w.page, 0) + 1
        kept.append(w)
    return kept, len(widgets) - len(kept)


def translate_workbook(dash: AzureDashboard) -> IRDashboard:
    base: dict[str, Any] = {
        "source_system": "azure",
        "source_key": printable(dash.key or dash.name, 10_000),
        "name": printable(dash.name, MAX_TITLE) or "workbook",
    }
    if dash.content is None:
        layout = Layout(column=1, row=1, width=12, height=2)
        reason = f"{DR.UNREADABLE}: {DR.shown(dash.error, 160)}"
        return IRDashboard(
            **base,
            widgets=[placeholder(base["name"], layout, "workbook", [reason])],
            confidence=Confidence.MANUAL,
            reasons=[reason],
        )
    state = State(dash)
    items = _items(dash.content)
    _collect(items, state)
    _convert_params(state)
    _walk(items, state, None, 0)
    widgets, dropped = _cap(state.widgets)
    variables = [state.variables[n] for n in sorted(state.used) if n in state.variables]
    reasons = [DR.N_LAYOUT, *dash.notes, *sorted(state.notes)]
    converted = [w for w in widgets if w.kind is not WidgetKind.UNSUPPORTED]
    if not widgets:
        confidence, reasons = Confidence.UNSUPPORTED, [DR.NO_ITEMS]
    elif not converted:
        confidence = Confidence.MANUAL
        reasons.append("no item could be converted")
    else:
        confidence = Confidence.APPROXIMATE
        reasons.append(f"{len(widgets) - len(converted)} of {len(widgets)} items not converted")
    if dropped:
        reasons.append(f"{dropped} items over the New Relic widget limits were not converted")
    return IRDashboard(
        **base,
        widgets=widgets,
        confidence=confidence,
        reasons=reasons,
        variables=variables[:MAX_VARIABLES],
    )
