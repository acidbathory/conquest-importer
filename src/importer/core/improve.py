"""Measure conversion quality offline, rank what blocks it, and catch regressions.

This is the measuring half of the improvement loop (see docs/dev/improvement-loop.md). It reads
only the fixtures in the repository, so a run is free and repeatable, and it never talks to any
cloud or to New Relic. A change is an improvement when `compare` finds no regression against the
approved baseline, whatever else it does.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from importer.core.ir import IRMonitor
from importer.core.remediation import steps_for
from importer.core.stats import Run, SourceStats, build_stats, reason_pattern
from importer.core.taxonomy import classify
from importer.sources import REGISTRY

# source name -> fixture folders below the repository root
FIXTURES: dict[str, list[str]] = {
    "cloudwatch": ["tests/fixtures/cloudwatch", "tests/fixtures/cloudwatch-real"],
    "gcp": ["tests/fixtures/gcp"],
    "dynatrace": ["tests/fixtures/dynatrace"],
}
TOP = 10


@dataclass(frozen=True)
class Regression:
    source: str
    what: str

    def __str__(self) -> str:
        return f"{self.source}: {self.what}"


def _monitors(root: Path, extra: dict[str, list[Path]] | None) -> dict[str, list[IRMonitor]]:
    found: dict[str, list[IRMonitor]] = {}
    folders = {k: [root / p for p in v] for k, v in FIXTURES.items()}
    for name, more in (extra or {}).items():
        folders.setdefault(name, []).extend(more)
    for name, paths in folders.items():
        source = REGISTRY[name]
        monitors: list[IRMonitor] = []
        for path in paths:
            if path.exists():
                monitors += source.translate(source.load(path))
        found[name] = monitors
    return found


def _summary(stats: SourceStats, monitors: list[IRMonitor]) -> dict[str, Any]:
    ranked = stats.reasons_not_converted.most_common(TOP)
    categories = {reason_pattern(r): classify(r).value for m in monitors for r in m.reasons}
    unguided = sorted(
        {
            reason_pattern(r)
            for m in monitors
            for r in m.reasons
            if not (m.remediation or steps_for([r]))
        }
        & set(stats.reasons_not_converted)
    )
    return {
        "total": stats.total,
        "converted": stats.converted,
        "rate": round(stats.converted / stats.total, 4) if stats.total else 0.0,
        "by_confidence": {c.value: n for c, n in sorted(stats.by_confidence.items())},
        "top_blockers": [
            {"pattern": p, "count": n, "category": categories.get(p, "")} for p, n in ranked
        ],
        "unguided": unguided,
    }


def measure(root: Path, extra: dict[str, list[Path]] | None = None) -> dict[str, Any]:
    """Convert every fixture and summarise per source. `extra` adds folders for a source."""
    monitors = _monitors(root, extra)
    stats = build_stats([Run(root, ms) for ms in monitors.values() if ms])
    sources = {
        name: _summary(stats[name], monitors[name]) for name in sorted(monitors) if name in stats
    }
    return {"measured": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ"), "sources": sources}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[Regression]:
    """What got worse: fewer conversions, a lower rate, a lost source, or a new unguided reason."""
    problems: list[Regression] = []
    for name, old in baseline.get("sources", {}).items():
        new = current.get("sources", {}).get(name)
        if new is None:
            problems.append(Regression(name, "no longer measured"))
            continue
        if new["total"] == old["total"] and new["converted"] < old["converted"]:
            problems.append(
                Regression(
                    name, f"converted {new['converted']} of {new['total']}, was {old['converted']}"
                )
            )
        elif new["total"] != old["total"] and new["rate"] < old["rate"]:
            problems.append(Regression(name, f"rate {new['rate']:.2%}, was {old['rate']:.2%}"))
        problems += [
            Regression(name, f"no guidance for: {p}")
            for p in sorted(set(new["unguided"]) - set(old["unguided"]))
        ]
    return problems


def render(result: dict[str, Any]) -> str:
    """A short markdown view: the rate per source and the blockers worth fixing first."""
    lines = [f"# Conversion measure ({result['measured']})", ""]
    for name, s in result["sources"].items():
        lines += [f"## {name}: {s['converted']} of {s['total']} converted ({s['rate']:.0%})", ""]
        lines += [f"- {b['count']} x {b['pattern']} [{b['category']}]" for b in s["top_blockers"]]
        lines += [f"- NO GUIDANCE: {p}" for p in s["unguided"]]
        lines.append("")
    return "\n".join(lines)


def record(history: Path, result: dict[str, Any]) -> None:
    """Append one measurement to the history file (one JSON object per line)."""
    history.parent.mkdir(parents=True, exist_ok=True)
    with history.open("a") as fh:
        fh.write(json.dumps(result, sort_keys=True) + "\n")
