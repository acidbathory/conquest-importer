"""A condition on a log-based metric as a count of Log events, using the metric's own log filter.

A log-based metric counts the log entries that match its filter (a counter), or records a value
pulled out of each entry (a distribution, or a counter with a value extractor). Only the first is a
count of Log events. The filter grammar is the one `log_scope.py` reads for log-match conditions,
so what that cannot say stays manual here with the same reason.

Threshold units: the metric holds one count per minute. ALIGN_DELTA and ALIGN_SUM add the minutes
of the window, which is the count in the window; ALIGN_RATE is per second, so the threshold is
multiplied by the window; ALIGN_MEAN is the mean per minute, multiplied by the minutes in the
window. UNVERIFIED against live data: the attribute names of forwarded logs.
"""

import re
from typing import Any

from importer.core.ir import (
    Confidence,
    CountFilter,
    CountSignal,
    Occurrences,
    Signal,
    SignalKind,
    TagFilter,
)
from importer.sources.gcp import datapath
from importer.sources.gcp.extras import GcpExtras
from importer.sources.gcp.log_scope import log_filters
from importer.sources.gcp.plus import Built
from importer.sources.gcp.scope import ScopeFail, metric_filter

EXTRACT = re.compile(r"EXTRACT\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)")
COUNT_ALIGNERS = {
    "ALIGN_DELTA": "sum",
    "ALIGN_SUM": "sum",
    "ALIGN_RATE": "rate",
    "ALIGN_MEAN": "mean",
}
SHAPE_NOTE = (
    "counts the log entries that match the metric's filter in each window ({filter}); the "
    "project's logs must be forwarded to New Relic and the attribute names of forwarded Google "
    "Cloud logs are not confirmed"
)


def _attribute(metric: dict[str, Any], label: str) -> str:
    """The log field behind a metric label, when it is a plain EXTRACT(field)."""
    from importer.sources.gcp.translate import Fail

    expression = str(metric.get("labelExtractors", {}).get(label, ""))
    found = EXTRACT.fullmatch(expression)
    if found is None:
        shown = expression[:80] or "none"
        raise Fail(
            Confidence.MANUAL,
            f"the label {label!r} of the log-based metric is computed by {shown!r}, which a "
            "Log attribute cannot reproduce; extract that value at ingest (a parsing rule) and "
            "filter or facet on the new attribute",
        )
    return found.group(1)


def _distribution_workaround(name: str, metric: dict[str, Any]) -> str:
    extractor = str(metric.get("valueExtractor", ""))[:120] or "no extractor"
    return (
        f"the log-based metric {name!r} records a value from each log entry ({extractor}), not "
        "a count; once the logs are forwarded, New Relic can compute it with a query such as "
        "percentile(<attribute>, 95) or sum(<attribute>) on Log events that match the metric's "
        "log filter, <attribute> being the field named in the extractor"
    )


