"""Grafana panel types and the New Relic widget each maps to (research note, section 4.1).

`group` says how a type counts in the coverage plan:

- core: the repository's dashboard IR can express the widget today, so the panel converts when
  its queries do;
- extra: New Relic has the widget but the IR cannot create it yet, so the panel is reported
  separately ("with extra widget types");
- none: no New Relic widget, whatever the query.
"""

from dataclasses import dataclass

from importer.sources.grafana.dashboards.reasons import OTHER_LABEL

# types the frontend renames when it opens a dashboard (PanelModel.autoMigrateAngular)
ALIASES = {
    "graph": "timeseries",
    "singlestat": "stat",
    "grafana-singlestat-panel": "stat",
    "table-old": "table",
    "grafana-piechart-panel": "piechart",
    "grafana-worldmap-panel": "geomap",
    "natel-discrete-panel": "state-timeline",
}
CORE, EXTRA, NONE = "core", "extra", "none"
# modern type -> (group, New Relic widget); an empty widget means there is none
TABLE: dict[str, tuple[str, str]] = {
    "timeseries": (CORE, "viz.line"),
    "stat": (CORE, "viz.billboard"),
    "table": (CORE, "viz.table"),
    "piechart": (CORE, "viz.pie"),
    "text": (CORE, "viz.markdown"),
    "gauge": (EXTRA, "gauge"),  # the gauge widget id is not confirmed
    "bargauge": (EXTRA, "viz.bullet"),
    "barchart": (EXTRA, "viz.bar"),
    "heatmap": (EXTRA, "viz.heatmap"),
    "histogram": (EXTRA, "viz.histogram"),
    "logs": (EXTRA, "log table"),
    "logstable": (EXTRA, "log table"),
    "trend": (EXTRA, "viz.line"),
    **dict.fromkeys(
        (
            "state-timeline",
            "status-history",
            "news",
            "alertlist",
            "dashlist",
            "geomap",
            "nodeGraph",
            "traces",
            "flamegraph",
            "canvas",
            "xychart",
            "candlestick",
            "annolist",
            "debug",
            "live",
        ),
        (NONE, ""),
    ),
}
LOG_TYPES = frozenset({"logs", "logstable"})


@dataclass(frozen=True)
class PanelType:
    label: str  # a name from the table above, or "other"; safe to put in a reason
    group: str
    widget: str


def panel_type(raw: str) -> PanelType:
    """The panel type after the frontend's renames; an unknown plugin is `other`."""
    name = ALIASES.get(raw, raw)
    entry = TABLE.get(name)
    if entry is None:
        return PanelType(OTHER_LABEL, NONE, "")
    return PanelType(name, entry[0], entry[1])
