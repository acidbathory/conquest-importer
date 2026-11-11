"""Alert conditions on an uptime check as a query on New Relic Synthetics results.

A Google uptime check run becomes a `SyntheticCheck` event once the monitor exists in New Relic
(the plan is `synthetic-monitor-plan.json`, written by `convert-gcp-uptime`; nothing is created
automatically). `check_passed` is false when a location failed, so a count of failed runs per
window stands for the Google count of failing locations; `request_latency` is the `duration` of
the run in milliseconds.

UNVERIFIED (not run against a live account): the attribute names `monitorName`, `result`
(`SUCCESS` or `FAILED`), `location` and `duration` of SyntheticCheck.
"""

from typing import Any

from importer.core.ir import (
    Aggregation,
    Confidence,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Occurrences,
    Signal,
    SignalKind,
    TagFilter,
)
from importer.sources.gcp.extras import GcpExtras
from importer.sources.gcp.plus import Built
from importer.sources.gcp.scope import ScopeFail, metric_filter

EVENT = "SyntheticCheck"
FAILED = "filter(count(*), WHERE result = 'FAILED')"
SUCCESS_SHARE = "filter(count(*), WHERE result = 'SUCCESS') / count(*)"
LATENCY = {
    "ALIGN_MEAN": (Aggregation.AVG, "REDUCE_MEAN", None),
    "ALIGN_MAX": (Aggregation.MAX, "REDUCE_MAX", None),
    "ALIGN_MIN": (Aggregation.MIN, "REDUCE_MIN", None),
    "ALIGN_PERCENTILE_50": (Aggregation.AVG, "REDUCE_NONE", 50),
    "ALIGN_PERCENTILE_95": (Aggregation.AVG, "REDUCE_NONE", 95),
    "ALIGN_PERCENTILE_99": (Aggregation.AVG, "REDUCE_NONE", 99),
}
# a percentile per location, then the mean over locations: approximated by one percentile
_PERCENTILE_REDUCERS: dict[int | None, tuple[str, ...]] = {
    None: (),
    50: ("REDUCE_MEAN",),
    95: ("REDUCE_MEAN",),
    99: ("REDUCE_MEAN",),
}
GROUPS = {
    "metric.label.check_id": "monitorName",
    "resource.label.host": "monitorName",
    "metric.label.checker_location": "location",
}
SSL_WORKAROUND = (
    "New Relic Synthetics results carry no certificate expiry; create a certificate check "
    "monitor for the host (it fails a set number of days before the certificate expires) and "
    "alert on its failures"
)
OTHER_WORKAROUND = (
    "the metric {metric!r} of an uptime check has no field in New Relic Synthetics results; "
    "alert on SyntheticCheck failures instead, or on a scripted monitor that asserts it"
)


def _monitors(
    uptime: dict[str, dict[str, Any]], tags: list[TagFilter]
) -> tuple[list[TagFilter], list[str]]:
    from importer.sources.gcp.translate import Fail

    out: list[TagFilter] = []
    notes: list[str] = []
    for tag in tags:
        if tag.key == "gcp.projectId":
            notes.append("the project filter is not part of the query: monitors are per account")
            continue
        if tag.key != "metric.check_id" or tag.negate or tag.wildcard:
            raise Fail(
                Confidence.MANUAL,
                f"the uptime filter on {tag.key!r} is not supported (only equals on check_id)",
            )
        ids = tag.values or [tag.value]
        names: list[str] = []
        for check_id in ids:
            check = uptime.get(check_id)
            if check is None:
                raise Fail(
                    Confidence.MANUAL,
                    f"the uptime check {check_id!r} is not in the export's uptime-checks "
                    "(deleted, or in another project); re-run extract-gcp",
                )
            names.append(str(check.get("displayName") or check_id))
        out.append(
            TagFilter(key="monitorName", value="", values=names)
            if len(names) > 1
            else TagFilter(key="monitorName", value=names[0])
        )
    if not out:
        notes.append("no check_id filter: every Synthetics monitor of the account is counted")
    return out, notes


