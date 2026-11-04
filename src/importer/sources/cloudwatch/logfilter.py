"""CloudWatch Logs metric filters: read them, translate simple patterns, link alarms to them.

A metric filter turns matching log events into a CloudWatch metric (for example "ERROR" lines into
`Custom/app ErrorCount`). An alarm on that metric is really an alarm on a log query, so in New
Relic it becomes a count of matching `Log` events instead of a metric condition.

Only patterns that translate faithfully are converted: plain terms (all required, or excluded with
a leading `-`), quoted phrases, and JSON patterns that compare string fields with `=` or `!=`
joined by `&&`. OR terms, numeric comparisons, space-delimited patterns and anything else become
manual work with the reason attached. Pattern text is untrusted: values reach NRQL only through
the usual escaping in the emitter.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.core.ir import CountFilter
from importer.sources.cloudwatch.models import CloudWatchAlarm, LogMetricRef

FILE_NAME = "metric-filters.json"
LOG_GROUP_ATTRIBUTE = "logGroup"  # attribute New Relic adds to logs forwarded from CloudWatch Logs
# The NRQL emitter renames these attribute keys for other log sources; do not let them through.
RESERVED_KEYS = {"host", "status"}
_JSON_CONDITION = re.compile(r'^\$\.([A-Za-z_][A-Za-z0-9_.]*)\s*(!=|=)\s*"([^"\\]*)"$')
_BAD_CHARS = re.compile(r"[%\\\x00-\x1f]")
MAX_TERM = 200


class UnsupportedPattern(Exception):
    """The pattern cannot be translated faithfully; the message says why."""


@dataclass
class Parsed:
    filters: list[CountFilter]


def _term_filter(text: str, negate: bool) -> CountFilter:
    if not text or len(text) > MAX_TERM or _BAD_CHARS.search(text):
        raise UnsupportedPattern("a term is empty, too long or contains % or a backslash")
    if "*" in text:
        raise UnsupportedPattern("wildcards inside a term are not converted")
    return CountFilter(key="message", value=f"*{text}*", negate=negate, wildcard=True)


def _unstructured(pattern: str) -> list[CountFilter]:
    filters: list[CountFilter] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i].isspace():
            i += 1
            continue
        negate = False
        if pattern[i] == "?":
            raise UnsupportedPattern("OR terms (?term) are not converted")
        if pattern[i] == "-":
            negate, i = True, i + 1
        if i < n and pattern[i] == '"':
            end = pattern.find('"', i + 1)
            if end == -1:
                raise UnsupportedPattern("unbalanced quote in the pattern")
            text, i = pattern[i + 1 : end], end + 1
        else:
            start = i
            while i < n and not pattern[i].isspace():
                i += 1
            text = pattern[start:i]
        filters.append(_term_filter(text, negate))
    return filters


def _json_pattern(pattern: str) -> list[CountFilter]:
    body = pattern.strip()[1:-1].strip()
    if "||" in body or "(" in body or "[" in body:
        raise UnsupportedPattern(
            "OR, grouping and array conditions in JSON patterns are not converted"
        )
    filters: list[CountFilter] = []
    for part in body.split("&&"):
        match = _JSON_CONDITION.match(part.strip())
        if not match:
            raise UnsupportedPattern(
                "only string comparisons with = or != are converted; numbers, booleans, "
                "IS NULL and other operators need a hand-written query"
            )
        key, op, value = match.groups()
        if key in RESERVED_KEYS:
            raise UnsupportedPattern(f"the field name {key!r} is not converted")
        if _BAD_CHARS.search(value) or len(value) > MAX_TERM:
            raise UnsupportedPattern("a value contains % or a backslash, or is too long")
        filters.append(CountFilter(key=key, value=value, negate=op == "!=", wildcard="*" in value))
    return filters


def parse_pattern(pattern: str) -> Parsed:
    """Translate a CloudWatch filter pattern into New Relic log filters, or say why not."""
    text = pattern.strip()
    if not text:
        return Parsed([])  # an empty pattern matches every event in the group
    if text.startswith("{") and text.endswith("}"):
        return Parsed(_json_pattern(text))
    if text.startswith("["):
        raise UnsupportedPattern("space-delimited patterns are not converted")
    return Parsed(_unstructured(text))


def load_metric_filters(folder: Path) -> list[dict[str, Any]]:
    """Read `metric-filters.json` from an export's `logs` folder; [] if it is not there."""
    path = folder / FILE_NAME
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    filters = data.get("metricFilters", []) if isinstance(data, dict) else []
    return [f for f in filters if isinstance(f, dict)]


def attach_log_filters(alarms: list[CloudWatchAlarm], filters: list[dict[str, Any]]) -> None:
    """Link each alarm to the metric filters that publish the metric it watches."""
    by_metric: dict[tuple[str, str], list[LogMetricRef]] = {}
    for f in filters:
        for t in f.get("metricTransformations", []) or []:
            ref = LogMetricRef(
                filter_name=str(f.get("filterName", "")),
                log_group=str(f.get("logGroupName", "")),
                pattern=str(f.get("filterPattern", "")),
                value=str(t.get("metricValue", "")),
                has_dimensions=bool(t.get("dimensions")),
            )
            by_metric.setdefault(
                (str(t.get("metricNamespace", "")), str(t.get("metricName", ""))), []
            ).append(ref)
    for alarm in alarms:
        if alarm.Namespace and alarm.MetricName:
            alarm.log_metric_filters = by_metric.get((alarm.Namespace, alarm.MetricName), [])
