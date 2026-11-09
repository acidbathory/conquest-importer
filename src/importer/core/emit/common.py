"""Shared emitter rules: which IR monitors can be emitted, and how windows map to New Relic."""

import importlib
import re
from dataclasses import dataclass

from importer.core.emit.nrql import build_nrql
from importer.core.ir import Confidence, EmitOptions, IRMonitor, SignalKind, Threshold


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


# Bounds of New Relic's NRQL condition settings (docs/research/newrelic-notifications-and-nrql-
# reference.md, section 2). Where the documentation conflicts the narrower range is kept.
MAX_TERMS = 2
EXPIRATION_SECONDS = (30, 172800)
TIMER_SECONDS = (5, 1200)
DELAY_SECONDS = {"EVENT_FLOW": (0, 1200), "CADENCE": (0, 3600)}
VIOLATION_TIME_LIMIT_SECONDS = (300, 2592000)
MAX_TITLE_TEMPLATE = 256
MIN_SLIDE_BY_SECONDS = 30
RUNBOOK_URL = re.compile(r"https?://\S{1,2000}")
COUNT_KINDS = (SignalKind.LOG, SignalKind.APM)


def streaming_method(monitor: IRMonitor) -> str:
    """EVENT_FLOW, EVENT_TIMER or CADENCE: the monitor's own choice, else the default."""
    options = monitor.emit
    if options is not None and options.aggregation_method is not None:
        return options.aggregation_method
    if monitor.signal.kind in (SignalKind.METRIC, SignalKind.HEARTBEAT):
        return dict(emit_defaults().aggregation_method_by_source).get(
            monitor.source_system, "EVENT_FLOW"
        )
    return "EVENT_FLOW"


def aggregation_timer(monitor: IRMonitor) -> int:
    """Seconds EVENT_TIMER waits after the last point; the window, kept within bounds."""
    options = monitor.emit
    if options is not None and options.aggregation_timer_seconds is not None:
        return options.aggregation_timer_seconds
    low, high = TIMER_SECONDS
    return min(max(aggregation_window(monitor), low), high)


def fill_settings(monitor: IRMonitor) -> tuple[str, float | None] | None:
    """(fill option, fill value) to emit, or None to leave New Relic's default (NONE)."""
    options = monitor.emit
    if options is not None and options.fill_option is not None:
        return options.fill_option, options.fill_value
    if options is not None and options.fill_value is not None:
        return "NONE", options.fill_value  # rejected by the fill check
    default = emit_defaults().count_fill
    return default if default is not None and monitor.signal.kind in COUNT_KINDS else None


def _check_terms(thresholds: list[Threshold]) -> None:
    if len(thresholds) > MAX_TERMS:
        raise CannotEmit(
            "more than two thresholds: New Relic allows one critical and one warning term"
        )
    if len({t.severity for t in thresholds}) != len(thresholds):
        raise CannotEmit("two thresholds have the same severity; New Relic allows one of each")


def _check_expiration(monitor: IRMonitor) -> None:
    minutes = monitor.no_data.timeout_minutes
    if monitor.no_data.notify and minutes:
        low, high = EXPIRATION_SECONDS
        if not low <= minutes * 60 <= high:
            raise CannotEmit(f"loss-of-signal timeout {minutes * 60}s is outside 30s-48h")


def _check_method(monitor: IRMonitor) -> None:
    method = streaming_method(monitor)
    if method == "EVENT_TIMER":
        low, high = TIMER_SECONDS
        timer = aggregation_timer(monitor)
        if not low <= timer <= high:
            raise CannotEmit(f"aggregation timer {timer}s is outside {low}s-20min")
        return
    low, high = DELAY_SECONDS[method]
    delay = aggregation_delay(monitor)
    if not low <= delay <= high:
        raise CannotEmit(f"aggregation delay {delay}s is outside {low}s-{high}s for {method}")


def _check_fill(monitor: IRMonitor) -> None:
    fill = fill_settings(monitor)
    if fill is None:
        return
    option, value = fill
    if option == "STATIC" and value is None:
        raise CannotEmit("fill option STATIC needs a fill value")
    if option != "STATIC" and value is not None:
        raise CannotEmit("a fill value needs fill option STATIC")


def _check_slide_by(monitor: IRMonitor, options: EmitOptions) -> None:
    slide = options.slide_by_seconds
    if slide is None:
        return
    agg = aggregation_window(monitor)
    if slide >= agg or agg % slide or slide < MIN_SLIDE_BY_SECONDS:
        raise CannotEmit(
            f"slide-by {slide}s must divide the {agg}s aggregation window and be smaller than "
            f"it (at least {MIN_SLIDE_BY_SECONDS}s)"
        )


def _check_text(options: EmitOptions) -> None:
    limit = options.violation_time_limit_seconds
    low, high = VIOLATION_TIME_LIMIT_SECONDS
    if limit is not None and not low <= limit <= high:
        raise CannotEmit(f"violation time limit {limit}s is outside 5min-30 days")
    title = options.title_template
    if title is not None and len(title) > MAX_TITLE_TEMPLATE:
        raise CannotEmit(
            f"title template is {len(title)} characters; New Relic allows {MAX_TITLE_TEMPLATE}"
        )
    url = options.runbook_url
    if url is not None and not RUNBOOK_URL.fullmatch(url):
        raise CannotEmit("runbook URL is not an http or https address")


def check_limits(monitor: IRMonitor) -> None:
    """Raise CannotEmit when a setting is outside what New Relic accepts."""
    _check_terms(monitor.thresholds)
    _check_expiration(monitor)
    _check_method(monitor)
    _check_fill(monitor)
    options = monitor.emit
    if options is None:
        return
    _check_slide_by(monitor, options)
    _check_text(options)
    if options.ignore_on_expected_termination and not (
        monitor.no_data.notify and monitor.no_data.timeout_minutes
    ):
        raise CannotEmit("ignore on expected termination needs a loss-of-signal timeout")


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
    check_limits(monitor)
    return window


def nrql_for(monitor: IRMonitor) -> str:
    emittable_window(monitor)
    try:
        return build_nrql(monitor, null_safe_default=emit_defaults().null_safe_count)
    except ValueError as exc:
        raise CannotEmit(str(exc)) from None


# Registers the remediation rules for the reasons above (it must come after CannotEmit).
importlib.import_module("importer.core.emit_limits_rules")
