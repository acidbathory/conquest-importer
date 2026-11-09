"""Shared emitter rules: which IR monitors can be emitted, and how windows map to New Relic."""

from dataclasses import dataclass

from importer.core.emit.nrql import build_nrql
from importer.core.ir import Confidence, IRMonitor, SignalKind


@dataclass(frozen=True)
class EmitDefaults:
    """What the emitters do when the IR does not say. Each value reproduces the output of the
    releases before these options existed. Changing one changes the bytes of every converted
    fixture; tests/test_emitter_byte_identity.py shows which, and a person decides.
    """

    muting_rules_handling: str = "NOTIFY_ALL_ISSUES"
    notification_triggers: tuple[str, ...] = ()  # empty: leave it to New Relic's defaults
    incident_preference: str | None = None  # None: the Terraform policy sets nothing
    # source system -> aggregation method for metric and heartbeat signals; empty: EVENT_FLOW
    aggregation_method_by_source: tuple[tuple[str, str], ...] = ()
    count_fill: tuple[str, float] | None = None  # fill option and value for log and APM counts
    null_safe_count: bool = False  # wrap log and APM counts in filter(count(*), WHERE ...)


EMIT_DEFAULTS = EmitDefaults()


def emit_defaults() -> EmitDefaults:
    """Read at call time, so a test (or a later decision) can replace EMIT_DEFAULTS."""
    return EMIT_DEFAULTS


# A source "avg over the last 5 minutes" is evaluated as 1-minute windows that must breach
# for 5 minutes.
# This is a close but not identical semantic; the report states it once for all monitors.
AGGREGATION_WINDOW_SECONDS = 60
AGGREGATION_DELAY_SECONDS = 120
MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 86400
EMITTABLE_KINDS = (SignalKind.METRIC, SignalKind.LOG, SignalKind.APM, SignalKind.HEARTBEAT)


MAX_NAME_LENGTH = 128
MIN_AGGREGATION_WINDOW_SECONDS = 30
MAX_AGGREGATION_WINDOW_SECONDS = 3600
SOURCE_LABELS = {"cloudwatch": "CloudWatch alarm", "gcp": "Google Cloud alert policy"}
RESOURCE_PREFIXES = {"cloudwatch": "cw", "gcp": "gcp"}


def condition_name(name: str) -> str:
    """Name as it will be stored. New Relic drops control characters from names, so we remove
    them ourselves: the name we send then equals the name stored, which keeps re-runs idempotent."""
    return "".join(c for c in name if c.isprintable()).strip()[:MAX_NAME_LENGTH]


def source_description(monitor: IRMonitor) -> str:
    label = SOURCE_LABELS.get(monitor.source_system, monitor.source_system)
    ident = monitor.source_key or str(monitor.source_id)
    return f"Imported from {label} {condition_name(ident)}"


def aggregation_window(monitor: IRMonitor) -> int:
    return monitor.signal.aggregation_window_seconds or AGGREGATION_WINDOW_SECONDS


def aggregation_delay(monitor: IRMonitor) -> int:
    return monitor.signal.aggregation_delay_seconds or AGGREGATION_DELAY_SECONDS


class CannotEmit(ValueError):
    """The monitor cannot be emitted; the message is the reason."""


def emittable_window(monitor: IRMonitor) -> int:
    """Validate that a monitor can be emitted and return its breach duration in seconds."""
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        raise CannotEmit(f"confidence is {monitor.confidence.value}")
    if monitor.signal.kind not in EMITTABLE_KINDS:
        raise CannotEmit(f"signal kind {monitor.signal.kind.value} is not supported")
    window = monitor.signal.window_seconds
    if window is None:
        raise CannotEmit("no evaluation window")
    if not monitor.thresholds:
        raise CannotEmit("no thresholds")
    if not MIN_DURATION_SECONDS <= window <= MAX_DURATION_SECONDS:
        raise CannotEmit(f"evaluation window {window}s is outside 60s-24h")
    agg = aggregation_window(monitor)
    if not MIN_AGGREGATION_WINDOW_SECONDS <= agg <= MAX_AGGREGATION_WINDOW_SECONDS:
        raise CannotEmit(f"aggregation window {agg}s is outside 30s-1h")
    if window % agg:
        raise CannotEmit(f"duration {window}s is not a multiple of the {agg}s aggregation window")
    return window


def nrql_for(monitor: IRMonitor) -> str:
    emittable_window(monitor)
    try:
        return build_nrql(monitor)
    except ValueError as exc:
        raise CannotEmit(str(exc)) from None
