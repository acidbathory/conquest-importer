"""Dynatrace workflows and SLOs to IR.

One IR monitor per workflow or SLO. Only one shape converts: a scheduled workflow that runs one
narrow DQL log count and notifies when the count passes a number. A Davis problem workflow is a
notification route, not a condition, and an SLO is not converted yet; both stay manual with steps.
Reasons name only fixed vocabulary and parsed numbers, never text from the export, so they are
safe to print. `translate` never raises.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

from importer.core.ids import source_id
from importer.core.ir import (
    Confidence,
    CountFilter,
    CountSignal,
    IRMonitor,
    Occurrences,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.sources.dynatrace.models import DtItem, DtSlo, DtTask, DtWorkflow

DQL_ACTION = "dynatrace.automations:execute-dql-query"
MIN_WINDOW_SECONDS = 60
MAX_WINDOW_SECONDS = 3600  # the longest count window New Relic evaluates
DEFAULT_WINDOW_SECONDS = 300
OPERATORS = {
    ">": Operator.ABOVE,
    ">=": Operator.ABOVE_OR_EQUALS,
    "<": Operator.BELOW,
    "<=": Operator.BELOW_OR_EQUALS,
}
# fragment of the action name -> label shown in reasons; first match wins
ACTION_LABELS = (
    ("email", "email"),
    ("slack", "slack"),
    ("pagerduty", "pagerduty"),
    ("jira", "jira"),
    ("msteams", "ms teams"),
    ("servicenow", "servicenow"),
    ("opsgenie", "opsgenie"),
    ("http", "http request"),
)
SECONDS_PER_UNIT = {"m": 60, "h": 3600}

# the only DQL accepted: fetch logs[, from:now() - N(m|h)] | filter <field> == "<value>" [and ...]
# | summarize <name> = count(). A value has no control characters and only the escapes \" and \\.
FETCH = re.compile(r"fetch\s+logs(?:\s*,\s*from:\s*now\(\)\s*-\s*(\d+)\s*([mh]))?")
TERM = r'([A-Za-z_][A-Za-z0-9_.]*)\s*==\s*"((?:[^"\\\x00-\x1f\x7f]|\\["\\])*)"'
FILTER = re.compile(rf"filter\s+{TERM}(?:\s+(?:and|AND)\s+{TERM})*")
SUMMARIZE = re.compile(r"summarize\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*count\(\)")
CONDITION = re.compile(
    r'\{\{\s*result\("([^"]+)"\)\["records"\]\[0\]\["([^"]+)"\]\s*(>=|<=|>|<)\s*'
    r"(-?\d+(?:\.\d+)?)\s*\}\}"
)
TIMEFRAME = re.compile(r"now(?:-\d+[smhdw])?")

DAVIS_REASON = "Davis problem notification workflow"
SLO_REASON = "SLO is not converted automatically"
INACTIVE_REASON = "the Dynatrace trigger was inactive"
DRAFT_REASON = "the workflow was a draft (not deployed)"
OTHER_TRIGGER = (
    "workflow trigger is a manual or other event trigger, not a schedule or a Davis problem"
)
SHAPE = "workflow tasks are not one DQL query plus notify tasks gated by a condition"
STATES = "workflow tasks run on a state of the query task other than OK"
NOT_NARROW = "DQL query is not the supported pattern (fetch logs, equality filters, one count)"
BAD_THRESHOLD = (
    "the notify condition expression has a threshold that is not a usable number "
    "(a finite number, not below 0)"
)
LONG_REASON = "{what} is longer than one hour, the longest count window New Relic evaluates"
# notes on a converted workflow, most specific first; the two generic ones go last
LOG_NOTE = (
    "the logs must be forwarded to New Relic with the same attribute names; the mapping of "
    "loglevel to level is not confirmed"
)
SCHEDULE_NOTE = (
    "Dynatrace ran this on a schedule; New Relic evaluates continuously over {window}s "
    "windows, so the timing may differ"
)
BELOW_NOTE = (
    "below: an empty result is 0 in Dynatrace but no data in New Relic, so it may differ and "
    "not fire without loss of signal"
)
NOT_INTERVAL_NOTE = (
    "the schedule is not an interval, so the window comes from the time range and may differ"
)
LOOKBACK_NOTE = (
    "the workflow ran every {interval}s but counts the last {window}s; the window is "
    "{window}s and may differ"
)
NO_RANGE_NOTE = (
    "there is no time range; Dynatrace's default range is not modelled, so the window is the "
    "schedule interval and may differ"
)


class _Fail(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class _Count:
    """What the narrow DQL pattern means: log filters, the summarize name, and the range."""

    filters: list[CountFilter]
    name: str
    lookback_seconds: int


def _key(identifier: str, name: str) -> str:
    """A stable key; exports can repeat an id (scrubbed copies do), so the name is part of it."""
    return f"{identifier}/{name}"


def _split_pipes(query: str) -> list[str]:
    """Split on `|` outside double-quoted strings."""
    parts: list[str] = []
    current: list[str] = []
    quoted = escaped = False
    for char in query:
        if char == "|" and not quoted:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
        if escaped:
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
    parts.append("".join(current).strip())
    return parts


def _unescape(value: str) -> str:
    """Undo the only two escapes the pattern allows (TERM rejects any other)."""
    return re.sub(r'\\(["\\])', r"\1", value)


def parse_dql(query: str) -> _Count:
    """Parse the narrow log-count pattern, or raise _Fail with a fixed reason."""
    parts = _split_pipes(query)
    if len(parts) != 3:
        raise _Fail(f"{NOT_NARROW}: it needs exactly a fetch, a filter and a summarize step")
    fetch = FETCH.fullmatch(parts[0])
    if fetch is None or (fetch[1] is not None and int(fetch[1]) == 0):
        raise _Fail(f"{NOT_NARROW}: the first step is not fetch logs with an hour or minute range")
    if not FILTER.fullmatch(parts[1]):
        raise _Fail(
            f'{NOT_NARROW}: the filter has a test other than field == "value" joined by and, '
            "or a value with a control character or an escape other than a quote or backslash"
        )
    summarize = SUMMARIZE.fullmatch(parts[2])
    if summarize is None:
        raise _Fail(f"{NOT_NARROW}: the last step is not a single summarize name = count()")
    filters = [
        CountFilter(key=field, value=_unescape(value))
        for field, value in re.findall(TERM, parts[1])
    ]
    amount, unit = fetch.groups()
    seconds = int(amount) * SECONDS_PER_UNIT[unit] if amount else 0
    return _Count(filters, summarize[1], seconds)


def _gate_ok(task: DtTask, query_task: str) -> bool:
    """True when the task runs only if the query task succeeded (no states, or exactly OK)."""
    states = task.conditions.get("states")
    return not states or states == {query_task: "OK"}


def _threshold(tasks: dict[str, DtTask], query_task: str, name: str) -> tuple[Operator, float]:
    """The one comparison every notify task is gated by, or raise _Fail."""
    notify = [task for key, task in tasks.items() if key != query_task]
    if not notify:
        raise _Fail(f"{SHAPE}: there is no notify task")
    found: set[tuple[str, str]] = set()
    for task in notify:
        match = CONDITION.fullmatch(str(task.conditions.get("custom", "")).strip())
        if match is None or match[1] != query_task or match[2] != name:
            raise _Fail(
                "the notify condition expression is not a comparison of the query's count "
                "against a number (>, >=, < or <=)"
            )
        if not _gate_ok(task, query_task):
            raise _Fail(STATES)
        found.add((match[3], match[4]))
    if len(found) > 1:
        raise _Fail("the notify condition expression differs between notify tasks")
    ((symbol, number),) = found
    value = float(number)
    if not math.isfinite(value) or value < 0:
        raise _Fail(BAD_THRESHOLD)
    return OPERATORS[symbol], value


def _window(schedule: dict[str, Any], lookback: int) -> tuple[int, list[str]]:
    """The count window in seconds and the notes about it.

    The query's own range decides the window, because the threshold counts that many seconds of
    logs. Without a range the schedule interval is used.
    """
    trigger = schedule.get("trigger")
    minutes = trigger.get("intervalMinutes") if isinstance(trigger, dict) else None
    interval = (
        minutes * 60
        if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0
        else 0
    )
    notes: list[str] = []
    if not interval:
        notes.append(NOT_INTERVAL_NOTE)
    if lookback:
        window = lookback
        if interval and interval != window:
            notes.append(LOOKBACK_NOTE.format(interval=interval, window=window))
        what = f"the query's time range of {window}s"
    else:
        window = interval or DEFAULT_WINDOW_SECONDS
        notes.append(NO_RANGE_NOTE)
        what = f"the schedule interval of {window}s"
    if window > MAX_WINDOW_SECONDS:
        raise _Fail(LONG_REASON.format(what=what))
    return max(MIN_WINDOW_SECONDS, window), notes


def action_label(action: str) -> str:
    """A fixed label for a task action (never the action text itself)."""
    lowered = action.lower()
    return next((name for part, name in ACTION_LABELS if part in lowered), "other")


def _labels(tasks: dict[str, DtTask]) -> list[str]:
    """Fixed labels for the notify actions, in order and without repeats."""
    labels: list[str] = []
    for task in tasks.values():
        if task.action == DQL_ACTION:
            continue
        label = action_label(task.action)
        if label not in labels:
            labels.append(label)
    return labels


def _number(value: object) -> str:
    """A plain number for a reason, or "" for anything else (huge, nan, inf, not a number)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return ""
    try:
        number = float(value)
    except OverflowError:
        return ""
    return f"{value:g}" if math.isfinite(number) else ""


