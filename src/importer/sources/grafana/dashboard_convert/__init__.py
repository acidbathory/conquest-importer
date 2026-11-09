"""Grafana dashboards to New Relic dashboards.

Importing this package registers the `grafana` dashboard source (so `importer convert-dashboards
--source grafana` works), the manual-step rules and the taxonomy phrases for the converter's own
reasons. The coverage planner (`sources/grafana/dashboards/`) decides which panels convert; this
package writes them. The fixtures are dashboards, not the alert exports that the improvement
measure converts, so they are not registered with `register_fixtures`.
"""

from importer.sources.base import DashboardSource, register_dashboards
from importer.sources.grafana.dashboard_convert import (
    remediation_rules,  # noqa: F401  (registers its rules)
)
from importer.sources.grafana.dashboard_convert.translate import (
    Item,
    load_items,
    translate_dashboards,
)

DASHBOARDS: DashboardSource[Item] = DashboardSource(
    name="grafana",
    input_help=(
        "file or folder of Grafana dashboard JSON (`importer extract-grafana-dashboards` writes it)"
    ),
    load=load_items,
    translate=translate_dashboards,
)
register_dashboards(DASHBOARDS)
