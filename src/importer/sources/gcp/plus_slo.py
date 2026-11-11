"""Alert conditions on an SLO (`select_slo_burn_rate(...)` and friends) as New Relic conditions.

The alert policy holds only the SLO's name. The SLO (goal, period, indicator) comes from the
export's `slos/`. What converts:

- `select_slo_burn_rate(slo, lookback)` on a request-based SLO with good (or bad) and total
  metric filters. Burn rate is the error ratio over the lookback divided by the error budget
  (1 - goal), so `burn > X` is `bad/total > X * (1 - goal)`, or `good/total < 1 - X * (1 - goal)`.
  That is a ratio condition the translator already converts (`ratio_series`), over one lookback
  window of at most 3600s (New Relic's longest aggregation window).

What stays manual, with the service level to create instead:

- a lookback over 3600s (a 6 hour burn rate is not one New Relic window; the service level page
  offers burn-rate alerts over longer windows),
- `select_slo_health`, `select_slo_compliance` and `select_slo_budget_fraction`, which look at the
  whole SLO period (days) while an NRQL condition sees one hour at most,
- distribution-cut, windows-based and basic indicators, which are not a ratio of two event counts.

UNVERIFIED: the exact semantics Google gives select_slo_health (the page that defines it could not
be fetched offline), so it is not converted. The service level periods New Relic offers (1, 7 and
28 days) are from the service level docs as the Dynatrace plan also uses them.
"""

import re
from typing import Any

from importer.core.ir import Confidence
from importer.sources.gcp import datapath
from importer.sources.gcp.extras import GcpExtras
from importer.sources.gcp.plus import Rewritten
from importer.sources.gcp.scope import ScopeFail, metric_filter

BURN = "burn_rate"
# a comparison on the burn rate, and the same comparison on the good-events ratio
FLIP = {
    "COMPARISON_GT": "COMPARISON_LT",
    "COMPARISON_GE": "COMPARISON_LE",
    "COMPARISON_LT": "COMPARISON_GT",
    "COMPARISON_LE": "COMPARISON_GE",
}
NR_PERIOD_DAYS = (1, 7, 28)
WHOLE_PERIOD = (
    "{selector} reads the SLI over the whole SLO period ({period}); a New Relic condition sees "
    "one window of at most 3600s, so it cannot express it"
)


def _short(text: object, limit: int = 200) -> str:
    return " ".join(str(text).split())[:limit]


def _ratio(slo: dict[str, Any]) -> dict[str, Any] | None:
    sli = slo.get("serviceLevelIndicator", {})
    ratio = sli.get("requestBased", {}).get("goodTotalRatio") if isinstance(sli, dict) else None
    return ratio if isinstance(ratio, dict) else None


def _period_text(slo: dict[str, Any]) -> str:
    if slo.get("rollingPeriod"):
        text = str(slo["rollingPeriod"])
        digits = text[:-1]
        if text.endswith("s") and digits.isascii() and digits.isdecimal():
            days = int(digits) / 86400
            return f"rolling {days:g} days"
        return f"rolling {text[:20]}"
    return f"calendar {str(slo.get('calendarPeriod') or 'unknown')[:20].lower()}"


def _nr_period(slo: dict[str, Any]) -> str:
    text = str(slo.get("rollingPeriod", ""))
    digits = text[:-1]
    if text.endswith("s") and digits.isascii() and digits.isdecimal():
        days = round(int(digits) / 86400)
        if days in NR_PERIOD_DAYS:
            return f"{days} DAY"
        near = min(NR_PERIOD_DAYS, key=lambda d: abs(d - days))
        return f"{near} DAY (Google's {days} days is not offered; the nearest is used)"
    return "7 DAY (a calendar period is a rolling one in New Relic; choose 1, 7 or 28 days)"


def service_level_text(slo: dict[str, Any]) -> str:
    """What to create in New Relic instead: a service level with this target, period and events."""
    goal = slo.get("goal")
    target = f"{goal * 100:g}%" if isinstance(goal, int | float) else "the SLO's goal"
    sli = slo.get("serviceLevelIndicator", {})
    kinds = [k for k in ("requestBased", "windowsBased", "basicSli") if k in sli]
    ratio = _ratio(slo)
    if ratio:
        good = ratio.get("goodServiceFilter")
        bad = ratio.get("badServiceFilter")
        total = ratio.get("totalServiceFilter")
        events = (
            f"good events from {_short(good)}" if good else f"bad events from {_short(bad)}"
        ) + f", valid events from {_short(total)}"
    elif "requestBased" in kinds:
        cut = sli["requestBased"].get("distributionCut", {})
        limit = cut.get("range", {})
        events = (
            "good events are the requests whose value is within "
            f"{_short(limit, 80)} of the distribution {_short(cut.get('distributionFilter'))}; "
            "the Google integration publishes percentiles of a distribution metric, not the "
            "count under a bound, so use the request events (APM Transaction duration, or the "
            "load balancer's own request count with a response-time attribute) for good events"
        )
    elif "windowsBased" in kinds:
        events = (
            "a windows-based indicator is the share of time windows that were good, not a count "
            "of events; make one row per window (a query with TIMESERIES at the window size) "
            "and count the good windows over all windows"
        )
    else:
        events = (
            "a basic indicator (App Engine, Cloud Run, Istio) is defined by Google from the "
            "service's own request metrics; use those request counts for good and valid events"
        )
    return (
        f"create a New Relic service level with target {target}, period {_nr_period(slo)} and "
        f"{events}, then add its burn-rate alert in the service level page"
    )


