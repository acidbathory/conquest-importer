"""Intermediate representation (IR): a vendor-neutral description of a monitor.

Every extractor produces IR and every emitter consumes it. See docs/ir-spec.md.
Threshold values in the IR are already in New Relic units (metric scale applied).
"""

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class Confidence(StrEnum):
    EXACT = "exact"  # converted with no caveats
    APPROXIMATE = "approximate"  # converted, but review the listed reasons
    MANUAL = "manual"  # cannot be converted automatically; a human must rebuild it
    UNSUPPORTED = "unsupported"  # no New Relic equivalent in this prototype


class SignalKind(StrEnum):
    METRIC = "metric"
    LOG = "log"
    APM = "apm"
    HEARTBEAT = "heartbeat"
    COMPOSITE = "composite"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"


class Operator(StrEnum):
    ABOVE = "above"
    ABOVE_OR_EQUALS = "above_or_equals"
    BELOW = "below"
    BELOW_OR_EQUALS = "below_or_equals"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"


class Aggregation(StrEnum):
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    SUM = "sum"


class TagFilter(BaseModel):
    key: str
    value: str = ""
    absent: bool = False  # match only rows where this attribute is not set


class NrTarget(BaseModel):
    """Where a metric lives in New Relic (resolved from the metric map).

    Exactly one of attribute or select is set. `select` is a trusted NRQL
    expression taken from the metric map file, never from monitor text.
    """

    event: str
    attribute: str | None = None
    select: str | None = None


class MetricTerm(BaseModel):
    metric: str  # source metric name, e.g. system.cpu.user
    space_aggregation: Aggregation
    nr_target: NrTarget | None = None


class Num(BaseModel):
    kind: Literal["num"] = "num"
    value: float


class Ref(BaseModel):
    kind: Literal["ref"] = "ref"
    name: str  # key into MetricSignal.terms


class BinOp(BaseModel):
    kind: Literal["op"] = "op"
    op: Literal["+", "-", "*", "/"]
    left: "Expr"
    right: "Expr"


Expr = Annotated[Num | Ref | BinOp, Field(discriminator="kind")]
BinOp.model_rebuild()


class MetricSignal(BaseModel):
    """One or more metric terms combined by an optional arithmetic expression.

    All terms share the same scope and group_by, so one NRQL query can express them.
    """

    terms: dict[str, MetricTerm]
    expression: Expr | None = None  # None means a single term
    aggregation: Aggregation  # time aggregation over the window
    scope: list[TagFilter] = []
    group_by: list[str] = []


class CountFilter(BaseModel):
    key: str
    value: str
    negate: bool = False
    wildcard: bool = False  # value contains * which becomes a LIKE match


class CountSignal(BaseModel):
    """Count of log lines or APM events matching filters (kind LOG or APM)."""

    filters: list[CountFilter] = []
    group_by: list[str] = []


class HeartbeatSignal(BaseModel):
    """Presence check: alert when the source stops reporting."""

    check: str
    group_by: list[str] = []
    nr_target: NrTarget


class Occurrences(StrEnum):
    ALL = "all"  # every evaluation window in the duration must breach
    AT_LEAST_ONCE = "at_least_once"  # one breaching window in the duration is enough


class BaselineSpec(BaseModel):
    """A baseline (learned-range) condition instead of a fixed threshold.

    `deviations` is the width of the normal band in standard deviations; the condition opens a
    violation when the signal leaves the band in `direction`.
    """

    direction: Literal["upper_only", "lower_only", "upper_and_lower"]
    deviations: float


class Signal(BaseModel):
    kind: SignalKind
    raw_query: str
    window_seconds: int | None = None  # how long a breach must last
    aggregation_window_seconds: int | None = None  # size of each evaluated datapoint
    aggregation_delay_seconds: int | None = None  # how long to wait for late data
    occurrences: Occurrences = Occurrences.ALL
    metric: MetricSignal | None = None
    count: CountSignal | None = None
    heartbeat: HeartbeatSignal | None = None
    baseline: BaselineSpec | None = None


class Threshold(BaseModel):
    severity: Severity
    operator: Operator
    value: float


class NoData(BaseModel):
    notify: bool = False
    timeout_minutes: int | None = None


class Notification(BaseModel):
    message_template: str = ""
    handles: list[str] = []  # e.g. @pagerduty-x; channels must be set up manually


class IRMonitor(BaseModel):
    source_system: str = "datadog"
    source_id: int
    source_key: str = ""  # original identifier when it is not an integer (for example an ARN)
    source_type: str
    name: str
    tags: list[str] = []
    signal: Signal
    thresholds: list[Threshold] = []
    no_data: NoData = NoData()
    notification: Notification = Notification()
    confidence: Confidence
    reasons: list[str] = []
    remediation: list[str] = []  # specific manual steps; see core/remediation.py for generic ones
    unmapped: dict[str, Any] = Field(default_factory=dict)