def convert(extras: GcpExtras, name: str, body: dict[str, object]) -> Built | None:
    from importer.sources.gcp.translate import (
        OPERATORS,
        Fail,
        _check_limits,
        _first_aggregation,
        _threshold_trigger,
        _whole_windows,
        period_of,
        seconds,
        whole_minutes,
    )

    if extras.log_metrics is None:
        return None
    metric = extras.log_metrics.get(name)
    if metric is None:
        raise Fail(
            Confidence.MANUAL,
            f"the log-based metric {name!r} is not in the export's log-metrics (deleted, or "
            "defined in another project); re-run extract-gcp for the project that owns it",
        )
    if metric.get("disabled"):
        raise Fail(Confidence.MANUAL, f"the log-based metric {name!r} is disabled in Google")
    if metric.get("valueType") == "DISTRIBUTION" or metric.get("valueExtractor"):
        raise Fail(Confidence.MANUAL, _distribution_workaround(name, metric))
    try:
        filters, notes = log_filters(str(metric.get("filter", "")))
        scope = metric_filter(str(body.get("filter", "")))
    except ScopeFail as err:
        raise Fail(Confidence.MANUAL, f"log-based metric {name!r}: {err}") from err
    filters = [*filters, *_label_filters(metric, scope.filters)]
    agg, aligner = _first_aggregation(body)
    mode = COUNT_ALIGNERS.get(aligner)
    if mode is None:
        raise Fail(
            Confidence.MANUAL,
            f"the aligner {aligner or 'none'} on a log-based counter is not a count of log "
            "entries; use ALIGN_DELTA, ALIGN_SUM, ALIGN_RATE or ALIGN_MEAN",
        )
    reducer = str(agg.get("crossSeriesReducer") or "REDUCE_NONE")
    if reducer not in ("REDUCE_NONE", "REDUCE_SUM"):
        raise Fail(Confidence.MANUAL, f"the reducer {reducer} on log counts is not supported")
    group_by = _group_by(metric, agg)
    notes = [*notes, *_threshold_trigger(body, agg), *scope.notes]
    period, period_note = whole_minutes(period_of(agg))
    window, window_note = _whole_windows(seconds(body.get("duration")), period)
    _check_limits(period, window)
    notes += [n for n in (period_note, window_note) if n]
    scale = {"sum": 1.0, "rate": float(period), "mean": period / 60}[mode]
    if mode != "sum":
        notes.append(
            f"the threshold is multiplied by {scale:g} because {aligner} is "
            f"{'per second' if mode == 'rate' else 'the mean per minute'} and New Relic counts "
            f"the entries in the whole window of {period}s"
        )
    if reducer == "REDUCE_NONE" and not group_by:
        notes.append(
            "Google evaluates each time series of the metric (one per resource) on its own; "
            "this counts the matching entries of all resources together, so group by the "
            "resource attribute to alert for each one once the attribute name is known"
        )
    notes.append(SHAPE_NOTE.format(filter=_short(str(metric.get("filter", "")))))
    operator = OPERATORS.get(str(body.get("comparison", "")))
    if operator is None:
        raise Fail(Confidence.MANUAL, f"comparison {body.get('comparison')!r} is not supported")
    signal = Signal(
        kind=SignalKind.LOG,
        raw_query=name,
        window_seconds=window,
        aggregation_window_seconds=period,
        occurrences=Occurrences.ALL,
        count=CountSignal(filters=filters, group_by=group_by),
    )
    value = float(str(body.get("thresholdValue", 0))) * scale
    return Built(signal, operator, value, notes, datapath.LOG_FORWARDING)


def _short(text: str) -> str:
    return " ".join(text.split())[:160]


def _label_filters(metric: dict[str, Any], tags: list[TagFilter]) -> list[CountFilter]:
    from importer.sources.gcp.translate import Fail

    out: list[CountFilter] = []
    for tag in tags:
        if not tag.key.startswith("metric."):
            raise Fail(
                Confidence.MANUAL,
                f"the condition filters on {tag.key!r}, a resource label; the attribute that "
                "carries it on forwarded Google Cloud logs is not confirmed, so add the "
                "equivalent WHERE term by hand",
            )
        key = _attribute(metric, tag.key.removeprefix("metric."))
        out.append(CountFilter(**{**tag.model_dump(exclude={"absent"}), "key": key}))
    return out


def _group_by(metric: dict[str, Any], agg: dict[str, object]) -> list[str]:
    from importer.sources.gcp.translate import Fail

    raw = agg.get("groupByFields")
    fields = raw if isinstance(raw, list) else []
    out: list[str] = []
    for field in map(str, fields):
        if not field.startswith("metric.label."):
            raise Fail(
                Confidence.MANUAL,
                f"group by {field!r} on a log-based metric needs the log attribute for that "
                "resource label, which is not confirmed for forwarded Google Cloud logs",
            )
        out.append(_attribute(metric, field.removeprefix("metric.label.")))
    return out