def _criteria(criteria: dict[str, Any]) -> str:
    parts = [f"target {_number(criteria.get('target'))}".strip()]
    if warning := _number(criteria.get("warning")):
        parts.append(f"warning {warning}")
    start, end = criteria.get("timeframeFrom"), criteria.get("timeframeTo")
    if all(isinstance(t, str) and TIMEFRAME.fullmatch(t) for t in (start, end)):
        parts.append(f"timeframe {start} to {end}")
    return "SLO criteria: " + ", ".join(parts)


def _monitor(
    key: str, source_type: str, name: str, confidence: Confidence, reasons: list[str], **rest: Any
) -> IRMonitor:
    rest.setdefault("signal", Signal(kind=SignalKind.UNKNOWN, raw_query=name))
    return IRMonitor(
        source_system="dynatrace",
        source_id=source_id(key),
        source_key=key,
        source_type=source_type,
        name=name,
        confidence=confidence,
        reasons=reasons,
        **rest,
    )


def _active(trigger: dict[str, Any]) -> bool:
    for kind in ("schedule", "eventTrigger"):
        part = trigger.get(kind)
        if isinstance(part, dict):
            return part.get("isActive") is not False
    return True


def _scheduled(wf: DtWorkflow, key: str, schedule: dict[str, Any]) -> IRMonitor:
    queries = [(name, task) for name, task in wf.tasks.items() if task.action == DQL_ACTION]
    if len(queries) != 1:
        raise _Fail(f"{SHAPE}: it has {len(queries)} DQL tasks")
    name, task = queries[0]
    count = parse_dql(str(task.input.get("query", "")))
    operator, value = _threshold(wf.tasks, name, count.name)
    window, notes = _window(schedule, count.lookback_seconds)
    reasons = [BELOW_NOTE] if operator in (Operator.BELOW, Operator.BELOW_OR_EQUALS) else []
    return _monitor(
        key,
        "workflow",
        wf.title,
        Confidence.APPROXIMATE,
        [*reasons, *notes],
        signal=Signal(
            kind=SignalKind.LOG,
            raw_query=wf.title,
            window_seconds=window,
            aggregation_window_seconds=window,
            occurrences=Occurrences.ALL,
            count=CountSignal(filters=count.filters),
        ),
        thresholds=[Threshold(severity=Severity.CRITICAL, operator=operator, value=value)],
    )


