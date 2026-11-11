"""Measure Azure scheduled query rule (KQL) conversion on the official and live fixtures, offline.

Usage:
    uv run python scripts/analysis/azure_kql_measure.py [--json OUT]

Counts as the scoreboard does (docs/dev/overseer.md): a rule is converted when its confidence is
exact or approximate; a manual or unsupported rule is not, and a placeholder never counts. Each
rule is one distinct alert as `load_alerts` returns it (the baseline method of
docs/research/azure-baseline.md). The "before" column is the measurement taken with the
pre-subset translator (every table from an agent or service was refused): 0 of 15 official
and 0 of 9 live rules.

Nothing here talks to Azure or New Relic.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from importer.core.ir import Confidence
from importer.sources.azure.models import load_alerts
from importer.sources.azure.translate import translate

ROOT = Path(__file__).resolve().parent.parent.parent
SETS = {
    "official": (ROOT / "tests" / "fixtures" / "azure" / "scheduled_query_rules", 0),
    "live": (ROOT / "tests" / "fixtures" / "azure-live" / "scheduled_query_rules", 0),
}
CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)


def measure() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for label, (folder, before) in SETS.items():
        monitors = translate(load_alerts(folder))
        done = [m for m in monitors if m.confidence in CONVERTED]
        blockers = Counter(
            m.reasons[0].split(":")[0] if ":" in m.reasons[0] else m.reasons[0][:60]
            for m in monitors
            if m.confidence not in CONVERTED and m.reasons
        )
        result[label] = {
            "rules": len(monitors),
            "converted_before": before,
            "converted_after": len(done),
            "manual": len(monitors) - len(done),
            "data_path": dict(Counter(m.data_path for m in done)),
            "blockers": dict(blockers.most_common()),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write the numbers to this file")
    args = parser.parse_args()
    numbers = measure()
    for label, row in numbers.items():
        print(
            f"{label}: {row['converted_before']} -> {row['converted_after']} of {row['rules']} "
            f"converted; data paths {row['data_path']}; blockers {row['blockers']}"
        )
    if args.json:
        args.json.write_text(json.dumps(numbers, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
