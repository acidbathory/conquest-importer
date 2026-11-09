"""Dynatrace workflows and SLOs to IR.

One IR monitor per workflow or SLO. Only one shape converts: a scheduled workflow that runs one
narrow DQL log count and notifies when the count passes a number. A Davis problem workflow is a
notification route, not a condition, and an SLO is not converted yet; both stay manual with steps.
Reasons name only fixed vocabulary and parsed numbers, never text from the export, so they are
safe to print. `translate` never raises.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Any

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
# | summarize <name> = count()
FETCH = re.compile(r"fetch\s+logs(?:\s*,\s*from:\s*now\(\)\s*-\s*(\d+)\s*([mh]))?")
TERM = r'([A-Za-z_][A-Za-z0-9_.]*)\s*==\s*"((?:[^"\\]|\\.)*)"'
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
OTHER_TRIGGER = (
    "workflow trigger is a manual or other event trigger, not a schedule or a Davis problem"
)
SHAPE = "workflow tasks are not one DQL query plus notify tasks gated by a condition"
NOT_NARROW = "DQL query is not the supported pattern (fetch logs, equality filters, one count)"
LOG_NOTE = (
    "converts to a count of Log events, so the logs must be forwarded to New Relic; the "
    "attribute names must match the forwarded logs, and the mapping of a Dynatrace attribute "
    "such as loglevel to New Relic's level is not confirmed"
)
SCHEDULE_NOTE = (
    "the Dynatrace workflow ran on a schedule, while New Relic evaluates continuously in "
    "windows of {window}s, so the moment it fires may differ"
)
BELOW_NOTE = (
    "a below condition: Dynatrace counts an empty result as 0, but in New Relic no matching "
    "logs means no data, so it may differ and not fire unless loss of signal is set"
)
NOT_INTERVAL_NOTE = (
    "the schedule is not an interval, so the window is taken from the query's time range"
)
LOOKBACK_NOTE = (
    "the query looks back {lookback}s but the workflow ran every {window}s; the New Relic "
    "window is {window}s and may differ"
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


def source_id(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16)


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
    return re.sub(r"\\(.)", r"\1", value)


def parse_dql(query: str) -> _Count:
    """Parse the narrow log-count pattern, or raise _Fail with a fixed reason."""
    parts = _split_pipes(query)
    if len(parts) != 3:
        raise _Fail(f"{NOT_NARROW}: it needs exactly a fetch, a filter and a summarize step")
    fetch = FETCH.fullmatch(parts[0])
    if fetch is None:
        raise _Fail(f"{NOT_NARROW}: the first step is not fetch logs with an hour or minute range")
    if not FILTER.fullmatch(parts[1]):
        raise _Fail(
            f'{NOT_NARROW}: the filter has a test other than field == "value" joined by and'
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
        found.add((match[3], match[4]))
    if len(found) > 1:
        raise _Fail("the notify condition expression differs between notify tasks")
    ((symbol, number),) = found
    return OPERATORS[symbol], float(number)


def _window(schedule: dict[str, Any], lookback: int) -> tuple[int, list[str]]:
    """The count window in seconds (whole minutes, from the interval) and notes about it."""
    trigger = schedule.get("trigger")
    minutes = trigger.get("intervalMinutes") if isinstance(trigger, dict) else None
    notes: list[str] = []
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        window = minutes * 60
        if lookback and lookback != window:
            notes.append(LOOKBACK_NOTE.format(lookback=lookback, window=window))
    else:
        window = lookback or DEFAULT_WINDOW_SECONDS
        notes.append(NOT_INTERVAL_NOTE)
    window = max(MIN_WINDOW_SECONDS, -(-window // 60) * 60)
    if window > MAX_WINDOW_SECONDS:
        raise _Fail(
            f"the schedule interval of {window}s is longer than one hour, the longest count "
            "window New Relic evaluates"
        )
    return window, notes


def _labels(tasks: dict[str, DtTask]) -> list[str]:
    """Fixed labels for the notify actions, in order and without repeats."""
    labels: list[str] = []
    for task in tasks.values():
        if task.action == DQL_ACTION:
            continue
        action = task.action.lower()
        label = next((name for part, name in ACTION_LABELS if part in action), "other")
        if label not in labels:
            labels.append(label)
    return labels


def _number(value: object) -> str:
    ok = isinstance(value, int | float) and not isinstance(value, bool)
    return f"{value:g}" if ok else ""


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


def _scheduled(wf: DtWorkflow, schedule: dict[str, Any]) -> IRMonitor:
    queries = [(key, task) for key, task in wf.tasks.items() if task.action == DQL_ACTION]
    if len(queries) != 1:
        raise _Fail(f"{SHAPE}: it has {len(queries)} DQL tasks")
    key, task = queries[0]
    count = parse_dql(str(task.input.get("query", "")))
    operator, value = _threshold(wf.tasks, key, count.name)
    window, notes = _window(schedule, count.lookback_seconds)
    reasons = [LOG_NOTE, SCHEDULE_NOTE.format(window=window), *notes]
    if operator in (Operator.BELOW, Operator.BELOW_OR_EQUALS):
        reasons.append(BELOW_NOTE)
    return _monitor(
        _key(wf.id, wf.title),
        "workflow",
        wf.title,
        Confidence.APPROXIMATE,
        reasons,
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


def _route(wf: DtWorkflow) -> IRMonitor:
    trigger = wf.trigger
    schedule = trigger.get("schedule")
    event = trigger.get("eventTrigger")
    if isinstance(schedule, dict):
        return _scheduled(wf, schedule)
    config = event.get("triggerConfiguration") if isinstance(event, dict) else None
    if isinstance(config, dict) and config.get("type") == "davis-problem":
        return _monitor(
            _key(wf.id, wf.title), "workflow", wf.title, Confidence.MANUAL, [DAVIS_REASON]
        )
    raise _Fail(OTHER_TRIGGER)


def translate_workflow(wf: DtWorkflow) -> IRMonitor:
    try:
        monitor = _route(wf)
    except _Fail as fail:
        monitor = _monitor(
            _key(wf.id, wf.title), "workflow", wf.title, Confidence.MANUAL, [fail.reason]
        )
    if labels := _labels(wf.tasks):
        monitor.reasons.append(
            f"notification tasks ({', '.join(labels)}) must be configured manually"
        )
    if not _active(wf.trigger):
        monitor.reasons.append(INACTIVE_REASON)
        monitor.unmapped["enabled"] = False
    return monitor


def translate_slo(slo: DtSlo) -> IRMonitor:
    reasons = [SLO_REASON, *(_criteria(c) for c in slo.criteria)]
    return _monitor(_key(slo.id, slo.name), "slo", slo.name, Confidence.MANUAL, reasons)


def translate(items: list[DtItem]) -> list[IRMonitor]:
    return [translate_workflow(i) if isinstance(i, DtWorkflow) else translate_slo(i) for i in items]
