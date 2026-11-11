"""Datadog monitors to IR monitors: one IR monitor per Datadog monitor.

`translate` never raises. Converted: `query alert` plain thresholds (and arithmetic between series)
and `log alert` counts, both as approximate static conditions (a query-bearing object is never
exact). Every other monitor type is manual or unsupported with a reason that names what Datadog
does and, where an approximate conversion is planned, the milestone of
docs/research/datadog-verified.md section 14.1. Nothing here talks to Datadog or New Relic.
"""

from importer.core.ids import source_id
from importer.core.ir import Confidence, IRMonitor
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import base_fields, manual, message_parts
from importer.sources.datadog.logs import translate_log
from importer.sources.datadog.metric import translate_metric
from importer.sources.datadog.models import DatadogMonitor

# API monitor type -> the reason it is not converted. A type missing here is unknown.
MANUAL_TYPES = {
    "composite": R.M_COMPOSITE,
    "service check": R.M_SERVICE_CHECK,
    "event alert": R.M_EVENT,
    "event-v2 alert": R.M_EVENT,
    "slo alert": R.M_SLO,
    "synthetics alert": R.M_SYNTHETICS,
    "process alert": R.M_PROCESS,
    "rum alert": R.M_RUM,
    "trace-analytics alert": R.M_TRACE,
    "ci-pipelines alert": R.M_CI,
    "ci-tests alert": R.M_CI,
    "audit alert": R.M_AUDIT,
    "error-tracking alert": R.M_ERROR_TRACKING,
    "database-monitoring alert": R.M_DATABASE,
    "network-performance alert": R.M_NETWORK,
    "network-path alert": R.M_NETWORK,
    "cost alert": R.M_COST,
    "data-quality alert": R.M_DATA_OBSERVABILITY,
    "data-jobs alert": R.M_DATA_OBSERVABILITY,
    "llm-observability alert": R.M_LLM,
}
METRIC_TYPES = ("query alert", "metric alert")


def _other(m: DatadogMonitor) -> IRMonitor:
    base = base_fields(m)
    kind = m.type_text
    if not kind:
        return manual(base, [R.M_NO_TYPE], confidence=Confidence.UNSUPPORTED)
    reason = MANUAL_TYPES.get(kind)
    if reason is None:
        return manual(base, [R.M_UNKNOWN_TYPE], confidence=Confidence.UNSUPPORTED)
    _, _, notes = message_parts(m)
    return manual(base, [reason, *notes])


def translate_monitor(m: DatadogMonitor) -> IRMonitor:
    kind = m.type_text
    if kind in METRIC_TYPES:
        return translate_metric(m)
    if kind == "log alert":
        return translate_log(m)
    return _other(m)


def translate(monitors: list[DatadogMonitor]) -> list[IRMonitor]:
    """All monitors; a repeated source key gets an index so ids and file names stay distinct."""
    seen: dict[str, int] = {}
    out: list[IRMonitor] = []
    for monitor in monitors:
        result = translate_monitor(monitor)
        count = seen.get(result.source_key, 0) + 1
        seen[result.source_key] = count
        if count > 1:
            key = f"{result.source_key}#{count}"
            result = result.model_copy(update={"source_key": key, "source_id": source_id(key)})
        out.append(result)
    return out
