"""Display options of Cloud Monitoring charts: units and thresholds.

A threshold colour maps to a New Relic severity (red is critical, yellow and orange are warning;
other colours have no New Relic counterpart and are named in a note). The direction decides the
bound: `ABOVE` is a `from_value`, `BELOW` a `to_value`. A billboard takes only a `from_value`
(critical or warning above it), so a `BELOW` threshold on a scorecard is noted, not written.
"""

from typing import Any

from importer.core.dashboard_ir import Threshold, ThresholdSeverity, Visualization, WidgetOptions
from importer.core.text import printable

# UCUM units Cloud Monitoring uses -> New Relic unit names. A fraction shown as a percentage
# ("10^2.%") is a 0 to 1 value, which New Relic's PERCENTAGE formats as 0% to 100%; the plain "%"
# unit holds 0 to 100 and has no New Relic unit that would not multiply it by 100.
UNITS = {
    "By": "BYTES",
    "By/s": "BYTES_PER_SECOND",
    "bit": "BITS",
    "bit/s": "BITS_PER_SECOND",
    "ms": "MS",
    "s": "SECONDS",
    "Hz": "HERTZ",
    "10^2.%": "PERCENTAGE",
}
SEVERITIES = {
    "RED": ThresholdSeverity.CRITICAL,
    "YELLOW": ThresholdSeverity.WARNING,
    "ORANGE": ThresholdSeverity.WARNING,
}
THRESHOLD_VIZ = (Visualization.LINE, Visualization.TABLE, Visualization.BILLBOARD)


def unit_of(queries: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """The New Relic unit when every data set that names a unit names the same mapped one."""
    units: set[str] = set()
    for q in queries:
        tsq = q.get("timeSeriesQuery")
        raw = tsq.get("unitOverride") if isinstance(tsq, dict) else None
        if isinstance(raw, str) and raw:
            units.add(raw)
    if len(units) != 1:
        return None, (["the data sets use several units; no unit is set"] if units else [])
    (unit,) = units
    if unit in UNITS:
        return UNITS[unit], []
    if unit == "%":
        return None, ["the unit % holds 0 to 100, which New Relic has no unit for; none is set"]
    return None, []


def thresholds_of(raw: object, viz: Visualization) -> tuple[list[Threshold], list[str]]:
    """Thresholds the widget can show, and a note for each one left out."""
    items = [t for t in raw if isinstance(t, dict)] if isinstance(raw, list) else []
    if not items:
        return [], []
    if viz not in THRESHOLD_VIZ:
        return [], ["thresholds are not migrated (this chart type shows none)"]
    kept: list[Threshold] = []
    notes: list[str] = []
    seen: set[ThresholdSeverity] = set()
    for item in items:
        value = item.get("value")
        severity = SEVERITIES.get(str(item.get("color", "")).upper())
        below = str(item.get("direction", "")).upper() == "BELOW"
        if isinstance(value, bool) or not isinstance(value, int | float):
            notes.append("a threshold without a number is not migrated")
        elif severity is None:
            colour = printable(str(item.get("color") or "no colour"), 20)
            notes.append(f"a threshold coloured {colour} is not migrated (only red and yellow)")
        elif viz == Visualization.BILLBOARD and (below or severity in seen):
            notes.append(
                "a scorecard threshold below a value, or a second one of the same colour, is not "
                "migrated (New Relic shows one critical and one warning bound, above a value)"
            )
        else:
            seen.add(severity)
            kept.append(
                Threshold(
                    severity=severity,
                    from_value=None if below else float(value),
                    to_value=float(value) if below else None,
                )
            )
    return kept, notes


def options_of(unit: str | None, viz: Visualization) -> WidgetOptions:
    return WidgetOptions(unit=unit) if unit and viz != Visualization.MARKDOWN else WidgetOptions()
