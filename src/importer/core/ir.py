"""Intermediate representation (IR): a vendor-neutral description of a monitor.

Every extractor produces IR and every emitter consumes it. See docs/ir-spec.md.
Threshold values in the IR are already in New Relic units (metric scale applied).
"""

import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


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
    COUNT = "count"
    STDDEV = "stddev"
    BUCKET_PERCENTILE = "bucket_percentile"  # bucketPercentile(<metric>_bucket, p)


class FilterBase(BaseModel):
    """What a scope or log filter matches: one attribute against a value, a list or a pattern."""

    key: str
    value: str
    negate: bool = False  # != , NOT IN or NOT LIKE instead of = , IN or LIKE
    wildcard: bool = False  # value contains * which becomes a LIKE match
    values: list[str] = []  # when set, the match is IN (values) and `value` is ignored
    regex: bool = False  # value is an RE2 pattern matched in full (RLIKE); not with wildcard/values

    @model_validator(mode="after")
    def _regex_is_exclusive(self) -> Self:
        if self.regex and (self.wildcard or self.values):
            raise ValueError("regex cannot be combined with wildcard or values")
        return self


class TagFilter(FilterBase):
    value: str = ""
    absent: bool = False  # match only rows where this attribute is not set

    @model_serializer(mode="wrap")
    def _omit_defaults(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Saved IR keeps its old shape: the match fields appear only when they are used."""
        data: dict[str, Any] = handler(self)
        for name, default in (
            ("negate", False),
            ("wildcard", False),
            ("values", []),
            ("regex", False),
        ):
            if data.get(name) == default:
                data.pop(name, None)
        return data


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
    scope: list[
        TagFilter
    ] = []  # restricts this term only (emitted as filter(), on top of the signal's)
    percentile: float | None = None  # 1 to 99.99, exactly when the aggregation is BUCKET_PERCENTILE

    @model_validator(mode="after")
    def _percentile_matches_aggregation(self) -> "MetricTerm":
        if self.space_aggregation is Aggregation.BUCKET_PERCENTILE:
            if self.percentile is None or not 1 <= self.percentile <= 99.99:
                raise ValueError("a bucket percentile term needs a percentile from 1 to 99.99")
        elif self.percentile is not None:
            raise ValueError("percentile is only for the bucket percentile aggregation")
        return self

    @model_serializer(mode="wrap")
    def _omit_empty_scope(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Saved IR keeps its old shape: `scope` and `percentile` appear only when set."""
        data: dict[str, Any] = handler(self)
        if not data.get("scope"):
            data.pop("scope", None)
        if data.get("percentile") is None:
            data.pop("percentile", None)
        return data


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


class CountFilter(FilterBase):
    # a numeric comparison, or whether the attribute is set; None is a plain match on `value`
    compare: Literal["gt", "gte", "lt", "lte", "exists", "missing"] | None = None

    @model_serializer(mode="wrap")
    def _omit_compare(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Saved IR keeps its old shape: `compare` appears only when it is used."""
        data: dict[str, Any] = handler(self)
        if data.get("compare") is None:
            data.pop("compare", None)
        if data.get("regex") is False:
            data.pop("regex", None)
        return data


class CountSignal(BaseModel):
    """Count of log lines or APM events matching filters (kind LOG or APM)."""

    filters: list[CountFilter] = []
    group_by: list[str] = []


class HeartbeatSignal(BaseModel):
    """Presence check: alert when the source stops reporting."""

    check: str
    group_by: list[str] = []
    scope: list[TagFilter] = []
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


class EmitOptions(BaseModel):
    """Optional New Relic condition settings a source or a person can ask for.

    Every field defaults to "not set", which reproduces the earlier output byte for byte; the
    emitters fall back to the defaults in emit/common.py (EmitDefaults). Bounds are checked at
    emission, so a value New Relic would reject becomes a "not emitted" reason.
    """

    aggregation_method: Literal["EVENT_FLOW", "EVENT_TIMER", "CADENCE"] | None = None
    aggregation_timer_seconds: int | None = None  # EVENT_TIMER only
    fill_option: Literal["NONE", "STATIC", "LAST_VALUE"] | None = None
    fill_value: float | None = None  # with fill_option STATIC
    slide_by_seconds: int | None = None
    violation_time_limit_seconds: int | None = None
    title_template: str | None = None  # untrusted: escaped on output
    runbook_url: str | None = None  # untrusted: escaped on output
    ignore_on_expected_termination: bool | None = None
    # wrap a log or APM count in filter(count(*), WHERE ...) so an empty window gives 0, not NULL
    # (docs/research/count-null-empty-window.md); None follows EmitDefaults.null_safe_count
    null_safe_count: bool | None = None
    null_safe_scope: list[str] = []  # filter keys that stay in the outer WHERE (the entity)
    # untrusted text appended to the condition description (documentation, labels, notes)
    description_note: str | None = None
    # "predict future behavior": open the violation when the signal is predicted to breach
    predict_by_seconds: int | None = None
    # loss-of-signal behaviour; None keeps today's open=True, close=False
    open_violation_on_expiration: bool | None = None
    close_violations_on_expiration: bool | None = None


COMPOUND_ALIASES = "ABCDE"  # New Relic accepts only these five capital letters (seen live)
MAX_TRIGGER_LENGTH = 200
COMPOUND_TOKEN = re.compile(r"\s*(\(|\)|AND\b|OR\b|NOT\b|[A-Z]\b)")


class CompoundSpec(BaseModel):
    """A New Relic compound condition: a boolean expression over 2 to 5 other monitors.

    `components` holds the source keys of the component monitors in alias order: the first is
    alias A, the second B, and so on. `trigger` combines the aliases with AND, OR, NOT and
    parentheses, and must use every alias it has a component for. The components must be created
    first (enabled, as New Relic requires) and their ids filled in; see core/apply.py. Facets are
    ignored and the threshold duration is New Relic's default: each component carries its own.
    """

    trigger: str
    components: list[str]

    @model_validator(mode="after")
    def _valid_expression(self) -> Self:
        if not 2 <= len(self.components) <= len(COMPOUND_ALIASES):
            raise ValueError("a compound condition has 2 to 5 components")
        if len(set(self.components)) != len(self.components):
            raise ValueError("a compound condition names each component once")
        if len(self.trigger) > MAX_TRIGGER_LENGTH:
            raise ValueError(f"the trigger is longer than {MAX_TRIGGER_LENGTH} characters")
        tokens = _compound_tokens(self.trigger)
        used = {t for t in tokens if len(t) == 1 and t.isalpha()}
        if used != set(COMPOUND_ALIASES[: len(self.components)]):
            raise ValueError("the trigger must use exactly one alias per component, from A")
        return self


def _compound_tokens(trigger: str) -> list[str]:
    """The tokens of a well-formed trigger expression, or ValueError."""
    tokens: list[str] = []
    at, end = 0, len(trigger.rstrip())
    while at < end:
        found = COMPOUND_TOKEN.match(trigger, at)
        if found is None:
            raise ValueError("the trigger may use only A to E, AND, OR, NOT and parentheses")
        tokens.append(found[1])
        at = found.end()
    depth, operand = 0, True  # operand: the next token must start an operand
    for part in tokens:
        if operand and part in ("NOT", "("):
            depth += part == "("
        elif operand and len(part) == 1 and part.isalpha():
            operand = False
        elif not operand and part in ("AND", "OR"):
            operand = True
        elif not operand and part == ")" and depth:
            depth -= 1
        else:
            raise ValueError("the trigger is not a well-formed expression")
    if operand or depth:
        raise ValueError("the trigger is not a well-formed expression")
    return tokens


class IRMonitor(BaseModel):
    source_system: str
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
    emit: EmitOptions | None = None
    # What must be in place for the converted query to return data (GCP: native, needs-...).
    # None = not stated; "native" = nothing beyond the platform link, left out of saved IR.
    data_path: str | None = None
    # Set only on a compound monitor (signal kind COMPOSITE), which combines other monitors;
    # see CompoundSpec. Left out of saved IR when None, so every other monitor keeps its shape.
    compound: CompoundSpec | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_emit(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Saved IR keeps its old shape: `emit` and `compound` appear only when set."""
        data: dict[str, Any] = handler(self)
        if data.get("emit") is None:
            data.pop("emit", None)
        if data.get("compound") is None:
            data.pop("compound", None)
        if data.get("data_path") in (None, "native"):
            data.pop("data_path", None)
        return data
