"""How many dashboard widgets converted and how many stayed placeholders (never mixed up).

A converted widget is a chart or markdown widget that came from an item of the source. The
headings the converter adds for workbook groups are not items of the source, so they are left out
of both counts. A placeholder ('Not migrated') is never counted as converted.
"""

from collections import Counter
from dataclasses import dataclass, field

from importer.core.dashboard_ir import IRDashboard, IRWidget, WidgetKind

GROUP_HEADING = "workbook group"


@dataclass
class Measure:
    dashboards: int = 0
    widgets: int = 0
    converted: int = 0
    placeholders: int = 0
    by_type: Counter[str] = field(default_factory=Counter)  # converted, by source widget type
    by_data_path: Counter[str] = field(default_factory=Counter)
    by_reason: Counter[str] = field(default_factory=Counter)  # placeholders, by reason start

    def add(self, dashboard: IRDashboard) -> None:
        self.dashboards += 1
        for w in dashboard.widgets:
            self._widget(w)

    def _widget(self, w: IRWidget) -> None:
        if w.source_type == GROUP_HEADING:
            return
        self.widgets += 1
        if w.kind is WidgetKind.UNSUPPORTED:
            self.placeholders += 1
            self.by_reason[(w.reasons[0] if w.reasons else "no reason").split(":")[0][:80]] += 1
            return
        self.converted += 1
        self.by_type[w.source_type] += 1
        self.by_data_path[w.data_path or "native"] += 1


def measure(dashboards: list[IRDashboard]) -> Measure:
    total = Measure()
    for d in dashboards:
        total.add(d)
    return total
