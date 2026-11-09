"""Small helpers shared by the converters of the Dynatrace classic source."""

import math

from importer.core.emit.common import MAX_AGGREGATION_WINDOW_SECONDS, MAX_DURATION_SECONDS
from importer.core.ids import source_id
from importer.core.ir import Confidence, IRMonitor, Signal, SignalKind
from importer.core.text import printable

SOURCE = "dynatrace-classic"
SHOWN_LIMIT = 60


class Fail(Exception):
    """A conversion that must stay manual; `reason` is safe to put in a report."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def shown(text: str, limit: int = SHOWN_LIMIT) -> str:
    """Untrusted text as a short quoted literal: control characters become '?', quotes escaped."""
    return repr(printable(text, limit))


def finite(value: float, what: str) -> float:
    """The number, or a failure when it is not finite (an export can hold 1e999)."""
    if not math.isfinite(value):
        raise Fail(f"{what} is not a finite number")
    return value


def check_limits(period: int, window: int) -> None:
    """Manual when New Relic would refuse the window or duration at emit time."""
    if period > MAX_AGGREGATION_WINDOW_SECONDS:
        raise Fail(
            f"aggregation window {period}s is over New Relic's limit of "
            f"{MAX_AGGREGATION_WINDOW_SECONDS}s"
        )
    if window > MAX_DURATION_SECONDS:
        raise Fail(f"duration {window}s is over New Relic's limit of {MAX_DURATION_SECONDS}s")


def monitor(
    *,
    key: str,
    name: str,
    source_type: str,
    confidence: Confidence,
    reasons: list[str],
    signal: Signal | None = None,
) -> IRMonitor:
    """An IR monitor of this source; a missing signal is an unknown one (inventory, failures)."""
    return IRMonitor(
        source_system=SOURCE,
        source_id=source_id(key),
        source_key=key,
        source_type=source_type,
        name=name,
        signal=signal or Signal(kind=SignalKind.UNKNOWN, raw_query=name),
        confidence=confidence,
        reasons=reasons,
    )
