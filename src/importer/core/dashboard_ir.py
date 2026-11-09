"""Vendor-neutral dashboard IR: what every source's dashboards are translated into.

Layout uses New Relic's grid (12 columns); sources convert from their own grid. A widget holds
its series as MetricSignals, so NRQL is still built in one place (core/emit/nrql.py). A translator
that already produces NRQL puts it in `raw_series` instead; core/dashboard_nrql.py checks it.

Fields added after the first AWS and GCP translators (table, raw NRQL, automatic bucket, options,
thresholds, variables) default to "absent" and are left out of the serialised IR when they have
their default, so a dashboard that does not use them serialises exactly as before.
"""

from enum import StrEnum
from typing import Any, ClassVar, Self

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from importer.core.dashboard_nrql import (
    VARIABLE_NAME,
    check_raw_nrql,
    check_variable_usage,
    check_variables_defined,
)
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
    TABLE = "viz.table"


# Charts over time need TIMESERIES; the others show one value per series.
TIMESERIES_VISUALIZATIONS = {Visualization.LINE, Visualization.AREA}

# The Terraform provider documents widget height as 1 to 12 (research note
# dynatrace-dashboards-inventory-datapath.md, DOCS N1). The provider schema carries no limit on the
# attribute, so the value is UNVERIFIED against New Relic itself. Layout still accepts up to 100,
# because the AWS and GCP translators clamp to that; the emitters refuse what the provider refuses.
PROVIDER_MAX_HEIGHT = 12

CHARTS = {v for v in Visualization if v != Visualization.MARKDOWN}
# Which widget types carry each option, from the provider schema (newrelic_one_dashboard):
# `threshold` blocks exist on widget_line and widget_table, `critical`/`warning` on
# widget_billboard, `y_axis_left_zero` on widget_line only, `y_axis_left_min/max` on every chart.
OPTION_SUPPORT: dict[str, set[Visualization]] = {
    "unit": CHARTS,
    "decimals": CHARTS,
    "legend": CHARTS,
    "stacked": {Visualization.BAR, Visualization.AREA},
    "y_min": {Visualization.LINE, Visualization.AREA, Visualization.BAR},
    "y_max": {Visualization.LINE, Visualization.AREA, Visualization.BAR},
    "y_zero": {Visualization.LINE},
    "thresholds": {Visualization.LINE, Visualization.TABLE, Visualization.BILLBOARD},
}
MAX_VARIABLES = 50
MAX_ITEMS = 500
MAX_TEXT = 200


class OmitDefaults(BaseModel):
    """Leaves the named optional fields out of serialised output while they hold their default."""

    omit_when_default: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _serialise(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for name in self.omit_when_default:
            default = type(self).model_fields[name].get_default(call_default_factory=True)
            if getattr(self, name) == default:
                data.pop(name, None)
        return data


class Layout(BaseModel):
    column: int = Field(ge=1, le=12)
    row: int = Field(ge=1)
    width: int = Field(ge=1, le=12)
    height: int = Field(ge=1, le=100)


def layout_problem(layout: Layout) -> str | None:
    """Why the provider would refuse this layout, or None."""
    if layout.height > PROVIDER_MAX_HEIGHT:
        return (
            f"widget height {layout.height} is above the {PROVIDER_MAX_HEIGHT} "
            "that the New Relic provider accepts"
        )
    return None


class Series(OmitDefaults):
    omit_when_default: ClassVar[tuple[str, ...]] = ("auto_period",)

    label: str | None = None
    signal: MetricSignal
    period_seconds: int = Field(ge=1, le=86400)
    # TIMESERIES AUTO instead of a fixed bucket; period_seconds is then ignored. A nullable
    # period_seconds would be the other way to say this, but core/emit/nrql.py reads it as an int.
    auto_period: bool = False


class RawNrqlSeries(BaseModel):
    """A query the translator already built. The label is for the report and the legend only."""

    label: str | None = Field(default=None, max_length=100)
    nrql: str

    @field_validator("nrql")
    @classmethod
    def _safe(cls, value: str) -> str:
        check_raw_nrql(value)
        return value


class Severity(StrEnum):
    # Values from the provider documentation; the schema does not enumerate them (UNVERIFIED).
    WARNING = "warning"
    SEVERE = "severe"
    CRITICAL = "critical"
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"


class Threshold(BaseModel):
    severity: Severity
    from_value: float | None = Field(default=None, allow_inf_nan=False)
    to_value: float | None = Field(default=None, allow_inf_nan=False)
    name: str | None = Field(default=None, max_length=100)
    column_name: str | None = Field(default=None, max_length=MAX_TEXT)  # table widgets only

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.from_value is None and self.to_value is None:
            raise ValueError("a threshold needs from_value, to_value or both")
        if None not in (self.from_value, self.to_value) and self.from_value > self.to_value:  # type: ignore[operator]
            raise ValueError("threshold from_value is above to_value")
        return self


class WidgetOptions(BaseModel):
    unit: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=r"^[^\x00-\x1f\x7f]+$"
    )
    decimals: int | None = Field(default=None, ge=0, le=10)  # UNVERIFIED output name; not emitted
    legend: bool | None = None
    stacked: bool | None = None  # UNVERIFIED NerdGraph id; modelled, not emitted
    y_min: float | None = Field(default=None, allow_inf_nan=False)
    y_max: float | None = Field(default=None, allow_inf_nan=False)
    y_zero: bool | None = None

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.y_min is not None and self.y_max is not None and self.y_min > self.y_max:
            raise ValueError("y_min is above y_max")
        return self


