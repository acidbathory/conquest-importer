"""Conditions that need a definition the alert policy does not carry.

Three kinds, each in its own module: log-based metrics (`plus_log_metrics`), SLO selectors
(`plus_slo`) and uptime checks (`plus_uptime`). `resolve` is the one hook the translator calls: it
returns None when the condition is not one of these or when the export has no definitions (an
older export), so the earlier behaviour is unchanged; a Fail means "manual, with this reason".

The modules import the translator's helpers inside their functions, because the translator
imports this one.
"""

import re
from dataclasses import dataclass, field

from importer.core.ir import Confidence, IRMonitor, Operator, Signal
from importer.sources.gcp.datapath import metric_types_in
from importer.sources.gcp.extras import GcpExtras

SLO_SELECTOR = re.compile(
    r"select_slo_(?P<kind>[a-z_]+)\(\s*\"(?P<name>[^\"]{1,300})\"\s*(?:,\s*\"(?P<arg>[^\"]{1,20})\"\s*)?\)"
)
LOG_METRIC_PREFIX = "logging.googleapis.com/user/"
UPTIME_PREFIX = "monitoring.googleapis.com/uptime_check/"


@dataclass
class Rewritten:
    """The condition body to translate instead (an SLO becomes a ratio of two metrics)."""

    body: dict[str, object]
    notes: list[str] = field(default_factory=list)
    data_path: str = ""


@dataclass
class Built:
    """A finished signal (a count of Log events, or a Synthetics check query)."""

    signal: Signal
    operator: Operator
    value: float
    notes: list[str]
    data_path: str = ""


def resolve(
    extras: GcpExtras | None, kind: str, body: dict[str, object]
) -> Rewritten | Built | None:
    if extras is None or kind != "conditionThreshold":
        return None
    from importer.sources.gcp import plus_log_metrics, plus_slo, plus_uptime

    text = str(body.get("filter", ""))
    if found := SLO_SELECTOR.search(text):
        return plus_slo.convert(extras, found, body)
    types = metric_types_in(text)
    if not types:
        return None
    if types[0].startswith(LOG_METRIC_PREFIX):
        return plus_log_metrics.convert(extras, types[0].removeprefix(LOG_METRIC_PREFIX), body)
    if types[0].startswith(UPTIME_PREFIX):
        return plus_uptime.convert(extras, types[0].removeprefix(UPTIME_PREFIX), body)
    return None


def finish(
    monitor: IRMonitor, extras: GcpExtras | None, kind: str, body: dict[str, object]
) -> None:
    """Set the data path of a converted condition that `resolve` handled (after tag_monitor)."""
    if extras is None or kind != "conditionThreshold":
        return
    if monitor.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE):
        return
    from importer.sources.gcp import datapath, plus_slo

    text = str(body.get("filter", ""))
    types = metric_types_in(text)
    if types and types[0].startswith(LOG_METRIC_PREFIX) and extras.log_metrics is not None:
        monitor.data_path = datapath.LOG_FORWARDING
        monitor.reasons = [r for r in monitor.reasons if not r.startswith("data path: unknown")]
    elif found := SLO_SELECTOR.search(text):
        path = plus_slo.data_path(extras, found)
        if path:
            monitor.data_path = path
            monitor.reasons = [r for r in monitor.reasons if not r.startswith("data path: unknown")]
