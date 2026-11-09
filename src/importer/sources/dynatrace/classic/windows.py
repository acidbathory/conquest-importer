"""Dynatrace samples to New Relic windows: violating samples, sliding window, offset, no data.

A Dynatrace sample is one minute. The event opens when `violating` of the last `samples` samples
breach, not necessarily in a row. New Relic offers "every window in the duration" or "at least one
window in the duration"; neither counts breaches in a longer span. The first, with a duration of
`violating` minutes, is used: it is stricter when `violating` is below `samples`. The second is
refused because one breaching minute would open an alert event
(docs/research/dynatrace-alerting-objects-deep.md section 6.3).
"""

from dataclasses import dataclass, field

from importer.core.ir import NoData
from importer.sources.dynatrace.classic.common import Fail, check_limits

SAMPLE_SECONDS = 60
DEFAULT_DELAY_SECONDS = 120
MAX_OFFSET_MINUTES = 60


@dataclass
class SampleWindow:
    window_seconds: int
    delay_seconds: int
    no_data: NoData
    notes: list[str] = field(default_factory=list)


def _counts(violating: int, samples: int) -> None:
    if violating < 1 or samples < 1:
        raise Fail("the number of violating samples and the sliding window must be at least 1")
    if violating > samples:
        raise Fail(
            f"violating samples ({violating}) are more than the sliding window ({samples} samples)"
        )


def _offset(minutes: int | None) -> tuple[int, list[str]]:
    if minutes is None or minutes <= 0:
        if minutes is not None and minutes < 0:
            raise Fail("the query offset is negative")
        return DEFAULT_DELAY_SECONDS, []
    if minutes > MAX_OFFSET_MINUTES:
        raise Fail(f"query offset {minutes} minutes is over the supported maximum of 60")
    delay = max(DEFAULT_DELAY_SECONDS, minutes * SAMPLE_SECONDS)
    return delay, [
        f"the query offset of {minutes} minutes becomes an aggregation delay of {delay}s: New "
        "Relic waits before it aggregates, where Dynatrace evaluates older data"
    ]


def sample_window(
    violating: int,
    samples: int,
    dealerting: int | None,
    offset_minutes: int | None,
    alert_on_no_data: bool,
) -> SampleWindow:
    """The breach duration, aggregation delay and no-data setting, with the losses stated."""
    _counts(violating, samples)
    window = violating * SAMPLE_SECONDS
    check_limits(SAMPLE_SECONDS, window)
    delay, notes = _offset(offset_minutes)
    if violating < samples:
        notes.insert(
            0,
            f"{violating} of {samples} violating samples (not necessarily in a row) becomes "
            f"{violating} consecutive one-minute breaches, which is stricter: a series that "
            "breaks every second minute opens the Dynatrace event and never opens the New "
            "Relic one",
        )
    if dealerting is not None:
        notes.append(
            f"dealertingSamples {dealerting} has no equivalent: New Relic closes the alert "
            "event when the data stops breaching"
        )
    no_data = NoData()
    if alert_on_no_data:
        no_data = NoData(notify=True, timeout_minutes=violating)
        notes.append(
            f"missing data counts as a violating sample in Dynatrace; here loss of signal opens "
            f"an alert event after {violating} minutes without data, and only for a signal that "
            "has reported before"
        )
    return SampleWindow(window, delay, no_data, notes)
