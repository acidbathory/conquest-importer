"""How many Datadog dashboard widgets converted and how many stayed placeholders.

Data widgets are the ones that query data; notes and free text are markdown and are counted
apart, and the headings the converter adds for nested groups are items of neither. A placeholder
is never counted as converted.
"""

from collections import Counter
from dataclasses import dataclass, field

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.sources.datadog.dashboards.widgets import MARKDOWN_TEXT_KEY

HEADING = "group"
REASON_WIDTH = 60


@dataclass
class Measure:
    dashboards: int = 0
    data_widgets: int = 0
    converted: int = 0
    placeholders: int = 0
    notes: int = 0  # markdown widgets that converted (note and free_text)
    note_placeholders: int = 0
    by_reason: Counter[str] = field(default_factory=Counter)  # data placeholders, first reason

    def add(self, dashboard: IRDashboard) -> None:
        self.dashboards += 1
        for w in dashboard.widgets:
            if w.source_type == HEADING:
                continue
            is_note = w.source_type in MARKDOWN_TEXT_KEY
            if w.kind is WidgetKind.UNSUPPORTED:
                if is_note:
                    self.note_placeholders += 1
                    continue
                self.data_widgets += 1
                self.placeholders += 1
                head = (w.reasons[0] if w.reasons else "no reason").split(":")[0]
                self.by_reason[head[:REASON_WIDTH]] += 1
            elif is_note:
                self.notes += 1
            else:
                self.data_widgets += 1
                self.converted += 1


def measure(dashboards: list[IRDashboard]) -> Measure:
    total = Measure()
    for d in dashboards:
        total.add(d)
    return total
