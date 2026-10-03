"""Aggregate conversion results across runs: how much converts, and why the rest does not.

Input is one or more `convert` output folders (each holds `ir/*.json`). Nothing here talks to
any service. Reasons are free text with specifics in them (metric names, ids), so they are first
reduced to a pattern; otherwise every failure would look unique.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from importer.core.ir import Confidence, IRMonitor
from importer.core.report import md_cell
from importer.core.text import printable

CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)

# Applied in order. Each one replaces a specific detail with a placeholder.
_PATTERN_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"'[^']*'|\"[^\"]*\""), "'{X}'"),  # quoted names and values
    (re.compile(r"\[[^\]]*\]"), "{LIST}"),  # python-style lists
    (re.compile(r"\([^)]*,[^)]*\)"), "({LIST})"),  # parenthesised lists, e.g. alarm names
    (re.compile(r"(?<=: )[@\w./-]+(?:, [@\w./-]+)*$"), "{LIST}"),  # trailing "label: a, b, c"
    (re.compile(r"(?<!\w)\d+(?:, \d+)*"), "{N}"),  # numbers and lists of numbers
]


def reason_pattern(reason: str) -> str:
    """Reduce a reason to its shape, so 'no mapping for metric a' and '... b' group together."""
    for rule, replacement in _PATTERN_RULES:
        reason = rule.sub(replacement, reason)
    return " ".join(reason.split())


@dataclass
class TypeStats:
    by_confidence: Counter[Confidence] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        return self.by_confidence.total()

    @property
    def converted(self) -> int:
        return sum(self.by_confidence[c] for c in CONVERTED)


@dataclass
class SourceStats:
    by_type: dict[str, TypeStats] = field(default_factory=lambda: defaultdict(TypeStats))
    # Reason pattern -> number of monitors that carry it (a monitor counts once per pattern).
    reasons_not_converted: Counter[str] = field(default_factory=Counter)
    reasons_converted: Counter[str] = field(default_factory=Counter)

    @property
    def by_confidence(self) -> Counter[Confidence]:
        total: Counter[Confidence] = Counter()
        for t in self.by_type.values():
            total.update(t.by_confidence)
        return total

    @property
    def total(self) -> int:
        return sum(t.total for t in self.by_type.values())

    @property
    def converted(self) -> int:
        return sum(t.converted for t in self.by_type.values())


@dataclass
class Run:
    path: Path
    monitors: list[IRMonitor]


def load_run(folder: Path) -> Run:
    """Read the IR files of one `convert` output folder."""
    ir_dir = folder / "ir"
    files = sorted(ir_dir.glob("*.json")) if ir_dir.is_dir() else []
    if not files:
        raise ValueError(f"{folder}: no ir/*.json found (is this a convert output folder?)")
    return Run(folder, [IRMonitor.model_validate_json(f.read_text()) for f in files])


def build_stats(runs: list[Run]) -> dict[str, SourceStats]:
    """Group every monitor of every run by source platform."""
    stats: dict[str, SourceStats] = defaultdict(SourceStats)
    for run in runs:
        for m in run.monitors:
            s = stats[m.source_system]
            s.by_type[m.source_type].by_confidence[m.confidence] += 1
            target = s.reasons_converted if m.confidence in CONVERTED else s.reasons_not_converted
            target.update({reason_pattern(r) for r in m.reasons})
    return dict(sorted(stats.items()))


def _cell(text: str, limit: int = 300) -> str:
    return md_cell(printable(text, 10_000), limit)


def _percent(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "n/a"


def _ranked(counter: Counter[str], top: int) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:top]


def render_markdown(runs: list[Run], stats: dict[str, SourceStats], top: int = 10) -> str:
    lines = [
        "# Conversion stats",
        "",
        "Runs: " + ", ".join(f"{_cell(str(r.path), 80)} ({len(r.monitors)})" for r in runs),
        "",
        "> Rates describe only the runs listed above. Input that is synthetic or hand-picked "
        "gives optimistic numbers: do not present them as customer results. A monitor counts as "
        "converted when it is labelled exact or approximate (approximate still needs review).",
    ]
    for source, s in stats.items():
        lines += [
            "",
            f"## {_cell(source, 40)}: {s.total} monitors, {s.converted} converted "
            f"({_percent(s.converted, s.total)})",
            "",
            "| Confidence | Count |",
            "|---|---|",
        ]
        lines += [f"| {c.value} | {s.by_confidence[c]} |" for c in Confidence]
        lines += [
            "",
            "| Type | Total | Converted | Rate |",
            "|---|---|---|---|",
        ]
        for name, t in sorted(s.by_type.items()):
            lines.append(
                f"| {_cell(name, 60)} | {t.total} | {t.converted} | "
                f"{_percent(t.converted, t.total)} |"
            )
        if s.reasons_not_converted:
            lines += [
                "",
                "### Reasons on monitors that did not convert",
                "",
                "Each monitor lists all of its reasons, and the data does not say which one "
                "blocked it. A reason that is also common on converted monitors is probably not "
                "the blocker.",
                "",
                "| Reason pattern | Monitors | Also on converted monitors |",
                "|---|---|---|",
            ]
            lines += [
                f"| {_cell(p)} | {n} | {s.reasons_converted[p]} |"
                for p, n in _ranked(s.reasons_not_converted, top)
            ]
        if s.reasons_converted:
            lines += [
                "",
                "### Review notes on converted monitors",
                "",
                "| Reason pattern | Monitors |",
                "|---|---|",
            ]
            lines += [f"| {_cell(p)} | {n} |" for p, n in _ranked(s.reasons_converted, top)]
    return "\n".join(lines) + "\n"
