"""Pieces every Azure translator uses: the IR base, severity, timing and the manual result."""

from dataclasses import dataclass
from typing import Any

from importer.core.ids import source_id
from importer.core.ir import (
    Confidence,
    EmitOptions,
    IRMonitor,
    Severity,
    Signal,
    SignalKind,
)
from importer.sources.azure import reasons as R
from importer.sources.azure.models import AzureAlert, iso_seconds

MIN_WINDOW = 60
MAX_WINDOW = 21600  # NerdGraph aggregationWindow limit (6 hours); see core/emit/common.py
LONG_WINDOW = 3600  # above this, a violation waits for a full window
MIN_SLIDE = 30
LEVEL = {"critical": Severity.CRITICAL, "warning": Severity.WARNING}


class Fail(Exception):
    """A rule part that cannot be converted; `reasons` say why, `steps` are specific advice."""

    def __init__(self, reason: str, steps: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.steps = steps or []


def key_of(alert: AzureAlert) -> str:
    return alert.id or f"{alert.family}/{alert.name}"


def base_fields(alert: AzureAlert, key: str, source_type: str, name: str) -> dict[str, Any]:
    return {
        "source_system": "azure",
        "source_id": source_id(key),
        "source_key": key,
        "source_type": source_type,
        "name": name,
        "tags": [f"azure:{alert.family}"],
    }


def manual(
    base: dict[str, Any],
    reasons: list[str],
    steps: list[str] | None = None,
    *,
    confidence: Confidence = Confidence.MANUAL,
    unmapped: dict[str, Any] | None = None,
) -> IRMonitor:
    return IRMonitor(
        **base,
        signal=Signal(kind=SignalKind.UNKNOWN, raw_query=str(base["name"])),
        confidence=confidence,
        reasons=list(dict.fromkeys(reasons)),
        remediation=steps or [],
        unmapped=unmapped or {},
    )


@dataclass(frozen=True)
class Level:
    severity: Severity
    label: str  # Sev2
    note: str


def severity_level(raw: object) -> Level:
    """Sev0 and Sev1 are critical; Sev2 to Sev4 are warnings (docs/research section 4.3)."""
    number: int | None = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        number = raw
    elif isinstance(raw, str) and raw[:3].lower() == "sev" and raw[3:].isdigit():
        number = int(raw[3:])
    if number is None or not 0 <= number <= 4:
        return Level(Severity.WARNING, "unknown", R.N_SEVERITY_BAD.format(value=R.shown(raw, 20)))
    level = Severity.CRITICAL if number <= 1 else Severity.WARNING
    label = f"Sev{number}"
    return Level(level, label, R.N_SEVERITY.format(label=label, level=level.value))


@dataclass(frozen=True)
class Timing:
    window: int
    options: EmitOptions
    notes: list[str]


def timing(props: dict[str, Any], *, logs: bool = False) -> Timing:
    """Window, frequency and the New Relic streaming settings that stand for them."""
    window = iso_seconds(props.get("windowSize"))
    freq = iso_seconds(props.get("evaluationFrequency"))
    if window is None and freq is None:
        raise Fail(R.M_WINDOW_MISSING)
    window = window if window is not None else freq or 0
    freq = freq if freq is not None else window
    if window < MIN_WINDOW:
        raise Fail(R.M_WINDOW_SHORT)
    if window > MAX_WINDOW:
        raise Fail(R.M_WINDOW_LONG)
    notes = [(R.N_LATENCY_LOG if logs else R.N_LATENCY).format(freq=freq, window=window)]
    slide: int | None = None
    if freq < window and freq >= MIN_SLIDE and window % freq == 0:
        slide = freq
        notes.append(R.N_SLIDE.format(freq=freq, window=window))
    elif freq != window:
        notes.append(R.N_FREQ_MISMATCH.format(freq=freq, window=window))
    if window > LONG_WINDOW:
        notes.append(R.N_LONG_WINDOW.format(window=window))
    method = None if logs else "EVENT_TIMER"  # forwarded logs keep the default streaming
    options = EmitOptions(aggregation_method=method, slide_by_seconds=slide)
    return Timing(window, options, notes)


def common_notes(props: dict[str, Any], level: Level) -> list[str]:
    """Notes every converted rule carries: severity, disabled, autoMitigate, resolve, mute."""
    notes = [level.note]
    if props.get("enabled") is False:
        notes.append(R.N_DISABLED)
    if props.get("autoMitigate") is False:
        notes.append(R.N_AUTO_MITIGATE)
    resolve = props.get("resolveConfiguration")
    if isinstance(resolve, dict) and resolve.get("timeToResolve"):
        notes.append(R.N_TIME_TO_RESOLVE.format(value=R.shown(resolve["timeToResolve"], 20)))
    if props.get("muteActionsDuration"):
        notes.append(R.N_MUTE.format(value=R.shown(props["muteActionsDuration"], 20)))
    return notes
