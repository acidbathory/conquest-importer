"""Pieces the workbook and portal dashboard converters share: layout, results and metric charts."""

from dataclasses import dataclass, field
from typing import Any

from importer.core.dashboard_ir import (
    TIMESERIES_VISUALIZATIONS,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Series,
    Visualization,
    WidgetKind,
)
from importer.core.emit.nrql import identifier, widget_nrql
from importer.core.ir import (
    Aggregation,
    Confidence,
    MetricSignal,
    MetricTerm,
    NrTarget,
    TagFilter,
)
from importer.core.text import printable
from importer.sources.azure import dashboard_reasons as DR
from importer.sources.azure import reasons as R
from importer.sources.azure.common import Fail
from importer.sources.azure.models import NameMap
from importer.sources.azure.names import PLATFORM_TYPE, dimension_attribute, metric_name

MAX_TITLE = 200
MAX_TEXT = 6000
POLL_SECONDS = 300
COLLECTOR = TagFilter(key="collector.name", value="azure-monitor")
RESOURCE_ID = "azure.resourceId"
VAR_MARK = "__azure_dashboard_variable__"
# Portal and workbook aggregation numbers: seen in the public workbook templates (Sum 1 on
# transaction counts, Average 4 on latencies, Max 3 on cache load, Count 7 on request counts).
AGGREGATION_NUMBERS = {1: "sum", 2: "min", 3: "max", 4: "avg", 7: "count"}
AGGREGATIONS = {
    "sum": Aggregation.SUM,
    "min": Aggregation.MIN,
    "max": Aggregation.MAX,
    "avg": Aggregation.AVG,
    "count": Aggregation.SUM,  # written with a count() select
}
HEIGHT = {"markdown": 2, "chart": 3, "table": 4, "billboard": 2, "pie": 3}


