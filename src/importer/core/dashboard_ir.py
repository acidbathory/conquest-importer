"""Vendor-neutral dashboard IR: what every source's dashboards are translated into.

Layout uses New Relic's grid (12 columns); sources convert from their own grid. A widget holds
its series as MetricSignals, so NRQL is still built in one place (core/emit/nrql.py).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from importer.core.ir import Confidence, MetricSignal


class WidgetKind(StrEnum):
    CHART = "chart"
    MARKDOWN = "markdown"
    UNSUPPORTED = "unsupported"


class Visualization(StrEnum):
    LINE = "viz.line"
    AREA = "viz.area"
    BAR = "viz.bar"
    PIE = "viz.pie"
    BILLBOARD = "viz.billboard"
    MARKDOWN = "viz.markdown"


# Charts over time need TIMESERIES; the others show one value per series.
TIMESERIES_VISUALIZATIONS = {Visualization.LINE, Visualization.AREA}


class Layout(BaseModel):
    column: int = Field(ge=1, le=12)
    row: int = Field(ge=1)
    width: int = Field(ge=1, le=12)
    height: int = Field(ge=1, le=100)


class Series(BaseModel):
    label: str | None = None
    signal: MetricSignal
    period_seconds: int = Field(ge=1, le=86400)


class IRWidget(BaseModel):
    kind: WidgetKind
    source_type: str  # the source's own widget type, for the report
    title: str
    layout: Layout
    visualization: Visualization | None = None
    series: list[Series] = []
    text: str | None = None  # markdown widgets
    confidence: Confidence
    reasons: list[str] = []


class IRDashboard(BaseModel):
    source_system: str
    source_key: str  # the dashboard's name in the source system
    name: str
    widgets: list[IRWidget]
    confidence: Confidence
    reasons: list[str] = []