def davis_config(wf: DtWorkflow) -> dict[str, Any] | None:
    """The Davis problem trigger's settings (its `value`), or None for any other trigger."""
    event = wf.trigger.get("eventTrigger")
    config = event.get("triggerConfiguration") if isinstance(event, dict) else None
    if not isinstance(config, dict) or config.get("type") != "davis-problem":
        return None
    value = config.get("value")
    return value if isinstance(value, dict) else {}


def _route(wf: DtWorkflow, key: str) -> IRMonitor:
    schedule = wf.trigger.get("schedule")
    if isinstance(schedule, dict):
        return _scheduled(wf, key, schedule)
    if davis_config(wf) is not None:
        return _monitor(key, "workflow", wf.title, Confidence.MANUAL, [DAVIS_REASON])
    raise _Fail(OTHER_TRIGGER)


def translate_workflow(wf: DtWorkflow, key: str | None = None) -> IRMonitor:
    key = key or _key(wf.id, wf.title)
    try:
        monitor = _route(wf, key)
    except _Fail as fail:
        monitor = _monitor(key, "workflow", wf.title, Confidence.MANUAL, [fail.reason])
    if labels := _labels(wf.tasks):
        monitor.reasons.append(
            f"notification tasks ({', '.join(labels)}) must be configured manually"
        )
    if not _active(wf.trigger):
        monitor.reasons.append(INACTIVE_REASON)
        monitor.unmapped["enabled"] = False
    if not wf.isDeployed:
        monitor.reasons.append(DRAFT_REASON)
    if monitor.confidence is Confidence.APPROXIMATE:  # the generic notes go last
        window = monitor.signal.window_seconds
        monitor.reasons += [LOG_NOTE, SCHEDULE_NOTE.format(window=window)]
    return monitor


def translate_slo(slo: DtSlo, key: str | None = None) -> IRMonitor:
    reasons = [SLO_REASON, *(_criteria(c) for c in slo.criteria)]
    return _monitor(key or _key(slo.id, slo.name), "slo", slo.name, Confidence.MANUAL, reasons)


def translate(items: list[DtItem]) -> list[IRMonitor]:
    """One monitor per item; a repeated id and name gets an index so ids stay distinct."""
    seen: dict[str, int] = {}
    monitors: list[IRMonitor] = []
    for item in items:
        base = _key(item.id, item.title if isinstance(item, DtWorkflow) else item.name)
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}#{seen[base]}"
        monitors.append(
            translate_workflow(item, key)
            if isinstance(item, DtWorkflow)
            else translate_slo(item, key)
        )
    return monitors
