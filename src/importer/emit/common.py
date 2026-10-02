"""Shared emitter rules: which IR monitors can be emitted, and how windows map to New Relic."""

from importer.emit.nrql import build_nrql
from importer.models.ir import Confidence, IRMonitor, SignalKind

# A Datadog "avg over last 5m" becomes: evaluate 1-minute windows, breach for 5 minutes.
# This is a close but not identical semantic; the report states it once for all monitors.
AGGREGATION_WINDOW_SECONDS = 60
AGGREGATION_DELAY_SECONDS = 120
MIN_DURATION_SECONDS = 60
MAX_DURATION_SECONDS = 86400
EMITTABLE_KINDS = (SignalKind.METRIC, SignalKind.LOG, SignalKind.APM, SignalKind.HEARTBEAT)


MAX_NAME_LENGTH = 128


def condition_name(name: str) -> str:
    """Name as it will be stored. New Relic drops control characters from names, so we remove
    them ourselves: the name we send then equals the name stored, which keeps re-runs idempotent."""
    return "".join(c for c in name if c.isprintable()).strip()[:MAX_NAME_LENGTH]


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
    return window


def nrql_for(monitor: IRMonitor) -> str:
    emittable_window(monitor)
    try:
        return build_nrql(monitor)
    except ValueError as exc:
        raise CannotEmit(str(exc)) from None
