"""Write the notification models back in the API shape the loaders read."""

from typing import Any

from importer.sources.grafana.notifications.models import MuteTiming, PolicyNode, TimeInterval

UNREADABLE_MATCHER = (
    "?"  # reads back as a matcher that cannot be parsed, so the node stays undecidable
)


def policy_to_payload(node: PolicyNode) -> dict[str, Any]:
    body: dict[str, Any] = {"receiver": node.receiver}
    if node.group_by is not None:
        body["group_by"] = node.group_by
    triples = [[m.name, m.op, m.value] for m in node.matchers]
    if triples:
        body["object_matchers"] = triples
    if node.unparsed_matchers:
        body["matchers"] = [UNREADABLE_MATCHER] * node.unparsed_matchers
    if node.continue_:
        body["continue"] = True
    intervals = {
        "mute_time_intervals": node.mute_time_intervals,
        "active_time_intervals": node.active_time_intervals,
    }
    timings = {
        "group_wait": node.group_wait_seconds,
        "group_interval": node.group_interval_seconds,
        "repeat_interval": node.repeat_interval_seconds,
    }
    body.update({k: v for k, v in intervals.items() if v})
    body.update({k: f"{v}s" for k, v in timings.items() if v})
    if node.routes:
        body["routes"] = [policy_to_payload(r) for r in node.routes]
    return body


def _interval_payload(interval: TimeInterval) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if interval.times:
        body["times"] = [{"start_time": t.start, "end_time": t.end} for t in interval.times]
    for key in ("weekdays", "days_of_month", "months", "years"):
        values = getattr(interval, key)
        if values:
            body[key] = values
    if interval.location:
        body["location"] = interval.location
    return body


def mute_timings_to_payload(timings: list[MuteTiming]) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "time_intervals": [_interval_payload(i) for i in t.intervals]}
        for t in timings
    ]
