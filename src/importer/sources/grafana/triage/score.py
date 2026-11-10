"""Score and bucket for one dashboard.

The score is 0 to 100 and only orders dashboards inside a bucket; the bucket comes from rules a
person can read. With no usage file the signals are weak, so a dashboard is "migrate now" only
when someone starred it. Usage, when given, decides.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from math import log10

from importer.sources.grafana.triage.signals import Signals
from importer.sources.grafana.triage.usage import Usage

STALE_DAYS = 90  # not viewed this long: skip
RECENT_DAYS = 30
NOW_SCORE = 60


class Bucket(StrEnum):
    NOW = "migrate now"
    LATER = "migrate later"
    QUICKSTART = "replace with quickstart"
    SKIP = "skip"


@dataclass
class Verdict:
    bucket: Bucket
    score: int
    reasons: list[str] = field(default_factory=list)


def quickstart_for(sig: Signals) -> str | None:
    """HOOK: the name of a New Relic quickstart that replaces this dashboard, or None.

    No quickstart map exists in this branch yet. When it lands, look the dashboard up here (by
    grafana.com id for community dashboards, else by datasource and metric prefix) and return
    the quickstart name; `decide` then buckets it as "replace with quickstart".
    """
    del sig  # unused until the map exists
    return None


def _skip(reason: str, score: int = 0) -> Verdict:
    return Verdict(Bucket.SKIP, score, [reason])


def _hard_skip(sig: Signals, usage: Usage | None, dup_of: str, today: date) -> str | None:
    if sig.empty:
        return "empty dashboard"
    if usage is not None and usage.views_30d == 0:
        return "no views in the last 30 days"
    if usage is not None and usage.last_viewed is not None:
        age = (today - usage.last_viewed).days
        if age > STALE_DAYS:
            return f"last viewed {age} days ago"
    if usage is None or usage.views_30d == 0:  # in use: title words and copies only lower the score
        for flag in ("test", "copy", "tmp"):
            if flag in sig.title_flags:
                return f"title says '{flag}'"
        if dup_of:
            return f"duplicate of {dup_of}"
        if sig.provisioned and not sig.starred:
            return "provisioned (shipped with the stack, not built by the team)"
    return None


def decide(
    sig: Signals, usage: Usage | None, *, dup_of: str, convertible: float, today: date
) -> Verdict:
    """Bucket and score. `convertible` is the converter's share of widgets that converted."""
    why = _hard_skip(sig, usage, dup_of, today)
    if why:
        return _skip(why)
    reasons: list[str] = []
    score = 20
    if usage is not None:
        score += min(40, int(15 * log10(1 + usage.views_30d)))
        reasons.append(f"{usage.views_30d} views in 30 days")
        if usage.last_viewed and (today - usage.last_viewed).days <= RECENT_DAYS:
            score += 10
    if sig.starred:
        score += 30
        reasons.append("starred")
    if "old" in sig.title_flags or "deprecated" in sig.title_flags:
        score -= 25
        reasons.append("title says old or deprecated")
    if usage is not None and dup_of:
        reasons.append(f"near duplicate of {dup_of}, but viewed")
    score += int(20 * max(0.0, min(1.0, convertible)))
    score = max(0, min(100, score))
    if (quick := quickstart_for(sig)) is not None:
        return Verdict(Bucket.QUICKSTART, score, [f"quickstart: {quick}", *reasons])
    known = usage is not None or sig.starred
    now = known and score >= NOW_SCORE
    if not known:
        reasons.append("no usage data")
    return Verdict(Bucket.NOW if now else Bucket.LATER, score, reasons)
