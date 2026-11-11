"""Measure Datadog conversion on the 70 real integration monitors, offline and reproducible.

Usage:
    uv run python scripts/analysis/datadog_measure.py [--monitors DIR] [--markdown OUT] [--json OUT]

Reads the BSD-3-Clause integration monitors in tests/fixtures/datadog/real/monitors (never the
synthetic hostile cases), converts them with `convert --source datadog` logic, and counts as the
scoreboard does: converted means confidence exact or approximate; manual and unsupported are their
own lines; a placeholder is never counted as converted (this source makes none). Also checks that
every converted monitor emits (NRQL and NerdGraph payload) and every other one has remediation.
Nothing here talks to Datadog or New Relic.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from importer.core.emit.common import CannotEmit, nrql_for  # noqa: E402
from importer.core.ir import Confidence, IRMonitor  # noqa: E402
from importer.core.remediation import remedies_for  # noqa: E402
from importer.core.stats import reason_pattern  # noqa: E402
from importer.sources.datadog.models import load_monitors  # noqa: E402
from importer.sources.datadog.translate import translate  # noqa: E402

CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)
DEFAULT_DIR = ROOT / "tests" / "fixtures" / "datadog" / "real" / "monitors"


def measure(folder: Path) -> dict[str, object]:
    monitors = translate(load_monitors(folder))
    by_type: dict[str, Counter[str]] = {}
    reasons: Counter[str] = Counter()
    emitted = 0
    unguided = 0
    for m in monitors:
        by_type.setdefault(m.source_type, Counter())[m.confidence.value] += 1
        if m.confidence in CONVERTED:
            try:
                nrql_for(m)
                emitted += 1
            except CannotEmit:
                reasons["not emitted"] += 1
        else:
            reasons[_first(m)] += 1
            unguided += 0 if (remedies_for(m.reasons) or m.remediation) else 1
    total = len(monitors)
    converted = sum(1 for m in monitors if m.confidence in CONVERTED)
    return {
        "total": total,
        "converted": converted,
        "emitted": emitted,
        "manual": sum(1 for m in monitors if m.confidence is Confidence.MANUAL),
        "unsupported": sum(1 for m in monitors if m.confidence is Confidence.UNSUPPORTED),
        "unguided": unguided,
        "by_type": {t: dict(c) for t, c in sorted(by_type.items())},
        "manual_reasons": dict(reasons.most_common()),
        "approximate_notes": dict(_notes(monitors).most_common()),
    }


def _first(m: IRMonitor) -> str:
    return reason_pattern(m.reasons[0].split(":")[0] if m.reasons else "no reason")


def _notes(monitors: list[IRMonitor]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for m in monitors:
        if m.confidence in CONVERTED:
            counts.update(reason_pattern(r)[:90] for r in m.reasons)
    return counts


def render(result: dict[str, object]) -> str:
    lines = [
        f"Monitors: {result['total']}. Converted (approximate or better): {result['converted']}. "
        f"Manual: {result['manual']}. Unsupported: {result['unsupported']}. Emitted as NRQL "
        f"conditions: {result['emitted']}. Without remediation steps: {result['unguided']}.",
        "",
        "| Type | converted (approximate) | manual | unsupported |",
        "|---|---:|---:|---:|",
    ]
    by_type: dict[str, dict[str, int]] = result["by_type"]  # type: ignore[assignment]
    for kind, counts in by_type.items():
        converted = counts.get("exact", 0) + counts.get("approximate", 0)
        lines.append(
            f"| {kind} | {converted} | {counts.get('manual', 0)} | {counts.get('unsupported', 0)} |"
        )
    lines += ["", "| Reason a monitor is not converted | Monitors |", "|---|---:|"]
    reasons: dict[str, int] = result["manual_reasons"]  # type: ignore[assignment]
    lines += [f"| {reason} | {n} |" for reason, n in reasons.items()]
    lines += ["", "| Note on a converted monitor | Monitors |", "|---|---:|"]
    notes: dict[str, int] = result["approximate_notes"]  # type: ignore[assignment]
    lines += [f"| {note} | {n} |" for note, n in notes.items()]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitors", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = measure(args.monitors)
    text = render(result)
    sys.stdout.write(text)
    if args.markdown:
        args.markdown.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
