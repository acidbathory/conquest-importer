"""Panel display options read from the raw panel, and what each becomes in a widget.

The coverage planner's panel model keeps only what it needs for the plan, so units, thresholds,
axis limits, legend and stacking are read here from the panel body (modern `fieldConfig` and
`options`, and the legacy graph and singlestat fields). Values are clamped and typed at once; the
body is not kept. Colours map by family (research note, section 4.2): green is success, yellow
and orange are warning, red is critical.
"""

import colorsys
import math
import re
from typing import Any

from pydantic import BaseModel, Field

from importer.core.dashboard_ir import MAX_NUMBER, Threshold, ThresholdSeverity, Visualization
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboards.common import mapping, sequence, text, whole

MAX_STEPS = 50
MAX_CONTENT = 10_000
# UNVERIFIED: the strings New Relic accepts for `units.unit` are not enumerated in the pages read;
# these are the names of the New Relic chart units. One sandbox read-back confirms or fixes them.
UNITS = {
    "bytes": "BYTES",
    "decbytes": "BYTES",
    "Bps": "BYTES_PER_SECOND",
    "binBps": "BYTES_PER_SECOND",
    "decBps": "BYTES_PER_SECOND",
    "bits": "BITS",
    "bps": "BITS_PER_SECOND",
    "ms": "MS",
    "s": "SECONDS",
    "reqps": "REQUESTS_PER_SECOND",
    "hertz": "HERTZ",
}
NO_UNIT = frozenset({"", "none", "short", "string", "locale", "countps", "ops"})
HEX = re.compile(r"#([0-9a-fA-F]{6})\b")
RGB = re.compile(r"rgba?\( ?(\d{1,3}) ?, ?(\d{1,3}) ?, ?(\d{1,3})")
FAMILIES = ("green", "yellow", "orange", "red")


class Step(BaseModel):
    value: float | None = None  # None is the base step
    color: str = ""


class Display(BaseModel):
    unit: str = ""
    decimals: int | None = None
    low: float | None = None
    high: float | None = None
    steps: list[Step] = Field(default_factory=list)
    percentage: bool = False
    color_mode: str = ""
    shown_as: str = ""  # thresholdsStyle.mode of a time series
    legend: bool | None = None
    stacking: str = ""
    fill: int = 0
    draw: str = ""
    interval: str = ""
    content: str = ""
    mode: str = ""


def number(value: object) -> float | None:
    """A finite number inside the range the IR accepts, else None; booleans and text are not."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or abs(value) > MAX_NUMBER:
        return None
    return float(value)


def _steps(config: dict[str, Any]) -> list[Step]:
    found: list[Step] = []
    for item in sequence(config.get("steps"))[:MAX_STEPS]:
        step = mapping(item)
        found.append(Step(value=number(step.get("value")), color=text(step.get("color"), 40)))
    return found


def _cut(raw: str) -> float | None:
    return number(float(raw)) if re.fullmatch(r"-?\d+(\.\d+)?", raw) else None


def _legacy_steps(body: dict[str, Any]) -> list[Step]:
    """A singlestat's `thresholds: "70,90"` with three `colors`."""
    raw = [x for x in str(body.get("thresholds", "")).replace(" ", "").split(",")[:10] if x]
    cuts = [_cut(x) for x in raw]
    colors = [c for c in sequence(body.get("colors")) if isinstance(c, str)]
    if not cuts or None in cuts or len(colors) != len(cuts) + 1:
        return []
    steps = [Step(value=None, color=text(colors[0], 40))]
    steps += [Step(value=cut, color=text(c, 40)) for cut, c in zip(cuts, colors[1:], strict=True)]
    return steps


def _flag(*values: object) -> bool | None:
    """The first of the values that is a real boolean."""
    return next((v for v in values if isinstance(v, bool)), None)


def _decimals(value: object) -> int | None:
    found = number(value)
    return None if found is None else whole(found, 0, 10, 0)


def read_display(body: dict[str, Any]) -> Display:
    """Display options of one panel body, modern or legacy."""
    config = mapping(body.get("fieldConfig"))
    defaults = mapping(config.get("defaults"))
    custom = mapping(defaults.get("custom"))
    options = mapping(body.get("options"))
    legend = mapping(options.get("legend")).get("showLegend")
    old_legend = mapping(body.get("legend")).get("show")
    thresholds = mapping(defaults.get("thresholds"))
    axis = mapping((sequence(body.get("yaxes")) or [None])[0])
    content = options.get("content", body.get("content"))
    stack = mapping(custom.get("stacking")).get("mode")
    steps = _steps(thresholds) or _legacy_steps(body)
    return Display(
        unit=text(defaults.get("unit") or body.get("format") or axis.get("format"), 40),
        decimals=_decimals(defaults.get("decimals", body.get("decimals", axis.get("decimals")))),
        low=number(defaults.get("min", number(axis.get("min")))),
        high=number(defaults.get("max", number(axis.get("max")))),
        steps=steps,
        percentage=thresholds.get("mode") == "percentage",
        color_mode=text(mapping(defaults.get("color")).get("mode"), 40),
        shown_as=text(mapping(custom.get("thresholdsStyle")).get("mode"), 40),
        legend=_flag(legend, old_legend),
        stacking=text(stack, 20) or ("normal" if body.get("stack") is True else ""),
        fill=whole(custom.get("fillOpacity", body.get("fill")), 0, 100, 0),
        draw=text(custom.get("drawStyle"), 20)
        or ("bars" if body.get("bars") is True and body.get("lines") is False else ""),
        interval=text(body.get("interval"), 20),
        content=content[:MAX_CONTENT] if isinstance(content, str) else "",
        mode=text(options.get("mode", body.get("mode")), 20),
    )