def clean_text(text: str) -> str:
    """Markdown text with line breaks kept and every other control character removed."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or c.isprintable())[:MAX_TEXT]


def result(
    title: str,
    layout: Layout,
    source_type: str,
    confidence: Confidence,
    reasons: list[str],
    *,
    kind: WidgetKind = WidgetKind.CHART,
    page: str | None = None,
    **extra: Any,
) -> IRWidget:
    return IRWidget(
        kind=kind,
        source_type=printable(source_type, 30),
        title=printable(title, MAX_TITLE) or "widget",
        layout=layout,
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        page=page,
        **extra,
    )


def placeholder(
    title: str, layout: Layout, source_type: str, reasons: list[str], page: str | None = None
) -> IRWidget:
    return result(
        title,
        layout,
        source_type,
        Confidence.MANUAL,
        sorted(set(reasons)),
        kind=WidgetKind.UNSUPPORTED,
        page=page,
    )


def markdown(
    title: str,
    layout: Layout,
    source_type: str,
    text: str,
    notes: list[str],
    page: str | None = None,
) -> IRWidget:
    return result(
        title,
        layout,
        source_type,
        Confidence.APPROXIMATE if notes else Confidence.EXACT,
        notes,
        kind=WidgetKind.MARKDOWN,
        visualization=Visualization.MARKDOWN,
        text=clean_text(text),
        page=page,
    )


@dataclass
class Flow:
    """Places boxes left to right on the 12-column grid and wraps to a new row when full."""

    column: int = 1
    row: int = 1
    row_height: int = 0

    def place(self, width: int, height: int) -> Layout:
        width = max(1, min(width, 12))
        if self.column + width - 1 > 12:
            self.row += self.row_height
            self.column, self.row_height = 1, 0
        layout = Layout(column=self.column, row=self.row, width=width, height=max(1, height))
        self.column += width
        self.row_height = max(self.row_height, height)
        return layout


@dataclass
class Pages:
    """One Flow per page; rows restart at 1 on each page."""

    flows: dict[str | None, Flow] = field(default_factory=dict)

    def place(self, page: str | None, width: int, height: int) -> Layout:
        return self.flows.setdefault(page, Flow()).place(width, height)


@dataclass
class MetricSpec:
    namespace: str  # resource type, such as microsoft.compute/virtualmachines
    name: str  # Azure metric name
    aggregation: str  # sum, min, max, avg, count
    split_by: str | None = None
    label: str | None = None


@dataclass
class MetricScope:
    resource_ids: list[str] = field(default_factory=list)  # literal ARM ids
    variable: str | None = None  # New Relic dashboard variable holding resource ids
    several: bool = False  # more than one resource can match, so chart one line per resource
    notes: list[str] = field(default_factory=list)


@dataclass
class MetricChart:
    series: list[Series]
    raw: list[RawNrqlSeries]
    notes: list[str]
    namespaces: list[str]


def _series_for(
    spec: MetricSpec, scope: MetricScope, names: NameMap, notes: list[str]
) -> MetricSignal:
    found = metric_name(names, spec.namespace, spec.name)
    if found is None:
        if spec.namespace and not PLATFORM_TYPE.fullmatch(spec.namespace.lower()):
            raise Fail(f"{DR.METRIC}: {R.M_CUSTOM_NAMESPACE}: {R.shown(spec.namespace, 60)}")
        raise Fail(f"{DR.METRIC}: {R.M_NO_NAME}: {R.shown(spec.name, 60)}")
    try:
        select = f"count({identifier(found.attribute)})" if spec.aggregation == "count" else None
        if select is None:
            identifier(found.attribute)
    except ValueError:
        raise Fail(f"{DR.METRIC}: {R.M_NO_NAME}: {R.shown(spec.name, 60)}") from None
    notes.append(
        (R.N_NAME_MAPPED if found.from_map else R.N_NAME_DERIVED).format(name=found.attribute)
    )
    facets: list[str] = []
    if spec.split_by:
        attr = dimension_attribute(names, found.prefix, spec.split_by)
        if attr is None:
            raise Fail(f"{DR.METRIC}: {R.M_DIMENSION}: {R.shown(spec.split_by, 40)}")
        facets.append(attr)
        notes.append(R.N_ATTR_DERIVED.format(names=attr))
    filters = [COLLECTOR]
    if scope.resource_ids:
        filters.append(TagFilter(key=RESOURCE_ID, value="", values=sorted(scope.resource_ids)))
    elif scope.variable:
        filters.append(TagFilter(key=RESOURCE_ID, value=VAR_MARK))
    target = NrTarget(event="Metric", attribute=found.attribute, select=select)
    aggregation = AGGREGATIONS[spec.aggregation]
    return MetricSignal(
        terms={"a": MetricTerm(metric=spec.name, space_aggregation=aggregation, nr_target=target)},
        aggregation=aggregation,
        scope=filters,
        group_by=[*([RESOURCE_ID] if scope.several else []), *facets],
    )


def metric_chart(
    specs: list[MetricSpec], scope: MetricScope, names: NameMap, viz: Visualization
) -> MetricChart:
    """The series of a metric chart; raises Fail with the reason when a metric cannot convert."""
    notes: list[str] = list(scope.notes)
    timeseries = viz in TIMESERIES_VISUALIZATIONS
    series: list[Series] = []
    for spec in specs:
        label = printable(spec.label, 100) if spec.label and len(specs) > 1 else None
        series.append(
            Series(
                label=label,
                signal=_series_for(spec, scope, names, notes),
                period_seconds=POLL_SECONDS,
                auto_period=timeseries,
            )
        )
    if scope.several:
        notes.append(DR.N_MULTI_RESOURCE)
    if len({spec.name for spec in specs}) > 1:
        notes.append(DR.N_DOUBLE_METRIC)
    if scope.variable:
        raw = [RawNrqlSeries(nrql=_variable_nrql(s, timeseries, scope.variable)) for s in series]
        return MetricChart([], raw, list(dict.fromkeys(notes)), [s.namespace for s in specs])
    return MetricChart(series, [], list(dict.fromkeys(notes)), [s.namespace for s in specs])


def _variable_nrql(series: Series, timeseries: bool, variable: str) -> str:
    query = widget_nrql(series.model_copy(update={"auto_period": False}), False)
    query = query.replace(f"{RESOURCE_ID} = '{VAR_MARK}'", f"{RESOURCE_ID} IN ({{{{{variable}}}}})")
    return query + (" TIMESERIES AUTO" if timeseries else "")


def chart_widget(
    title: str,
    layout: Layout,
    source_type: str,
    viz: Visualization,
    chart: MetricChart,
    page: str | None,
    extra_notes: list[str],
) -> IRWidget:
    fields: dict[str, Any] = {"series": chart.series} if chart.series else {"raw_series": chart.raw}
    return result(
        title,
        layout,
        source_type,
        Confidence.APPROXIMATE,
        [*chart.notes, *extra_notes],
        visualization=viz,
        page=page,
        data_path=None,  # native: the Azure Monitor integration
        **fields,
    )