def convert(extras: GcpExtras, metric: str, body: dict[str, object]) -> Built | None:
    from importer.sources.gcp.translate import (
        OPERATORS,
        Fail,
        _check_limits,
        _threshold_trigger,
        _whole_windows,
        period_of,
        seconds,
        whole_minutes,
    )

    if extras.uptime is None:
        return None
    if metric == "time_until_ssl_cert_expires":
        raise Fail(Confidence.MANUAL, SSL_WORKAROUND)
    if metric not in ("check_passed", "request_latency"):
        raise Fail(Confidence.MANUAL, OTHER_WORKAROUND.format(metric=metric))
    try:
        scope = metric_filter(str(body.get("filter", "")))
    except ScopeFail as err:
        raise Fail(Confidence.MANUAL, str(err)) from err
    monitors, notes = _monitors(extras.uptime, scope.filters)
    agg = _single_stage(body)
    aligner = str(agg.get("perSeriesAligner", ""))
    reducer = str(agg.get("crossSeriesReducer") or "REDUCE_NONE")
    group_by = _groups(agg, notes)
    select: str | None
    if metric == "check_passed":
        if reducer == "REDUCE_COUNT_FALSE":
            select, aggregation = FAILED, Aggregation.SUM
            notes.append(
                "Google counts the locations whose latest run failed; this counts the failed "
                "runs in the window, which is the same number when the window is the check's "
                "period"
            )
        elif aligner == "ALIGN_FRACTION_TRUE" and reducer in ("REDUCE_NONE", "REDUCE_MEAN"):
            select, aggregation = SUCCESS_SHARE, Aggregation.SUM
        else:
            raise Fail(
                Confidence.MANUAL,
                f"check_passed with the aligner {aligner or 'none'} and reducer {reducer} is not "
                "supported; use REDUCE_COUNT_FALSE (failing locations) or ALIGN_FRACTION_TRUE",
            )
    else:
        found = LATENCY.get(aligner)
        if found is None or reducer not in (
            found[1],
            "REDUCE_NONE",
            *_PERCENTILE_REDUCERS[found[2]],
        ):
            raise Fail(
                Confidence.MANUAL,
                f"request_latency with the aligner {aligner or 'none'} and reducer {reducer} "
                "is not supported (mean, max, min or a percentile, reduced the same way)",
            )
        aggregation, percentile = found[0], found[2]
        select = f"percentile(duration, {percentile})" if percentile else None
        notes.append("request_latency is in milliseconds, like the duration of a Synthetics run")
        if percentile and reducer != "REDUCE_NONE":
            notes.append(
                "Google takes the percentile per location and then the mean over locations; "
                "this is the percentile over all runs in the window, which differs slightly"
            )
    period, period_note = whole_minutes(period_of(agg))
    check_period = _check_period(extras.uptime, monitors)
    if check_period > period:
        period = -(-check_period // 60) * 60
        notes.append(
            f"the window is raised to {period}s, the period of the check, so that every location "
            "has run once in each window"
        )
    window, window_note = _whole_windows(seconds(body.get("duration")), period)
    _check_limits(period, window)
    notes += [n for n in (period_note, window_note, *_threshold_trigger(body, agg)) if n]
    operator = OPERATORS.get(str(body.get("comparison", "")))
    if operator is None:
        raise Fail(Confidence.MANUAL, f"comparison {body.get('comparison')!r} is not supported")
    target = (
        NrTarget(event=EVENT, select=select)
        if select
        else NrTarget(event=EVENT, attribute="duration")
    )
    notes.append(
        "needs the Synthetics monitor of the uptime check (see synthetic-monitor-plan.json from "
        "convert-gcp-uptime); the attribute names of SyntheticCheck are not confirmed on live data"
    )
    term = MetricTerm(
        metric=f"uptime_check/{metric}", space_aggregation=aggregation, nr_target=target
    )
    signal = Signal(
        kind=SignalKind.METRIC,
        raw_query=metric,
        window_seconds=window,
        aggregation_window_seconds=period,
        occurrences=Occurrences.ALL,
        metric=MetricSignal(
            terms={"a": term}, aggregation=aggregation, scope=monitors, group_by=group_by
        ),
    )
    return Built(signal, operator, float(str(body.get("thresholdValue", 0))), notes)


def _groups(agg: dict[str, Any], notes: list[str]) -> list[str]:
    from importer.sources.gcp.translate import Fail

    raw = agg.get("groupByFields")
    out: list[str] = []
    for field in map(str, raw if isinstance(raw, list) else []):
        attribute = GROUPS.get(field)
        if attribute is None:
            raise Fail(Confidence.MANUAL, f"group by {field!r} has no Synthetics attribute")
        if field == "resource.label.host":
            notes.append("grouped by monitor: a monitor has one host")
        if attribute not in out:
            out.append(attribute)
    return out


def _check_period(uptime: dict[str, dict[str, Any]], monitors: list[TagFilter]) -> int:
    from importer.sources.gcp.translate import seconds

    names = {n for m in monitors for n in (m.values or [m.value])}
    periods = [
        seconds(c.get("period")) for c in uptime.values() if str(c.get("displayName")) in names
    ]
    return max(periods, default=0)


def _single_stage(body: dict[str, object]) -> dict[str, Any]:
    """The one aggregation stage (this module reads its aligners itself)."""
    from importer.sources.gcp.translate import Fail

    stages = body.get("aggregations")
    if isinstance(stages, list) and len(stages) > 1:
        raise Fail(Confidence.MANUAL, "second-stage aggregation is not supported")
    first = stages[0] if isinstance(stages, list) and stages else {}
    return first if isinstance(first, dict) else {}