# --- unit ---


def new_relic_unit(unit: str) -> tuple[str | None, list[str]]:
    """The New Relic unit for a Grafana unit, or None with a note when there is none."""
    if unit == "percent":  # 0 to 100; PERCENTAGE would show 50 as 5000%
        return None, [r.N_PERCENT]
    if unit == "percentunit":  # 0 to 1: PERCENTAGE formats 1 as 100% (research note, section 4.4)
        return "PERCENTAGE", []
    if unit in UNITS:
        return UNITS[unit], []
    if unit in NO_UNIT:
        return None, []
    return None, [r.N_UNIT]


# --- thresholds ---


def family(color: str) -> str:
    """green, yellow, orange, red, or other, from a Grafana colour name or a CSS colour."""
    name = color.lower()
    for word in FAMILIES:
        if word in name:
            return word
    rgb = None
    if (hexa := HEX.search(name)) is not None:
        value = hexa[1]
        rgb = tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    elif (func := RGB.search(name)) is not None:
        rgb = tuple(min(255, int(x)) for x in func.groups())
    if rgb is None:
        return "other"
    hue, saturation, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    degrees = hue * 360
    if saturation < 0.25:
        return "other"
    if degrees < 15 or degrees >= 345:
        return "red"
    if degrees < 70:
        return "orange" if degrees < 45 else "yellow"
    return "green" if degrees < 170 else "other"


BILLBOARD = {
    "red": ThresholdSeverity.CRITICAL,
    "orange": ThresholdSeverity.WARNING,
    "yellow": ThresholdSeverity.WARNING,
}
LINE = {
    "green": ThresholdSeverity.SUCCESS,
    "yellow": ThresholdSeverity.WARNING,
    "orange": ThresholdSeverity.WARNING,
    "red": ThresholdSeverity.CRITICAL,
}
SHOWN = frozenset({"line", "dashed", "area", "line+area", "dashed+area"})


def _ordered(steps: list[Step]) -> list[Step]:
    base = [s for s in steps if s.value is None][:1]
    rest = sorted((s for s in steps if s.value is not None), key=lambda s: s.value or 0.0)
    return base + rest


def _billboard(steps: list[Step]) -> tuple[list[Threshold], list[str]]:
    ordered = _ordered(steps)
    bad_first = bool(ordered) and ordered[0].value is None and family(ordered[0].color) in BILLBOARD
    if bad_first and any(family(s.color) == "green" for s in ordered[1:]):
        return [], [r.N_THRESHOLD_ORDER]  # bad at low values, good at high values
    found: dict[ThresholdSeverity, Threshold] = {}
    dropped = False
    for step in ordered:
        if step.value is None:
            continue
        severity = BILLBOARD.get(family(step.color))
        if severity is None:
            dropped = dropped or family(step.color) != "green"
        elif severity not in found:
            found[severity] = Threshold(severity=severity, from_value=step.value)
    return list(found.values()), [r.N_THRESHOLD_COLORS] if dropped else []


def _bands(steps: list[Step]) -> tuple[list[Threshold], list[str]]:
    ordered = _ordered(steps)
    found: list[Threshold] = []
    dropped = False
    for step, after in zip(ordered, [*ordered[1:], None], strict=True):
        severity = LINE.get(family(step.color))
        if severity is None:
            dropped = True
            continue
        low, high = step.value, after.value if after is not None else None
        if low is not None or high is not None:
            found.append(Threshold(severity=severity, from_value=low, to_value=high))
    return found, [r.N_THRESHOLD_COLORS] if dropped else []


def thresholds_for(display: Display, viz: Visualization) -> tuple[list[Threshold], list[str]]:
    """Threshold blocks for the widget and the notes about what was left out.

    Grafana keeps default thresholds on every panel; they count only where the panel shows them:
    a stat or gauge coloured by thresholds, a time series with a thresholds style.
    """
    steps = display.steps
    shown_on_stat = display.color_mode in ("", "thresholds")
    if not steps:
        return [], []
    if viz == Visualization.BILLBOARD and shown_on_stat:
        return ([], [r.N_THRESHOLD_MODE]) if display.percentage else _billboard(steps)
    if viz in (Visualization.LINE, Visualization.AREA) and display.shown_as in SHOWN:
        if display.percentage:
            return [], [r.N_THRESHOLD_MODE]
        return _bands(steps) if viz == Visualization.LINE else ([], [r.N_THRESHOLD_TYPE])
    return [], []