class IRWidget(OmitDefaults):
    omit_when_default: ClassVar[tuple[str, ...]] = ("raw_series", "options", "thresholds")

    kind: WidgetKind
    source_type: str  # the source's own widget type, for the report
    title: str
    layout: Layout
    visualization: Visualization | None = None
    series: list[Series] = []
    text: str | None = None  # markdown widgets
    confidence: Confidence
    reasons: list[str] = []
    raw_series: list[RawNrqlSeries] = []
    options: WidgetOptions = WidgetOptions()
    thresholds: list[Threshold] = []

    @model_validator(mode="after")
    def _options_fit_the_widget(self) -> Self:
        used = [n for n in OPTION_SUPPORT if self._is_set(n)]
        for name in used:
            if self.visualization not in OPTION_SUPPORT[name]:
                raise ValueError(f"option {name} is not available on {self.visualization}")
        for t in self.thresholds:
            self._check_threshold(t)
        return self

    def _is_set(self, name: str) -> bool:
        if name == "thresholds":
            return bool(self.thresholds)
        return getattr(self.options, name) is not None

    def _check_threshold(self, t: Threshold) -> None:
        if t.column_name is not None and self.visualization != Visualization.TABLE:
            raise ValueError("a threshold column_name is only for table widgets")
        if self.visualization == Visualization.BILLBOARD and (
            t.severity not in (Severity.CRITICAL, Severity.WARNING) or t.from_value is None
        ):
            raise ValueError(
                "a billboard takes only critical and warning thresholds with from_value"
            )


class VariableType(StrEnum):
    NRQL = "nrql"
    ENUM = "enum"
    STRING = "string"


class ReplacementStrategy(StrEnum):
    DEFAULT = "default"
    IDENTIFIER = "identifier"
    NUMBER = "number"
    STRING = "string"


class VariableItem(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TEXT)
    value: str = Field(max_length=MAX_TEXT)


class IRVariable(BaseModel):
    """A dashboard template variable; a widget query refers to it as {{name}}."""

    name: str = Field(pattern=r"^" + VARIABLE_NAME + r"$")
    title: str = Field(min_length=1, max_length=MAX_TEXT)
    type: VariableType
    default_values: list[str] = Field(default=[], max_length=MAX_ITEMS)
    multi_select: bool = False
    replacement: ReplacementStrategy = ReplacementStrategy.DEFAULT
    items: list[VariableItem] = Field(default=[], max_length=MAX_ITEMS)  # enum variables
    nrql: str | None = None  # nrql variables: a query that returns the values
    account_ids: list[int] = Field(default=[], max_length=20)  # nrql variables; empty = default
    excluded: bool = False
    ignore_time_range: bool = False
    show_apply_action: bool = False

    @field_validator("account_ids")
    @classmethod
    def _positive(cls, value: list[int]) -> list[int]:
        if any(v < 1 for v in value):
            raise ValueError("account ids must be positive")
        return value

    @model_validator(mode="after")
    def _shape_matches_type(self) -> Self:
        if self.type == VariableType.ENUM:
            if not self.items:
                raise ValueError("an enum variable needs items")
            if len({i.value for i in self.items}) != len(self.items):
                raise ValueError("enum item values must be unique")
        elif self.items:
            raise ValueError("only enum variables have items")
        if self.type == VariableType.NRQL:
            self._check_query()
        elif self.nrql is not None or self.account_ids:
            raise ValueError("only nrql variables have a query or account ids")
        if self.type == VariableType.STRING and self.multi_select:
            raise ValueError("a string variable cannot be multi-select")
        return self

    def _check_query(self) -> None:
        if not self.nrql:
            raise ValueError("an nrql variable needs a query")
        check_raw_nrql(self.nrql)
        # Nested variables are Pro-plan only (research note); a translator must resolve them.
        if check_variable_usage(self.nrql):
            raise ValueError("a variable query may not use other variables")


class IRDashboard(OmitDefaults):
    omit_when_default: ClassVar[tuple[str, ...]] = ("variables",)

    source_system: str
    source_key: str  # the dashboard's name in the source system
    name: str
    widgets: list[IRWidget]
    confidence: Confidence
    reasons: list[str] = []
    variables: list[IRVariable] = Field(default=[], max_length=MAX_VARIABLES)

    @model_validator(mode="after")
    def _variables_are_consistent(self) -> Self:
        names = [v.name for v in self.variables]
        if len(set(names)) != len(names):
            raise ValueError("variable names must be unique")
        queries = [r.nrql for w in self.widgets for r in w.raw_series]
        check_variables_defined(queries, names)
        return self
