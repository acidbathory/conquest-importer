"""Measure Azure conversion on the official example files, by alert type.

What is counted: every distinct alert rule that an official example (Azure REST API specs) returns
in a response body, converted to IR monitors. A Prometheus rule group counts once per alert rule.
What is not counted: examples with no resource body (delete, status, 204), exact copies of a
resource that several example files repeat, quickstart templates, dashboards, workbooks, action
groups and alert processing rules (not alert conditions), and the hostile synthetic files (they
test the reader, they are not an estate). The same method is used by docs/research/azure-baseline.md
and `tests/test_azure_baseline.py` keeps the two in step.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from importer.core.ir import Confidence, IRMonitor
from importer.sources.azure.models import load_alerts
from importer.sources.azure.translate import translate

FOLDERS = (
    ("metric_alerts", "Metric alerts"),
    ("scheduled_query_rules", "Scheduled query rules (KQL)"),
    ("activity_log_alerts", "Activity log alerts"),
    ("prometheus_rule_groups", "Prometheus rule groups"),
    ("smart_detector_alert_rules", "Smart detector rules"),
)


@dataclass
class Row:
    label: str
    monitors: list[IRMonitor] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.monitors)

    def count(self, *kinds: Confidence) -> int:
        return sum(1 for m in self.monitors if m.confidence in kinds)

    @property
    def converted(self) -> int:
        return self.count(Confidence.EXACT, Confidence.APPROXIMATE)

    def blockers(self, top: int = 3) -> list[tuple[str, int]]:
        """The first reason of each unconverted monitor, with the variable tail cut off."""
        found = Counter(
            m.reasons[0].split(":")[0]
            for m in self.monitors
            if m.confidence not in (Confidence.EXACT, Confidence.APPROXIMATE) and m.reasons
        )
        return found.most_common(top)


def measure(fixtures: Path) -> list[Row]:
    rows: list[Row] = []
    for folder, label in FOLDERS:
        alerts = load_alerts(fixtures / folder)
        rows.append(Row(label, translate(alerts)))
    return rows


def render(rows: list[Row]) -> str:
    lines = [
        "| Alert type | Rules | Converted (approximate or better) | Manual | Unsupported |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {r.label} | {r.total} | {r.converted} | {r.count(Confidence.MANUAL)} | "
        f"{r.count(Confidence.UNSUPPORTED)} |"
        for r in rows
    )
    total = sum(r.total for r in rows)
    done = sum(r.converted for r in rows)
    manual = sum(r.count(Confidence.MANUAL) for r in rows)
    unsupported = sum(r.count(Confidence.UNSUPPORTED) for r in rows)
    lines.append(f"| All | {total} | {done} | {manual} | {unsupported} |")
    return "\n".join(lines) + "\n"
