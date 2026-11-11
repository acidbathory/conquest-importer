"""Pieces every Datadog translator uses: the IR base, thresholds, timing, no-data, notes."""

import math
from dataclasses import dataclass, field
from typing import Any

from importer.core.ids import source_id
from importer.core.ir import (
    Confidence,
    IRMonitor,
    NoData,
    Notification,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.datadog import reasons as R
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.sanitize import (
    count_handles,
    mask_message,
    safe_tags,
    shown,
)

MIN_WINDOW = 60
MAX_WINDOW = 21600  # six hours, the NerdGraph aggregationWindow limit (see core/emit/common.py)
LONG_WINDOW = 3600
SLIDE = 60
MAX_DELAY = 1200  # evaluation delay New Relic accepts for streaming conditions
MAX_NO_DATA_MINUTES = 2880
OPERATORS = {
    ">": Operator.ABOVE,
    ">=": Operator.ABOVE_OR_EQUALS,
    "<": Operator.BELOW,
    "<=": Operator.BELOW_OR_EQUALS,
    "==": Operator.EQUALS,
    "!=": Operator.NOT_EQUALS,
}
# options Datadog has that no New Relic condition setting matches; they are listed, not migrated
UNMAPPED_OPTIONS = (
    "renotify_interval",
    "renotify_statuses",
    "renotify_occurrences",
    "escalation_message",
    "require_full_window",
    "new_group_delay",
    "new_host_delay",
    "group_retention_duration",
    "timeout_h",
    "notify_by",
    "notify_audit",
    "notification_preset_name",
    "silenced",
    "locked",
    "scheduling_options",
    "threshold_windows",
    "avalanche_window",
    "restriction_query",
    "include_tags",
    "enable_logs_sample",
    "enable_samples",
)
UNMAPPED_THRESHOLDS = ("critical_recovery", "warning_recovery", "ok", "unknown")


class Fail(Exception):
    """A part that cannot be converted; `reason` says why, `steps` are specific advice."""

    def __init__(self, reason: str, steps: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.steps = steps or []


def base_fields(m: DatadogMonitor) -> dict[str, Any]:
    key = m.source_key
    name = shown(m.name_text, 128) or f"Datadog monitor {shown(key, 60)}"
    return {
        "source_system": "datadog",
        "source_id": source_id(key),
        "source_key": key,
        "source_type": shown(m.type_text or "unknown", 60),
        "name": name,
        "tags": [f"datadog:{shown(m.type_text or 'unknown', 40)}", *safe_tags(m.tag_list)],
    }


def manual(
    base: dict[str, Any],
    reasons: list[str],
    steps: list[str] | None = None,
    *,
    confidence: Confidence = Confidence.MANUAL,
    unmapped: dict[str, Any] | None = None,
) -> IRMonitor:
    return IRMonitor(
        **base,
        signal=Signal(kind=SignalKind.UNKNOWN, raw_query=str(base["name"])),
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        remediation=steps or [],
        unmapped=unmapped or {},
    )


def number(value: object) -> float | None:
    """A finite JSON number (a bool is not one), else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass
class Thresholds:
    terms: list[Threshold]
    notes: list[str] = field(default_factory=list)


def thresholds(
    m: DatadogMonitor, op: str, query_value: float, scale: float, priority: int | None
) -> Thresholds:
    """Critical (from the query) and warning (from the options) terms, in New Relic units."""
    if op not in OPERATORS:
        raise Fail(R.M_OPERATOR)
    operator = OPERATORS[op]
    option = m.options_dict.get("thresholds")
    options = option if isinstance(option, dict) else {}
    notes: list[str] = []
    stated = number(options.get("critical"))
    if stated is not None and stated != query_value:
        notes.append(R.N_CRITICAL_DIFFERS)
    warning = number(options.get("warning"))
    severity = Severity.CRITICAL
    if priority is not None and priority >= 3 and warning is None:
        severity = Severity.WARNING
        notes.append(R.N_PRIORITY_WARNING.format(level=priority))
    elif priority is not None:
        notes.append(R.N_PRIORITY.format(level=priority))
    terms = [Threshold(severity=severity, operator=operator, value=query_value * scale)]
    if warning is not None:
        terms.append(Threshold(severity=Severity.WARNING, operator=operator, value=warning * scale))
    return Thresholds(terms, notes)


@dataclass
class Timing:
    window: int
    slide: int | None
    notes: list[str]


def timing(window: int) -> Timing:
    """Aggregation window, sliding step and the notes that go with them."""
    if window < MIN_WINDOW:
        raise Fail(R.M_WINDOW_SHORT)
    if window > MAX_WINDOW:
        raise Fail(R.M_WINDOW_LONG)
    notes: list[str] = []
    slide: int | None = None
    if window > LONG_WINDOW:
        notes += [R.N_LONG_WINDOW.format(window=window), R.N_NO_SLIDE.format(window=window)]
    elif window > MIN_WINDOW:
        slide = SLIDE
        notes.append(R.N_SLIDE)
    return Timing(window, slide, notes)


@dataclass
class Options:
    no_data: NoData
    delay: int | None
    notes: list[str]
    unmapped: dict[str, Any]


def _small(value: object) -> str:
    return shown(value, 80)


def options_for(m: DatadogMonitor, window: int, *, event_style: bool) -> Options:
    """Loss of signal, evaluation delay, and the options that are listed as not migrated."""
    opts = m.options_dict
    notes: list[str] = []
    missing = opts.get("on_missing_data")
    notify = opts.get("notify_no_data") is True or missing == "show_and_notify_no_data"
    no_data = NoData()
    if notify:
        stated = opts.get("no_data_timeframe")
        minutes = (
            int(stated)
            if isinstance(stated, int | float) and not isinstance(stated, bool) and stated >= 1
            else max(1, -(-2 * window // 60))  # documented default: twice the window
        )
        if minutes > MAX_NO_DATA_MINUTES:
            notes.append(R.N_NO_DATA_CLAMPED.format(minutes=minutes))
            minutes = MAX_NO_DATA_MINUTES
        no_data = NoData(notify=True, timeout_minutes=minutes)
        notes.append(R.N_NO_DATA.format(minutes=minutes))
    elif event_style and missing == "resolve":
        notes.append(R.N_RESOLVE_NO_DATA.format(value=missing))
    delay: int | None = None
    stated_delay = opts.get("evaluation_delay")
    if isinstance(stated_delay, int | float) and not isinstance(stated_delay, bool):
        if stated_delay > MAX_DELAY:
            notes.append(R.N_DELAY_CLAMPED.format(delay=int(stated_delay), limit=MAX_DELAY))
            delay = MAX_DELAY
        elif stated_delay > 0:
            delay = int(stated_delay)
    unmapped: dict[str, Any] = {}
    for key in UNMAPPED_OPTIONS:
        value = opts.get(key)
        if value not in (None, False, "", 0, [], {}):
            unmapped[key] = _small(value)
    th = opts.get("thresholds")
    if isinstance(th, dict):
        for key in UNMAPPED_THRESHOLDS:
            if th.get(key) is not None:
                unmapped[f"thresholds.{key}"] = _small(th[key])
    if missing not in (None, "default", "show_no_data", "show_and_notify_no_data", "resolve"):
        unmapped["on_missing_data"] = _small(missing)
    return Options(no_data, delay, notes, unmapped)


def message_parts(m: DatadogMonitor) -> tuple[Notification, str, list[str]]:
    """The masked message (for the IR and the description) and the notes about its handles."""
    message = m.message_text
    masked = mask_message(message)
    notes: list[str] = []
    handles = count_handles(message)
    if handles:
        notes.append(R.N_MESSAGE_HANDLES.format(count=handles))
    return Notification(message_template=masked), (f"message: {masked}" if masked else ""), notes


def priority_note(priority: int | None) -> dict[str, Any]:
    """The one-line fact the description starts with (`unmapped["description_note"]`)."""
    return {"description_note": f"Datadog priority P{priority}"} if priority is not None else {}