def _lookback(arg: str | None) -> int:
    from importer.sources.gcp.translate import Fail, seconds

    value = seconds(arg or "")
    if value <= 0:
        raise Fail(Confidence.MANUAL, "select_slo_burn_rate has no readable lookback period")
    return value


def convert(extras: GcpExtras, found: re.Match[str], body: dict[str, object]) -> Rewritten | None:
    from importer.sources.gcp.translate import Fail

    if extras.slos is None:
        return None
    selector = f"select_slo_{found['kind']}"
    slo = extras.slo(found["name"])
    if slo is None:
        raise Fail(
            Confidence.MANUAL,
            f"{selector}: the SLO {found['name'].rsplit('/', 1)[-1]!r} is not in the export's "
            "slos (deleted, or in another project); re-run extract-gcp for the project that "
            "owns it",
        )
    if found["kind"] != BURN:
        raise Fail(
            Confidence.MANUAL,
            WHOLE_PERIOD.format(selector=selector, period=_period_text(slo))
            + "; "
            + service_level_text(slo),
        )
    ratio = _ratio(slo)
    goal = slo.get("goal")
    if ratio is None or not isinstance(goal, int | float) or not 0 < goal < 1:
        raise Fail(
            Confidence.MANUAL,
            f"{selector}: the SLO is not a request-based good/total ratio with a goal below "
            f"100%; {service_level_text(slo)}",
        )
    lookback = _lookback(found["arg"])
    if lookback > 3600:
        raise Fail(
            Confidence.MANUAL,
            f"{selector} over {lookback}s: a New Relic condition aggregates over at most 3600s, "
            f"so a burn rate over a longer lookback is not one window; {service_level_text(slo)}",
        )
    comparison = str(body.get("comparison", ""))
    if comparison not in FLIP:
        raise Fail(Confidence.MANUAL, f"comparison {comparison!r} is not supported")
    total = ratio.get("totalServiceFilter")
    good, bad = ratio.get("goodServiceFilter"), ratio.get("badServiceFilter")
    if not total or not (good or bad):
        raise Fail(
            Confidence.MANUAL,
            f"{selector}: the SLI needs a total filter and a good or bad filter; "
            f"{service_level_text(slo)}",
        )
    try:
        float(str(body.get("thresholdValue", 0)))
    except ValueError:
        raise Fail(Confidence.MANUAL, "the threshold is not a number") from None
    burn = float(str(body.get("thresholdValue", 0)))
    budget = 1 - float(goal)
    use_good = bool(good)
    new_body: dict[str, object] = {
        **{k: v for k, v in body.items() if k in ("trigger", "evaluationMissingData")},
        "filter": good if use_good else bad,
        "denominatorFilter": total,
        "aggregations": [
            {
                "alignmentPeriod": f"{lookback}s",
                "perSeriesAligner": "ALIGN_DELTA",
                "crossSeriesReducer": "REDUCE_SUM",
            }
        ],
        "duration": "0s",
        "comparison": FLIP[comparison] if use_good else comparison,
        "thresholdValue": round(1 - burn * budget, 12) if use_good else round(burn * budget, 12),
    }
    note = (
        f"{selector} with a burn rate over {burn:g} is the error ratio over {lookback}s above "
        f"{burn:g} x {budget:.6g} (the error budget of the {goal * 100:g}% goal); the SLO's "
        f"{'good' if use_good else 'bad'} and total request filters are compared as one ratio "
        "of counts per window. The SLO itself ("
        f"{_period_text(slo)}) is not created: "
        + service_level_text(slo).replace("create", "to track it too, create", 1)
    )
    return Rewritten(new_body, [note], data_path_of(good or bad))


def data_path_of(filter_text: object) -> str:
    try:
        found = metric_filter(str(filter_text))
    except ScopeFail:
        return ""
    return datapath.path_of_metric(found.metric_type)


def data_path(extras: GcpExtras, found: re.Match[str]) -> str:
    """The data path of the metric an SLO's good (or bad) filter reads."""
    slo = extras.slo(found["name"]) if extras.slos is not None else None
    ratio = _ratio(slo) if slo else None
    if ratio is None:
        return ""
    return data_path_of(ratio.get("goodServiceFilter") or ratio.get("badServiceFilter"))
